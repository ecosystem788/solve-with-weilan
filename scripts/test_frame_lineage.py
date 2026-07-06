"""Regression tests for WeiLan Memory 0.4 causal frame lineage and branch governance."""

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


def open_frame(workspace, environment, *, scope=None, branch="main", relation=None, parents=()):
    arguments = [
        "open",
        "--level",
        "L2",
        "--workspace",
        workspace,
        "--problem",
        "lineage fixture",
        "--success",
        "lineage transition accepted",
    ]
    if scope:
        arguments.extend(["--scope", scope, "--branch", branch, "--relation", relation])
        for parent in parents:
            arguments.extend(["--parent", parent])
    return run(arguments, environment)


def close_frame(frame_id, environment):
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
            "isolated lineage fixture has no durable cross-task conclusion",
        ],
        environment,
    )
    return run(
        [
            "close",
            "--frame-id",
            frame_id,
            "--outcome",
            "success",
            "--verdict",
            "lineage fixture closed",
        ],
        environment,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-lineage-test-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        Path(workspace).mkdir()

        anchor = open_frame(workspace, environment)["frame_id"]
        close_frame(anchor, environment)
        base = open_frame(
            workspace,
            environment,
            scope="memory-system",
            relation="continue",
            parents=[anchor],
        )
        if base["causal"]["parent_frame_ids"] != [anchor]:
            raise AssertionError("bootstrap continuation did not preserve its causal parent")

        open_parent = completed(
            [
                "open",
                "--level",
                "L2",
                "--workspace",
                workspace,
                "--problem",
                "must fail",
                "--success",
                "must fail",
                "--scope",
                "memory-system",
                "--branch",
                "main",
                "--relation",
                "continue",
                "--parent",
                base["frame_id"],
            ],
            environment,
        )
        if open_parent.returncode == 0 or "must be closed" not in open_parent.stderr:
            raise AssertionError("an open frame was accepted as a causal parent")
        close_frame(base["frame_id"], environment)

        fork = open_frame(
            workspace,
            environment,
            scope="memory-system",
            branch="experiment",
            relation="fork",
            parents=[base["frame_id"]],
        )
        close_frame(fork["frame_id"], environment)
        main_next = open_frame(
            workspace,
            environment,
            scope="memory-system",
            branch="main",
            relation="continue",
            parents=[base["frame_id"]],
        )
        close_frame(main_next["frame_id"], environment)
        joined = open_frame(
            workspace,
            environment,
            scope="memory-system",
            branch="main",
            relation="join",
            parents=[main_next["frame_id"], fork["frame_id"]],
        )
        if joined["causal"]["joined_branch_ids"] != ["experiment"]:
            raise AssertionError("join did not explicitly identify the consumed branch")
        close_frame(joined["frame_id"], environment)

        lineage = run(
            ["lineage-show", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if not lineage["valid"] or lineage["record_count"] != 4:
            raise AssertionError("continue/fork/join lineage did not validate")
        if lineage["branches"]["main"]["head_frame_id"] != joined["frame_id"]:
            raise AssertionError("join did not advance the target branch")
        if lineage["branches"]["experiment"]["status"] != "joined":
            raise AssertionError("joined source branch remained active")

        joined_branch = completed(
            [
                "open",
                "--level",
                "L2",
                "--workspace",
                workspace,
                "--problem",
                "must fail",
                "--success",
                "must fail",
                "--scope",
                "memory-system",
                "--branch",
                "experiment",
                "--relation",
                "continue",
                "--parent",
                fork["frame_id"],
            ],
            environment,
        )
        if joined_branch.returncode == 0 or "joined branch cannot continue" not in joined_branch.stderr:
            raise AssertionError("a joined branch silently continued")

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
                "continue lineage fixture",
            ],
            environment,
        )
        run(
            [
                "memory-update",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--focus",
                "lineage fixture",
                "--status",
                "active",
                "--next",
                "verify activation",
                "--source",
                f"frame:{joined['frame_id']}",
            ],
            environment,
        )
        activation = run(
            ["memory-recall", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if activation["activation"]["state"] != "ACTIVE":
            raise AssertionError("lineage governance changed activation authority")

        concurrent_workspace = str(root / "concurrent")
        Path(concurrent_workspace).mkdir()
        concurrent_anchor = open_frame(concurrent_workspace, environment)["frame_id"]
        close_frame(concurrent_anchor, environment)
        concurrent_base = open_frame(
            concurrent_workspace,
            environment,
            scope="memory-system",
            relation="continue",
            parents=[concurrent_anchor],
        )["frame_id"]
        close_frame(concurrent_base, environment)
        command = [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT),
            "open",
            "--level",
            "L2",
            "--workspace",
            concurrent_workspace,
            "--problem",
            "parallel writer",
            "--success",
            "one writer wins",
            "--scope",
            "memory-system",
            "--branch",
            "main",
            "--relation",
            "continue",
            "--parent",
            concurrent_base,
        ]
        writers = [
            subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            for _ in range(2)
        ]
        writer_results = [writer.communicate(timeout=20) + (writer.returncode,) for writer in writers]
        successful = [item for item in writer_results if item[2] == 0]
        conflicts = [item for item in writer_results if item[2] != 0]
        if len(successful) != 1 or len(conflicts) != 1:
            raise AssertionError(f"same-head concurrent writers were not serialized: {writer_results}")
        if "branch head conflict" not in conflicts[0][1]:
            raise AssertionError("losing concurrent writer did not report a branch head conflict")
        parallel_lineage = run(
            ["lineage-show", "--workspace", concurrent_workspace, "--scope", "memory-system"],
            environment,
        )
        if not parallel_lineage["valid"] or parallel_lineage["record_count"] != 2:
            raise AssertionError("parallel conflict left a corrupt or duplicate lineage transition")

        root_frame = open_frame(
            workspace,
            environment,
            scope="independent-root",
            relation="root",
        )
        close_frame(root_frame["frame_id"], environment)
        root_lineage = run(
            ["lineage-show", "--workspace", workspace, "--scope", "independent-root"],
            environment,
        )
        if not root_lineage["valid"] or root_lineage["record_count"] != 1:
            raise AssertionError("root lineage did not validate")

        print(
            json.dumps(
                {
                    "valid": True,
                    "causal_parent_closure": True,
                    "continue_fork_join": True,
                    "joined_branch_governance": True,
                    "same_head_parallel_writers": "one_success_one_conflict",
                    "activation_authority_preserved": True,
                    "root_and_bootstrap_supported": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
