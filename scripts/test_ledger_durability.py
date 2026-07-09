"""Regression tests for ledger durability short-stave fixes (barrel-fix-v1).

Covers:
- torn-tail quarantine and self-healing appends (frames, transaction journal);
- strictness preserved for real mid-file corruption;
- corrupt transaction journal no longer bricks frame reads (blast radius);
- governance validate-before-append (no invalid replay is persisted);
- transaction/runner ledger identity is case-insensitive on raw scope text;
- evidence-promote, memory-consolidate, and transaction staging hold the
  workspace/scope contract fence.
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_core
import transaction
import weilan_trace


WORKSPACE = r"D:\LedgerDurabilityTest"
SCOPE = "durability"


@pytest.fixture()
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "method-state"
    monkeypatch.setenv("WEILAN_METHOD_HOME", str(home))
    return home


def frame_path(frame_id):
    return runtime_core.state_root() / "frames" / "2026-01-01" / f"{frame_id}.jsonl"


def write_frame_event(path, frame_id, event_type, data):
    event = weilan_trace.make_event(frame_id, event_type, "L2", WORKSPACE, data)
    weilan_trace.append_event(path, event)
    return event


def test_frame_torn_tail_is_quarantined_and_reads_survive(state_home):
    frame_id = "frame-torn-tail"
    path = frame_path(frame_id)
    first = write_frame_event(path, frame_id, "frame_opened", {"problem": "torn"})

    # simulate a crash mid-append: unterminated fragment with a split
    # multi-byte UTF-8 character at the end
    with path.open("ab") as handle:
        handle.write(b'{"partial": "\xe5')

    # crash-window read: torn final line is skipped, real events survive
    events = weilan_trace.read_events(path)
    assert [e["event_id"] for e in events] == [first["event_id"]]

    # the next append quarantines the fragment and stays parseable
    second = write_frame_event(path, frame_id, "candidate_admitted", {"candidate_id": "c1"})
    events = weilan_trace.read_events(path)
    assert [e["event_id"] for e in events] == [first["event_id"], second["event_id"]]

    sidecar = path.with_name(path.name + ".torn")
    assert sidecar.exists()
    quarantined = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    assert quarantined[0]["reason"] == "torn_tail_from_interrupted_append"
    assert quarantined[0]["fragment_preview"].startswith('{"partial')
    assert quarantined[0]["fragment_length"] == len(b'{"partial": "\xe5')
    assert isinstance(quarantined[0]["fragment_offset"], int)

    # the quarantine sidecar must not shadow the frame ledger glob
    assert weilan_trace.find_frame(frame_id) == path


def test_mid_file_corruption_still_raises(state_home):
    frame_id = "frame-mid-corruption"
    path = frame_path(frame_id)
    write_frame_event(path, frame_id, "frame_opened", {"problem": "strict"})
    with path.open("ab") as handle:
        handle.write(b"garbage{not json}\n")
    with path.open("ab") as handle:
        handle.write(
            (json.dumps(weilan_trace.make_event(frame_id, "note", "L2", WORKSPACE, {}),
                        ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        )
    with pytest.raises(ValueError):
        weilan_trace.read_events(path)


def test_transaction_journal_torn_tail_self_heals(state_home, tmp_path):
    journal = tmp_path / "journal" / "2026-01-01.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b'{"broken')

    # crash-window read tolerates the unterminated tail
    assert transaction.raw_read_jsonl(journal) == []

    record = {"schema_version": "test", "value": 1}
    transaction.append_jsonl(journal, record)
    assert transaction.raw_read_jsonl(journal) == [record]

    sidecar = journal.with_name(journal.name + ".torn")
    assert sidecar.exists()
    fragment = json.loads(sidecar.read_text(encoding="utf-8").splitlines()[0])
    assert fragment["fragment_preview"] == '{"broken'
    assert fragment["fragment_offset"] == 0
    assert fragment["fragment_length"] == len(b'{"broken')


def test_terminated_invalid_utf8_still_raises(state_home, tmp_path):
    """A newline-terminated line with invalid UTF-8 is corruption, not data."""

    journal = tmp_path / "journal-utf8" / "2026-01-01.jsonl"
    journal.parent.mkdir(parents=True)
    good = {"schema_version": "test", "value": 1}
    transaction.append_jsonl(journal, good)
    with journal.open("ab") as handle:
        handle.write(b'{"x":"\xff"}\n')
    with pytest.raises(ValueError):
        transaction.raw_read_jsonl(journal)

    frame_id = "frame-invalid-utf8"
    path = frame_path(frame_id)
    write_frame_event(path, frame_id, "frame_opened", {"problem": "utf8"})
    with path.open("ab") as handle:
        handle.write(b'{"x":"\xff"}\n')
    with pytest.raises(ValueError):
        weilan_trace.read_events(path)


def test_duplicate_fragment_text_is_not_false_quarantined(state_home, tmp_path):
    """Quarantine is byte-range identity; identical later text stays a hard error."""

    journal = tmp_path / "journal-dup" / "2026-01-01.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b"BAD")

    record = {"schema_version": "test", "value": 1}
    transaction.append_jsonl(journal, record)  # quarantines fragment "BAD" at offset 0
    assert transaction.raw_read_jsonl(journal) == [record]

    # an unrelated, terminated bad line with the same text later in the ledger
    with journal.open("ab") as handle:
        handle.write(b"BAD\n")
    transaction.append_jsonl(journal, {"schema_version": "test", "value": 2})
    with pytest.raises(ValueError):
        transaction.raw_read_jsonl(journal)


def test_corrupt_transaction_journal_does_not_brick_frame_reads(state_home):
    frame_id = "frame-blast-radius"
    path = frame_path(frame_id)
    first = write_frame_event(path, frame_id, "frame_opened", {"problem": "radius"})

    wk = runtime_core.workspace_key(WORKSPACE)
    sk = runtime_core.scope_key(SCOPE)

    # a pending envelope in the frame ledger points at this journal
    envelope = {
        "schema_version": transaction.PENDING_SCHEMA_VERSION,
        "workspace_key": wk,
        "scope_key": sk,
        "transaction_id": "tx-missing",
        "intent_id": "i0001",
        "bundle_hash": "x",
        "participant": "frame",
        "payload_hash": "x",
        "payload": {"ignored": True},
    }
    with path.open("ab") as handle:
        handle.write((json.dumps(envelope, sort_keys=True) + "\n").encode("utf-8"))

    journal_dir = (
        runtime_core.state_root() / "memory" / "transactions" / "workspaces" / wk / sk
    )
    journal_dir.mkdir(parents=True)
    (journal_dir / "2026-01-01.jsonl").write_bytes(b"corrupt journal line\n")

    # previously: ValueError("invalid pending transaction envelope: ...") for
    # every frame read in the workspace; now the envelope stays hidden
    events = weilan_trace.read_events(path)
    assert [e["event_id"] for e in events] == [first["event_id"]]


def governance_register_data(ref="goal:barrel"):
    return {
        "target_ref": ref,
        "target_kind": "goal",
        "scale": "task",
        "source_refs": ["file:durability-test"],
        "source_snapshots": [{"ref": "file:durability-test", "exists": True}],
    }


def test_governance_invalid_replay_rejected_before_append(state_home, monkeypatch):
    monkeypatch.setattr(weilan_trace, "assert_governance_write_allowed", lambda w, s: None)

    _, _, path = weilan_trace.append_governance_event(
        WORKSPACE, SCOPE, "target_registered", governance_register_data()
    )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    # a second identical registration is an invalid replay (duplicate target)
    with pytest.raises(RuntimeError, match="invalid replay"):
        weilan_trace.append_governance_event(
            WORKSPACE, SCOPE, "target_registered", governance_register_data()
        )

    # the losing event must NOT be durably appended, and the ledger must replay clean
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    _, state = weilan_trace.reduce_governance(WORKSPACE, SCOPE)
    assert state["issues"] == []
    assert set(state["targets"]) == {"goal:barrel"}


def prepare_kwargs(root, scope_text):
    return dict(
        workspace=WORKSPACE,
        scope=scope_text,
        workspace_key=runtime_core.workspace_key(WORKSPACE),
        scope_key=runtime_core.scope_key(scope_text),
        idempotency_key="case-test-1",
        contract_hash="contract-1",
        proposal={"proposal_hash": "proposal-1"},
        intents=[
            {
                "participant": "frame",
                "target_relpath": "frames/2026-01-01/case-test.jsonl",
                "payload": {"kind": "test"},
            }
        ],
    )


def test_transaction_identity_ignores_scope_case(state_home, tmp_path):
    root = tmp_path / "root"
    prepared, state = transaction.prepare_transaction(root, **prepare_kwargs(root, "Memory-System"))
    assert state["issues"] == []

    # same scope typed with different casing maps to the same journal;
    # previously this permanently wedged it with "identity changed"
    aborted, state = transaction.abort_transaction(
        root,
        workspace=WORKSPACE,
        scope="memory-system",
        workspace_key=runtime_core.workspace_key(WORKSPACE),
        scope_key=runtime_core.scope_key("memory-system"),
        transaction_id=prepared["transaction_id"],
        reason="case-insensitive identity regression test",
    )
    assert state["issues"] == []
    assert aborted["state"] == "ABORTED"


def fence_lock_paths(root, wk, sk):
    fences = Path(root) / "memory" / "metabolism" / "fences" / "workspaces" / wk
    return fences / ".workspace-contract.lock", fences / "scopes" / sk / ".scope-contract.lock"


def test_transaction_staging_holds_contract_fence(state_home, tmp_path):
    root = tmp_path / "root"
    transaction.prepare_transaction(root, **prepare_kwargs(root, "fence-check"))
    workspace_lock, scope_lock = fence_lock_paths(
        root, runtime_core.workspace_key(WORKSPACE), runtime_core.scope_key("fence-check")
    )
    assert workspace_lock.exists()
    assert scope_lock.exists()


def test_evidence_promote_holds_contract_fence(state_home):
    args = argparse.Namespace(
        workspace=WORKSPACE,
        scope=SCOPE,
        evidence_id="missing-evidence",
        kind="decision",
        summary="s",
        detail="",
        tag=[],
        source=[],
        supersedes=[],
        conflicts_with=[],
        stable=True,
        reusable=True,
        privacy_reviewed=True,
    )
    with pytest.raises(ValueError, match="expected one scoped evidence record"):
        weilan_trace.command_evidence_promote(args)
    workspace_lock, scope_lock = fence_lock_paths(
        runtime_core.state_root(),
        runtime_core.workspace_key(WORKSPACE),
        runtime_core.scope_key(SCOPE),
    )
    assert workspace_lock.exists()
    assert scope_lock.exists()


def test_memory_consolidate_holds_contract_fence(state_home, tmp_path, capsys):
    source_file = tmp_path / "source.md"
    source_file.write_text("durable fact\n", encoding="utf-8")
    args = argparse.Namespace(
        workspace=WORKSPACE,
        scope=SCOPE,
        kind="decision",
        summary="barrel-fix consolidation fence regression",
        detail="",
        tag=[],
        source=[str(source_file)],
        supersedes=[],
        conflicts_with=[],
    )
    weilan_trace.command_memory_consolidate(args)
    result = json.loads(capsys.readouterr().out)
    assert result["saved"] is True
    workspace_lock, scope_lock = fence_lock_paths(
        runtime_core.state_root(),
        runtime_core.workspace_key(WORKSPACE),
        runtime_core.scope_key(SCOPE),
    )
    assert workspace_lock.exists()
    assert scope_lock.exists()


def main():
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
