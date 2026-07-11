from __future__ import annotations

import json
from pathlib import Path

import wake_brief


STAMP = "2026-07-11T09:00:00+00:00"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def recall_fixture() -> dict:
    return {
        "activation": {"state": "ACTIVE", "continuation_allowed": True},
        "control": {"state": "active", "sources": ["control:event-1"]},
        "freshness": {"fresh": True},
    }


def prospective_fixture(due: bool = True) -> dict:
    goals = []
    if due:
        goals.append(
            {
                "goal_ref": "goal:due",
                "state": "ACTIVE",
                "not_before": "2026-07-11T08:00:00+00:00",
                "causal_events": [{"event_id": "ready-1", "state": "READY"}],
            }
        )
    return {"sources": ["prospective:ledger-1"], "goals": goals}


def seed_root(root: Path, *, owner: bool = True, peer: bool = True) -> None:
    if owner:
        write_jsonl(root / "observer-inbox.jsonl", [{"id": "owner-new", "text": "work"}])
    else:
        write_jsonl(root / "observer-inbox.jsonl", [])
    write_jsonl(root / "observer-inbox-processed.jsonl", [])
    write_jsonl(root / "delegate-inbox-replies.jsonl", [])
    write_jsonl(root / "peer-room.jsonl", [{"from": "claude", "text": "route"}] if peer else [])


def build(root: Path, *, due: bool = True) -> dict:
    return wake_brief.build_brief(
        root=root,
        workspace="D:\\WeilanSkillEvolution",
        scope="skill-evolution",
        updated_at_utc=STAMP,
        now_utc=STAMP,
        recall_fixture=recall_fixture(),
        prospective_fixture=prospective_fixture(due),
    )


def test_site_fingerprint_hash_is_stable_for_same_input(tmp_path: Path) -> None:
    seed_root(tmp_path)

    first = build(tmp_path)
    (tmp_path / "brief-cursor.json").unlink()
    second = build(tmp_path)

    assert first["site_fingerprint"]["hash"] == second["site_fingerprint"]["hash"]


def test_site_fingerprint_booleans_cover_empty_and_non_empty(tmp_path: Path) -> None:
    seed_root(tmp_path)
    non_empty = build(tmp_path)
    assert non_empty["site_fingerprint"]["inbox_has_work"] is True
    assert non_empty["site_fingerprint"]["prospective_has_due"] is True
    assert non_empty["site_fingerprint"]["peer_chat_has_route_change"] is True

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    seed_root(empty_root, owner=False, peer=False)
    empty = build(empty_root, due=False)
    assert empty["site_fingerprint"]["inbox_has_work"] is False
    assert empty["site_fingerprint"]["prospective_has_due"] is False
    assert empty["site_fingerprint"]["peer_chat_has_route_change"] is False


def test_site_fingerprint_keeps_sources_and_source_refs(tmp_path: Path) -> None:
    seed_root(tmp_path)

    brief = build(tmp_path)

    source_refs = [source["ref"] for source in brief["sources"]]
    assert brief["site_fingerprint"]["source_refs"] == source_refs
    assert "sources" in brief
    assert (tmp_path / "peer-room.jsonl").as_posix() in source_refs
    assert "control:event-1" in source_refs
    assert "prospective:ledger-1" in source_refs


def test_site_fingerprint_preserves_cursor_fallback_status(tmp_path: Path) -> None:
    seed_root(tmp_path)
    cursor_path = tmp_path / "brief-cursor.json"
    cursor = wake_brief.write_cursor(tmp_path, cursor_path, "skill-evolution", STAMP)
    cursor["files"]["peer-room.jsonl"]["byte_offset"] = 10_000
    cursor_path.write_text(json.dumps(cursor), encoding="utf-8")

    brief = build(tmp_path)

    assert brief["cursor_status"] == {"status": "full_rescan", "reason": "offset_oob"}
    assert brief["site_fingerprint"]["peer_chat_has_route_change"] is True
    assert brief["site_fingerprint"]["hash"]


def test_site_fingerprint_hash_records_scan_mode(tmp_path: Path) -> None:
    seed_root(tmp_path, owner=False, peer=False)

    full_rescan = build(tmp_path, due=False)
    incremental = build(tmp_path, due=False)

    assert full_rescan["cursor_status"] == {"status": "full_rescan", "reason": "no_cursor"}
    assert incremental["cursor_status"] == {"status": "incremental"}
    assert full_rescan["site_fingerprint"]["inbox_has_work"] is False
    assert incremental["site_fingerprint"]["inbox_has_work"] is False
    assert full_rescan["site_fingerprint"]["prospective_has_due"] is False
    assert incremental["site_fingerprint"]["prospective_has_due"] is False
    assert full_rescan["site_fingerprint"]["peer_chat_has_route_change"] is False
    assert incremental["site_fingerprint"]["peer_chat_has_route_change"] is False
    assert full_rescan["site_fingerprint"]["hash"] != incremental["site_fingerprint"]["hash"]
