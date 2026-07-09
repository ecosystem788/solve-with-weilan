"""Regression and fault-injection tests for Memory 0.7b transactions."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from transaction import (
    commit_transaction,
    contract_fence,
    load_transaction_records,
    normalize_intents,
    prepare_transaction,
    recover_transaction,
    reduce_transactions,
    resolve_pending_record,
    stable_hash,
    transaction_visibility_scope,
)


SCRIPT = Path(__file__).with_name("weilan_trace.py")
SCOPE = "memory-system"


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


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def activate(workspace, environment, directive):
    return run(
        [
            "memory-control",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--state",
            "active",
            "--directive",
            directive,
        ],
        environment,
    )


def setup_ready_fixture(root, method_state, environment, name):
    workspace_path = root / name
    workspace_path.mkdir()
    workspace = str(workspace_path)
    activate(workspace, environment, f"activate {name}")
    frame_id = run(
        [
            "open",
            "--level",
            "L2",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--branch",
            "main",
            "--relation",
            "root",
            "--problem",
            "0.7b fixture",
            "--success",
            "fixture ready",
        ],
        environment,
    )["frame_id"]
    run(
        [
            "persistence-audit",
            "--frame-id",
            frame_id,
            "--trigger",
            "round_end",
            "--decision",
            "not_persisted",
            "--reason",
            "temporary 0.7b fixture",
        ],
        environment,
    )
    run(
        [
            "close",
            "--frame-id",
            frame_id,
            "--outcome",
            "success",
            "--verdict",
            "fixture closed",
        ],
        environment,
    )
    source = workspace_path / "source.txt"
    source.write_text("stable transaction source", encoding="utf-8")
    run(
        [
            "governance-target-register",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--target-ref",
            "goal:task",
            "--target-kind",
            "goal",
            "--scale",
            "task",
            "--death-line",
            "verified invalidity",
            "--reentry-condition",
            "new evidence",
            "--source",
            str(source),
        ],
        environment,
    )
    baseline_evidence_id = run(
        [
            "evidence-capture",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--signal",
            "verified_result",
            "--claim",
            "A stable fixture result supports transaction testing",
            "--source",
            f"conversation:{name}#baseline",
        ],
        environment,
    )["evidence_id"]
    contract = run(
        ["metabolic-contract", "--workspace", workspace, "--scope", SCOPE],
        environment,
    )
    proposal = run(
        [
            "metabolic-propose",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--expected-contract-hash",
            contract["contract_hash"],
            "--trigger-kind",
            "frame_closed",
            "--trigger-ref",
            f"frame:{frame_id}",
            "--disposition",
            "continue",
            "--target-ref",
            "goal:task",
            "--branch",
            "main",
        ],
        environment,
    )
    return {
        "workspace": workspace,
        "frame_id": frame_id,
        "baseline_evidence_id": baseline_evidence_id,
        "contract": contract,
        "proposal": proposal,
        "method_state": method_state,
    }


def jsonl_records(root):
    records = []
    for path in root.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def record_for(records, workspace, schema, predicate=lambda value: True):
    matches = [
        value
        for value in records
        if value.get("workspace") == workspace
        and value.get("schema_version") == schema
        and predicate(value)
    ]
    if not matches:
        raise AssertionError(f"fixture record not found for {schema}")
    return matches[-1]


def successor_plan(fixture, suffix):
    records = jsonl_records(fixture["method_state"])
    workspace = fixture["workspace"]
    parent = fixture["frame_id"]
    now = utc_now()
    frame_id = f"wf-transaction-{suffix}-{uuid.uuid4().hex[:8]}"
    lineage_event_id = str(uuid.uuid4())

    parent_frame = record_for(
        records,
        workspace,
        "weilan_method_event_v0.1",
        lambda value: value.get("frame_id") == parent and value.get("event_type") == "frame_opened",
    )
    frame = copy.deepcopy(parent_frame)
    frame.update(
        {
            "event_id": str(uuid.uuid4()),
            "frame_id": frame_id,
            "timestamp_utc": now,
        }
    )
    frame["data"]["problem"] = "transaction-created successor"
    frame["data"]["success_criteria"] = "committed atomically"
    frame["data"]["causal"] = {
        "schema_version": "weilan_frame_lineage_v0.4",
        "lineage_event_id": lineage_event_id,
        "scope": SCOPE,
        "branch_id": "main",
        "relation": "continue",
        "parent_frame_ids": [parent],
        "joined_branch_ids": [],
    }

    lineage_base = record_for(
        records,
        workspace,
        "weilan_frame_lineage_v0.4",
        lambda value: value.get("frame_id") == parent and "branch_id" in value,
    )
    lineage = copy.deepcopy(lineage_base)
    lineage.update(
        {
            "event_id": lineage_event_id,
            "timestamp_utc": now,
            "frame_id": frame_id,
            "relation": "continue",
            "parent_frame_ids": [parent],
            "joined_branch_ids": [],
        }
    )

    governance_base = record_for(
        records,
        workspace,
        "weilan_governance_event_v0.6",
        lambda value: value.get("event_type") == "target_registered",
    )
    governance = copy.deepcopy(governance_base)
    governance.update(
        {
            "event_id": str(uuid.uuid4()),
            "timestamp_utc": now,
            "sequence": governance_base["sequence"] + 1,
        }
    )
    governance["data"]["target_ref"] = f"goal:transaction-{suffix}"
    governance["data"]["parent_target_ref"] = "goal:task"

    evidence_base = record_for(
        records,
        workspace,
        "weilan_conversation_evidence_v0.5",
        lambda value: value.get("evidence_id") == fixture["baseline_evidence_id"],
    )
    evidence = copy.deepcopy(evidence_base)
    claim = f"Transaction {suffix} preserves atomic cross-ledger visibility"
    evidence.update(
        {
            "evidence_id": str(uuid.uuid4()),
            "timestamp_utc": now,
            "claim": claim,
            "claim_hash": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            "sources": [f"conversation:transaction-{suffix}#verified"],
            "source_snapshots": [
                {
                    "kind": "conversation",
                    "ref": f"conversation:transaction-{suffix}#verified",
                }
            ],
        }
    )

    audit_base = record_for(
        records,
        workspace,
        "weilan_persistence_audit_v0.5.1",
        lambda value: value.get("frame_id") == parent,
    )
    audit = copy.deepcopy(audit_base)
    audit.update(
        {
            "audit_id": str(uuid.uuid4()),
            "timestamp_utc": now,
            "frame_id": frame_id,
            "trigger": "round_end",
            "decision": "NOT_PERSISTED",
            "evidence_id": None,
            "semantic_memory_id": None,
            "reason": "transaction fixture audit",
        }
    )

    return {
        "frame_id": frame_id,
        "evidence_id": evidence["evidence_id"],
        "target_ref": governance["data"]["target_ref"],
        "intents": [
            {"participant": "frame", "record": frame},
            {"participant": "lineage", "record": lineage},
            {"participant": "governance", "record": governance},
            {"participant": "evidence", "record": evidence},
            {"participant": "persistence_audit", "record": audit},
        ],
    }


def write_input_files(root, prefix, proposal, plan):
    proposal_path = root / f"{prefix}-proposal.json"
    plan_path = root / f"{prefix}-plan.json"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    plan_path.write_text(json.dumps({"intents": plan["intents"]}, ensure_ascii=False), encoding="utf-8")
    return proposal_path, plan_path


def cli_transaction_test(root, method_state, environment):
    fixture = setup_ready_fixture(root, method_state, environment, "commit-workspace")
    plan = successor_plan(fixture, "commit")
    proposal_path, plan_path = write_input_files(
        root, "commit", fixture["proposal"], plan
    )
    arguments = [
        "metabolic-prepare",
        "--workspace",
        fixture["workspace"],
        "--scope",
        SCOPE,
        "--idempotency-key",
        "commit-once",
        "--proposal-file",
        str(proposal_path),
        "--plan-file",
        str(plan_path),
    ]
    prepared = run(arguments, environment)
    if prepared["state"] != "PREPARED" or prepared["pending_events_visible"]:
        raise AssertionError("prepare did not remain invisible")
    journal_count = prepared["journal_head"]["sequence"]
    repeated_prepare = run(arguments, environment)
    if repeated_prepare["transaction_id"] != prepared["transaction_id"]:
        raise AssertionError("idempotency key created a second transaction")
    if repeated_prepare["journal_head"]["sequence"] != journal_count:
        raise AssertionError("repeated prepare appended duplicate journal events")

    hidden_frame = completed(["show", "--frame-id", plan["frame_id"]], environment)
    evidence_before = run(
        ["evidence-show", "--workspace", fixture["workspace"], "--scope", SCOPE],
        environment,
    )
    target_before = run(
        [
            "governance-show",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            plan["target_ref"],
        ],
        environment,
    )
    if hidden_frame.returncode == 0 or evidence_before["evidence_count"] != 1 or target_before["targets"]:
        raise AssertionError("pending participant event became visible before commit")

    committed = run(
        [
            "metabolic-commit",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--transaction-id",
            prepared["transaction_id"],
            "--expected-contract-hash",
            fixture["contract"]["contract_hash"],
        ],
        environment,
    )
    if committed["state"] != "COMMITTED" or not committed["receipt"]:
        raise AssertionError("commit did not produce a durable receipt")
    if not committed["pending_events_visible"] or committed["background_loop_started"]:
        raise AssertionError("commit visibility or background-loop boundary is wrong")

    shown_frame = run(["show", "--frame-id", plan["frame_id"]], environment)
    lineage = run(
        ["lineage-show", "--workspace", fixture["workspace"], "--scope", SCOPE],
        environment,
    )
    evidence_after = run(
        ["evidence-show", "--workspace", fixture["workspace"], "--scope", SCOPE],
        environment,
    )
    target_after = run(
        [
            "governance-show",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--target-ref",
            plan["target_ref"],
        ],
        environment,
    )
    audit_after = run(
        ["persistence-audit-show", "--frame-id", plan["frame_id"]], environment
    )
    if (
        len(shown_frame) != 1
        or lineage["branches"]["main"]["head_frame_id"] != plan["frame_id"]
        or evidence_after["evidence_count"] != 2
        or plan["target_ref"] not in target_after["targets"]
        or "round_end" not in audit_after["completed_triggers"]
    ):
        raise AssertionError("committed cross-ledger bundle was not atomically visible")

    repeat_commit = run(
        [
            "metabolic-commit",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--transaction-id",
            prepared["transaction_id"],
            "--expected-contract-hash",
            fixture["contract"]["contract_hash"],
        ],
        environment,
    )
    if repeat_commit["receipt"]["receipt_hash"] != committed["receipt"]["receipt_hash"]:
        raise AssertionError("repeated commit changed its receipt")
    repeat_after_commit = run(arguments, environment)
    if repeat_after_commit["receipt"]["receipt_hash"] != committed["receipt"]["receipt_hash"]:
        raise AssertionError("repeated prepare after commit was not idempotent")


def stale_and_abort_test(root, method_state, environment):
    fixture = setup_ready_fixture(root, method_state, environment, "abort-workspace")
    plan = successor_plan(fixture, "abort")
    proposal_path, plan_path = write_input_files(root, "abort", fixture["proposal"], plan)
    prepared = run(
        [
            "metabolic-prepare",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--idempotency-key",
            "abort-on-stale",
            "--proposal-file",
            str(proposal_path),
            "--plan-file",
            str(plan_path),
        ],
        environment,
    )
    run(
        [
            "memory-control",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--state",
            "paused",
            "--directive",
            "pause before transaction commit",
        ],
        environment,
    )
    paused = completed(
        [
            "metabolic-commit",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--transaction-id",
            prepared["transaction_id"],
            "--expected-contract-hash",
            fixture["contract"]["contract_hash"],
        ],
        environment,
    )
    if paused.returncode == 0 or "explicitly active scope" not in paused.stderr:
        raise AssertionError("paused scope permitted transaction commit")
    activate(fixture["workspace"], environment, "resume after paused commit check")
    stale = completed(
        [
            "metabolic-commit",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--transaction-id",
            prepared["transaction_id"],
            "--expected-contract-hash",
            fixture["contract"]["contract_hash"],
        ],
        environment,
    )
    if stale.returncode == 0 or "contract_head_changed" not in stale.stderr:
        raise AssertionError("stale contract committed")
    aborted = run(
        [
            "metabolic-abort",
            "--workspace",
            fixture["workspace"],
            "--scope",
            SCOPE,
            "--transaction-id",
            prepared["transaction_id"],
            "--reason",
            "contract head changed before commit",
        ],
        environment,
    )
    if aborted["state"] != "ABORTED" or not aborted["receipt"]:
        raise AssertionError("abort did not produce a receipt")
    if completed(["show", "--frame-id", plan["frame_id"]], environment).returncode == 0:
        raise AssertionError("aborted frame became visible")


def fault_recovery_test(root):
    state = root / "fault-state"
    workspace = str(root / "fault-workspace")
    scope = "fault-scope"
    wk = "workspace-key"
    sk = "scope-key"
    intents = normalize_intents(
        [
            {
                "participant": "fixture-a",
                "target_relpath": "participants/a.jsonl",
                "payload": {"event_id": "a", "value": 1},
            },
            {
                "participant": "fixture-b",
                "target_relpath": "participants/b.jsonl",
                "payload": {"event_id": "b", "value": 2},
            },
        ]
    )
    proposal = {"proposal_hash": "proposal-hash", "admissible": True}
    try:
        prepare_transaction(
            state,
            workspace=workspace,
            scope=scope,
            workspace_key=wk,
            scope_key=sk,
            idempotency_key="fault-once",
            contract_hash="contract-hash",
            proposal=proposal,
            intents=intents,
            fail_after_pending=1,
        )
        raise AssertionError("fault injection did not interrupt prepare")
    except RuntimeError as exc:
        if "injected_failure" not in str(exc):
            raise
    journal = reduce_transactions(load_transaction_records(state, wk, sk))
    transaction = next(iter(journal["transactions"].values()))
    if transaction["state"] != "PREPARING":
        raise AssertionError("prepare crash did not remain PREPARING")
    first_pending = json.loads((state / "participants" / "a.jsonl").read_text(encoding="utf-8"))
    if resolve_pending_record(state, first_pending, []) is not None:
        raise AssertionError("partially staged record became visible")

    recovered, _ = recover_transaction(
        state,
        workspace=workspace,
        scope=scope,
        workspace_key=wk,
        scope_key=sk,
        transaction_id=transaction["transaction_id"],
    )
    if recovered["state"] != "PREPARED":
        raise AssertionError("recovery did not finish staging")
    if resolve_pending_record(state, first_pending, []) is not None:
        raise AssertionError("recovery implicitly committed a transaction")
    with transaction_visibility_scope():
        if resolve_pending_record(state, first_pending, []) is not None:
            raise AssertionError("pre-commit snapshot unexpectedly saw pending data")
        try:
            commit_transaction(
                state,
                workspace=workspace,
                scope=scope,
                workspace_key=wk,
                scope_key=sk,
                transaction_id=transaction["transaction_id"],
                expected_contract_hash="contract-hash",
                current_contract_loader=lambda: "contract-hash",
                fail_before_receipt=True,
            )
            raise AssertionError("receipt fault injection did not interrupt commit")
        except RuntimeError as exc:
            if "injected_failure" not in str(exc):
                raise
        second_pending = json.loads((state / "participants" / "b.jsonl").read_text(encoding="utf-8"))
        if resolve_pending_record(state, second_pending, []) is not None:
            raise AssertionError("one read snapshot straddled the commit boundary")
    after_commit = reduce_transactions(load_transaction_records(state, wk, sk))
    transaction = after_commit["transactions"][transaction["transaction_id"]]
    if transaction["state"] != "COMMITTED" or transaction["receipt"] is not None:
        raise AssertionError("commit/receipt crash boundary was not preserved")
    if resolve_pending_record(state, first_pending, []) is None or resolve_pending_record(
        state, second_pending, []
    ) is None:
        raise AssertionError("committed records were not visible before receipt recovery")
    relocated = state / "participants" / "relocated.jsonl"
    relocated.write_text(json.dumps(first_pending), encoding="utf-8")
    relocation_warnings = []
    if resolve_pending_record(
        state, first_pending, relocation_warnings, source_path=relocated
    ) is not None or not relocation_warnings:
        raise AssertionError("relocated pending envelope bypassed target-path integrity")
    recovered, state_view = recover_transaction(
        state,
        workspace=workspace,
        scope=scope,
        workspace_key=wk,
        scope_key=sk,
        transaction_id=transaction["transaction_id"],
    )
    if not recovered["receipt"]:
        raise AssertionError("recovery did not regenerate the missing receipt")
    head = state_view["head_sequence"]
    repeated, repeated_state = commit_transaction(
        state,
        workspace=workspace,
        scope=scope,
        workspace_key=wk,
        scope_key=sk,
        transaction_id=transaction["transaction_id"],
        expected_contract_hash="contract-hash",
        current_contract_loader=lambda: "changed-by-own-commit",
    )
    if repeated_state["head_sequence"] != head or not repeated["receipt"]:
        raise AssertionError("repeated committed transaction was not idempotent")
    forged_records = copy.deepcopy(load_transaction_records(state, wk, sk))
    receipt_record = next(
        record
        for record in forged_records
        if record.get("event_type") == "transaction_receipted"
    )
    forged_body = receipt_record["data"]["receipt"]
    forged_body["transaction_id"] = "tx-forged"
    forged_body["contract_hash"] = "contract-forged"
    forged_body["intent_count"] = 999
    receipt_record["data"]["receipt_hash"] = stable_hash(forged_body)
    forged_state = reduce_transactions(forged_records)
    if not any("receipt body does not match" in issue for issue in forged_state["issues"]):
        raise AssertionError("self-hashed forged receipt was accepted")
    changed_intents = copy.deepcopy(intents)
    changed_intents[0]["payload"]["value"] = 99
    try:
        prepare_transaction(
            state,
            workspace=workspace,
            scope=scope,
            workspace_key=wk,
            scope_key=sk,
            idempotency_key="fault-once",
            contract_hash="contract-hash",
            proposal=proposal,
            intents=changed_intents,
        )
        raise AssertionError("idempotency key accepted different content")
    except ValueError as exc:
        if "idempotency_conflict" not in str(exc):
            raise


def atomic_contract_fence_test(root):
    state = root / "atomic-fence-state"
    wk = "fence-workspace"
    sk = "fence-scope"
    transaction, _ = prepare_transaction(
        state,
        workspace="workspace",
        scope="scope",
        workspace_key=wk,
        scope_key=sk,
        idempotency_key="atomic-fence",
        contract_hash="head-0",
        proposal={"proposal_hash": "proposal-fence"},
        intents=[
            {
                "participant": "fixture",
                "target_relpath": "participants/fence.jsonl",
                "payload": {"event_id": "fence"},
            }
        ],
    )
    actual = {"hash": "head-0"}
    loader_entered = threading.Event()
    errors = []
    writer_observed = []

    def current_contract_loader():
        loader_entered.set()
        time.sleep(0.15)
        return actual["hash"]

    def commit_worker():
        try:
            commit_transaction(
                state,
                workspace="workspace",
                scope="scope",
                workspace_key=wk,
                scope_key=sk,
                transaction_id=transaction["transaction_id"],
                expected_contract_hash="head-0",
                current_contract_loader=current_contract_loader,
            )
        except Exception as exc:
            errors.append(exc)

    def writer_worker():
        if not loader_entered.wait(2):
            errors.append(RuntimeError("contract loader was not entered"))
            return
        with contract_fence(state, wk, sk):
            journal = reduce_transactions(load_transaction_records(state, wk, sk))
            writer_observed.append(
                journal["transactions"][transaction["transaction_id"]]["state"]
            )
            actual["hash"] = "head-1"

    commit_thread = threading.Thread(target=commit_worker)
    writer_thread = threading.Thread(target=writer_worker)
    commit_thread.start()
    writer_thread.start()
    commit_thread.join(5)
    writer_thread.join(5)
    if commit_thread.is_alive() or writer_thread.is_alive():
        raise AssertionError("contract fence deadlocked")
    if errors:
        raise errors[0]
    if writer_observed != ["COMMITTED"] or actual["hash"] != "head-1":
        raise AssertionError("contract-affecting writer entered between check and commit")


def direct_control_writer_fence_test(root, method_state, environment):
    workspace_path = root / "direct-control-fence"
    workspace_path.mkdir()
    workspace = str(workspace_path.resolve())
    activate(workspace, environment, "activate direct control fence fixture")
    normalized = os.path.normcase(os.path.normpath(workspace))
    wk = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    sk = hashlib.sha256(SCOPE.casefold().encode("utf-8")).hexdigest()[:12]
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(SCRIPT),
        "memory-control",
        "--workspace",
        workspace,
        "--scope",
        SCOPE,
        "--state",
        "active",
        "--directive",
        "writer must wait for contract fence",
    ]
    with contract_fence(method_state, wk, sk):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        time.sleep(0.2)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"control writer bypassed contract fence: {stdout} {stderr}"
            )
    stdout, stderr = process.communicate(timeout=5)
    if process.returncode:
        raise AssertionError(f"control writer failed after fence release: {stderr}")


def main():
    with tempfile.TemporaryDirectory(prefix="weilan-transaction-test-", dir=str(Path.home())) as temporary:
        root = Path(temporary)
        method_state = root / "method-state"
        environment = os.environ.copy()
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_CODEX_SESSIONS_HOME"] = str(root / "sessions")
        environment["WEILAN_METHOD_HOME"] = str(method_state)
        cli_transaction_test(root, method_state, environment)
        stale_and_abort_test(root, method_state, environment)
        fault_recovery_test(root)
        atomic_contract_fence_test(root)
        direct_control_writer_fence_test(root, method_state, environment)
        transaction_source = Path(__file__).with_name("transaction.py").read_text(encoding="utf-8")
        if "while True" in transaction_source or "threading" in transaction_source or "asyncio" in transaction_source:
            raise AssertionError("0.7b introduced a background runner")
        print(
            json.dumps(
                {
                    "valid": True,
                    "pending_invisible_before_commit": True,
                    "cross_ledger_atomic_visibility": True,
                    "stale_contract_rejected": True,
                    "paused_scope_commit_blocked": True,
                    "abort_keeps_pending_invisible": True,
                    "prepare_and_commit_idempotent": True,
                    "idempotency_conflict_rejected": True,
                    "prepare_crash_recovered_without_commit": True,
                    "commit_survives_missing_receipt": True,
                    "receipt_replayable": True,
                    "forged_receipt_rejected": True,
                    "contract_check_and_commit_atomic": True,
                    "control_writer_shares_contract_fence": True,
                    "relocated_envelope_rejected": True,
                    "background_loop_disabled": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
