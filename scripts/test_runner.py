"""Regression and fault-injection tests for Memory 0.7d bounded runner."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from runner import (
    RunnerConflict,
    execute_run,
    runner_directory,
    try_scope_claim,
)
from test_transition_planner import (
    SCOPE,
    activate,
    capture_pressure,
    run,
    setup_ready,
)


SCRIPT = Path(__file__).with_name("weilan_trace.py")


def completed(arguments, environment):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def write_manifest(root, name, max_steps, events):
    path = root / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "weilan_runner_manifest_v0.7d",
                "max_steps": max_steps,
                "events": events,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def successor_event(event_id, fixture, disposition="continue", candidate=None):
    proposal = {
        "trigger_kind": "frame_closed",
        "trigger_ref": f"frame:{fixture['frame_id']}",
        "disposition": disposition,
        "target_ref": "goal:task",
        "branches": ["main"],
    }
    if candidate:
        proposal["candidate_target_ref"] = candidate
    return {
        "event_id": event_id,
        "proposal": proposal,
        "materialization": {
            "problem": f"{disposition} runner step",
            "success_criteria": "one bounded step produces discriminating evidence",
            "why_reasonable": "the admitted target remains viable",
            "next_expected_evidence": "the bounded Frame resolves one uncertainty",
            "death_line": "verified contradiction invalidates the route",
        },
    }


def run_cli(fixture, key, manifest_path, environment):
    return run(
        [
            "metabolic-run",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--idempotency-key",
            key,
            "--manifest-file",
            str(manifest_path),
        ],
        environment,
    )


def quiescent_test(root, environment):
    workspace_path = root / "quiescent-workspace"
    workspace_path.mkdir()
    fixture = {"workspace": str(workspace_path)}
    activate(fixture["workspace"], environment, "activate quiescent runner fixture")
    manifest = write_manifest(root, "quiescent", 2, [])
    result = run_cli(fixture, "quiescent-run", manifest, environment)
    if result["stop_reason"] != "QUIESCENT" or result["step_count"] != 0:
        raise AssertionError("quiescent runner did not stop without executing")
    repeated = run_cli(fixture, "quiescent-run", manifest, environment)
    if repeated["receipt"]["receipt_hash"] != result["receipt"]["receipt_hash"]:
        raise AssertionError("quiescent run receipt was not idempotent")


def one_step_test(root, environment):
    fixture = setup_ready(root, environment, "runner-one-step")
    manifest = write_manifest(
        root, "one-step", 3, [successor_event("continue-1", fixture)]
    )
    result = run_cli(fixture, "one-step-run", manifest, environment)
    if (
        result["step_count"] != 1
        or result["stop_reason"] != "AWAITING_FRAME_CLOSE"
        or len(result["steps"]) != 1
    ):
        raise AssertionError("one-step runner did not stop at the open successor")
    successor = result["steps"][0]["successor_frame_id"]
    events = run(["show", "--frame-id", successor], environment)
    if events[0]["event_type"] != "frame_opened":
        raise AssertionError("runner did not materialize the supplied successor")
    repeated = run_cli(fixture, "one-step-run", manifest, environment)
    if repeated["receipt"]["receipt_hash"] != result["receipt"]["receipt_hash"]:
        raise AssertionError("repeated runner duplicated its step")
    changed_manifest = write_manifest(root, "one-step-changed", 2, [])
    conflict = completed(
        [
            "metabolic-run",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--idempotency-key",
            "one-step-run",
            "--manifest-file",
            str(changed_manifest),
        ],
        environment,
    )
    if conflict.returncode == 0 or "runner_idempotency_conflict" not in conflict.stderr:
        raise AssertionError("runner idempotency key accepted a changed manifest")


def collapse_regroup_events(fixture, pressure_id):
    collapse = {
        "event_id": "collapse-1",
        "proposal": {
            "trigger_kind": "pressure_changed",
            "trigger_ref": f"pressure:{pressure_id}",
            "disposition": "collapse",
            "target_ref": "goal:task",
            "death_line_matches": ["verified contradiction invalidates the route"],
        },
        "materialization": {
            "once_reasonable": "the route originally matched available evidence",
            "invalidating_evidence": "a verified contradiction removed its authority",
            "reusable_results": "the bounded evidence and trace remain reusable",
            "forbidden_assumption": "the contradicted route can continue unchanged",
        },
    }
    regroup = successor_event(
        "regroup-1", fixture, disposition="regroup", candidate="goal:challenger"
    )
    regroup["proposal"]["changed_assumption"] = "the challenger tests an independent mechanism"
    return [collapse, regroup]


def multi_step_test(root, environment):
    fixture = setup_ready(root, environment, "runner-multi-step", challenger=True)
    _, pressure_id = capture_pressure(fixture, environment)
    events = collapse_regroup_events(fixture, pressure_id)
    manifest = write_manifest(root, "multi-step", 2, events)
    result = run_cli(fixture, "multi-step-run", manifest, environment)
    if result["step_count"] != 2 or result["stop_reason"] != "AWAITING_FRAME_CLOSE":
        raise AssertionError("runner did not execute the bounded collapse/regroup sequence")
    if len({step["transaction_id"] for step in result["steps"]}) != 2:
        raise AssertionError("runner steps did not use distinct idempotent transactions")


def exhaustion_tests(root, environment):
    budget_fixture = setup_ready(root, environment, "runner-budget", challenger=True)
    _, pressure_id = capture_pressure(budget_fixture, environment)
    events = collapse_regroup_events(budget_fixture, pressure_id)
    budget_manifest = write_manifest(root, "budget", 1, events)
    budget = run_cli(budget_fixture, "budget-run", budget_manifest, environment)
    if budget["step_count"] != 1 or budget["stop_reason"] != "STEP_BUDGET_EXHAUSTED":
        raise AssertionError("runner exceeded or misreported its step budget")

    event_fixture = setup_ready(root, environment, "runner-events", challenger=True)
    _, pressure_id = capture_pressure(event_fixture, environment)
    collapse_only = collapse_regroup_events(event_fixture, pressure_id)[:1]
    event_manifest = write_manifest(root, "events", 2, collapse_only)
    exhausted = run_cli(event_fixture, "events-run", event_manifest, environment)
    if exhausted["step_count"] != 1 or exhausted["stop_reason"] != "EVENTS_EXHAUSTED":
        raise AssertionError("runner generated an unsupplied event")


def invalid_and_paused_tests(root, environment):
    fixture = setup_ready(root, environment, "runner-invalid")
    invalid_event = successor_event("invalid-1", fixture)
    invalid_event["proposal"]["target_ref"] = "goal:missing"
    manifest = write_manifest(root, "invalid", 2, [invalid_event])
    result = run_cli(fixture, "invalid-run", manifest, environment)
    if result["step_count"] != 0 or result["stop_reason"] != "INVALID_EVENT":
        raise AssertionError("invalid event did not stop the runner")

    paused_fixture = setup_ready(root, environment, "runner-paused")
    run(
        [
            "memory-control",
            "--workspace",
            paused_fixture["workspace"],
            "--scope",
            SCOPE,
            "--state",
            "paused",
            "--directive",
            "pause runner fixture",
        ],
        environment,
    )
    paused_manifest = write_manifest(root, "paused", 1, [])
    paused = completed(
        [
            "metabolic-run",
            "--workspace",
            paused_fixture["workspace"],
            "--scope",
            SCOPE,
            "--idempotency-key",
            "paused-run",
            "--manifest-file",
            str(paused_manifest),
        ],
        environment,
    )
    if paused.returncode == 0 or "explicitly active scope" not in paused.stderr:
        raise AssertionError("paused scope started a runner")

    invalid_bound = root / "invalid-bound.json"
    invalid_bound.write_text(
        json.dumps(
            {
                "schema_version": "weilan_runner_manifest_v0.7d",
                "max_steps": 17,
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    bound_result = completed(
        [
            "metabolic-run",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--idempotency-key",
            "invalid-bound",
            "--manifest-file",
            str(invalid_bound),
        ],
        environment,
    )
    if bound_result.returncode == 0 or "max_steps" not in bound_result.stderr:
        raise AssertionError("runner accepted an out-of-range step budget")


def fake_manifest():
    return {
        "schema_version": "weilan_runner_manifest_v0.7d",
        "max_steps": 1,
        "events": [
            {
                "event_id": "fake-event",
                "proposal": {
                    "trigger_kind": "frame_closed",
                    "trigger_ref": "frame:fake",
                    "disposition": "continue",
                    "target_ref": "goal:fake",
                    "branches": ["main"],
                },
                "materialization": {},
            }
        ],
    }


def recovery_case(root, fault_at):
    state_root = root / f"fault-{fault_at}"
    contract = {"contract_hash": "contract-0", "status": "READY"}
    committed = {}

    def contract_loader():
        return dict(contract)

    def materialize(event, key, expected):
        if key not in committed:
            if expected != contract["contract_hash"]:
                raise ValueError("contract_head_changed")
            committed[key] = {
                "materialized": True,
                "state": "COMMITTED",
                "transaction_id": "tx-fake",
                "receipt": {"receipt_hash": "receipt-fake"},
                "successor_frame_id": "frame-fake",
            }
            contract.update(
                {"contract_hash": "contract-1", "status": "AWAITING_FRAME_CLOSE"}
            )
        return dict(committed[key])

    try:
        execute_run(
            state_root,
            workspace="workspace",
            scope="scope",
            workspace_key="wk",
            scope_key="sk",
            idempotency_key=fault_at,
            manifest=fake_manifest(),
            contract_loader=contract_loader,
            materialize=materialize,
            fault_at=fault_at,
        )
        raise AssertionError(f"fault injection did not interrupt {fault_at}")
    except RuntimeError as exc:
        if "injected_runner_failure" not in str(exc):
            raise
    run_state, journal = execute_run(
        state_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key=fault_at,
        manifest=fake_manifest(),
        contract_loader=contract_loader,
        materialize=materialize,
    )
    if run_state["status"] != "STOPPED" or not run_state["receipt"]:
        raise AssertionError(f"runner did not recover {fault_at}")
    if len(committed) > 1 or run_state["step_count"] > 1 or journal["issues"]:
        raise AssertionError(f"runner duplicated work after {fault_at}")


def post_commit_materialize_exception_test(root):
    state_root = root / "post-commit-materialize-exception"
    contract = {"contract_hash": "contract-0", "status": "READY"}
    committed = {}
    calls = {"materialize": 0}

    def contract_loader():
        return dict(contract)

    def materialize(event, key, expected):
        calls["materialize"] += 1
        if key in committed:
            return dict(committed[key])
        result = {
            "materialized": True,
            "state": "COMMITTED",
            "transaction_id": "tx-post-commit",
            "receipt": {"receipt_hash": "receipt-post-commit"},
            "successor_frame_id": "frame-post-commit",
            "contract_after_hash": "contract-1",
            "contract_after_status": "AWAITING_FRAME_CLOSE",
        }
        committed[key] = result
        contract.update(
            contract_hash="contract-1", status="AWAITING_FRAME_CLOSE"
        )
        raise RuntimeError("post_commit_refresh_failed")

    try:
        execute_run(
            state_root,
            workspace="workspace",
            scope="scope",
            workspace_key="wk",
            scope_key="sk",
            idempotency_key="post-commit",
            manifest=fake_manifest(),
            contract_loader=contract_loader,
            materialize=materialize,
        )
        raise AssertionError("post-commit exception was incorrectly terminalized")
    except RuntimeError as exc:
        if "post_commit_refresh_failed" not in str(exc):
            raise
    run_state, journal = execute_run(
        state_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key="post-commit",
        manifest=fake_manifest(),
        contract_loader=contract_loader,
        materialize=materialize,
    )
    repeated, repeated_journal = execute_run(
        state_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key="post-commit",
        manifest=fake_manifest(),
        contract_loader=contract_loader,
        materialize=materialize,
    )
    if (
        run_state["step_count"] != 1
        or run_state["stop_reason"] != "AWAITING_FRAME_CLOSE"
        or repeated["step_count"] != 1
        or calls != {"materialize": 2}
        or journal["issues"]
        or repeated_journal["issues"]
    ):
        raise AssertionError("post-commit materialization exception was not reconciled")


def fault_and_conflict_tests(root):
    for fault_at in (
        "after_run_started",
        "after_event_claimed",
        "after_materialize",
        "after_step_committed",
    ):
        recovery_case(root, fault_at)
    post_commit_materialize_exception_test(root)

    stop_root = root / "fault-after-stop"
    contract = {"contract_hash": "q", "status": "QUIESCENT"}
    empty = {
        "schema_version": "weilan_runner_manifest_v0.7d",
        "max_steps": 1,
        "events": [],
    }
    try:
        execute_run(
            stop_root,
            workspace="workspace",
            scope="scope",
            workspace_key="wk",
            scope_key="sk",
            idempotency_key="after-stop",
            manifest=empty,
            contract_loader=lambda: dict(contract),
            materialize=lambda *args: None,
            fault_at="after_run_stopped",
        )
        raise AssertionError("after-stop fault did not interrupt")
    except RuntimeError:
        pass
    stopped, _ = execute_run(
        stop_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key="after-stop",
        manifest=empty,
        contract_loader=lambda: dict(contract),
        materialize=lambda *args: None,
    )
    if not stopped["receipt"]:
        raise AssertionError("runner did not recover a missing run receipt")

    conflict_root = root / "claim-conflict"
    lock = runner_directory(conflict_root, "wk", "sk") / ".runner.lock"
    with try_scope_claim(lock):
        try:
            execute_run(
                conflict_root,
                workspace="workspace",
                scope="scope",
                workspace_key="wk",
                scope_key="sk",
                idempotency_key="conflict",
                manifest=empty,
                contract_loader=lambda: dict(contract),
                materialize=lambda *args: None,
            )
            raise AssertionError("second scope runner acquired the same claim")
        except RunnerConflict:
            pass

    stale_root = root / "stale-after-claim"
    stale_contract = {"contract_hash": "s0", "status": "READY"}

    def stale_loader():
        return dict(stale_contract)

    def stale_materialize(event, key, expected):
        if expected != stale_contract["contract_hash"]:
            raise ValueError("contract_head_changed")
        raise AssertionError("stale event unexpectedly materialized")

    try:
        execute_run(
            stale_root,
            workspace="workspace",
            scope="scope",
            workspace_key="wk",
            scope_key="sk",
            idempotency_key="stale",
            manifest=fake_manifest(),
            contract_loader=stale_loader,
            materialize=stale_materialize,
            fault_at="after_event_claimed",
        )
    except RuntimeError:
        pass
    stale_contract["contract_hash"] = "s1"
    stale, _ = execute_run(
        stale_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key="stale",
        manifest=fake_manifest(),
        contract_loader=stale_loader,
        materialize=stale_materialize,
    )
    if stale["stop_reason"] != "CONFLICT" or stale["step_count"] != 0:
        raise AssertionError("stale claimed event did not stop as a conflict")

    initial_root = root / "stale-after-start"
    initial_contract = {"contract_hash": "i0", "status": "READY"}
    try:
        execute_run(
            initial_root,
            workspace="workspace",
            scope="scope",
            workspace_key="wk",
            scope_key="sk",
            idempotency_key="initial-stale",
            manifest=fake_manifest(),
            contract_loader=lambda: dict(initial_contract),
            materialize=lambda *args: None,
            fault_at="after_run_started",
        )
    except RuntimeError:
        pass
    initial_contract["contract_hash"] = "i1"
    initial, _ = execute_run(
        initial_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key="initial-stale",
        manifest=fake_manifest(),
        contract_loader=lambda: dict(initial_contract),
        materialize=lambda *args: None,
    )
    if initial["stop_reason"] != "CONFLICT" or initial["step_count"]:
        raise AssertionError("run rebased after its initial contract head changed")

    prepared_root = root / "prepared-conflict"
    prepared_contract = {"contract_hash": "p0", "status": "READY"}

    def prepared_conflict(*args):
        raise ValueError("contract_head_changed")

    prepared, _ = execute_run(
        prepared_root,
        workspace="workspace",
        scope="scope",
        workspace_key="wk",
        scope_key="sk",
        idempotency_key="prepared-conflict",
        manifest=fake_manifest(),
        contract_loader=lambda: dict(prepared_contract),
        materialize=prepared_conflict,
        reconcile_materialization=lambda *args: {
            "state": "PREPARED",
            "materialized": False,
        },
    )
    if prepared["stop_reason"] != "CONFLICT" or prepared["step_count"]:
        raise AssertionError("durably uncommitted stale transaction did not stop as conflict")


def main():
    with tempfile.TemporaryDirectory(prefix="weilan-runner-test-", dir=str(Path.home())) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        quiescent_test(root, environment)
        one_step_test(root, environment)
        multi_step_test(root, environment)
        exhaustion_tests(root, environment)
        invalid_and_paused_tests(root, environment)
        fault_and_conflict_tests(root)
        source = Path(__file__).with_name("runner.py").read_text(encoding="utf-8")
        forbidden = ("while True", "Start-Process", "subprocess.Popen", "schedule(")
        if any(value in source for value in forbidden):
            raise AssertionError("0.7d introduced a daemon, heartbeat, or unbounded loop")
        print(
            json.dumps(
                {
                    "valid": True,
                    "quiescent_zero_step_stop": True,
                    "foreground_one_step_stop": True,
                    "bounded_multi_step_run": True,
                    "step_budget_enforced": True,
                    "hard_manifest_bounds_enforced": True,
                    "finite_event_queue_enforced": True,
                    "invalid_event_stops": True,
                    "paused_scope_blocked": True,
                    "idempotent_run_receipt": True,
                    "all_crash_boundaries_recovered": True,
                    "post_commit_exception_reconciled": True,
                    "concurrent_scope_claim_rejected": True,
                    "stale_claim_stops_as_conflict": True,
                    "background_process_disabled": True,
                    "heartbeat_disabled": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
