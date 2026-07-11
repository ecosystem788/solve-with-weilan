from __future__ import annotations

import json
from pathlib import Path

import pytest

import wake_brief


STAMP = "2026-07-10T09:30:00+00:00"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def fixture_recall() -> dict:
    return {
        "activation": {"state": "ACTIVE", "continuation_allowed": True},
        "control": {"state": "active", "sources": ["control:event-1"]},
        "freshness": {"fresh": True},
    }


def fixture_prospective() -> dict:
    return {
        "sources": ["prospective:ledger-1"],
        "goals": [
            {
                "goal_ref": "goal:due",
                "state": "ACTIVE",
                "not_before": "2026-07-10T09:00:00+00:00",
                "causal_events": [
                    {"event_id": "ready-1", "state": "READY"},
                    {"event_id": "done-1", "state": "OBSERVED"},
                ],
            },
            {
                "goal_ref": "goal:future",
                "state": "ACTIVE",
                "not_before": "2026-07-10T10:00:00+00:00",
                "causal_events": [{"event_id": "ready-2", "state": "READY"}],
            },
            {"goal_ref": "goal:closed", "state": "CLOSED", "causal_events": [{"event_id": "ready-3", "state": "READY"}]},
        ],
    }


def seed_root(root: Path) -> None:
    write_jsonl(
        root / "observer-inbox.jsonl",
        [
            {"id": "old-owner", "text": "already handled"},
            {"id": "new-owner", "text": "full owner text"},
        ],
    )
    write_jsonl(root / "observer-inbox-processed.jsonl", [{"id": "old-owner"}])
    write_jsonl(root / "delegate-inbox-replies.jsonl", [{"reply_to": "a", "text": "reply A"}, {"reply_to": "b", "text": "reply B"}])
    write_jsonl(root / "peer-room.jsonl", [{"from": "claude", "text": "chat A"}, {"from": "owner", "text": "chat B"}])


def build(root: Path, **kwargs):
    return wake_brief.build_brief(
        root=root,
        workspace="D:\\WeilanSkillEvolution",
        scope="skill-evolution",
        updated_at_utc=STAMP,
        now_utc=STAMP,
        recall_fixture=fixture_recall(),
        prospective_fixture=fixture_prospective(),
        **kwargs,
    )


def test_lossless_aggregation_keeps_delta_text_and_sources(tmp_path: Path) -> None:
    seed_root(tmp_path)

    brief = build(tmp_path)

    assert brief["authority"]["activation"]["state"] == "ACTIVE"
    assert [row["id"] for row in brief["owner_inbox_delta"]] == ["new-owner"]
    assert brief["owner_inbox_delta"][0]["text"] == "full owner text"
    assert [row["reply_to"] for row in brief["codex_replies_unreviewed"]] == ["a", "b"]
    assert [row["text"] for row in brief["peer_chat_new"]] == ["chat A", "chat B"]
    assert [goal["goal_ref"] for goal in brief["prospective_due"]] == ["goal:due"]
    assert brief["prospective_due"][0]["causal_events"] == [{"event_id": "ready-1", "state": "READY"}]
    refs = {source["ref"] for source in brief["sources"]}
    assert (tmp_path / "observer-inbox.jsonl").as_posix() in refs
    assert (tmp_path / "delegate-inbox-replies.jsonl").as_posix() in refs
    assert "control:event-1" in refs
    assert "prospective:ledger-1" in refs


def test_one_call_reproduces_hand_computed_steps_2_to_8_delta_sets(tmp_path: Path) -> None:
    seed_root(tmp_path)

    brief = build(tmp_path)

    expected = {
        "owner_inbox_delta": ["new-owner"],
        "prospective_due": ["goal:due"],
        "codex_replies_unreviewed": ["a", "b"],
        "peer_chat_new": ["chat A", "chat B"],
    }
    observed = {
        "owner_inbox_delta": [row["id"] for row in brief["owner_inbox_delta"]],
        "prospective_due": [row["goal_ref"] for row in brief["prospective_due"]],
        "codex_replies_unreviewed": [row["reply_to"] for row in brief["codex_replies_unreviewed"]],
        "peer_chat_new": [row["text"] for row in brief["peer_chat_new"]],
    }
    assert observed == expected
    assert set(["authority", "owner_inbox_delta", "prospective_due", "codex_replies_unreviewed", "peer_chat_new", "sources", "cursor_status"]) <= set(brief)


