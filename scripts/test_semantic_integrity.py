"""Acceptance tests for SE-0.3 provenance, conflict, and bounded forgetting."""

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
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def write_session(root, thread_id, turn_id, message):
    path = root / "sessions" / "2026" / "06" / "30" / f"rollout-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"id": thread_id}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
        {"type": "turn_context", "payload": {"turn_id": turn_id}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": message}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "recorded"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn_id}},
    ]
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")


def consolidate(workspace, scope, source, summary, environment, *extra):
    return run([
        "memory-consolidate", "--workspace", workspace, "--scope", scope,
        "--kind", "fact", "--summary", summary, "--source", source, *extra,
    ], environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="weilan-semantic-integrity-", dir=str(parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        environment["WEILAN_CODEX_SESSIONS_HOME"] = str(root / "sessions")
        workspace = str(root / "workspace")
        scope = "memory-system"
        Path(workspace).mkdir()
        source = root / "source.txt"
        source.write_text("stable", encoding="utf-8")
        write_session(root, "thread-exact", "turn-exact", "conversation memory requires promotion gate")

        bypass = completed([
            "memory-consolidate", "--workspace", workspace, "--scope", scope,
            "--kind", "decision", "--summary", "bypass", "--source", "conversation:thread-exact#turn-exact",
        ], environment)
        if bypass.returncode == 0 or "must pass evidence-capture" not in bypass.stderr:
            raise AssertionError("direct conversation-to-semantic bypass remained open")

        missing = completed([
            "evidence-capture", "--workspace", workspace, "--scope", scope,
            "--signal", "architectural_decision", "--claim", "unresolvable",
            "--source", "conversation:thread-exact#missing-turn",
        ], environment)
        if missing.returncode == 0 or "not resolvable" not in missing.stderr:
            raise AssertionError("unresolvable conversation locator was accepted")

        evidence = run([
            "evidence-capture", "--workspace", workspace, "--scope", scope,
            "--signal", "architectural_decision", "--claim", "conversation memory requires promotion gate",
            "--source", "conversation:thread-exact#turn-exact",
        ], environment)
        promoted = run([
            "evidence-promote", "--workspace", workspace, "--scope", scope,
            "--evidence-id", evidence["evidence_id"], "--kind", "decision",
            "--summary", "conversation memory requires promotion gate",
            "--stable", "--reusable", "--privacy-reviewed",
        ], environment)
        if not promoted["promoted"]:
            raise AssertionError("qualified conversation evidence did not promote")

        first = consolidate(workspace, scope, str(source), "route A is preferred", environment)
        second = consolidate(
            workspace, scope, str(source), "route B is preferred", environment,
            "--conflicts-with", first["memory_id"],
        )
        conflicts = run(["memory-conflicts", "--workspace", workspace, "--scope", scope], environment)
        pair = {first["memory_id"], second["memory_id"]}
        if not any(set(item["memory_ids"]) == pair for item in conflicts["unresolved_conflicts"]):
            raise AssertionError("declared semantic conflict was not observable")

        plan = run(["memory-retention-plan", "--workspace", workspace, "--scope", scope, "--max-active", "2"], environment)
        if not plan["proposed_dormant"]:
            raise AssertionError("bounded-retention plan did not identify overflow")
        dormant_id = plan["proposed_dormant"][0]["memory_id"]
        run([
            "memory-disposition", "--workspace", workspace, "--scope", scope,
            "--memory-id", dormant_id, "--state", "dormant", "--reason", "bounded active recall",
            "--source", f"memory:{dormant_id}",
        ], environment)
        search = run(["memory-search", "--workspace", workspace, "--scope", scope, "--query", "preferred"], environment)
        if dormant_id in {item["memory_id"] for item in search["results"]}:
            raise AssertionError("dormant memory remained in active recall")
        history = []
        for path in (root / "method-state" / "memory" / "semantic" / "workspaces").rglob("*.jsonl"):
            history.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        if dormant_id not in {item["memory_id"] for item in history}:
            raise AssertionError("bounded forgetting deleted semantic history")

        print(json.dumps({
            "valid": True,
            "promotion_gate_closed": True,
            "conversation_turn_resolved": True,
            "explicit_conflict_visible": True,
            "bounded_forgetting_preserves_history": True,
        }, indent=2))


if __name__ == "__main__":
    main()
