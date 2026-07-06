"""Regression tests for Memory 0.5 conversation evidence and promotion gate."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")


def write_session_fixture(root):
    session = root / "sessions" / "2026" / "06" / "30" / "rollout-thread-1.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    records = [{"type": "session_meta", "payload": {"id": "thread-1"}}]
    for turn_id in ("turn-1", "turn-2", "turn-3", "turn-4", "turn-4b", "turn-5"):
        records.extend(
            [
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
                {"type": "turn_context", "payload": {"turn_id": turn_id}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": f"source for {turn_id}"}},
                {"type": "event_msg", "payload": {"type": "agent_message", "message": f"ack {turn_id}"}},
                {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn_id}},
            ]
        )
    session.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def write_claude_session_fixture(root):
    session = root / "sessions" / "claude" / "project" / "claude-thread-1.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "summary", "summary": "not part of the public turn"},
        {
            "type": "user",
            "sessionId": "claude-thread-1",
            "uuid": "claude-turn-1",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "claude source"}],
            },
        },
        {
            "type": "assistant",
            "sessionId": "claude-thread-1",
            "uuid": "claude-assistant-1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private chain"},
                    {"type": "tool_use", "name": "shell", "input": {"command": "secret"}},
                    {"type": "text", "text": "claude ack"},
                ],
            },
        },
        {
            "type": "user",
            "sessionId": "claude-thread-1",
            "uuid": "claude-tool-result-1",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "tool output"}],
            },
        },
        {
            "type": "assistant",
            "sessionId": "claude-thread-1",
            "uuid": "claude-assistant-2",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "claude final"}],
            },
        },
        {
            "type": "user",
            "sessionId": "claude-thread-1",
            "uuid": "claude-turn-2",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "next turn"}],
            },
        },
    ]
    session.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


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


def capture(workspace, environment, signal, claim, *sources):
    arguments = [
        "evidence-capture",
        "--workspace",
        workspace,
        "--scope",
        "memory-system",
        "--signal",
        signal,
        "--claim",
        claim,
    ]
    for source in sources:
        arguments.extend(["--source", source])
    return run(arguments, environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-evidence-test-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        environment["WEILAN_CODEX_SESSIONS_HOME"] = str(root / "sessions")
        write_session_fixture(root)
        write_claude_session_fixture(root)
        workspace = str(root / "workspace")
        unrelated = str(root / "unrelated")
        Path(workspace).mkdir()

        casual = capture(
            workspace,
            environment,
            "casual_chat",
            "今天天气不错",
            "conversation:thread-1#turn-1",
        )
        if casual["saved"] or casual["persistence"] != "none":
            raise AssertionError("casual chat was persisted")
        evidence_root = root / "method-state" / "memory" / "evidence" / "workspaces"
        if evidence_root.exists() and list(evidence_root.rglob("*.jsonl")):
            raise AssertionError("casual chat created an evidence shard")

        claude = capture(
            workspace,
            environment,
            "architectural_decision",
            "Claude transcript snapshots public user and assistant text",
            "conversation:claude-thread-1#claude-turn-1",
        )
        if not claude["saved"]:
            raise AssertionError("Claude transcript conversation evidence was not captured")
        claude_record = run(
            [
                "evidence-show",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                claude["evidence_id"],
            ],
            environment,
        )["results"][0]
        claude_snapshot = claude_record["source_snapshots"][0]
        if claude_snapshot["session_format"] != "claude_transcript":
            raise AssertionError("Claude transcript snapshot did not record its source format")
        if claude_snapshot["message_count"] != 3:
            raise AssertionError("Claude transcript snapshot included non-public or next-turn content")
        expected_messages = [
            {"role": "user", "message": "claude source"},
            {"role": "assistant", "message": "claude ack"},
            {"role": "assistant", "message": "claude final"},
        ]
        expected_hash = json.dumps(expected_messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if claude_snapshot["content_hash"] != hashlib.sha256(expected_hash).hexdigest():
            raise AssertionError("Claude transcript snapshot hashed the wrong public message set")

        sensitive = capture(
            workspace,
            environment,
            "durable_constraint",
            "api_key=sk-1234567890abcdefghijklmnop",
            "conversation:thread-1#turn-2",
        )
        if sensitive["saved"] or "credential_like_material" not in sensitive["reason_codes"]:
            raise AssertionError("credential-like conversation content was not rejected")
        oversized_turn = capture(
            workspace,
            environment,
            "explicit_user_directive",
            "\n".join(f"line {index}" for index in range(7)),
            "conversation:thread-1#turn-3",
        )
        if oversized_turn["saved"] or "claim_exceeds_six_line_fragment_boundary" not in oversized_turn["reason_codes"]:
            raise AssertionError("multi-line raw conversation was not rejected")

        missing_conversation_source = completed(
            [
                "evidence-capture",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--signal",
                "architectural_decision",
                "--claim",
                "important but untraceable",
                "--source",
                "frame:missing",
            ],
            environment,
        )
        if missing_conversation_source.returncode == 0 or "conversation evidence requires" not in missing_conversation_source.stderr:
            raise AssertionError("evidence without a conversation source was accepted")

        important = capture(
            workspace,
            environment,
            "architectural_decision",
            "Memory 0.5 使用对话证据层与晋升门，普通闲聊不进入记忆",
            "conversation:thread-1#turn-4",
        )
        if not important["saved"] or important["semantic_memory_written"]:
            raise AssertionError("important evidence was not captured as a non-semantic candidate")
        candidate = run(
            [
                "evidence-show",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                important["evidence_id"],
            ],
            environment,
        )
        if candidate["results"][0]["promotion_status"] != "CANDIDATE":
            raise AssertionError("new evidence was not held as a candidate")

        rejected = run(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                important["evidence_id"],
                "--kind",
                "decision",
                "--summary",
                "Memory 0.5 使用有来源的对话证据和晋升门",
            ],
            environment,
        )
        if rejected["promoted"] or rejected["semantic_memory_written"]:
            raise AssertionError("failed promotion gate wrote semantic memory")
        expected_reasons = {
            "stability_not_confirmed",
            "cross_task_reuse_not_confirmed",
            "privacy_review_not_confirmed",
        }
        if not expected_reasons.issubset(set(rejected["reason_codes"])):
            raise AssertionError("promotion gate omitted required checks")
        semantic_search_before = run(
            [
                "memory-search",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--query",
                "Memory 0.5 晋升门",
            ],
            environment,
        )
        if semantic_search_before["results"]:
            raise AssertionError("rejected evidence appeared in semantic recall")

        wrong_kind_evidence = capture(
            workspace,
            environment,
            "durable_constraint",
            "普通闲聊不得进入语义记忆",
            "conversation:thread-1#turn-4b",
        )
        wrong_kind = run(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                wrong_kind_evidence["evidence_id"],
                "--kind",
                "decision",
                "--summary",
                "普通闲聊不得进入语义记忆",
                "--stable",
                "--reusable",
                "--privacy-reviewed",
            ],
            environment,
        )
        if wrong_kind["promoted"] or "semantic_kind_not_allowed_for_signal" not in wrong_kind["reason_codes"]:
            raise AssertionError("signal-to-semantic-kind gate was not enforced")

        promoted = run(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                important["evidence_id"],
                "--kind",
                "decision",
                "--summary",
                "Memory 0.5 使用有来源的对话证据和晋升门",
                "--detail",
                "普通闲聊默认拒绝，证据候选通过来源、稳定、复用与隐私检查后才进入语义记忆。",
                "--tag",
                "memory-0.5",
                "--stable",
                "--reusable",
                "--privacy-reviewed",
            ],
            environment,
        )
        if not promoted["promoted"] or not promoted["semantic_memory_written"]:
            raise AssertionError("qualified evidence did not pass the promotion gate")
        semantic_search_after = run(
            [
                "memory-search",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--query",
                "Memory 0.5 晋升门",
            ],
            environment,
        )
        if not semantic_search_after["results"]:
            raise AssertionError("promoted evidence was not semantically searchable")
        semantic = semantic_search_after["results"][0]
        if f"evidence:{important['evidence_id']}" not in semantic["sources"]:
            raise AssertionError("promoted semantic memory lost its evidence source")
        if not semantic["sources_fresh"]:
            raise AssertionError("promoted evidence source was unexpectedly stale")
        promoted_status = run(
            [
                "evidence-show",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                important["evidence_id"],
            ],
            environment,
        )
        if promoted_status["results"][0]["promotion_status"] != "PROMOTED":
            raise AssertionError("evidence status did not reflect promotion")

        duplicate = completed(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                important["evidence_id"],
                "--kind",
                "decision",
                "--summary",
                "Memory 0.5 使用有来源的对话证据和晋升门",
                "--stable",
                "--reusable",
                "--privacy-reviewed",
            ],
            environment,
        )
        if duplicate.returncode == 0 or "already promoted" not in duplicate.stderr:
            raise AssertionError("one evidence fragment was promoted twice")

        mutable_source = root / "decision.txt"
        mutable_source.write_text("v1", encoding="utf-8")
        stale = capture(
            workspace,
            environment,
            "durable_constraint",
            "来源变化后不得晋升旧对话证据",
            "conversation:thread-1#turn-5",
            str(mutable_source),
        )
        mutable_source.write_text("v2", encoding="utf-8")
        stale_gate = run(
            [
                "evidence-promote",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--evidence-id",
                stale["evidence_id"],
                "--kind",
                "constraint",
                "--summary",
                "来源变化后不得晋升旧对话证据",
                "--stable",
                "--reusable",
                "--privacy-reviewed",
            ],
            environment,
        )
        if stale_gate["promoted"] or "evidence_sources_stale" not in stale_gate["reason_codes"]:
            raise AssertionError("stale evidence source passed the promotion gate")

        activation_source = root / "activation.txt"
        activation_source.write_text("stable", encoding="utf-8")
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
                "continue evidence fixture",
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
                "evidence fixture",
                "--status",
                "active",
                "--next",
                "verify authority",
                "--source",
                str(activation_source),
            ],
            environment,
        )
        activation = run(
            ["memory-recall", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if activation["activation"]["state"] != "ACTIVE":
            raise AssertionError("conversation evidence changed activation authority")

        unrelated_evidence = run(
            ["evidence-show", "--workspace", unrelated, "--scope", "memory-system"],
            environment,
        )
        if unrelated_evidence["results"]:
            raise AssertionError("conversation evidence leaked across workspaces")

        audit_records = []
        for path in (root / "method-state" / "memory" / "evidence" / "promotions").rglob("*.jsonl"):
            audit_records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        if any("summary" in record for record in audit_records):
            raise AssertionError("promotion audit persisted rejected semantic text instead of a hash")

        print(
            json.dumps(
                {
                    "valid": True,
                    "casual_chat_persistence": "none",
                    "sensitive_fragment_persistence": "none",
                    "raw_conversation_boundary": "six_lines",
                    "failed_gate_semantic_writes": 0,
                    "qualified_promotion": True,
                    "evidence_provenance_preserved": True,
                    "stale_source_rejected": True,
                    "cross_workspace_isolated": True,
                    "activation_authority_preserved": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
