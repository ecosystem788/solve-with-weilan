"""Regression tests for Memory 0.6 governance ledger and self projection."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from governance import reduce_events


SCRIPT = Path(__file__).with_name("weilan_trace.py")


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
        raise AssertionError(f"command failed: {result.stderr}")
    return json.loads(result.stdout)


def activate(workspace, environment, directive="activate governance fixture"):
    return run(
        [
            "memory-control",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--state",
            "active",
            "--directive",
            directive,
        ],
        environment,
    )


def capture(workspace, environment, claim, turn):
    return run(
        [
            "evidence-capture",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--signal",
            "architectural_decision",
            "--claim",
            claim,
            "--source",
            f"conversation:governance-test#{turn}",
        ],
        environment,
    )["evidence_id"]


def promote(workspace, environment, evidence_id, summary):
    return run(
        [
            "evidence-promote",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--evidence-id",
            evidence_id,
            "--kind",
            "decision",
            "--summary",
            summary,
            "--stable",
            "--reusable",
            "--privacy-reviewed",
        ],
        environment,
    )


def register_target(
    workspace,
    environment,
    target_ref,
    kind,
    scale,
    source,
    parent=None,
    death_line=None,
    reentry=None,
):
    arguments = [
        "governance-target-register",
        "--workspace",
        workspace,
        "--scope",
        "memory-system",
        "--target-ref",
        target_ref,
        "--target-kind",
        kind,
        "--scale",
        scale,
        "--source",
        source,
    ]
    if parent:
        arguments.extend(["--parent-target-ref", parent])
    if death_line:
        arguments.extend(["--death-line", death_line])
    if reentry:
        arguments.extend(["--reentry-condition", reentry])
    return run(arguments, environment)


def adversarial_collapse_pressure_test():
    def event(sequence, event_type, data):
        return {
            "schema_version": "weilan_governance_event_v0.6",
            "event_id": f"adversarial-{sequence}",
            "sequence": sequence,
            "event_type": event_type,
            "data": data,
        }

    registration = event(
        1,
        "target_registered",
        {
            "target_ref": "goal:adversarial",
            "target_kind": "goal",
            "scale": "task",
            "death_lines": ["stop"],
            "reentry_condition": "new evidence",
            "source_refs": ["evidence:e"],
            "source_snapshots": [{"ref": "evidence:e"}],
        },
    )
    pressure = event(
        2,
        "pressure_recorded",
        {
            "pressure_id": "support-pressure",
            "target_ref": "goal:adversarial",
            "kind": "support",
            "strength": "strong",
            "scale": "task",
            "evidence_refs": ["evidence:e"],
            "source_snapshots": [{"ref": "evidence:e"}],
            "source_pressure_ids": [],
            "required_change": "keep route",
            "causal_frame_id": "frame:f",
        },
    )
    collapse_data = {
        "target_ref": "goal:adversarial",
        "from_state": "ACTIVE",
        "to_state": "COLLAPSED",
        "pressure_ids": ["support-pressure"],
        "evidence_refs": ["evidence:e"],
        "source_snapshots": [{"ref": "evidence:e"}],
        "causal_frame_id": "frame:f",
        "matched_death_lines": ["stop"],
        "trace": {
            "once_reasonable": "yes",
            "invalidating_evidence": "evidence:e",
            "reusable_results": "result",
            "forbidden_assumption": "assumption",
            "reentry_condition": "new evidence",
        },
    }
    active_support = reduce_events(
        [registration, pressure, event(3, "target_collapsed", collapse_data)],
        lambda snapshots: True,
    )
    if (
        active_support["targets"]["goal:adversarial"]["state"] == "COLLAPSED"
        or not any("not adverse" in issue for issue in active_support["issues"])
    ):
        raise AssertionError("support pressure authorized collapse")
    invalidated_support = reduce_events(
        [
            registration,
            pressure,
            event(
                3,
                "pressure_invalidated",
                {"pressure_id": "support-pressure", "reason": "withdrawn"},
            ),
            event(4, "target_collapsed", collapse_data),
        ],
        lambda snapshots: True,
    )
    if (
        invalidated_support["targets"]["goal:adversarial"]["state"]
        == "COLLAPSED"
        or not any("inactive" in issue for issue in invalidated_support["issues"])
    ):
        raise AssertionError("invalidated pressure authorized collapse")


def record_pressure(
    workspace,
    environment,
    target_ref,
    kind,
    strength,
    evidence_id,
    frame_id,
):
    return run(
        [
            "governance-pressure-record",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--target-ref",
            target_ref,
            "--kind",
            kind,
            "--strength",
            strength,
            "--evidence",
            f"evidence:{evidence_id}",
            "--required-change",
            "future existence claims must be verified against current sources",
            "--frame-id",
            frame_id,
        ],
        environment,
    )["pressure"]


def transition(
    workspace,
    environment,
    target_ref,
    transition_name,
    evidence_id,
    frame_id,
    pressure_id=None,
    extra=None,
):
    arguments = [
        "governance-target-transition",
        "--workspace",
        workspace,
        "--scope",
        "memory-system",
        "--target-ref",
        target_ref,
        "--transition",
        transition_name,
        "--evidence",
        f"evidence:{evidence_id}",
        "--frame-id",
        frame_id,
    ]
    if pressure_id:
        arguments.extend(["--pressure-id", pressure_id])
    if extra:
        arguments.extend(extra)
    return run(arguments, environment)


def main():
    adversarial_collapse_pressure_test()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-governance-test-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_CODEX_SESSIONS_HOME"] = str(root / "sessions")
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        unrelated = str(root / "unrelated")
        corrupt_workspace = str(root / "corrupt")
        sequence_workspace = str(root / "sequence-corrupt")
        Path(workspace).mkdir()
        Path(unrelated).mkdir()
        Path(corrupt_workspace).mkdir()
        Path(sequence_workspace).mkdir()
        activate(workspace, environment)

        anchor = run(
            [
                "open",
                "--level",
                "L2",
                "--workspace",
                workspace,
                "--problem",
                "governance fixture",
                "--success",
                "fixture complete",
            ],
            environment,
        )["frame_id"]
        run(
            [
                "close",
                "--frame-id",
                anchor,
                "--outcome",
                "success",
                "--verdict",
                "fixture anchor",
            ],
            environment,
        )
        source = root / "governance-source.txt"
        source.write_text("stable governance source", encoding="utf-8")

        register_target(
            workspace,
            environment,
            "goal:task",
            "goal",
            "task",
            str(source),
            death_line="task objective cannot satisfy original success criteria",
            reentry="new evidence restores a viable task objective",
        )
        register_target(
            workspace,
            environment,
            "assumption:kernel-api",
            "assumption",
            "local",
            str(source),
            parent="goal:task",
            death_line="verified source contradicts the API existence claim",
            reentry="a new verified source establishes the API",
        )
        register_target(
            workspace,
            environment,
            "route:continuity",
            "route",
            "continuity",
            str(source),
            death_line="continuity route suppresses valid branch evidence",
            reentry="new promoted evidence supports the route",
        )

        contradiction_evidence = capture(
            workspace,
            environment,
            "当前代码检索证明记忆中的 kernel API 不存在",
            "contradiction",
        )
        support_evidence = capture(
            workspace,
            environment,
            "另一项独立检索支持检查 API 后再行动",
            "support",
        )
        contradiction = record_pressure(
            workspace,
            environment,
            "assumption:kernel-api",
            "contradiction",
            "critical",
            contradiction_evidence,
            anchor,
        )
        duplicate = record_pressure(
            workspace,
            environment,
            "assumption:kernel-api",
            "contradiction",
            "critical",
            contradiction_evidence,
            anchor,
        )
        support = record_pressure(
            workspace,
            environment,
            "assumption:kernel-api",
            "support",
            "medium",
            support_evidence,
            anchor,
        )
        state = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if not state["pressures"][contradiction["pressure_id"]]["active"]:
            raise AssertionError("primary contradiction pressure was inactive")
        duplicate_state = state["pressures"][duplicate["pressure_id"]]
        if duplicate_state["active"] or not duplicate_state["duplicate_of"]:
            raise AssertionError("duplicate evidence contributed pressure twice")
        vector = state["pressure_vectors"]["assumption:kernel-api"]
        if set(vector) != {"support", "contradiction"}:
            raise AssertionError("support and contradiction were incorrectly cancelled")
        if vector["contradiction"]["strongest"] != "critical":
            raise AssertionError("critical contradiction was averaged away")
        if state["pressure_vectors"].get("goal:task"):
            raise AssertionError("local pressure leaked to task scale without propagation")

        transition(
            workspace,
            environment,
            "goal:task",
            "pause",
            support_evidence,
            anchor,
        )
        paused_target = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )["targets"]["goal:task"]
        if paused_target["state"] != "PAUSED":
            raise AssertionError("target pause was not represented independently")
        transition(
            workspace,
            environment,
            "goal:task",
            "resume",
            support_evidence,
            anchor,
        )
        transition(
            workspace,
            environment,
            "goal:task",
            "block",
            support_evidence,
            anchor,
        )
        blocked_target = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )["targets"]["goal:task"]
        if blocked_target["state"] != "BLOCKED":
            raise AssertionError("target block was not represented independently")
        transition(
            workspace,
            environment,
            "goal:task",
            "resume",
            support_evidence,
            anchor,
        )

        task_pressure = run(
            [
                "governance-pressure-propagate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--source-pressure-id",
                contradiction["pressure_id"],
                "--target-ref",
                "goal:task",
                "--reason",
                "explicit task-level review",
                "--frame-id",
                anchor,
            ],
            environment,
        )["pressure"]
        unauthorized_continuity = completed(
            [
                "governance-pressure-propagate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--source-pressure-id",
                task_pressure["pressure_id"],
                "--target-ref",
                "route:continuity",
                "--reason",
                "must require promoted evidence",
                "--frame-id",
                anchor,
            ],
            environment,
        )
        if unauthorized_continuity.returncode == 0 or "requires promoted evidence" not in unauthorized_continuity.stderr:
            raise AssertionError("candidate evidence propagated to continuity scale")
        promote(
            workspace,
            environment,
            contradiction_evidence,
            "当前代码检索证明记忆中的 kernel API 不存在",
        )
        continuity_pressure = run(
            [
                "governance-pressure-propagate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--source-pressure-id",
                task_pressure["pressure_id"],
                "--target-ref",
                "route:continuity",
                "--reason",
                "promoted evidence permits explicit continuity propagation",
                "--frame-id",
                anchor,
            ],
            environment,
        )["pressure"]
        if not continuity_pressure["active"]:
            raise AssertionError("authorized propagated pressure was inactive")

        run(
            [
                "evidence-disposition",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                contradiction_evidence,
                "--state",
                "withdrawn",
                "--reason",
                "fixture withdraws the contradiction source",
            ],
            environment,
        )
        withdrawn_state = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        for pressure_id in (
            contradiction["pressure_id"],
            task_pressure["pressure_id"],
            continuity_pressure["pressure_id"],
        ):
            if withdrawn_state["pressures"][pressure_id]["active"]:
                raise AssertionError("withdrawn evidence continued contributing pressure")
        if withdrawn_state["pressures"][support["pressure_id"]]["active"] is not True:
            raise AssertionError("unrelated support pressure was incorrectly withdrawn")

        collapse_evidence = capture(
            workspace,
            environment,
            "新的独立代码检索再次证明 kernel API 不存在",
            "collapse",
        )
        collapse_pressure = record_pressure(
            workspace,
            environment,
            "assumption:kernel-api",
            "contradiction",
            "critical",
            collapse_evidence,
            anchor,
        )
        transition(
            workspace,
            environment,
            "assumption:kernel-api",
            "warn",
            collapse_evidence,
            anchor,
            pressure_id=collapse_pressure["pressure_id"],
        )
        transition(
            workspace,
            environment,
            "assumption:kernel-api",
            "probation",
            collapse_evidence,
            anchor,
            pressure_id=collapse_pressure["pressure_id"],
        )
        transition(
            workspace,
            environment,
            "assumption:kernel-api",
            "collapse",
            collapse_evidence,
            anchor,
            pressure_id=collapse_pressure["pressure_id"],
            extra=[
                "--death-line-match",
                "verified source contradicts the API existence claim",
                "--once-reasonable",
                "the API was remembered from an older context",
                "--invalidating-evidence",
                "two independent current-source checks contradict it",
                "--reusable-results",
                "the source verification procedure",
                "--forbidden-assumption",
                "remembered APIs exist without current inspection",
            ],
        )
        collapsed = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if collapsed["targets"]["assumption:kernel-api"]["state"] != "COLLAPSED":
            raise AssertionError("target did not collapse")
        if collapsed["targets"]["goal:task"]["state"] != "ACTIVE":
            raise AssertionError("local target collapse killed its parent task")
        same_evidence_reentry = completed(
            [
                "governance-target-transition",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--target-ref",
                "assumption:kernel-api",
                "--transition",
                "reenter",
                "--evidence",
                f"evidence:{collapse_evidence}",
                "--frame-id",
                anchor,
            ],
            environment,
        )
        if same_evidence_reentry.returncode == 0 or "requires evidence not used" not in same_evidence_reentry.stderr:
            raise AssertionError("collapsed target reentered without new evidence")
        reentry_evidence = capture(
            workspace,
            environment,
            "新的已验证实现增加了 kernel API",
            "reentry",
        )
        transition(
            workspace,
            environment,
            "assumption:kernel-api",
            "reenter",
            reentry_evidence,
            anchor,
        )

        run(
            [
                "governance-pressure-invalidate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--pressure-id",
                support["pressure_id"],
                "--reason",
                "support pressure no longer applies after reentry",
            ],
            environment,
        )

        register_target(
            workspace,
            environment,
            "route:old",
            "route",
            "task",
            str(source),
        )
        register_target(
            workspace,
            environment,
            "route:new",
            "route",
            "task",
            str(source),
        )
        transition(
            workspace,
            environment,
            "route:old",
            "supersede",
            reentry_evidence,
            anchor,
            extra=["--replacement-target-ref", "route:new"],
        )

        register_target(
            workspace,
            environment,
            "goal:parent-terminal",
            "goal",
            "task",
            str(source),
        )
        register_target(
            workspace,
            environment,
            "route:parent-child",
            "route",
            "local",
            str(source),
            parent="goal:parent-terminal",
        )
        child_pressure = record_pressure(
            workspace,
            environment,
            "route:parent-child",
            "support",
            "medium",
            reentry_evidence,
            anchor,
        )
        parent_with_active_child = completed(
            [
                "governance-target-transition",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--target-ref",
                "goal:parent-terminal",
                "--transition",
                "complete",
                "--evidence",
                f"evidence:{reentry_evidence}",
                "--frame-id",
                anchor,
            ],
            environment,
        )
        if parent_with_active_child.returncode == 0 or "terminal children" not in parent_with_active_child.stderr:
            raise AssertionError("parent became terminal while a child remained active")
        transition(
            workspace,
            environment,
            "route:parent-child",
            "complete",
            reentry_evidence,
            anchor,
        )
        completed_child_pressure = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )["pressures"][child_pressure["pressure_id"]]
        if completed_child_pressure["active"] or "target_missing_or_terminal" not in completed_child_pressure["inactive_reasons"]:
            raise AssertionError("pressure against a terminal target remained active")
        transition(
            workspace,
            environment,
            "goal:parent-terminal",
            "complete",
            reentry_evidence,
            anchor,
        )
        child_under_terminal_parent = completed(
            [
                "governance-target-register",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--target-ref",
                "route:late-child",
                "--target-kind",
                "route",
                "--scale",
                "local",
                "--parent-target-ref",
                "goal:parent-terminal",
                "--source",
                str(source),
            ],
            environment,
        )
        if child_under_terminal_parent.returncode == 0 or "parent target is terminal" not in child_under_terminal_parent.stderr:
            raise AssertionError("active child was registered under a terminal parent")

        replay_one = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        replay_two = run(
            ["governance-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if replay_one != replay_two:
            raise AssertionError("identical governance event streams did not replay deterministically")
        projection_one = run(
            ["self-project", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        projection_two = run(
            ["self-project", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if projection_one["projection_hash"] != projection_two["projection_hash"]:
            raise AssertionError("read-only self projection was not deterministic")
        if projection_one["can_authorize_actions"]:
            raise AssertionError("self projection acquired control authority")

        run(
            [
                "memory-control",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--state",
                "paused",
                "--directive",
                "pause governance fixture",
            ],
            environment,
        )
        paused_projection = run(
            ["self-project", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if paused_projection["control"]["continuation_allowed"]:
            raise AssertionError("self projection restored a paused scope")
        paused_write = completed(
            [
                "governance-target-register",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--target-ref",
                "goal:forbidden",
                "--target-kind",
                "goal",
                "--scale",
                "task",
                "--source",
                str(source),
            ],
            environment,
        )
        if paused_write.returncode == 0 or "explicitly active scope" not in paused_write.stderr:
            raise AssertionError("governance write bypassed paused control")
        activate(workspace, environment, "resume fixture after paused projection test")

        activate(unrelated, environment, "activate unrelated workspace")
        cross_workspace = completed(
            [
                "governance-target-register",
                "--workspace",
                unrelated,
                "--scope",
                "memory-system",
                "--target-ref",
                "goal:cross",
                "--target-kind",
                "goal",
                "--scale",
                "task",
                "--source",
                f"evidence:{reentry_evidence}",
            ],
            environment,
        )
        if cross_workspace.returncode == 0 or "another workspace" not in cross_workspace.stderr:
            raise AssertionError("governance evidence leaked across workspaces")

        activate(corrupt_workspace, environment, "activate corrupt fixture")
        corrupt_key = run(
            [
                "memory-control",
                "--workspace",
                corrupt_workspace,
                "--scope",
                "memory-system",
                "--state",
                "active",
                "--directive",
                "obtain workspace key",
            ],
            environment,
        )
        corrupt_path = (
            root
            / "method-state"
            / "memory"
            / "governance"
            / "workspaces"
            / Path(corrupt_key["path"]).parent.name
        )
        # Create one valid target, then append a supported event name with an
        # impossible transition and incomplete collapse payload.
        corrupt_source = root / "corrupt-source.txt"
        corrupt_source.write_text("source", encoding="utf-8")
        valid_corrupt = register_target(
            corrupt_workspace,
            environment,
            "goal:corrupt",
            "goal",
            "task",
            str(corrupt_source),
        )
        ledger_path = Path(valid_corrupt["path"])
        first_record = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
        malformed_transition = {
            **first_record,
            "event_id": "malformed-supported-transition",
            "sequence": 2,
            "event_type": "target_collapsed",
            "data": {
                "target_ref": "goal:corrupt",
                "from_state": "BOGUS",
                "to_state": "COLLAPSED",
                "pressure_ids": [],
                "evidence_refs": [],
                "source_snapshots": [],
                "causal_frame_id": anchor,
                "matched_death_lines": [],
                "trace": None,
            },
        }
        with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(malformed_transition, ensure_ascii=False) + "\n")
        corrupt_show = completed(
            [
                "governance-show",
                "--workspace",
                corrupt_workspace,
                "--scope",
                "memory-system",
            ],
            environment,
        )
        if corrupt_show.returncode == 0:
            raise AssertionError("governance replay accepted an invalid supported transition")

        activate(sequence_workspace, environment, "activate sequence corruption fixture")
        sequence_source = root / "sequence-source.txt"
        sequence_source.write_text("source", encoding="utf-8")
        valid_sequence = register_target(
            sequence_workspace,
            environment,
            "goal:sequence-one",
            "goal",
            "task",
            str(sequence_source),
        )
        sequence_path = Path(valid_sequence["path"])
        sequence_record = json.loads(sequence_path.read_text(encoding="utf-8").splitlines()[0])
        duplicate_sequence = json.loads(json.dumps(sequence_record))
        duplicate_sequence["event_id"] = "duplicate-sequence-event"
        duplicate_sequence["data"]["target_ref"] = "goal:sequence-two"
        with sequence_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(duplicate_sequence, ensure_ascii=False) + "\n")
        sequence_show = completed(
            [
                "governance-show",
                "--workspace",
                sequence_workspace,
                "--scope",
                "memory-system",
            ],
            environment,
        )
        if sequence_show.returncode == 0:
            raise AssertionError("governance replay accepted a duplicate sequence")

        print(
            json.dumps(
                {
                    "valid": True,
                    "deterministic_replay": True,
                    "withdrawn_evidence_pressure_removed": True,
                    "duplicate_evidence_not_double_counted": True,
                    "support_contradiction_preserved": True,
                    "explicit_scale_propagation": True,
                    "local_collapse_did_not_kill_parent": True,
                    "reentry_requires_new_evidence": True,
                    "self_projection_read_only": True,
                    "paused_scope_not_restored": True,
                    "target_pause_block_resume_distinct": True,
                    "terminal_parent_child_invariant": True,
                    "terminal_target_pressure_inactive": True,
                    "cross_workspace_isolated": True,
                    "supported_transition_fault_detected": True,
                    "collapse_requires_active_adverse_pressure": True,
                    "duplicate_sequence_detected": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
