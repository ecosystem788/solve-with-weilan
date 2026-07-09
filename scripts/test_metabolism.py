"""Regression tests for Memory 0.7a read-only metabolic contracts and proposals."""

import hashlib
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


def activate(workspace, environment, directive="activate metabolism fixture"):
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


def open_root(workspace, environment, problem="metabolism fixture"):
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
            "root",
            "--problem",
            problem,
            "--success",
            "fixture complete",
        ],
        environment,
    )["frame_id"]


def close_audited(frame_id, environment):
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
            "temporary 0.7a regression fixture",
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
            "fixture closed",
        ],
        environment,
    )


def register_goal(workspace, source, environment, target_ref="goal:task"):
    return run(
        [
            "governance-target-register",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--target-ref",
            target_ref,
            "--target-kind",
            "goal",
            "--scale",
            "task",
            "--death-line",
            "verified contradiction invalidates the current objective",
            "--reentry-condition",
            "new evidence restores a viable objective",
            "--source",
            str(source),
        ],
        environment,
    )


def capture(workspace, environment):
    return run(
        [
            "evidence-capture",
            "--workspace",
            workspace,
            "--scope",
            "memory-system",
            "--signal",
            "verified_result",
            "--claim",
            "当前验证结果对目标形成关键矛盾压力",
            "--source",
            "conversation:metabolism-test#critical-pressure",
        ],
        environment,
    )["evidence_id"]


