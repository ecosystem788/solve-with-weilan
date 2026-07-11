"""Build a compact wake briefing without changing authority sources.

This module is deliberately independent of weilan_trace.py internals.  The CLI
may shell out to the public trace command, while tests inject fixture JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


CURSOR_SCHEMA = "wake_cursor_v0.1"
TRACKED_CURSOR_FILES = ("peer-room.jsonl", "delegate-inbox-replies.jsonl")

# Sentinel files that must exist under a live wake root.  Their absence means
# --root points at the wrong directory (e.g. a deployed scripts dir that holds
# only the script, not the inbox/chat files).  When they are missing the brief
# must NOT present as a healthy `incremental` empty result -- that is the silent
# false-zero the deployed-root audit surfaced.  observer-inbox.jsonl is deliberately
# excluded: the wake prompt treats its absence as normal ("不存在就跳过").
REQUIRED_SOURCE_FILES = ("peer-room.jsonl", "delegate-inbox-replies.jsonl")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                rows.append(
                    {
                        "parse_error": str(exc),
                        "source": f"{path.as_posix()}:{line_no}",
                        "raw": line.rstrip("\n"),
                    }
                )
                continue
            if isinstance(value, dict):
                rows.append(value)
            else:
                rows.append(
                    {
                        "parse_error": "jsonl row is not an object",
                        "source": f"{path.as_posix()}:{line_no}",
                        "raw": value,
                    }
                )
    return rows


def _jsonl_from_bytes(path: Path, data: bytes, start_line: int = 0) -> list[dict[str, Any]]:
    text = data.decode("utf-8")
    rows: list[dict[str, Any]] = []
    for offset, line in enumerate(text.splitlines(), start=start_line + 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            rows.append({"parse_error": str(exc), "source": f"{path.as_posix()}:{offset}", "raw": line})
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            rows.append({"parse_error": "jsonl row is not an object", "source": f"{path.as_posix()}:{offset}", "raw": value})
    return rows


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return len(data.splitlines())


def cursor_entry_for(path: Path, byte_offset: int | None = None) -> dict[str, Any]:
    data = path.read_bytes() if path.exists() else b""
    prefix = data if byte_offset is None else data[:byte_offset]
    return {
        "byte_offset": len(prefix),
        "line_count": _line_count(prefix),
        "file_size": len(data),
        "content_tail_hash": _sha256(prefix),
    }


def load_cursor(cursor_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not cursor_path.exists():
        return None, "no_cursor"
    try:
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "unreadable_cursor"
    if not isinstance(cursor, dict) or cursor.get("schema_version") != CURSOR_SCHEMA:
        return None, "unreadable_cursor"
    return cursor, None


def validate_cursor(root: Path, cursor: dict[str, Any] | None, initial_reason: str | None) -> tuple[str, str | None]:
    if cursor is None:
        return "full_rescan", initial_reason or "no_cursor"

    files = cursor.get("files")
    if not isinstance(files, dict):
        return "full_rescan", "unreadable_cursor"

    for name in TRACKED_CURSOR_FILES:
        entry = files.get(name)
        if not isinstance(entry, dict):
            return "full_rescan", "no_cursor"

        path = root / name
        data = path.read_bytes() if path.exists() else b""
        size = len(data)
        stored_size = int(entry.get("file_size", 0))
        offset = int(entry.get("byte_offset", 0))
        stored_lines = int(entry.get("line_count", 0))
        stored_hash = str(entry.get("content_tail_hash", ""))

        if size < stored_size:
            return "full_rescan", "file_shrank"
        if offset > size:
            return "full_rescan", "offset_oob"

        prefix = data[:offset]
        if _sha256(prefix) != stored_hash:
            return "full_rescan", "prefix_mismatch"
        if _line_count(prefix) != stored_lines:
            return "full_rescan", "line_count_mismatch"

    return "incremental", None


def write_cursor(root: Path, cursor_path: Path, scope: str, updated_at_utc: str) -> dict[str, Any]:
    cursor = {
        "schema_version": CURSOR_SCHEMA,
        "scope": scope,
        "files": {name: cursor_entry_for(root / name) for name in TRACKED_CURSOR_FILES},
        "updated_at_utc": updated_at_utc,
    }
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cursor, ensure_ascii=False, indent=2) + "\n"
    # The cursor is a DISPOSABLE cache — validate_cursor already falls back to
    # full_rescan on any mismatch/corruption — so it does not need atomic-
    # replace durability. Prefer atomic (temp+os.replace) where the runtime can
    # do it, but degrade to a direct in-place write when os.replace is blocked
    # (Codex's workspace-write sandbox lets it create the temp but denies the
    # rename → WinError 5; 2026-07-10 incident). A torn in-place write just
    # trips full_rescan next wake — safe, never a data-loss path.
    tmp_path = cursor_path.with_suffix(cursor_path.suffix + ".tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, cursor_path)
    except OSError:
        cursor_path.write_text(payload, encoding="utf-8")
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return cursor


def _processed_ids(path: Path) -> set[str]:
    return {str(row["id"]) for row in read_jsonl(path) if "id" in row}


def owner_inbox_delta(root: Path) -> list[dict[str, Any]]:
    processed = _processed_ids(root / "observer-inbox-processed.jsonl")
    return [row for row in read_jsonl(root / "observer-inbox.jsonl") if str(row.get("id", "")) not in processed]


def _tail_jsonl(root: Path, name: str, mode: str, cursor: dict[str, Any] | None) -> list[dict[str, Any]]:
    path = root / name
    if not path.exists():
        return []
    data = path.read_bytes()
    if mode == "incremental" and cursor is not None:
        offset = int(cursor["files"][name]["byte_offset"])
        prefix = data[:offset]
        return _jsonl_from_bytes(path, data[offset:], start_line=_line_count(prefix))
    return _jsonl_from_bytes(path, data, start_line=0)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _prospective_goals(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    for key in ("goals", "prospective_goals", "items", "entries"):
        value = raw.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def prospective_due(raw: Any, now_utc: str) -> list[dict[str, Any]]:
    now = _parse_time(now_utc)
    due: list[dict[str, Any]] = []
    for goal in _prospective_goals(raw):
        state = str(goal.get("state", goal.get("status", ""))).upper()
        if state != "ACTIVE":
            continue
        not_before = _parse_time(goal.get("not_before") or goal.get("not_before_utc"))
        if now is not None and not_before is not None and not_before > now:
            continue
        ready_events = [
            event
            for event in goal.get("causal_events", [])
            if isinstance(event, dict) and str(event.get("state", event.get("status", ""))).upper() == "READY"
        ]
        item = dict(goal)
        item["causal_events"] = ready_events
        due.append(item)
    return due


def _trace_script() -> str:
    return os.environ.get(
        "WEILAN_TRACE_SCRIPT",
        r"<HOST_ROOT>\skills\solve-with-weilan\scripts\weilan_trace.py",
    )


def _run_json(command: list[str], runner: Callable[[list[str]], str] | None = None) -> Any:
    try:
        output = runner(command) if runner else subprocess.check_output(command, text=True, encoding="utf-8")
        return json.loads(output)
    except Exception as exc:  # noqa: BLE001 - failures are reported in-band by contract.
        return {"error": str(exc), "command": command}


def _authority_from(recall_raw: Any) -> dict[str, Any]:
    if not isinstance(recall_raw, dict):
        return {"error": "memory-recall did not return a JSON object", "raw": recall_raw}
    return {
        "activation": recall_raw.get("activation"),
        "control": recall_raw.get("control"),
        "freshness": recall_raw.get("freshness"),
    }


def _source(ref: str, kind: str, **extra: Any) -> dict[str, Any]:
    item = {"ref": ref, "kind": kind}
    item.update(extra)
    return item


def _source_refs(sources: list[dict[str, Any]]) -> list[str]:
    return [str(source["ref"]) for source in sources if isinstance(source, dict) and "ref" in source]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(payload)


def site_fingerprint_for(brief: dict[str, Any]) -> dict[str, Any]:
    source_refs = _source_refs(brief.get("sources", []))
    fingerprint_subset = {
        "authority": brief.get("authority"),
        "owner_inbox_delta": brief.get("owner_inbox_delta", []),
        "prospective_due": brief.get("prospective_due", []),
        "peer_chat_new": brief.get("peer_chat_new", []),
        "cursor_status": brief.get("cursor_status"),
        "source_refs": source_refs,
    }
    return {
        "hash": _stable_hash(fingerprint_subset),
        "inbox_has_work": bool(brief.get("owner_inbox_delta")),
        "prospective_has_due": bool(brief.get("prospective_due")),
        "peer_chat_has_route_change": bool(brief.get("peer_chat_new")),
        "source_refs": source_refs,
    }


def build_brief(
    *,
    root: Path,
    workspace: str,
    scope: str,
    updated_at_utc: str,
    now_utc: str | None = None,
    recall_fixture: Any | None = None,
    prospective_fixture: Any | None = None,
    runner: Callable[[list[str]], str] | None = None,
) -> dict[str, Any]:
    root = Path(root)
    now = now_utc or updated_at_utc
    trace_script = _trace_script()

    recall_raw = (
        recall_fixture
        if recall_fixture is not None
        else _run_json([sys.executable, trace_script, "memory-recall", "--workspace", workspace, "--scope", scope], runner)
    )
    prospective_raw = (
        prospective_fixture
        if prospective_fixture is not None
        else _run_json([sys.executable, trace_script, "prospective-show", "--workspace", workspace, "--scope", scope], runner)
    )

    cursor_path = root / "brief-cursor.json"
    cursor, cursor_reason = load_cursor(cursor_path)
    cursor_mode, reason = validate_cursor(root, cursor, cursor_reason)

    brief = {
        "authority": _authority_from(recall_raw),
        "owner_inbox_delta": owner_inbox_delta(root),
        "prospective_due": prospective_due(prospective_raw, now),
        "codex_replies_unreviewed": _tail_jsonl(root, "delegate-inbox-replies.jsonl", cursor_mode, cursor),
        "peer_chat_new": _tail_jsonl(root, "peer-room.jsonl", cursor_mode, cursor),
        "sources": [
            _source("command:memory-recall", "command", workspace=workspace, scope=scope),
            _source("command:prospective-show", "command", workspace=workspace, scope=scope),
            _source((root / "observer-inbox.jsonl").as_posix(), "file"),
            _source((root / "observer-inbox-processed.jsonl").as_posix(), "file"),
            _source((root / "delegate-inbox-replies.jsonl").as_posix(), "file"),
            _source((root / "peer-room.jsonl").as_posix(), "file"),
            _source(cursor_path.as_posix(), "cursor"),
        ],
        "cursor_status": {"status": cursor_mode},
    }
    if reason:
        brief["cursor_status"]["reason"] = reason

    missing_sources = [name for name in REQUIRED_SOURCE_FILES if not (root / name).exists()]
    if missing_sources:
        # Wrong root: the live conversation files are absent.  Override the
        # healthy-looking mode with an abnormal status so the wake-prompt
        # fallback fires instead of trusting a blind, empty brief.
        prior_status = brief["cursor_status"]
        brief["cursor_status"] = {
            "status": "missing_sources",
            "missing_sources": missing_sources,
            "prior_status": prior_status,
        }

    for ref in _collect_source_refs(recall_raw):
        brief["sources"].append(_source(str(ref), "recall_source_ref"))
    for ref in _collect_source_refs(prospective_raw):
        brief["sources"].append(_source(str(ref), "prospective_source_ref"))

    brief["site_fingerprint"] = site_fingerprint_for(brief)

    write_cursor(root, cursor_path, scope, updated_at_utc)
    return brief


def _collect_source_refs(value: Any) -> list[Any]:
    refs: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "sources" and isinstance(child, list):
                refs.extend(child)
            else:
                refs.extend(_collect_source_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_collect_source_refs(child))
    return refs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scope", required=True)
    # Prefer an explicit --root; otherwise derive the live root from --workspace,
    # NOT from __file__.  Deriving from __file__ points a deployed copy at its own
    # scripts dir, which holds none of the inbox/chat files -> silent false-zero.
    parser.add_argument("--root", default=None)
    parser.add_argument("--updated-at-utc")
    args = parser.parse_args(argv)

    if args.root is not None:
        root = Path(args.root)
    else:
        root = Path(args.workspace) / "proposals" / "bounded-scheduler-v0.1" / "impl"

    stamp = args.updated_at_utc or datetime.now(timezone.utc).isoformat()
    brief = build_brief(
        root=root,
        workspace=args.workspace,
        scope=args.scope,
        updated_at_utc=stamp,
    )
    json.dump(brief, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
