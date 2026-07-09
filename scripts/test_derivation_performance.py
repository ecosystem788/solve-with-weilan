"""Regression tests for derivation memoization and bounded output (barrel-fix-v2).

Covers:
- one scoped ledger load per derivation scope, with warning replay;
- no memo reuse outside a scope, and no stale read-after-write across commands;
- memoized source snapshots are mutation-safe copies;
- tiered id lookup uses hints but still falls back to the global scan;
- hinted and unhinted id lookups do not share memo slots;
- a corrupt frame in a foreign workspace no longer breaks scoped indexing;
- a corrupt frame in the current workspace/scope still fails hard;
- show commands bound their listing output with --limit.
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_core
import weilan_trace


WORKSPACE = r"D:\DerivationPerfTest"
OTHER_WORKSPACE = r"D:\DerivationPerfOtherWorkspace"
SCOPE = "derivation"


@pytest.fixture()
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "method-state"
    monkeypatch.setenv("WEILAN_METHOD_HOME", str(home))
    return home


def write_evidence_record(index, scope=SCOPE, timestamp=None, workspace=WORKSPACE, evidence_id=None):
    directory = weilan_trace.evidence_directory(workspace, scope)
    record = {
        "schema_version": weilan_trace.EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id or f"ev-{index:04d}",
        "timestamp_utc": timestamp or f"2026-01-01T00:{index:02d}:00+00:00",
        "workspace": runtime_core.canonical_workspace(workspace),
        "scope": scope,
        "signal": "architectural_decision",
        "claim": f"claim {index}",
        "sources": [],
        "source_snapshots": [],
    }
    weilan_trace.append_event(directory / "2026-01-01.jsonl", record)
    return record


def write_semantic_record(memory_id, workspace=WORKSPACE, scope=SCOPE, timestamp="2026-01-01T00:00:00+00:00"):
    directory = (
        runtime_core.state_root() / "memory" / "semantic" / "workspaces"
        / runtime_core.workspace_key(workspace) / runtime_core.scope_key(scope)
    )
    record = {
        "schema_version": weilan_trace.SEMANTIC_SCHEMA_VERSION,
        "memory_id": memory_id,
        "timestamp_utc": timestamp,
        "authority": "derived_semantic_memory_never_authorizes_continuation",
        "workspace": runtime_core.canonical_workspace(workspace),
        "workspace_key": runtime_core.workspace_key(workspace),
        "scope": scope,
        "scope_key": runtime_core.scope_key(scope),
        "kind": "decision",
        "summary": f"semantic {memory_id}",
        "detail": "",
        "tags": [],
        "sources": [],
        "source_snapshots": [],
        "supersedes": [],
        "conflicts_with": [],
    }
    weilan_trace.append_event(directory / "2026-01-01.jsonl", record)
    return record


def test_scoped_load_runs_once_per_memo_scope(state_home, monkeypatch):
    write_evidence_record(1)
    calls = {"count": 0}
    original = weilan_trace.read_jsonl_records

    def counting(path, warnings):
        calls["count"] += 1
        return original(path, warnings)

    monkeypatch.setattr(weilan_trace, "read_jsonl_records", counting)

    with weilan_trace.derivation_memo_scope():
        first = weilan_trace.load_evidence_records(WORKSPACE, SCOPE)
        inside = calls["count"]
        second = weilan_trace.load_evidence_records(WORKSPACE, SCOPE)
        assert calls["count"] == inside
        assert first is second

    # outside any scope every load re-reads the ledger
    weilan_trace.load_evidence_records(WORKSPACE, SCOPE)
    after_first = calls["count"]
    weilan_trace.load_evidence_records(WORKSPACE, SCOPE)
    assert calls["count"] > after_first


def test_memo_replays_warnings_on_hit(state_home):
    directory = weilan_trace.evidence_directory(WORKSPACE, SCOPE)
    weilan_trace.append_event(directory / "2026-01-01.jsonl", {"schema_version": "wrong"})
    with weilan_trace.derivation_memo_scope():
        first_warnings = []
        weilan_trace.load_evidence_records(WORKSPACE, SCOPE, first_warnings)
        second_warnings = []
        weilan_trace.load_evidence_records(WORKSPACE, SCOPE, second_warnings)
    assert first_warnings and first_warnings == second_warnings


def test_no_stale_read_after_write_across_commands(state_home, tmp_path, capsys):
    source_file = tmp_path / "source.md"
    source_file.write_text("fact\n", encoding="utf-8")

    def consolidate(summary, supersedes=None):
        args = argparse.Namespace(
            workspace=WORKSPACE, scope=SCOPE, kind="decision", summary=summary,
            detail="", tag=[], source=[str(source_file)],
            supersedes=supersedes or [], conflicts_with=[],
        )
        weilan_trace.command_memory_consolidate(args)
        return json.loads(capsys.readouterr().out)

    first = consolidate("first durable decision")
    # the second command must observe the first entry (supersedes validation)
    second = consolidate("second decision replacing the first", [first["memory_id"]])
    assert second["saved"] is True
    assert second["active_entry_count"] == 1


def test_memoized_source_snapshot_is_mutation_safe(state_home, tmp_path):
    source_file = tmp_path / "snap.md"
    source_file.write_text("content\n", encoding="utf-8")
    ref = str(source_file)
    with weilan_trace.derivation_memo_scope():
        first = weilan_trace.source_snapshot(ref, WORKSPACE)
        first["injected"] = True
        second = weilan_trace.source_snapshot(ref, WORKSPACE)
        assert "injected" not in second
        assert second["exists"] is True


def test_tiered_lookup_hint_and_global_fallback(state_home):
    record = write_evidence_record(7)
    hinted = weilan_trace.find_evidence("ev-0007", workspace=WORKSPACE, scope=SCOPE)
    assert hinted["evidence_id"] == record["evidence_id"]
    # wrong hints must still resolve through the global tier
    fallback = weilan_trace.find_evidence(
        "ev-0007", workspace=r"D:\SomeOtherWorkspace", scope="unrelated"
    )
    assert fallback["evidence_id"] == record["evidence_id"]
    with pytest.raises(FileNotFoundError):
        weilan_trace.find_evidence("ev-missing", workspace=WORKSPACE, scope=SCOPE)


def test_lookup_memo_keeps_hint_semantics_separate(state_home):
    write_evidence_record(1, workspace=WORKSPACE, evidence_id="ev-duplicate")
    write_evidence_record(2, workspace=OTHER_WORKSPACE, evidence_id="ev-duplicate")
    write_semantic_record("mem-duplicate", workspace=WORKSPACE)
    write_semantic_record("mem-duplicate", workspace=OTHER_WORKSPACE)

    with weilan_trace.derivation_memo_scope():
        assert weilan_trace.find_evidence(
            "ev-duplicate", workspace=WORKSPACE, scope=SCOPE
        )["workspace"] == runtime_core.canonical_workspace(WORKSPACE)
        with pytest.raises(RuntimeError, match="multiple evidence"):
            weilan_trace.find_evidence("ev-duplicate")
        assert weilan_trace.find_semantic_memory(
            "mem-duplicate", workspace=WORKSPACE, scope=SCOPE
        )["workspace"] == runtime_core.canonical_workspace(WORKSPACE)
        with pytest.raises(RuntimeError, match="multiple semantic"):
            weilan_trace.find_semantic_memory("mem-duplicate")

    with weilan_trace.derivation_memo_scope():
        with pytest.raises(RuntimeError, match="multiple evidence"):
            weilan_trace.find_evidence("ev-duplicate")
        assert weilan_trace.find_evidence(
            "ev-duplicate", workspace=WORKSPACE, scope=SCOPE
        )["workspace"] == runtime_core.canonical_workspace(WORKSPACE)
        with pytest.raises(RuntimeError, match="multiple semantic"):
            weilan_trace.find_semantic_memory("mem-duplicate")
        assert weilan_trace.find_semantic_memory(
            "mem-duplicate", workspace=WORKSPACE, scope=SCOPE
        )["workspace"] == runtime_core.canonical_workspace(WORKSPACE)


def test_foreign_corrupt_frame_does_not_break_scoped_indexing(state_home):
    good = weilan_trace.make_event(
        "frame-good", "frame_opened", "L2", WORKSPACE, {"causal": {"scope": SCOPE}}
    )
    good_path = runtime_core.state_root() / "frames" / "2026-01-01" / "frame-good.jsonl"
    weilan_trace.append_event(good_path, good)

    foreign = weilan_trace.make_event(
        "frame-foreign", "frame_opened", "L2", OTHER_WORKSPACE, {"causal": {"scope": SCOPE}}
    )
    bad_path = runtime_core.state_root() / "frames" / "2026-01-01" / "frame-foreign.jsonl"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text(
        json.dumps(foreign, ensure_ascii=False) + "\nnot json\n",
        encoding="utf-8",
    )

    paths = weilan_trace.scoped_frame_paths(WORKSPACE, SCOPE)
    assert paths == [good_path]
    index = weilan_trace.build_episode_index_value(WORKSPACE, SCOPE)
    assert index["episode_count"] == 1


def test_current_scope_corrupt_frame_still_breaks_scoped_indexing(state_home):
    good = weilan_trace.make_event(
        "frame-good", "frame_opened", "L2", WORKSPACE, {"causal": {"scope": SCOPE}}
    )
    good_path = runtime_core.state_root() / "frames" / "2026-01-01" / "frame-good.jsonl"
    weilan_trace.append_event(good_path, good)

    bad = weilan_trace.make_event(
        "frame-bad", "frame_opened", "L2", WORKSPACE, {"causal": {"scope": SCOPE}}
    )
    bad_path = runtime_core.state_root() / "frames" / "2026-01-01" / "frame-bad.jsonl"
    bad_path.write_text(
        json.dumps(bad, ensure_ascii=False) + "\nnot json\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        weilan_trace.scoped_frame_paths(WORKSPACE, SCOPE)
    with pytest.raises(ValueError, match="invalid JSON"):
        weilan_trace.build_episode_index_value(WORKSPACE, SCOPE)


def test_evidence_show_bounds_output(state_home, capsys):
    for index in range(1, 26):
        write_evidence_record(index)

    def show(limit):
        args = argparse.Namespace(
            workspace=WORKSPACE, scope=SCOPE, evidence_id=None, limit=limit
        )
        weilan_trace.command_evidence_show(args)
        return json.loads(capsys.readouterr().out)

    default = show(20)
    assert default["evidence_count"] == 25
    assert default["returned_count"] == 20
    assert default["truncated"] is True
    # newest-first slice keeps the latest records
    returned_ids = {item["evidence_id"] for item in default["results"]}
    assert "ev-0025" in returned_ids and "ev-0001" not in returned_ids

    unlimited = show(0)
    assert unlimited["returned_count"] == 25
    assert unlimited["truncated"] is False


def test_prospective_show_bounds_causal_events(state_home, monkeypatch, capsys):
    monkeypatch.setattr(weilan_trace, "assert_scope_write_allowed", lambda w, s, o: None)
    for index in range(1, 26):
        weilan_trace.append_prospective_event(
            WORKSPACE, SCOPE, "causal_event_observed",
            {
                "causal_event_id": f"ce-{index:04d}",
                "event_kind": "tool",
                "event_name": f"tests_green_{index}",
                "observed_at_utc": f"2026-01-01T00:{index:02d}:00+00:00",
                "payload_hash": None,
                "sources": ["frame:none"],
                "source_snapshots": [{"ref": "frame:none", "exists": True}],
            },
        )
    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE, limit=20)
    weilan_trace.command_prospective_show(args)
    output = json.loads(capsys.readouterr().out)
    assert output["causal_event_count"] == 25
    assert len(output["causal_events"]) == 20
    assert output["causal_events_truncated"] is True
    assert "ce-0025" in output["causal_events"]


def test_transaction_show_limit_uses_reducer_order_not_id_sort(state_home, monkeypatch, capsys):
    old = {
        "transaction_id": "wtx-z-old",
        "idempotency_key": "old",
        "state": "PREPARED",
        "contract_hash": "c",
        "proposal_hash": "p",
        "request_hash": None,
        "plan_hash": None,
        "bundle_hash": "b",
        "intents": [],
        "receipt": None,
    }
    newest = {
        **old,
        "transaction_id": "wtx-a-new",
        "idempotency_key": "new",
    }
    state = {
        "issues": [],
        "head_event_id": "head",
        "head_sequence": 2,
        "transactions": {
            old["transaction_id"]: old,
            newest["transaction_id"]: newest,
        },
    }
    monkeypatch.setattr(weilan_trace, "load_transaction_records", lambda *args: [])
    monkeypatch.setattr(weilan_trace, "reduce_transactions", lambda records: state)

    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE, transaction_id=None, limit=1)
    weilan_trace.command_metabolic_transaction_show(args)
    output = json.loads(capsys.readouterr().out)
    assert output["transaction_count"] == 2
    assert output["returned_count"] == 1
    assert output["truncated"] is True
    assert output["transactions"][0]["transaction_id"] == "wtx-a-new"


def main():
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
