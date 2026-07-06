"""Regression tests for WeiLan Memory 0.3 semantic recall and archive planning."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")


def run(arguments, environment):
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return json.loads(completed.stdout)


def open_frame(workspace, environment, close=True):
    opened = run(
        [
            "open",
            "--level",
            "L2",
            "--workspace",
            workspace,
            "--problem",
            "semantic memory fixture",
            "--success",
            "fixture created",
        ],
        environment,
    )
    if close:
        run(
            [
                "close",
                "--frame-id",
                opened["frame_id"],
                "--outcome",
                "success",
                "--verdict",
                "fixture closed",
            ],
            environment,
        )
    return opened["frame_id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()

    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="weilan-semantic-test-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        unrelated = str(root / "unrelated")
        Path(workspace).mkdir()
        source = root / "source.txt"
        source.write_text("v1", encoding="utf-8")

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
                "continue semantic memory fixture",
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
                "semantic memory fixture",
                "--status",
                "active",
                "--next",
                "test recall authority",
                "--source",
                str(source),
            ],
            environment,
        )

        first = run(
            [
                "memory-consolidate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--kind",
                "decision",
                "--summary",
                "冷启动使用控制账本恢复作用域",
                "--tag",
                "cold-start",
                "--tag",
                "冷启动",
                "--source",
                str(source),
            ],
            environment,
        )
        chinese = run(
            [
                "memory-search",
                "--workspace",
                workspace,
                "--query",
                "冷启动",
            ],
            environment,
        )
        if not chinese["results"] or chinese["results"][0]["memory_id"] != first["memory_id"]:
            raise AssertionError("Chinese semantic search did not return the consolidated memory")
        if chinese["index_states"].get("memory-system") != "FRESH":
            raise AssertionError("consolidation did not leave a fresh index")
        if not chinese["results"][0]["sources_fresh"]:
            raise AssertionError("unchanged source was reported stale")
        activation = run(
            ["memory-recall", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if activation["activation"]["state"] != "ACTIVE":
            raise AssertionError("semantic consolidation changed activation authority")
        if chinese["authority"] != "recall_evidence_only_never_authorizes_continuation":
            raise AssertionError("semantic search did not declare its non-authoritative role")

        source.write_text("v2", encoding="utf-8")
        changed = run(
            ["memory-search", "--workspace", workspace, "--query", "cold-start"],
            environment,
        )
        if changed["results"][0]["sources_fresh"]:
            raise AssertionError("changed source was not reported stale")

        second = run(
            [
                "memory-consolidate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--kind",
                "decision",
                "--summary",
                "冷启动以作用域投影和控制头共同恢复",
                "--tag",
                "cold-start",
                "--source",
                str(source),
                "--supersedes",
                first["memory_id"],
            ],
            environment,
        )
        superseded = run(
            ["memory-search", "--workspace", workspace, "--query", "控制账本"],
            environment,
        )
        if first["memory_id"] in {item["memory_id"] for item in superseded["results"]}:
            raise AssertionError("superseded semantic memory remained searchable")
        current = run(
            ["memory-search", "--workspace", workspace, "--query", "作用域投影"],
            environment,
        )
        if not current["results"] or current["results"][0]["memory_id"] != second["memory_id"]:
            raise AssertionError("superseding memory was not searchable")

        semantic_file = Path(first["path"])
        fallback_id = str(uuid.uuid4())
        fallback = dict(current["results"][0])
        for key in ("score", "matched_tokens", "sources_fresh"):
            fallback.pop(key, None)
        fallback["memory_id"] = fallback_id
        fallback["timestamp_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        fallback["summary"] = "索引过期时回退扫描仍可见"
        fallback["tags"] = ["fallback"]
        fallback["supersedes"] = []
        with semantic_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(fallback, ensure_ascii=False, sort_keys=True) + "\n")
        fallback_search = run(
            ["memory-search", "--workspace", workspace, "--query", "回退扫描"],
            environment,
        )
        if fallback_search["index_states"].get("memory-system") != "STALE_FALLBACK_SCAN":
            raise AssertionError("stale semantic index was not detected")
        if not fallback_search["results"] or fallback_search["results"][0]["memory_id"] != fallback_id:
            raise AssertionError("stale-index fallback omitted a live entry")
        run(
            ["memory-index", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        rebuilt = run(
            ["memory-search", "--workspace", workspace, "--query", "回退扫描"],
            environment,
        )
        if rebuilt["index_states"].get("memory-system") != "FRESH":
            raise AssertionError("explicit index rebuild did not produce a fresh index")

        isolated = run(
            ["memory-search", "--workspace", unrelated, "--query", "冷启动"],
            environment,
        )
        if isolated["matched"] or isolated["results"]:
            raise AssertionError("semantic memory leaked across unrelated workspaces")

        referenced = open_frame(workspace, environment)
        eligible = open_frame(workspace, environment)
        unclosed = open_frame(workspace, environment, close=False)
        run(
            [
                "memory-consolidate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--kind",
                "fact",
                "--summary",
                "归档测试引用帧",
                "--source",
                f"frame:{referenced}",
            ],
            environment,
        )
        plan = run(
            ["memory-archive-plan", "--workspace", workspace, "--before", "9999-12-31"],
            environment,
        )
        eligible_ids = {item["frame_id"] for item in plan["eligible_closed_frames"]}
        blocked = {item["frame_id"]: item["reasons"] for item in plan["blocked_frames"]}
        if eligible not in eligible_ids:
            raise AssertionError("unreferenced closed frame was not eligible for archival")
        if referenced not in blocked or not any("referenced" in reason for reason in blocked[referenced]):
            raise AssertionError("referenced frame was not protected from archival")
        if unclosed not in blocked or "frame_not_closed" not in blocked[unclosed]:
            raise AssertionError("open frame was not protected from archival")
        if plan["mode"] != "plan_only_no_files_moved_or_deleted":
            raise AssertionError("archive planning unexpectedly became mutating")

        print(
            json.dumps(
                {
                    "valid": True,
                    "bilingual_search": True,
                    "supersedes": True,
                    "source_freshness": True,
                    "activation_authority_preserved": True,
                    "stale_index_fallback": True,
                    "cross_workspace_isolated": True,
                    "archive_plan_only": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