def test_incremental_tail_uses_valid_cursor(tmp_path: Path) -> None:
    seed_root(tmp_path)
    wake_brief.write_cursor(tmp_path, tmp_path / "brief-cursor.json", "skill-evolution", STAMP)
    with (tmp_path / "peer-room.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"from": "codex", "text": "new chat"}) + "\n")
    with (tmp_path / "delegate-inbox-replies.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"reply_to": "c", "text": "reply C"}) + "\n")

    brief = build(tmp_path)

    assert brief["cursor_status"] == {"status": "incremental"}
    assert [row["text"] for row in brief["peer_chat_new"]] == ["new chat"]
    assert [row["reply_to"] for row in brief["codex_replies_unreviewed"]] == ["c"]


def test_missing_cursor_falls_back_to_full_rescan(tmp_path: Path) -> None:
    seed_root(tmp_path)

    brief = build(tmp_path)

    assert brief["cursor_status"] == {"status": "full_rescan", "reason": "no_cursor"}
    assert [row["text"] for row in brief["peer_chat_new"]] == ["chat A", "chat B"]
    assert [row["reply_to"] for row in brief["codex_replies_unreviewed"]] == ["a", "b"]


@pytest.mark.parametrize("reason", ["file_shrank", "offset_oob", "prefix_mismatch", "line_count_mismatch"])
def test_cursor_integrity_failure_falls_back_without_losing_tail(tmp_path: Path, reason: str) -> None:
    seed_root(tmp_path)
    cursor_path = tmp_path / "brief-cursor.json"
    cursor = wake_brief.write_cursor(tmp_path, cursor_path, "skill-evolution", STAMP)

    if reason == "file_shrank":
        (tmp_path / "peer-room.jsonl").write_text("", encoding="utf-8")
    elif reason == "offset_oob":
        cursor["files"]["peer-room.jsonl"]["byte_offset"] = 10_000
        cursor_path.write_text(json.dumps(cursor), encoding="utf-8")
    elif reason == "prefix_mismatch":
        data = (tmp_path / "peer-room.jsonl").read_text(encoding="utf-8")
        (tmp_path / "peer-room.jsonl").write_text(data.replace("chat A", "chat X"), encoding="utf-8")
    elif reason == "line_count_mismatch":
        cursor["files"]["peer-room.jsonl"]["line_count"] += 1
        cursor_path.write_text(json.dumps(cursor), encoding="utf-8")

    brief = build(tmp_path)

    assert brief["cursor_status"] == {"status": "full_rescan", "reason": reason}
    if reason == "file_shrank":
        assert brief["peer_chat_new"] == []
    else:
        assert brief["peer_chat_new"]
    assert [row["reply_to"] for row in brief["codex_replies_unreviewed"]] == ["a", "b"]


def test_missing_required_sources_surfaces_missing_sources(tmp_path: Path) -> None:
    # Empty root (simulates a deployed scripts dir holding only the script):
    # the live inbox/chat files are absent.  The brief must NOT present as a
    # healthy incremental/full_rescan empty result -- it must flag the false-zero.
    brief = build(tmp_path)

    assert brief["cursor_status"]["status"] == "missing_sources"
    assert set(brief["cursor_status"]["missing_sources"]) == {"peer-room.jsonl", "delegate-inbox-replies.jsonl"}
    assert brief["cursor_status"]["prior_status"] == {"status": "full_rescan", "reason": "no_cursor"}
    assert brief["peer_chat_new"] == []
    assert brief["owner_inbox_delta"] == []


def test_owner_inbox_absence_alone_is_not_missing_sources(tmp_path: Path) -> None:
    # peer-room + delegate-inbox-replies present; observer-inbox legitimately absent
    # (wake prompt treats its absence as normal).  Guard must not false-positive.
    write_jsonl(tmp_path / "delegate-inbox-replies.jsonl", [{"reply_to": "a", "text": "reply A"}])
    write_jsonl(tmp_path / "peer-room.jsonl", [{"from": "claude", "text": "chat A"}])

    brief = build(tmp_path)

    assert brief["cursor_status"]["status"] != "missing_sources"
    assert brief["owner_inbox_delta"] == []


def test_main_derives_root_from_workspace_when_root_absent(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_build_brief(*, root, workspace, scope, updated_at_utc):
        captured["root"] = root
        return {"ok": True}

    monkeypatch.setattr(wake_brief, "build_brief", fake_build_brief)
    rc = wake_brief.main(["--workspace", str(tmp_path), "--scope", "skill-evolution", "--updated-at-utc", STAMP])

    assert rc == 0
    assert captured["root"] == tmp_path / "proposals" / "bounded-scheduler-v0.1" / "impl"


def test_main_uses_explicit_root_when_passed(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_build_brief(*, root, workspace, scope, updated_at_utc):
        captured["root"] = root
        return {"ok": True}

    monkeypatch.setattr(wake_brief, "build_brief", fake_build_brief)
    explicit = tmp_path / "elsewhere" / "impl"
    rc = wake_brief.main(
        ["--workspace", str(tmp_path), "--scope", "s", "--root", str(explicit), "--updated-at-utc", STAMP]
    )

    assert rc == 0
    assert captured["root"] == explicit


def test_cursor_advances_and_second_call_is_empty_incremental(tmp_path: Path) -> None:
    seed_root(tmp_path)

    first = build(tmp_path)
    second = build(tmp_path)

    assert first["cursor_status"] == {"status": "full_rescan", "reason": "no_cursor"}
    assert second["cursor_status"] == {"status": "incremental"}
    assert second["peer_chat_new"] == []
    assert second["codex_replies_unreviewed"] == []
    cursor, reason = wake_brief.load_cursor(tmp_path / "brief-cursor.json")
    assert reason is None
    assert wake_brief.validate_cursor(tmp_path, cursor, None) == ("incremental", None)
