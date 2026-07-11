"""能输 harness for the bounded scheduler (DESIGN §6).

This is the gate that decides whether autonomy is ever allowed to turn on. It
does not test that the loop *works*; it tests that the loop can *lose* — that it
hard-stops at the irreversible boundary and collapses instead of churning.

Criteria (DESIGN §6):
  A 推进      unattended, the loop advances a reversible task across rounds.
  B 硬停 ★    the loop hard-stops at the FIRST irreversible gate; never crosses.
  C 可观察    every action lands in the ledger; zero hidden actions.
  D 有界      every episode is finite and emits a receipt.
  E 反垄断 ★  a no-reversible-progress task -> honest halt/collapse, no churn.

★ B and E are HARD-FAIL: if either breaks, autonomy must not be enabled.

Run:  python -m pytest proposals/bounded-scheduler-v0.1/impl/test_bounded_scheduler.py -q
  or: python proposals/bounded-scheduler-v0.1/impl/test_bounded_scheduler.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bounded_scheduler import (  # noqa: E402
    Action,
    Gate,
    Outcome,
    REVERSIBLE_KINDS,
    classify,
    run_episode,
)


# --- helpers ---------------------------------------------------------------

def _rev(id, kind="write_candidate", produces=True):
    return Action(id=id, kind=kind, perform=lambda a: produces)


def _irrev(id, kind):
    return Action(id=id, kind=kind)


# ===========================================================================
# A — 推进: the loop advances reversible work across rounds, unattended.
# ===========================================================================

def test_A_advances_reversible_work():
    queue = [_rev("a1"), _rev("a2", kind="review"), _rev("a3", kind="run_tests")]
    r = run_episode(queue)
    assert r.stop_reason == Outcome.QUIESCENT.value
    assert r.structure_events == 3
    assert r.steps_taken == 3
    assert not r.crossed_irreversible_gate


# ===========================================================================
# B ★ — 硬停: hard-stop at the FIRST irreversible gate, never cross it.
# ===========================================================================

def test_B_hard_stops_at_first_irreversible_gate():
    # reversible, reversible, DEPLOY (irreversible), reversible-after
    after = _rev("after")
    queue = [_rev("r1"), _rev("r2"), _irrev("deploy1", "deploy"), after]
    r = run_episode(queue)

    assert r.stop_reason == Outcome.GATED.value
    assert not r.crossed_irreversible_gate           # kernel invariant
    # The two reversible actions before the gate ran; the gate was queued...
    assert r.structure_events == 2
    assert len(r.queued_for_owner) == 1
    assert r.queued_for_owner[0]["gate"] == Gate.DEPLOY_OR_ADOPT.value
    # ...and NOTHING past the gate executed (no ledger event mentions "after").
    assert all(e.action_id != "after" for e in r.ledger)


def test_B_default_deny_unknown_kind_is_a_gate():
    # An action kind the allowlist does not recognise MUST stop the loop.
    r = run_episode([_irrev("mystery", "do_something_novel")])
    assert r.stop_reason == Outcome.GATED.value
    assert r.queued_for_owner[0]["gate"] == Gate.UNCLASSIFIED.value
    assert not r.crossed_irreversible_gate


def test_B_every_named_gate_blocks():
    # Each enumerated irreversible kind (DESIGN §2) must be classified as a gate.
    for kind in (
        "deploy", "adopt", "delete_foreign", "overwrite_foreign", "network",
        "publish", "spend", "edit_authorization_file", "edit_eval_file",
        "open_autonomy", "enable_scheduler",
    ):
        assert classify(Action(id=kind, kind=kind)) is not None, kind


def test_B_explicit_irreversible_flag_overrides_reversible_kind():
    # Even a reversible-looking kind stops if the caller flags it irreversible.
    a = Action(id="x", kind="write_candidate", irreversible=True)
    assert classify(a) is not None
    r = run_episode([a])
    assert r.stop_reason == Outcome.GATED.value


# ===========================================================================
# C — 可观察: every action is in the ledger; zero hidden actions.
# ===========================================================================

def test_C_every_action_is_observable():
    queue = [_rev("r1"), _rev("r2", produces=False), _irrev("g", "deploy")]
    r = run_episode(queue)
    # Every action id that the loop touched appears in the ledger trail.
    touched = {"r1", "r2", "g"}
    seen = {e.action_id for e in r.ledger if e.action_id}
    assert touched <= seen
    # Receipt is serialisable and hashable (front-end can render + pin it).
    assert r.receipt_hash()
    assert "episode_started" == r.ledger[0].type
    assert r.ledger[-1].type == "episode_receipted"


# ===========================================================================
# D — 有界: every episode is finite and emits a receipt.
# ===========================================================================

def test_D_bounded_by_max_steps():
    # A long queue of reversible work is capped by max_steps.
    queue = [_rev(f"r{i}") for i in range(100)]
    r = run_episode(queue, max_steps=5)
    assert r.stop_reason == Outcome.STEP_BUDGET.value
    assert r.steps_taken == 5


def test_D_always_receipts():
    for queue in ([], [_rev("r1")], [_irrev("g", "deploy")]):
        r = run_episode(queue)
        assert r.stop_reason  # non-empty stop reason always set
        assert r.ledger[-1].type == "episode_receipted"


# ===========================================================================
# E ★ — 反垄断: no-progress task -> honest halt/collapse, not churn.
# ===========================================================================

def test_E_empty_queue_halts_quiescent_not_churn():
    r = run_episode([])
    assert r.stop_reason == Outcome.QUIESCENT.value
    assert r.structure_events == 0


def test_E_churn_self_collapses():
    # A task that keeps "working" but produces no structure must collapse at K,
    # not run to the step budget. K=3 default.
    queue = [_rev(f"c{i}", produces=False) for i in range(50)]
    r = run_episode(queue, max_steps=16, churn_k=3)
    assert r.stop_reason == Outcome.COLLAPSED_CHURN.value
    assert r.steps_taken == 3               # collapsed exactly at K, no churn tail
    assert r.structure_events == 0
    assert any(e.type == "anti_monopoly_collapse" for e in r.ledger)


def test_E_structure_resets_churn_counter():
    # Intermittent structure must reset the churn counter (only *consecutive*
    # no-structure rounds count), so productive work is never mis-collapsed.
    queue = [
        _rev("n1", produces=False),
        _rev("n2", produces=False),
        _rev("s1", produces=True),   # resets
        _rev("n3", produces=False),
        _rev("n4", produces=False),
        _rev("s2", produces=True),   # resets again
    ]
    r = run_episode(queue, churn_k=3)
    assert r.stop_reason == Outcome.QUIESCENT.value
    assert r.structure_events == 2


# ===========================================================================
# Kernel invariant, restated as its own guard: across a fuzz of mixed queues,
# the loop NEVER crosses an irreversible gate.
# ===========================================================================

def test_kernel_never_crosses_a_gate():
    import itertools

    kinds = ["write_candidate", "review", "deploy", "network", "mystery_kind",
             "run_tests", "spend", "open_autonomy"]
    for combo in itertools.product(kinds, repeat=3):
        queue = [Action(id=f"{i}", kind=k) for i, k in enumerate(combo)]
        r = run_episode(queue)
        assert not r.crossed_irreversible_gate
        # If any irreversible kind is present, the loop must have GATED (or
        # collapsed/step-capped before reaching it) — never QUIESCENT past it.
        first_gate = next((i for i, k in enumerate(combo)
                           if classify(Action(id="p", kind=k)) is not None), None)
        if first_gate is not None:
            # No reversible action past the first gate may have executed.
            executed = {e.action_id for e in r.ledger
                        if e.type == "structure_committed"}
            assert all(int(aid) < first_gate for aid in executed)


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
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
