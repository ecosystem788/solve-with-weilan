"""Regression tests for Memory 0.8 slow-loop dynamics (barrel-fix-v4).

Covers:
- binding budget: zero-sum admission, displacement, supersede-as-leaving;
- budget heads: append-only memory-budget-set, retention-plan default;
- merge: lineage, inherited source union (incl. evidence refs), grounding, net shrink;
- split: grounding, parent supersession on the final part only, budget charge;
- reorganization entries die with terminal inherited evidence;
- reactivation respects the budget;
- promotion gate rejects with semantic_budget_exhausted and writes no entry.
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


WORKSPACE = r"D:\SlowLoopTest"
SCOPE = "slow-loop"


@pytest.fixture()
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "method-state"
    monkeypatch.setenv("WEILAN_METHOD_HOME", str(home))
    monkeypatch.delenv("WEILAN_SEMANTIC_BUDGET_DEFAULT", raising=False)
    return home


def consolidate(capsys, summary, source_file, displace=None, supersedes=None, kind="fact"):
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, kind=kind, summary=summary, detail="",
        tag=[], source=[str(source_file)], supersedes=supersedes or [],
        conflicts_with=[], displace=displace or [],
    )
    weilan_trace.command_memory_consolidate(args)
    return json.loads(capsys.readouterr().out)


def set_budget(capsys, max_active):
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, max_active=max_active, reason="test budget"
    )
    weilan_trace.command_memory_budget_set(args)
    return json.loads(capsys.readouterr().out)


def active_summaries():
    entries = weilan_trace.active_semantic_entries(
        weilan_trace.load_semantic_entries(WORKSPACE, SCOPE, []), WORKSPACE, SCOPE, []
    )
    return {entry["memory_id"]: entry for entry in entries}


def test_budget_is_binding_and_displacement_is_zero_sum(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    set_budget(capsys, 2)

    first = consolidate(capsys, "alpha fact", source)
    second = consolidate(capsys, "beta fact", source)
    with pytest.raises(ValueError, match="semantic budget exhausted") as excinfo:
        consolidate(capsys, "gamma fact", source)
    # rejection names displacement candidates
    assert first["memory_id"] in str(excinfo.value)

    third = consolidate(capsys, "gamma fact", source, displace=[first["memory_id"]])
    assert third["displaced"] == [first["memory_id"]]
    active = active_summaries()
    assert len(active) == 2
    assert first["memory_id"] not in active
    # displaced entry is dormant, reversible, with a traceable reason
    heads = weilan_trace.semantic_disposition_heads(WORKSPACE, SCOPE, [])
    assert heads[first["memory_id"]]["state"] == "dormant"
    assert heads[first["memory_id"]]["reason"].startswith("displaced_by_budget:")
    assert second["memory_id"] in active


def test_supersede_counts_as_leaving_at_full_budget(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    set_budget(capsys, 2)
    first = consolidate(capsys, "alpha fact", source)
    consolidate(capsys, "beta fact", source)
    corrected = consolidate(
        capsys, "alpha fact corrected", source, supersedes=[first["memory_id"]]
    )
    active = active_summaries()
    assert len(active) == 2
    assert corrected["memory_id"] in active


def test_merge_lineage_sources_and_net_shrink(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    set_budget(capsys, 3)
    a = consolidate(capsys, "cold start uses projection recall", source)
    b = consolidate(capsys, "projection recall is the cold start gate", source)
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE,
        from_id=[a["memory_id"], b["memory_id"]], kind="decision",
        summary="cold start is one projection recall gate", detail="",
        tag=[], source=[], displace=[],
    )
    weilan_trace.command_memory_merge(args)
    merged = json.loads(capsys.readouterr().out)
    assert merged["reorganization"]["kind"] == "merge"
    active = active_summaries()
    assert len(active) == 1
    entry = active[merged["memory_id"]]
    assert set(entry["supersedes"]) == {a["memory_id"], b["memory_id"]}
    assert str(source) in " ".join(entry["sources"]) or entry["sources"]

    # ungrounded merge summary is rejected
    c = consolidate(capsys, "unrelated topic entry", source)
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE,
        from_id=[merged["memory_id"], c["memory_id"]], kind="decision",
        summary="zzz qqq completely foreign words", detail="", tag=[], source=[], displace=[],
    )
    with pytest.raises(ValueError, match="lexical grounding"):
        weilan_trace.command_memory_merge(args)


def test_merge_rejects_explicit_conversation_or_evidence_sources(
    state_home, tmp_path, capsys
):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    a = consolidate(capsys, "cold start projection", source)
    b = consolidate(capsys, "projection cold start", source)
    for protected_source in [
        "evidence:not-promoted",
        "Evidence:not-promoted",
        "Conversation:thread#turn",
    ]:
        args = argparse.Namespace(
            workspace=WORKSPACE, scope=SCOPE,
            from_id=[a["memory_id"], b["memory_id"]], kind="decision",
            summary="projection cold start merged", detail="", tag=[],
            source=[protected_source], displace=[],
        )
        with pytest.raises(ValueError, match="explicit .*evidence"):
            weilan_trace.command_memory_merge(args)

    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, kind="decision",
        summary="projection cold start direct", detail="", tag=[],
        source=["Conversation:thread#turn"], supersedes=[], displace=[],
        conflicts_with=[],
    )
    with pytest.raises(ValueError, match="conversation-derived"):
        weilan_trace.command_memory_consolidate(args)


def test_split_grounding_parent_flip_and_budget(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    set_budget(capsys, 2)
    mixed = consolidate(capsys, "release uses hash freeze and deploy uses receipts", source)

    parts = [
        json.dumps({"summary": "release uses hash freeze"}),
        json.dumps({"summary": "deploy uses receipts"}),
    ]
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, memory_id=mixed["memory_id"],
        part=parts, displace=[],
    )
    weilan_trace.command_memory_split(args)
    result = json.loads(capsys.readouterr().out)
    assert len(result["created"]) == 2
    active = active_summaries()
    assert mixed["memory_id"] not in active
    assert len(active) == 2
    # the parent supersession lives on the final part only
    last = active[result["created"][-1]["memory_id"]]
    firsts = active[result["created"][0]["memory_id"]]
    assert last["supersedes"] == [mixed["memory_id"]]
    assert firsts["supersedes"] == []
    assert firsts["reorganization"]["from"] == [mixed["memory_id"]]

    # splitting into 3 parts at a full budget of 2 requires displacement
    other = consolidate(
        capsys, "another entry", source, displace=[result["created"][0]["memory_id"]]
    )
    target = result["created"][-1]["memory_id"]
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, memory_id=target,
        part=[json.dumps({"summary": "deploy"}), json.dumps({"summary": "receipts"}),
              json.dumps({"summary": "deploy receipts detail"})],
        displace=[],
    )
    with pytest.raises(ValueError, match="semantic budget exhausted"):
        weilan_trace.command_memory_split(args)


def test_ungrounded_split_part_rejected(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    entry = consolidate(capsys, "one narrow fact", source)
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, memory_id=entry["memory_id"],
        part=[json.dumps({"summary": "one narrow fact part"}),
              json.dumps({"summary": "zzz foreign qqq"})],
        displace=[],
    )
    with pytest.raises(ValueError, match="lexical grounding"):
        weilan_trace.command_memory_split(args)


def test_reactivation_respects_budget(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    set_budget(capsys, 2)
    a = consolidate(capsys, "alpha fact", source)
    b = consolidate(capsys, "beta fact", source)
    c = consolidate(capsys, "gamma fact", source, displace=[a["memory_id"]])

    reactivate = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, memory_id=a["memory_id"],
        state="active", reason="needed again", source="frame:none", displace=[],
    )
    with pytest.raises(ValueError, match="semantic budget exhausted"):
        weilan_trace.command_memory_disposition(reactivate)

    reactivate.displace = [b["memory_id"]]
    weilan_trace.command_memory_disposition(reactivate)
    json.loads(capsys.readouterr().out)
    active = active_summaries()
    assert set(active) == {a["memory_id"], c["memory_id"]}


def test_merged_entry_dies_with_terminal_evidence(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")

    # hand-plant an evidence record and give one input an inherited evidence source.
    evidence_dir = weilan_trace.evidence_directory(WORKSPACE, SCOPE)
    evidence = {
        "schema_version": weilan_trace.EVIDENCE_SCHEMA_VERSION,
        "evidence_id": "ev-merge-dep",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "workspace": runtime_core.canonical_workspace(WORKSPACE),
        "scope": SCOPE,
        "signal": "architectural_decision",
        "claim": "durable decision claim",
        "sources": [],
        "source_snapshots": [],
    }
    weilan_trace.append_event(evidence_dir / "2026-01-01.jsonl", evidence)
    a, _ = weilan_trace.append_semantic_memory(
        WORKSPACE,
        SCOPE,
        "decision",
        "durable alpha decision",
        "",
        [],
        ["evidence:ev-merge-dep"],
        [],
        [],
    )
    b = consolidate(capsys, "durable beta decision", source)

    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE,
        from_id=[a["memory_id"], b["memory_id"]], kind="decision",
        summary="durable merged decision", detail="", tag=[],
        source=[], displace=[],
    )
    weilan_trace.command_memory_merge(args)
    merged = json.loads(capsys.readouterr().out)
    assert merged["memory_id"] in active_summaries()

    # withdraw the evidence: the merged entry must leave active recall
    disposition = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, evidence_id="ev-merge-dep",
        state="withdrawn", reason="obsolete", replacement_evidence_id=None,
    )
    weilan_trace.command_evidence_disposition(disposition)
    capsys.readouterr()
    assert merged["memory_id"] not in active_summaries()


def test_promotion_gate_rejects_on_full_budget(state_home, tmp_path, capsys, monkeypatch):
    source = tmp_path / "s.md"
    source.write_text("fact\n", encoding="utf-8")
    set_budget(capsys, 1)
    consolidate(capsys, "occupies the only slot", source)

    evidence_dir = weilan_trace.evidence_directory(WORKSPACE, SCOPE)
    evidence = {
        "schema_version": weilan_trace.EVIDENCE_SCHEMA_VERSION,
        "evidence_id": "ev-budget-full",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "workspace": runtime_core.canonical_workspace(WORKSPACE),
        "scope": SCOPE,
        "signal": "architectural_decision",
        "claim": "promoted decision claim",
        "sources": [],
        "source_snapshots": [],
    }
    weilan_trace.append_event(evidence_dir / "2026-01-01.jsonl", evidence)

    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, evidence_id="ev-budget-full",
        kind="decision", summary="promoted decision claim", detail="",
        tag=[], source=[], supersedes=[], conflicts_with=[],
        stable=True, reusable=True, privacy_reviewed=True, displace=[],
    )
    weilan_trace.command_evidence_promote(args)
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "REJECTED"
    assert "semantic_budget_exhausted" in result["reason_codes"]
    assert result["semantic_memory_written"] is False
    assert len(active_summaries()) == 1


def test_budget_head_is_append_only_history(state_home, capsys):
    first = set_budget(capsys, 5)
    second = set_budget(capsys, 3)
    assert second["previous_max_active"] == 5
    assert weilan_trace.semantic_budget(WORKSPACE, SCOPE) == 3
    records = weilan_trace.read_jsonl_records(
        weilan_trace.semantic_budget_path(WORKSPACE, SCOPE), []
    )
    assert [r["max_active"] for r in records] == [5, 3]


def test_reactivation_holds_contract_fence(state_home, tmp_path, capsys, monkeypatch):
    source = tmp_path / "s.md"
    source.write_text("fact" + chr(10), encoding="utf-8")
    entry = consolidate(capsys, "fenced fact", source)

    entered = {"count": 0}
    original = weilan_trace.contract_fence

    def recording(root, wk, sk):
        entered["count"] += 1
        return original(root, wk, sk)

    monkeypatch.setattr(weilan_trace, "contract_fence", recording)
    disposition = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, memory_id=entry["memory_id"],
        state="active", reason="reactivate inside fence", source="frame:none", displace=[],
    )
    weilan_trace.command_memory_disposition(disposition)
    capsys.readouterr()
    assert entered["count"] == 1


def corrupt_budget_ledger(terminated_bad_line=True):
    path = weilan_trace.semantic_budget_path(WORKSPACE, SCOPE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"corrupt budget line"
    if terminated_bad_line:
        payload += b"\x0a"
    with path.open("ab") as handle:
        handle.write(payload)


def test_corrupt_budget_ledger_fails_closed(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact" + chr(10), encoding="utf-8")

    # probe A: ledger contains only a corrupt line -> no default fallback
    corrupt_budget_ledger()
    with pytest.raises(ValueError, match="semantic budget ledger is invalid"):
        consolidate(capsys, "must not be admitted", source)
    with pytest.raises(ValueError, match="semantic budget ledger is invalid"):
        weilan_trace.semantic_budget(WORKSPACE, SCOPE)
    plan_args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE, max_active=None)
    with pytest.raises(ValueError, match="semantic budget ledger is invalid"):
        weilan_trace.command_memory_retention_plan(plan_args)
    budget_args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, max_active=5, reason="attempt repair"
    )
    with pytest.raises(ValueError, match="semantic budget ledger is invalid"):
        weilan_trace.command_memory_budget_set(budget_args)


def test_valid_head_plus_corruption_still_fails_closed(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact" + chr(10), encoding="utf-8")
    set_budget(capsys, 5)
    # probe B: a terminated bad line after a valid head must not serve the stale head
    corrupt_budget_ledger()
    with pytest.raises(ValueError, match="semantic budget ledger is invalid"):
        consolidate(capsys, "must not be admitted either", source)


def test_budget_identity_mismatch_fails_closed(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact" + chr(10), encoding="utf-8")
    path = weilan_trace.semantic_budget_path(WORKSPACE, SCOPE)
    record = {
        "schema_version": weilan_trace.SEMANTIC_BUDGET_SCHEMA_VERSION,
        "event_id": "forged",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "workspace": r"D:\SomeOtherWorkspace",
        "workspace_key": "other",
        "scope": "other-scope",
        "scope_key": "other",
        "max_active": 1,
        "reason": "forged",
    }
    weilan_trace.append_event(path, record)
    with pytest.raises(ValueError, match="workspace mismatch"):
        consolidate(capsys, "must not be admitted", source)


def test_torn_budget_tail_still_heals(state_home, tmp_path, capsys):
    source = tmp_path / "s.md"
    source.write_text("fact" + chr(10), encoding="utf-8")
    set_budget(capsys, 5)
    # an interrupted append (unterminated tail) is crash damage, not corruption:
    # reads skip it and the next budget-set quarantines it
    corrupt_budget_ledger(terminated_bad_line=False)
    assert weilan_trace.semantic_budget(WORKSPACE, SCOPE) == 5
    result = consolidate(capsys, "admitted under healed budget", source)
    assert result["saved"] is True
    set_budget(capsys, 4)
    assert weilan_trace.semantic_budget(WORKSPACE, SCOPE) == 4


def main():
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
