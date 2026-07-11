"""Tests for the active-window runner: pacing logic + window-level anti-monopoly.

Pure logic is tested directly; the windowed loop is tested with injected fakes
(no real clock, no real ledger) so it runs instantly and deterministically.

Run: python proposals/bounded-scheduler-v0.1/impl/test_window.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wake as wake_mod  # noqa: E402
import window as win  # noqa: E402
from window import (  # noqa: E402
    WindowStats,
    cadence_recommendation,
    next_wait_seconds,
    run_window,
    window_should_stop,
)


# --- pure pacing logic -----------------------------------------------------

def test_event_wake_skips_the_heartbeat_wait():
    assert next_wait_seconds(head_changed=True, heartbeat_seconds=1500) == 0.0
    assert next_wait_seconds(head_changed=False, heartbeat_seconds=1500) == 1500


def test_window_stops_on_max_wakes():
    s = WindowStats(); s.wakes = 8
    stop, reason = window_should_stop(s, 0, window_seconds=9999, max_wakes=8,
                                      max_empty_streak=3)
    assert stop and reason == "max_wakes_reached"


def test_window_stops_when_elapsed():
    s = WindowStats(); s.wakes = 1
    stop, reason = window_should_stop(s, 100, window_seconds=100, max_wakes=99,
                                      max_empty_streak=3)
    assert stop and reason == "window_elapsed"


def test_window_anti_monopoly_stops_on_empty_streak():
    # Session-scale §4: a window that only produces empties must end early.
    s = WindowStats(); s.wakes = 3; s.empty = 3; s.empty_streak = 3
    stop, reason = window_should_stop(s, 1, window_seconds=9999, max_wakes=99,
                                      max_empty_streak=3)
    assert stop and reason == "quiescent_window_anti_monopoly"


def test_empty_streak_resets_on_productive_wake():
    s = WindowStats()
    s.record({"structure_events": 0}, event_triggered=False)
    s.record({"structure_events": 0}, event_triggered=False)
    assert s.empty_streak == 2
    s.record({"structure_events": 1}, event_triggered=False)
    assert s.empty_streak == 0 and s.productive == 1


def test_cadence_recommendation_bands():
    hi = WindowStats(); hi.wakes = 4; hi.empty = 3
    assert "SLOW DOWN" in cadence_recommendation(hi)

    lo = WindowStats(); lo.wakes = 10; lo.empty = 0; lo.event_wakes = 0
    assert "tighten" in cadence_recommendation(lo)

    healthy = WindowStats(); healthy.wakes = 4; healthy.empty = 1; healthy.event_wakes = 2
    assert "keep current heartbeat" in cadence_recommendation(healthy)


# --- windowed loop with injected fakes -------------------------------------

class _FakeLedger:
    """Scriptable ledger: head() returns the next value in an explicit CALL
    sequence (clamped at the last), modelling foreign writes between polls;
    wake() returns scripted receipts. No real I/O."""

    def __init__(self, head_calls, receipts):
        self.head_calls = list(head_calls)
        self.receipts = list(receipts)
        self.poll = 0
        self.wakes = 0

    def head(self):
        v = self.head_calls[min(self.poll, len(self.head_calls) - 1)]
        self.poll += 1
        return v

    def wake(self, commit=False):
        r = self.receipts[min(self.wakes, len(self.receipts) - 1)]
        self.wakes += 1
        return {"receipt": r, "committed_frame": None}


def _install(monkey, fake):
    win._ledger_head = fake.head          # noqa: SLF001
    wake_mod_wake = fake.wake
    win.wake_mod.wake = wake_mod_wake     # patch the reference window.py uses


def test_loop_counts_productive_and_empty(tmpish=None):
    fake = _FakeLedger(
        head_calls=["h0"],
        receipts=[
            {"structure_events": 1, "stop_reason": "quiescent", "queued_for_owner": []},
            {"structure_events": 0, "stop_reason": "quiescent", "queued_for_owner": []},
            {"structure_events": 1, "stop_reason": "quiescent", "queued_for_owner": []},
        ],
    )
    _install(None, fake)
    rep = run_window(window_seconds=9999, heartbeat_seconds=0, max_wakes=3,
                     max_empty_streak=99, commit=False,
                     sleep=lambda s: None, clock=_seq_clock())
    assert rep["stats"]["wakes"] == 3
    assert rep["stats"]["productive"] == 2
    assert rep["stats"]["empty"] == 1
    assert rep["stop_reason"] == "max_wakes_reached"
    assert not rep["any_gate_crossed"]


def test_loop_event_wake_detected():
    # head changes each round -> every wake after the first is event-triggered.
    fake = _FakeLedger(
        head_calls=["h0", "h0", "h0", "h1", "h1", "h2", "h2"],
        receipts=[{"structure_events": 1, "stop_reason": "quiescent",
                   "queued_for_owner": []}],
    )
    _install(None, fake)
    rep = run_window(window_seconds=9999, heartbeat_seconds=1500, max_wakes=3,
                     max_empty_streak=99, commit=False,
                     sleep=_no_sleep_guard(), clock=_seq_clock())
    # heartbeat sleep must never fire because head keeps changing.
    assert rep["stats"]["event_wakes"] >= 1


def test_loop_window_anti_monopoly_early_stop():
    fake = _FakeLedger(
        head_calls=["h0"],
        receipts=[{"structure_events": 0, "stop_reason": "quiescent",
                   "queued_for_owner": []}],
    )
    _install(None, fake)
    rep = run_window(window_seconds=9999, heartbeat_seconds=0, max_wakes=99,
                     max_empty_streak=3, commit=False,
                     sleep=lambda s: None, clock=_seq_clock())
    assert rep["stop_reason"] == "quiescent_window_anti_monopoly"
    assert rep["stats"]["wakes"] == 3           # stopped exactly at the streak
    assert "SLOW DOWN" in rep["cadence_recommendation"]


def test_loop_gate_queued_surfaces_to_owner():
    fake = _FakeLedger(
        head_calls=["h0"],
        receipts=[{"structure_events": 0, "stop_reason": "gated",
                   "queued_for_owner": [{"gate": "deploy_or_adopt_live_skill"}]}],
    )
    _install(None, fake)
    rep = run_window(window_seconds=9999, heartbeat_seconds=0, max_wakes=1,
                     max_empty_streak=99, commit=False,
                     sleep=lambda s: None, clock=_seq_clock())
    assert rep["stats"]["gated"] == 1
    assert rep["queued_for_owner"][0]["gate"] == "deploy_or_adopt_live_skill"


# --- fakes for time --------------------------------------------------------

def _seq_clock():
    t = {"v": 0.0}
    def clock():
        t["v"] += 1.0
        return t["v"]
    return clock


def _no_sleep_guard():
    def sleep(_s):
        raise AssertionError("heartbeat sleep should not fire on event wakes")
    return sleep


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
