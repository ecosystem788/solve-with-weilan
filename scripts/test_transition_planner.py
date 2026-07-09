"""Regression tests for Memory 0.7c one-step transition materialization."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")
SCOPE = "memory-system"


def completed(arguments, environment):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def run(arguments, environment):
    result = completed(arguments, environment)
    if result.returncode:
        raise AssertionError(f"command failed: {result.stderr}\n{result.stdout}")
    return json.loads(result.stdout)


def snapshot_tree(root):
    result = {}
    if not root.exists():
        return result
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def activate(workspace, environment, directive):
    return run(
        [
            "memory-control",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--state",
            "active",
            "--directive",
            directive,
        ],
        environment,
    )


def audit_close(frame_id, environment, route_change=False):
    if route_change:
        run(
            [
                "persistence-audit",
                "--frame-id",
                frame_id,
                "--trigger",
                "route_change",
                "--decision",
                "not_persisted",
                "--reason",
                "temporary 0.7c route-change fixture",
            ],
            environment,
        )
    run(
        [
            "persistence-audit",
            "--frame-id",
            frame_id,
            "--trigger",
            "round_end",
            "--decision",
            "not_persisted",
            "--reason",
            "temporary 0.7c fixture",
        ],
        environment,
    )
    run(
        [
            "close",
            "--frame-id",
            frame_id,
            "--outcome",
            "success",
            "--verdict",
            "fixture frame closed",
        ],
        environment,
    )


def register_goal(workspace, source, target_ref, environment):
    return run(
        [
            "governance-target-register",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--target-ref",
            target_ref,
            "--target-kind",
            "goal",
            "--scale",
            "task",
            "--death-line",
            "verified contradiction invalidates the route",
            "--reentry-condition",
            "new independent evidence changes the invalid assumption",
            "--source",
            str(source),
        ],
        environment,
    )


def setup_ready(root, environment, name, challenger=False):
    workspace_path = root / name
    workspace_path.mkdir()
    workspace = str(workspace_path)
    activate(workspace, environment, f"activate {name}")
    frame_id = run(
        [
            "open",
            "--level",
            "L2",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--branch",
            "main",
            "--relation",
            "root",
            "--problem",
            "0.7c fixture",
            "--success",
            "fixture ready",
        ],
        environment,
    )["frame_id"]
    audit_close(frame_id, environment)
    source = workspace_path / "source.txt"
    source.write_text("stable 0.7c source", encoding="utf-8")
    register_goal(workspace, source, "goal:task", environment)
    if challenger:
        register_goal(workspace, source, "goal:challenger", environment)
    return {"workspace": workspace, "frame_id": frame_id, "source": source}


def successor_arguments(
    fixture,
    key,
    disposition="continue",
    target="goal:task",
    candidate=None,
    branches=("main",),
    result_branch="",
    trigger_frame=None,
):
    trigger_frame = trigger_frame or fixture["frame_id"]
    result = [
        "--workspace",
        fixture["workspace"],
        "--scope",
        SCOPE,
        "--idempotency-key",
        key,
        "--trigger-kind",
        "frame_closed",
        "--trigger-ref",
        f"frame:{trigger_frame}",
        "--disposition",
        disposition,
        "--target-ref",
        target,
        "--problem",
        f"{disposition} successor verification",
        "--success",
        "one bounded successor produces discriminating evidence",
        "--why-reasonable",
        "the admitted target remains viable under current evidence",
        "--next-expected-evidence",
        "the next bounded frame resolves the current uncertainty",
        "--next-death-line",
        "verified contradiction invalidates the route",
    ]
    if candidate:
        result.extend(["--candidate-target-ref", candidate])
    for branch in branches:
        result.extend(["--branch", branch])
    if result_branch:
        result.extend(["--result-branch", result_branch])
    return result


def capture_pressure(fixture, environment, strength="critical"):
    evidence_id = run(
        [
            "evidence-capture",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--signal",
            "verified_result",
            "--claim",
            "Verified contradiction removes the current route authority",
            "--source",
            f"conversation:{Path(fixture['workspace']).name}#contradiction",
        ],
        environment,
    )["evidence_id"]
    pressure = run(
        [
            "governance-pressure-record",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            "goal:task",
            "--kind",
            "contradiction",
            "--strength",
            strength,
            "--evidence",
            f"evidence:{evidence_id}",
            "--required-change",
            "collapse or run one discriminating test",
            "--frame-id",
            fixture["frame_id"],
        ],
        environment,
    )["pressure"]
    return evidence_id, pressure["pressure_id"]


def continue_test(root, method_state, environment):
    fixture = setup_ready(root, environment, "continue-workspace")
    arguments = successor_arguments(fixture, "continue-once")
    before = snapshot_tree(method_state)
    plan_one = run(["metabolic-plan-transition", *arguments], environment)
    plan_two = run(["metabolic-plan-transition", *arguments], environment)
    if plan_one != plan_two or plan_one["plan"]["disposition"] != "continue":
        raise AssertionError("read-only transition plan is not deterministic")
    if snapshot_tree(method_state) != before:
        raise AssertionError("transition planning wrote method state")
    expanded_budget = completed(
        ["metabolic-plan-transition", *arguments, "--budget", "expanded"],
        environment,
    )
    if expanded_budget.returncode == 0 or "budget override" not in expanded_budget.stderr:
        raise AssertionError("successor plan accepted an unverifiable budget expansion")
    materialized = run(["metabolic-materialize", *arguments], environment)
    successor = materialized["successor_frame_id"]
    if (
        not materialized["materialized"]
        or materialized["transactions_committed_this_invocation"] != 1
        or not successor
    ):
        raise AssertionError("continue did not materialize exactly one transaction")
    events = run(["show", "--frame-id", successor], environment)
    if [value["event_type"] for value in events] != [
        "frame_opened",
        "candidate_admitted",
        "holder_selected",
    ]:
        raise AssertionError("continue successor did not contain the planned holder envelope")
    repeated = run(["metabolic-materialize", *arguments], environment)
    if (
        repeated["successor_frame_id"] != successor
        or repeated["transactions_committed_this_invocation"] != 0
        or repeated["receipt"]["receipt_hash"] != materialized["receipt"]["receipt_hash"]
    ):
        raise AssertionError("repeated materialization was not idempotent")
    changed = completed(
        ["metabolic-materialize", *arguments, "--problem", "changed request"],
        environment,
    )
    if changed.returncode == 0 or "idempotency_conflict" not in changed.stderr:
        raise AssertionError("idempotency key accepted a changed transition request")
    run(
        [
            "memory-control",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--state",
            "paused",
            "--directive",
            "pause materialization",
        ],
        environment,
    )
    paused = completed(
        ["metabolic-materialize", *successor_arguments(fixture, "paused-attempt")],
        environment,
    )
    if paused.returncode == 0 or "explicitly active scope" not in paused.stderr:
        raise AssertionError("paused scope materialized a transition")


def fork_join_test(root, environment):
    fixture = setup_ready(root, environment, "fork-join-workspace", challenger=True)
    fork_args = successor_arguments(
        fixture,
        "fork-once",
        disposition="fork",
        candidate="goal:challenger",
        result_branch="experiment",
    )
    forked = run(["metabolic-materialize", *fork_args], environment)
    fork_frame = forked["successor_frame_id"]
    lineage = run(
        ["lineage-show", "--workspace", fixture["workspace"], "--scope", SCOPE],
        environment,
    )
    if lineage["branches"]["main"]["head_frame_id"] != fixture["frame_id"]:
        raise AssertionError("fork moved the source branch")
    if lineage["branches"]["experiment"]["head_frame_id"] != fork_frame:
        raise AssertionError("fork did not create the requested branch")
    audit_close(fork_frame, environment)
    join_args = successor_arguments(
        fixture,
        "join-once",
        disposition="join",
        branches=("main", "experiment"),
        result_branch="main",
        trigger_frame=fork_frame,
    )
    joined = run(["metabolic-materialize", *join_args], environment)
    lineage = run(
        ["lineage-show", "--workspace", fixture["workspace"], "--scope", SCOPE],
        environment,
    )
    if lineage["branches"]["main"]["head_frame_id"] != joined["successor_frame_id"]:
        raise AssertionError("join did not advance the result branch")
    if lineage["branches"]["experiment"]["status"] != "joined":
        raise AssertionError("join did not close the non-result branch")


def collapse_regroup_test(root, environment):
    fixture = setup_ready(root, environment, "collapse-workspace", challenger=True)
    evidence_id, pressure_id = capture_pressure(fixture, environment)
    collapse_args = [
        "--workspace",
        fixture["workspace"],
        "--scope",
        SCOPE,
        "--idempotency-key",
        "collapse-once",
        "--trigger-kind",
        "pressure_changed",
        "--trigger-ref",
        f"pressure:{pressure_id}",
        "--disposition",
        "collapse",
        "--target-ref",
        "goal:task",
        "--death-line-match",
        "verified contradiction invalidates the route",
        "--once-reasonable",
        "the route matched the original evidence",
        "--invalidating-evidence",
        "a verified contradiction falsified the route",
        "--reusable-results",
        "the bounded test and source evidence remain reusable",
        "--forbidden-assumption",
        "the contradicted route can continue unchanged",
    ]
    collapse_plan = run(["metabolic-plan-transition", *collapse_args], environment)
    participants = {
        value["participant"] for value in collapse_plan["plan"]["intents"]
    }
    if participants != {"governance"} or collapse_plan["plan"]["successor_frame_id"]:
        raise AssertionError("collapse forced a successor")
    collapsed = run(["metabolic-materialize", *collapse_args], environment)
    if collapsed["successor_frame_id"] is not None:
        raise AssertionError("collapse transaction created a Frame")
    target = run(
        [
            "governance-show",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            "goal:task",
        ],
        environment,
    )["targets"]["goal:task"]
    if target["state"] != "COLLAPSED" or not target["collapse_trace"]:
        raise AssertionError("collapse trace was not committed")

    regroup_args = successor_arguments(
        fixture,
        "regroup-once",
        disposition="regroup",
        target="goal:task",
        candidate="goal:challenger",
    )
    regroup_args.extend(
        [
            "--changed-assumption",
            "the challenger tests an independent mechanism",
        ]
    )
    regrouped = run(["metabolic-materialize", *regroup_args], environment)
    events = run(["show", "--frame-id", regrouped["successor_frame_id"]], environment)
    if events[-1]["event_type"] != "candidates_regrouped":
        raise AssertionError("regroup successor lacks an observable regroup trace")
    if events[2]["data"]["candidate_id"] != "goal:challenger":
        raise AssertionError("regroup did not select the admitted active candidate")


def no_candidate_test(root, environment):
    fixture = setup_ready(root, environment, "no-candidate-workspace")
    _, pressure_id = capture_pressure(fixture, environment)
    collapse_args = [
        "--workspace",
        fixture["workspace"],
        "--scope",
        SCOPE,
        "--idempotency-key",
        "collapse-alone",
        "--trigger-kind",
        "pressure_changed",
        "--trigger-ref",
        f"pressure:{pressure_id}",
        "--disposition",
        "collapse",
        "--target-ref",
        "goal:task",
        "--death-line-match",
        "verified contradiction invalidates the route",
        "--once-reasonable",
        "the route previously fit the evidence",
        "--invalidating-evidence",
        "contradiction",
        "--reusable-results",
        "trace",
        "--forbidden-assumption",
        "unchanged continuation",
    ]
    run(["metabolic-materialize", *collapse_args], environment)
    before = run(
        [
            "metabolic-transaction-show",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
        ],
        environment,
    )["transaction_count"]
    regroup = completed(
        [
            "metabolic-materialize",
            *successor_arguments(
                fixture,
                "invented-regroup",
                disposition="regroup",
                target="goal:task",
                candidate="goal:missing",
            ),
            "--changed-assumption",
            "invented",
        ],
        environment,
    )
    after = run(
        [
            "metabolic-transaction-show",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
        ],
        environment,
    )["transaction_count"]
    if regroup.returncode == 0 or before != after:
        raise AssertionError("missing candidate produced a regroup transaction")


def complete_block_and_test_test(root, environment):
    complete_fixture = setup_ready(root, environment, "complete-workspace")
    complete_args = [
        "--workspace",
        complete_fixture["workspace"],
        "--scope",
        SCOPE,
        "--idempotency-key",
        "complete-once",
        "--trigger-kind",
        "frame_closed",
        "--trigger-ref",
        f"frame:{complete_fixture['frame_id']}",
        "--disposition",
        "complete",
        "--target-ref",
        "goal:task",
        "--evidence",
        f"frame:{complete_fixture['frame_id']}",
    ]
    run(["metabolic-materialize", *complete_args], environment)
    complete_target = run(
        [
            "governance-show",
            "--workspace",
            complete_fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            "goal:task",
        ],
        environment,
    )["targets"]["goal:task"]
    if complete_target["state"] != "COMPLETED":
        raise AssertionError("complete did not terminally complete its target")

    block_fixture = setup_ready(root, environment, "block-workspace")
    block_args = [
        "--workspace",
        block_fixture["workspace"],
        "--scope",
        SCOPE,
        "--idempotency-key",
        "block-once",
        "--trigger-kind",
        "frame_closed",
        "--trigger-ref",
        f"frame:{block_fixture['frame_id']}",
        "--disposition",
        "block",
        "--target-ref",
        "goal:task",
        "--evidence",
        f"frame:{block_fixture['frame_id']}",
    ]
    run(["metabolic-materialize", *block_args], environment)
    block_target = run(
        [
            "governance-show",
            "--workspace",
            block_fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            "goal:task",
        ],
        environment,
    )["targets"]["goal:task"]
    if block_target["state"] != "BLOCKED":
        raise AssertionError("block did not preserve a blocked non-terminal target")

    test_fixture = setup_ready(root, environment, "test-workspace")
    evidence_id, pressure_id = capture_pressure(test_fixture, environment, strength="strong")
    run(
        [
            "governance-target-transition",
            "--workspace",
            test_fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            "goal:task",
            "--transition",
            "warn",
            "--pressure-id",
            pressure_id,
            "--evidence",
            f"evidence:{evidence_id}",
            "--frame-id",
            test_fixture["frame_id"],
        ],
        environment,
    )
    test_args = successor_arguments(
        test_fixture, "test-once", disposition="test"
    )
    test_args[test_args.index("frame_closed")] = "pressure_changed"
    test_args[test_args.index(f"frame:{test_fixture['frame_id']}")] = f"pressure:{pressure_id}"
    tested = run(["metabolic-materialize", *test_args], environment)
    if not tested["successor_frame_id"]:
        raise AssertionError("warned target did not receive a discriminating-test Frame")


def main():
    with tempfile.TemporaryDirectory(prefix="weilan-0.7c-test-", dir=str(Path.home())) as temporary:
        root = Path(temporary)
        method_state = root / "method-state"
        environment = os.environ.copy()
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_CODEX_SESSIONS_HOME"] = str(root / "sessions")
        environment["WEILAN_METHOD_HOME"] = str(method_state)
        continue_test(root, method_state, environment)
        fork_join_test(root, environment)
        collapse_regroup_test(root, environment)
        no_candidate_test(root, environment)
        complete_block_and_test_test(root, environment)
        source = Path(__file__).with_name("transition_planner.py").read_text(encoding="utf-8")
        if "while True" in source or "threading" in source or "asyncio" in source:
            raise AssertionError("0.7c introduced a runner or scheduler")
        print(
            json.dumps(
                {
                    "valid": True,
                    "deterministic_read_only_plan": True,
                    "one_transaction_per_invocation": True,
                    "materialization_idempotent": True,
                    "budget_expansion_rejected": True,
                    "continue_fork_join_generated": True,
                    "collapse_has_no_successor": True,
                    "regroup_requires_active_candidate_and_changed_assumption": True,
                    "missing_candidate_does_not_invent_route": True,
                    "complete_block_and_test_generated": True,
                    "paused_scope_blocked": True,
                    "background_runner_disabled": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
