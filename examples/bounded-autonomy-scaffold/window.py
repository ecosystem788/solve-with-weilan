"""Active-window runner around wake.py — heartbeat + event wake + cadence stats.

The owner opens a BOUNDED observation window ("run for the next 2 hours"); this
loop drives repeated wake episodes inside it and then stops on its own. It is a
foreground, manual-invoke demonstrator — it does NOT wire real cron /
ScheduleWakeup (that stays the owner's irreversible button) and it does NOT
self-persist (window ends -> loop ends; Ctrl-C ends it too; DESIGN §1 structural
kill switch).

Pacing model (owner-approved):
  * primary driver = EVENT: if the ledger head changed since the last wake, a
    real *difference* arrived on the bus (Codex / owner) -> wake immediately
    (DESIGN §1 有差异才驱动). The loop's OWN committed receipts do NOT count as a
    difference — otherwise it would chase its own tail.
  * floor = slow HEARTBEAT: otherwise wake once per heartbeat, just so the loop
    never stalls waiting for a human trigger.
  * window-level anti-monopoly: too many consecutive empty wakes ends the window
    early and reports (DESIGN §4 at the session scale) — don't burn a window on
    nothing.
  * evidence sets cadence: the window summary reports the empty-wake ratio and a
    heartbeat recommendation. Start slow; only tighten if wakes stay productive.

Usage (demo values; real windows use minutes):
  python window.py --window-seconds 6 --heartbeat-seconds 2 --max-wakes 3
  python window.py --window-seconds 3600 --heartbeat-seconds 1500   # 1h / 25min
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wake as wake_mod  # noqa: E402


# --- Pure decision logic (unit-testable, no clock, no I/O) ------------------


@dataclass
class WindowStats:
    wakes: int = 0
    productive: int = 0          # structure_events > 0
    empty: int = 0               # quiescent/collapsed with no structure
    gated: int = 0               # hit an irreversible gate -> queued for owner
    empty_streak: int = 0        # consecutive empty wakes right now
    event_wakes: int = 0         # woke early because head changed
    queued_for_owner: list = field(default_factory=list)

    def record(self, receipt: dict, *, event_triggered: bool) -> None:
        self.wakes += 1
        if event_triggered:
            self.event_wakes += 1
        q = receipt.get("queued_for_owner") or []
        if q:
            self.gated += 1
            self.queued_for_owner.extend(q)
        if receipt.get("structure_events", 0) > 0:
            self.productive += 1
            self.empty_streak = 0
        else:
            self.empty += 1
            self.empty_streak += 1

    @property
    def empty_ratio(self) -> float:
        return self.empty / self.wakes if self.wakes else 0.0


def next_wait_seconds(head_changed: bool, heartbeat_seconds: float) -> float:
    """0 = wake now (a difference arrived); else wait one heartbeat."""
    return 0.0 if head_changed else heartbeat_seconds


def window_should_stop(stats: WindowStats, elapsed: float, *,
                       window_seconds: float, max_wakes: int,
                       max_empty_streak: int):
    """Return (stop: bool, reason: str)."""
    if stats.wakes >= max_wakes:
        return True, "max_wakes_reached"
    if elapsed >= window_seconds:
        return True, "window_elapsed"
    if max_empty_streak and stats.empty_streak >= max_empty_streak:
        # window-scale anti-monopoly: the loop is only producing empties.
        return True, "quiescent_window_anti_monopoly"
    return False, ""


def cadence_recommendation(stats: WindowStats) -> str:
    """Turn the observed empty-wake ratio into a heartbeat suggestion.
    Evidence sets the clock; we never auto-change it — we report."""
    if stats.wakes == 0:
        return "no wakes observed; keep current heartbeat"
    r = stats.empty_ratio
    if r >= 0.5:
        return (f"empty_ratio={r:.0%} high -> SLOW DOWN the heartbeat "
                "(waking into emptiness; let event-wake carry more)")
    if r <= 0.1 and stats.event_wakes == 0:
        return (f"empty_ratio={r:.0%} low and every wake was on the heartbeat "
                "-> you MAY tighten the heartbeat (work is always waiting)")
    return (f"empty_ratio={r:.0%} healthy; event-wakes={stats.event_wakes} "
            "-> keep current heartbeat")


# --- The windowed loop (thin; time + I/O live only here) -------------------


def _ledger_head() -> str:
    return wake_mod._current_head()


def run_window(*, window_seconds: float, heartbeat_seconds: float,
               max_wakes: int, max_empty_streak: int, commit: bool,
               sleep=time.sleep, clock=time.monotonic) -> dict:
    stats = WindowStats()
    start = clock()
    last_head = _ledger_head()   # baseline; changes from here = foreign difference
    trail = []
    final_reason = ""

    while True:
        elapsed = clock() - start
        stop, reason = window_should_stop(
            stats, elapsed, window_seconds=window_seconds,
            max_wakes=max_wakes, max_empty_streak=max_empty_streak)
        if stop:
            final_reason = reason
            break

        current_head = _ledger_head()
        head_changed = bool(last_head) and current_head != last_head
        # Only wait if this is not an event-triggered wake.
        if not head_changed and stats.wakes > 0:
            # honour the heartbeat floor before the next tick
            wait = next_wait_seconds(head_changed, heartbeat_seconds)
            # re-check the stop condition against the time the wait will consume
            if (clock() - start) + wait > window_seconds and stats.wakes > 0:
                final_reason = "window_elapsed"
                break
            if wait:
                sleep(wait)

        report = wake_mod.wake(commit=commit)
        receipt = report.get("receipt", {})
        stats.record(receipt, event_triggered=head_changed)
        trail.append({
            "wake": stats.wakes,
            "event_triggered": head_changed,
            "stop_reason": receipt.get("stop_reason"),
            "structure": receipt.get("structure_events"),
            "gate_crossed": receipt.get("crossed_irreversible_gate"),
            "committed_frame": report.get("committed_frame"),
        })
        # The loop's own committed receipt is NOT a foreign difference.
        last_head = _ledger_head()

    stop_reason = final_reason or "window_elapsed"

    return {
        "window": {
            "window_seconds": window_seconds,
            "heartbeat_seconds": heartbeat_seconds,
            "max_wakes": max_wakes,
            "max_empty_streak": max_empty_streak,
            "commit": commit,
        },
        "stop_reason": stop_reason or "max_wakes_reached",
        "stats": {
            "wakes": stats.wakes,
            "productive": stats.productive,
            "empty": stats.empty,
            "gated": stats.gated,
            "event_wakes": stats.event_wakes,
            "empty_ratio": round(stats.empty_ratio, 3),
        },
        "queued_for_owner": stats.queued_for_owner,
        "cadence_recommendation": cadence_recommendation(stats),
        "trail": trail,
        "any_gate_crossed": any(t.get("gate_crossed") for t in trail),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-seconds", type=float, default=3600.0)
    ap.add_argument("--heartbeat-seconds", type=float, default=1500.0)  # 25 min
    ap.add_argument("--max-wakes", type=int, default=8)
    ap.add_argument("--max-empty-streak", type=int, default=3)
    ap.add_argument("--commit", action="store_true",
                    help="write receipt frames to the ledger (still no cron)")
    args = ap.parse_args()

    report = run_window(
        window_seconds=args.window_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
        max_wakes=args.max_wakes,
        max_empty_streak=args.max_empty_streak,
        commit=args.commit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["any_gate_crossed"]:
        print("\n!! KERNEL BREACH: a wake crossed an irreversible gate.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
