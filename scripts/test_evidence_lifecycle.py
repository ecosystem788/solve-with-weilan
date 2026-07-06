"""Regression tests for Memory 0.5.1 lifecycle, retraction, supersession, and persistence audit."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


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


def open_legacy(workspace, environment):
    return run(
        [
            "open",
            "--level",
            "L2",
            "--workspace",
            workspace,
            "--problem",
            "legacy anchor",
            "--success",
            "anchor closed",
        ],
        environment,
    )["frame_id"]


def open_lineaged(workspace, parent, environment, problem="lifecycle fixture"):
    return run(
        [
            "open",
            "--level",
            "L2",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--branch",
            "main",
            "--relation",
            "continue",
            "--parent",
            parent,
            "--problem",
            problem,
            "--success",
            "fixture verified",
        ],
        environment,
    )["frame_id"]


def close(frame_id, environment):
    return completed(
        [
            "close",
            "--frame-id",
            frame_id,
            "--outcome",
            "success",
            "--verdict",
            "fixture complete",
        ],
        environment,
    )


def audit_not_persisted(frame_id, trigger, reason, environment):
    return run(
        [
            "persistence-audit",
            "--frame-id",
            frame_id,
            "--trigger",
            trigger,
            "--decision",
            "not_persisted",
            "--reason",
            reason,
        ],
        environment,
    )


def capture(workspace, claim, turn, environment, signal="architectural_decision"):
    return run(
        [
            "evidence-capture",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--signal",
            signal,
            "--claim",
            claim,
            "--source",
            f"conversation:lifecycle-test#{turn}",
        ],
        environment,
    )["evidence_id"]


def promote(workspace, evidence_id, summary, environment, kind="decision"):
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
            kind,
            "--summary",
            summary,
            "--stable",
            "--reusable",
            "--privacy-reviewed",
        ],
        environment,
    )


def disposition(workspace, evidence_id, state, reason, environment, replacement=None):
    arguments = [
        "evidence-disposition",
        "--workspace",
        workspace,
        "--scope",
        "memory-system",
        "--evidence-id",
        evidence_id,
        "--state",
        state,
        "--reason",
        reason,
    ]
    if replacement:
        arguments.extend(["--replacement-evidence-id", replacement])
    return run(arguments, environment)


def search_ids(workspace, query, environment):
    result = run(
        [
            "memory-search",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--query",
            query,
            "--limit",
            "20",
        ],
        environment,
    )
    return {item["memory_id"] for item in result["results"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-lifecycle-test-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        unrelated = str(root / "unrelated")
        Path(workspace).mkdir()

        anchor = open_legacy(workspace, environment)
        if close(anchor, environment).returncode:
            raise AssertionError("legacy anchor did not close")

        omitted = open_lineaged(workspace, anchor, environment, "omitted persistence planning")
        missing_close = close(omitted, environment)
        if missing_close.returncode == 0 or "missing triggers: round_end" not in missing_close.stderr:
            raise AssertionError("lineaged frame closed without persistence audit")
        empty_reason = completed(
            [
                "persistence-audit",
                "--frame-id",
                omitted,
                "--trigger",
                "round_end",
                "--decision",
                "not_persisted",
            ],
            environment,
        )
        if empty_reason.returncode == 0 or "explicit --reason" not in empty_reason.stderr:
            raise AssertionError("empty non-persistence reason passed audit")
        audit_not_persisted(omitted, "round_end", "fixture contains no durable conclusion", environment)
        if close(omitted, environment).returncode:
            raise AssertionError("audited frame did not close")

        route = open_lineaged(workspace, omitted, environment, "route change audit")
        run(
            [
                "event",
                "--frame-id",
                route,
                "--type",
                "minimal_unit_collapsed",
                "--field",
                "scope=assumption",
                "--field",
                "former_holder=fixture_route",
                "--field",
                "invalidating_evidence=fixture",
            ],
            environment,
        )
        run(
            [
                "event",
                "--frame-id",
                route,
                "--type",
                "trace_emitted",
                "--field",
                "once_reasonable=fixture",
                "--field",
                "invalidating_evidence=fixture",
                "--field",
                "reusable_results=none",
                "--field",
                "forbidden_assumption=fixture",
                "--field",
                "reentry_condition=none",
            ],
            environment,
        )
        audit_not_persisted(route, "round_end", "fixture has no durable round conclusion", environment)
        missing_route = close(route, environment)
        if missing_route.returncode == 0 or "route_change" not in missing_route.stderr:
            raise AssertionError("route change closed without route_change persistence audit")
        audit_not_persisted(route, "route_change", "collapsed fixture route has no reusable result", environment)
        if close(route, environment).returncode:
            raise AssertionError("route-change-audited frame did not close")

        version = open_lineaged(workspace, route, environment, "version switch audit")
        run(
            [
                "memory-control",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--state",
                "active",
                "--directive",
                "switch fixture version",
                "--reason",
                "explicit_version_switch",
            ],
            environment,
        )
        audit_not_persisted(version, "round_end", "fixture has no durable round conclusion", environment)
        missing_version = close(version, environment)
        if missing_version.returncode == 0 or "version_switch" not in missing_version.stderr:
            raise AssertionError("version switch closed without version_switch persistence audit")
        audit_not_persisted(version, "version_switch", "fixture version switch has no durable decision", environment)
        if close(version, environment).returncode:
            raise AssertionError("version-switch-audited frame did not close")
        run(
            [
                "memory-control",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--state",
                "active",
                "--directive",
                "switch fixture after frame closure",
                "--reason",
                "explicit_version_switch_after_close",
            ],
            environment,
        )
        closed_audit = run(
            ["persistence-audit-show", "--frame-id", version],
            environment,
        )
        if not closed_audit["valid"] or closed_audit["missing_triggers"]:
            raise AssertionError("a later control retroactively invalidated a closed frame")

        audit_frame = open_lineaged(workspace, version, environment, "promoted persistence audit")
        audit_evidence = capture(
            workspace,
            "持久化审计必须引用已经晋升的证据",
            "audit-promoted",
            environment,
        )
        candidate_audit = completed(
            [
                "persistence-audit",
                "--frame-id",
                audit_frame,
                "--trigger",
                "round_end",
                "--decision",
                "promoted",
                "--evidence-id",
                audit_evidence,
            ],
            environment,
        )
        if candidate_audit.returncode == 0 or "has not passed Promotion Gate" not in candidate_audit.stderr:
            raise AssertionError("candidate evidence passed persistence audit as promoted")
        promote(
            workspace,
            audit_evidence,
            "持久化审计必须引用已经晋升的证据",
            environment,
        )
        run(
            [
                "persistence-audit",
                "--frame-id",
                audit_frame,
                "--trigger",
                "round_end",
                "--decision",
                "promoted",
                "--evidence-id",
                audit_evidence,
            ],
            environment,
        )
        if close(audit_frame, environment).returncode:
            raise AssertionError("promoted-evidence-audited frame did not close")

        withdrawn_evidence = capture(
            workspace,
            "旧规划 Alpha 应进入活动召回",
            "withdraw-plan",
            environment,
        )
        withdrawn_promotion = promote(
            workspace,
            withdrawn_evidence,
            "旧规划 Alpha 应进入活动召回",
            environment,
        )
        withdrawn_memory = withdrawn_promotion["semantic_memory_id"]
        if withdrawn_memory not in search_ids(workspace, "旧规划 Alpha", environment):
            raise AssertionError("promoted planning evidence was not searchable before withdrawal")
        disposition(
            workspace,
            withdrawn_evidence,
            "withdrawn",
            "user withdrew obsolete Alpha planning evidence",
            environment,
        )
        if withdrawn_memory in search_ids(workspace, "旧规划 Alpha", environment):
            raise AssertionError("withdrawn evidence continued influencing semantic recall")

        expired_evidence = capture(
            workspace,
            "过期规划 Beta 不得继续晋升",
            "expire-plan",
            environment,
        )
        disposition(
            workspace,
            expired_evidence,
            "expired",
            "planning horizon ended",
            environment,
        )
        expired_promotion = completed(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                expired_evidence,
                "--kind",
                "decision",
                "--summary",
                "过期规划 Beta 不得继续晋升",
                "--stable",
                "--reusable",
                "--privacy-reviewed",
            ],
            environment,
        )
        if expired_promotion.returncode == 0 or "state EXPIRED" not in expired_promotion.stderr:
            raise AssertionError("expired evidence passed Promotion Gate")

        old_evidence = capture(
            workspace,
            "规划 Gamma 使用旧约束",
            "supersede-old",
            environment,
        )
        replacement_evidence = capture(
            workspace,
            "规划 Gamma 使用新约束",
            "supersede-new",
            environment,
        )
        disposition(
            workspace,
            old_evidence,
            "superseded",
            "new conversation evidence replaces the old Gamma constraint",
            environment,
            replacement=replacement_evidence,
        )
        superseded_promotion = completed(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                old_evidence,
                "--kind",
                "decision",
                "--summary",
                "规划 Gamma 使用旧约束",
                "--stable",
                "--reusable",
                "--privacy-reviewed",
            ],
            environment,
        )
        if superseded_promotion.returncode == 0 or "state SUPERSEDED" not in superseded_promotion.stderr:
            raise AssertionError("superseded evidence passed Promotion Gate")
        replacement_state = run(
            [
                "evidence-show",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                replacement_evidence,
            ],
            environment,
        )["results"][0]["lifecycle_state"]
        if replacement_state != "CANDIDATE":
            raise AssertionError("replacement evidence did not remain an active candidate")

        cross_workspace = completed(
            [
                "evidence-disposition",
                "--workspace",
                unrelated,
                "--scope",
                "memory-system",
                "--evidence-id",
                replacement_evidence,
                "--state",
                "withdrawn",
                "--reason",
                "must not cross workspace",
            ],
            environment,
        )
        if cross_workspace.returncode == 0:
            raise AssertionError("evidence lifecycle leaked across workspaces")

        semantic_files = list(
            (root / "method-state" / "memory" / "semantic" / "workspaces").rglob("*.jsonl")
        )
        semantic_text = "\n".join(path.read_text(encoding="utf-8") for path in semantic_files)
        if withdrawn_memory not in semantic_text:
            raise AssertionError("withdrawal deleted semantic history instead of deactivating it")

        print(
            json.dumps(
                {
                    "valid": True,
                    "omitted_round_audit_blocked": True,
                    "route_change_audit_enforced": True,
                    "version_switch_audit_enforced": True,
                    "post_close_control_does_not_retroactively_invalidate": True,
                    "audit_failure_detected": True,
                    "withdrawn_semantic_recall_removed": True,
                    "expired_evidence_promotion_blocked": True,
                    "superseded_evidence_promotion_blocked": True,
                    "history_preserved": True,
                    "cross_workspace_isolated": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
