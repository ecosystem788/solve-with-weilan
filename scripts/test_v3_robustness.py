"""Regression tests for barrel-fix-v3 (depollution + remaining robustness staves).

Covers:
- archive-plan fails closed on reference-ledger corruption and never lists an
  unreadable frame as eligible;
- typed ContractConflict classification (including reworded messages) and the
  resumed-claim control gate in the bounded runner;
- legacy `open` canonicalizes the stored workspace;
- conversation source resolution never falls back to scanning the whole corpus;
- contended Windows lock raises a diagnosable timeout instead of bare EACCES;
- atomic JSON replace retries through transient PermissionError.
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import runner
import runtime_core
import transaction
import weilan_trace


WORKSPACE = r"D:\V3RobustnessTest"
SCOPE = "v3-robustness"


@pytest.fixture()
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "method-state"
    monkeypatch.setenv("WEILAN_METHOD_HOME", str(home))
    return home


def fake_manifest():
    return {
        "schema_version": "weilan_runner_manifest_v0.7d",
        "max_steps": 1,
        "events": [
            {
                "event_id": "fake-event",
                "proposal": {
                    "trigger_kind": "frame_closed",
                    "trigger_ref": "frame:fake",
                    "disposition": "continue",
                    "target_ref": "goal:fake",
                    "branches": ["main"],
                },
                "materialization": {},
            }
        ],
    }


def run_kwargs(root, key):
    return dict(
        workspace="workspace", scope="scope", workspace_key="wk", scope_key="sk",
        idempotency_key=key, manifest=fake_manifest(),
    )


def test_resumed_claim_pause_stops_with_true_reason(tmp_path):
    state_root = tmp_path / "resume-pause"
    contract = {"contract_hash": "contract-0", "status": "READY"}

    def contract_loader():
        return dict(contract)

    def materialize(event, key, expected):
        raise ValueError("transaction writes are not allowed for a paused scope")

    def reconcile(event, key, expected, exc):
        return {"state": "NOT_STARTED"}

    with pytest.raises(RuntimeError, match="injected_runner_failure"):
        runner.execute_run(
            state_root, **run_kwargs(state_root, "resume-pause"),
            contract_loader=contract_loader, materialize=materialize,
            reconcile_materialization=reconcile, fault_at="after_event_claimed",
        )

    # the scope is paused between crash and resume
    contract["status"] = "CONTROL_BLOCKED"
    run_state, journal = runner.execute_run(
        state_root, **run_kwargs(state_root, "resume-pause"),
        contract_loader=contract_loader, materialize=materialize,
        reconcile_materialization=reconcile,
    )
    assert run_state["status"] == "STOPPED"
    assert run_state["stop_reason"] == "CONTROL_BLOCKED"
    assert journal["issues"] == []


def test_reworded_contract_conflict_still_classifies_as_conflict(tmp_path):
    state_root = tmp_path / "typed-conflict"
    contract = {"contract_hash": "contract-0", "status": "READY"}

    def contract_loader():
        return dict(contract)

    def materialize(event, key, expected):
        raise transaction.ContractConflict(
            "expected contract does not match prepared transaction"
        )

    def reconcile(event, key, expected, exc):
        return {"state": "NOT_STARTED"}

    run_state, _ = runner.execute_run(
        state_root, **run_kwargs(state_root, "typed-conflict"),
        contract_loader=contract_loader, materialize=materialize,
        reconcile_materialization=reconcile,
    )
    assert run_state["status"] == "STOPPED"
    assert run_state["stop_reason"] == "CONFLICT"


def test_archive_plan_fails_closed_on_semantic_corruption(state_home, tmp_path, capsys):
    source_file = tmp_path / "fact.md"
    source_file.write_text("fact\n", encoding="utf-8")
    frame_id = "frame-old-closed"
    frame_path = runtime_core.state_root() / "frames" / "2020-01-01" / f"{frame_id}.jsonl"
    opened = weilan_trace.make_event(frame_id, "frame_opened", "L2", WORKSPACE, {})
    closed = weilan_trace.make_event(frame_id, "frame_closed", "L2", WORKSPACE, {"outcome": "success"})
    weilan_trace.append_event(frame_path, opened)
    weilan_trace.append_event(frame_path, closed)

    def plan():
        args = argparse.Namespace(workspace=WORKSPACE, before="2026-01-01")
        code = weilan_trace.command_memory_archive_plan(args)
        return code, json.loads(capsys.readouterr().out)

    code, clean = plan()
    assert code != 1
    assert [item["frame_id"] for item in clean["eligible_closed_frames"]] == [frame_id]

    # corrupt one semantic shard of this workspace: planning must fail closed
    semantic_dir = (
        runtime_core.state_root() / "memory" / "semantic" / "workspaces"
        / runtime_core.workspace_key(WORKSPACE) / runtime_core.scope_key(SCOPE)
    )
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "2020-01-01.jsonl").write_bytes(b"corrupt semantic line\n")
    code, blocked = plan()
    assert code == 1
    assert blocked["planning_blocked"] is True
    assert blocked["eligible_closed_frames"] == []
    assert blocked["corruption_warnings"]


def test_archive_plan_blocks_unreadable_frame(state_home, capsys):
    bad_path = runtime_core.state_root() / "frames" / "2020-01-01" / "frame-bad.jsonl"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(b"unreadable frame line\n{\"broken\": \"json\"\n")
    args = argparse.Namespace(workspace=WORKSPACE, before="2026-01-01")
    weilan_trace.command_memory_archive_plan(args)
    output = json.loads(capsys.readouterr().out)
    assert output["eligible_closed_frames"] == []
    assert any(
        "unreadable_frame_shard" in reason
        for item in output["blocked_frames"]
        for reason in item.get("reasons", [])
    )


def test_legacy_open_stores_canonical_workspace(state_home, capsys):
    args = argparse.Namespace(
        scope=None, level="L1", workspace=r"D:\V3RobustnessTest\sub\..",
        problem="p", success="s", budget=None,
    )
    weilan_trace.command_open(args)
    result = json.loads(capsys.readouterr().out)
    events = weilan_trace.read_events(Path(result["path"]))
    assert events[0]["workspace"] == runtime_core.canonical_workspace(r"D:\V3RobustnessTest")


def test_conversation_resolution_has_no_corpus_fallback(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "unrelated-session.jsonl").write_text("{}\n", encoding="utf-8")
    candidates = weilan_trace.conversation_session_candidates(sessions, "thread-xyz")
    assert candidates == []
    matched = sessions / "rollout-thread-xyz.jsonl"
    matched.write_text("{}\n", encoding="utf-8")
    assert weilan_trace.conversation_session_candidates(sessions, "thread-xyz") == [matched]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows lock semantics")
def test_contended_lock_times_out_with_diagnosable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("WEILAN_LOCK_TIMEOUT_S", "1")
    lock_path = tmp_path / "locks" / ".contended.lock"
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with runtime_core.exclusive_file_lock(lock_path):
            acquired.set()
            release.wait(timeout=30)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert acquired.wait(timeout=10)
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="lock timeout"):
            with runtime_core.exclusive_file_lock(lock_path):
                pass
        assert time.monotonic() - started < 25
    finally:
        release.set()
        thread.join(timeout=30)


def test_atomic_replace_retries_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "view.json"
    calls = {"count": 0}
    original = runtime_core.os.replace

    def flaky(src, dst):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise PermissionError("target held by a concurrent reader")
        return original(src, dst)

    monkeypatch.setattr(runtime_core.os, "replace", flaky)
    runtime_core.write_json_atomic(target, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert calls["count"] == 3


def main():
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