def snapshot_tree(root):
    result = {}
    if not root.exists():
        return result
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    with tempfile.TemporaryDirectory(prefix="weilan-metabolism-test-", dir=str(Path.home())) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_CODEX_SESSIONS_HOME"] = str(root / "sessions")
        method_state = root / "method-state"
        environment["WEILAN_METHOD_HOME"] = str(method_state)
        workspace = str(root / "workspace")
        open_workspace = str(root / "open-workspace")
        Path(workspace).mkdir()
        Path(open_workspace).mkdir()

        activate(workspace, environment)
        frame_id = open_root(workspace, environment)
        close_audited(frame_id, environment)
        source = root / "source.txt"
        source.write_text("stable source", encoding="utf-8")
        register_goal(workspace, source, environment)

        contract_one = run(
            ["metabolic-contract", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        contract_two = run(
            ["metabolic-contract", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if contract_one != contract_two or contract_one["status"] != "READY":
            raise AssertionError("metabolic contract was not deterministic and ready")
        if contract_one["can_authorize_actions"] or contract_one["can_commit"]:
            raise AssertionError("read-only contract acquired authority")
        if contract_one["bounds"] != {
            "max_steps": 1,
            "writes_allowed": False,
            "background_loop_allowed": False,
            "scope_expansion_allowed": False,
            "top_level_goal_creation_allowed": False,
            "budget_expansion_allowed": False,
        }:
            raise AssertionError("0.7a contract bounds changed")

        trigger_ref = f"frame:{frame_id}"
        state_before_proposal = snapshot_tree(method_state)
        proposal_one = run(
            [
                "metabolic-propose",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--expected-contract-hash",
                contract_one["contract_hash"],
                "--trigger-kind",
                "frame_closed",
                "--trigger-ref",
                trigger_ref,
                "--disposition",
                "continue",
                "--target-ref",
                "goal:task",
                "--branch",
                "main",
            ],
            environment,
        )
        proposal_two = run(
            [
                "metabolic-propose",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--expected-contract-hash",
                contract_one["contract_hash"],
                "--trigger-kind",
                "frame_closed",
                "--trigger-ref",
                trigger_ref,
                "--disposition",
                "continue",
                "--target-ref",
                "goal:task",
                "--branch",
                "main",
            ],
            environment,
        )
        if proposal_one != proposal_two or not proposal_one["admissible"]:
            raise AssertionError("identical transition proposals were not deterministic")
        if proposal_one["writes_planned"] or proposal_one["can_commit"]:
            raise AssertionError("0.7a proposal planned or authorized writes")
        if snapshot_tree(method_state) != state_before_proposal:
            raise AssertionError("read-only contract or proposal mutated method state")

        activate(workspace, environment, "change contract head")
        stale = completed(
            [
                "metabolic-propose",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--expected-contract-hash",
                contract_one["contract_hash"],
                "--trigger-kind",
                "frame_closed",
                "--trigger-ref",
                trigger_ref,
                "--disposition",
                "continue",
                "--target-ref",
                "goal:task",
                "--branch",
                "main",
            ],
            environment,
        )
        stale_result = json.loads(stale.stdout)
        if stale.returncode == 0 or "contract_head_changed" not in stale_result["reasons"]:
            raise AssertionError("stale metabolic contract remained admissible")

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
                "pause metabolism fixture",
            ],
            environment,
        )
        paused_contract = run(
            ["metabolic-contract", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if paused_contract["status"] != "CONTROL_BLOCKED" or paused_contract["allowed_dispositions"] != ["yield"]:
            raise AssertionError("paused scope did not become control-blocked and quiescent")
        activate(workspace, environment, "resume metabolism fixture")

        evidence_id = capture(workspace, environment)
        pressure = run(
            [
                "governance-pressure-record",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--target-ref",
                "goal:task",
                "--kind",
                "contradiction",
                "--strength",
                "critical",
                "--evidence",
                f"evidence:{evidence_id}",
                "--required-change",
                "collapse only after declared death-line admission",
                "--frame-id",
                frame_id,
            ],
            environment,
        )["pressure"]
        pressure_contract = run(
            ["metabolic-contract", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        collapse = run(
            [
                "metabolic-propose",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--expected-contract-hash",
                pressure_contract["contract_hash"],
                "--trigger-kind",
                "pressure_changed",
                "--trigger-ref",
                f"pressure:{pressure['pressure_id']}",
                "--disposition",
                "collapse",
                "--target-ref",
                "goal:task",
                "--death-line-match",
                "verified contradiction invalidates the current objective",
            ],
            environment,
        )
        if not collapse["admissible"] or collapse["successor_preview"] is not None:
            raise AssertionError("valid collapse proposal was rejected or forced a successor")

        run(
            [
                "governance-target-transition",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--target-ref",
                "goal:task",
                "--transition",
                "complete",
                "--evidence",
                f"evidence:{evidence_id}",
                "--frame-id",
                frame_id,
            ],
            environment,
        )
        quiescent = run(
            ["metabolic-contract", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if quiescent["status"] != "QUIESCENT" or quiescent["active_target_refs"]:
            raise AssertionError("terminal target did not produce a quiescent contract")
        yield_proposal = run(
            [
                "metabolic-propose",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--expected-contract-hash",
                quiescent["contract_hash"],
                "--trigger-kind",
                "control_changed",
                "--trigger-ref",
                f"control:{quiescent['control']['event_id']}",
                "--disposition",
                "yield",
            ],
            environment,
        )
        if not yield_proposal["admissible"]:
            raise AssertionError("quiescent yield proposal was rejected")

        activate(open_workspace, environment, "activate open-frame fixture")
        open_frame = open_root(open_workspace, environment, "open head fixture")
        open_source = root / "open-source.txt"
        open_source.write_text("source", encoding="utf-8")
        register_goal(open_workspace, open_source, environment, "goal:open")
        awaiting = run(
            [
                "metabolic-contract",
                "--workspace",
                open_workspace,
                "--scope",
                "memory-system",
            ],
            environment,
        )
        if awaiting["status"] != "AWAITING_FRAME_CLOSE" or awaiting["branches"]["main"]["head_frame_id"] != open_frame:
            raise AssertionError("open head did not block successor planning")

        print(
            json.dumps(
                {
                    "valid": True,
                    "deterministic_contract": True,
                    "deterministic_proposal": True,
                    "read_only_no_state_writes": True,
                    "stale_contract_rejected": True,
                    "paused_scope_control_blocked": True,
                    "critical_collapse_admitted_without_forced_regroup": True,
                    "terminal_target_quiescent": True,
                    "open_head_awaits_close": True,
                    "background_loop_disabled": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
