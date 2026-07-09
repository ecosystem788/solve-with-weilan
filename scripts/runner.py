"""Foreground-only bounded event runner for WeiLan Memory 0.7d."""

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from transaction import (
    ContractConflict,
    is_quarantined_torn_fragment,
    iter_ledger_lines,
    terminate_torn_tail,
    warn_torn_tail,
)


RUNNER_SCHEMA_VERSION = "weilan_runner_event_v0.7d"
MANIFEST_SCHEMA_VERSION = "weilan_runner_manifest_v0.7d"
RECEIPT_SCHEMA_VERSION = "weilan_runner_receipt_v0.7d"
EVENT_TYPES = (
    "run_started",
    "event_claimed",
    "step_committed",
    "run_stopped",
    "run_receipted",
)
MAX_STEPS = 16
MAX_EVENTS = 32
_LOCAL_CLAIMS = set()
STOP_REASONS = {
    "QUIESCENT",
    "AWAITING_FRAME_CLOSE",
    "AUDIT_BLOCKED",
    "CONTROL_BLOCKED",
    "INVALID",
    "STEP_BUDGET_EXHAUSTED",
    "EVENTS_EXHAUSTED",
    "CONFLICT",
    "INVALID_EVENT",
    "ABORTED",
}


class RunnerConflict(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def run_id_for(workspace_key, scope_key, idempotency_key):
    material = f"{workspace_key}\n{scope_key}\n{idempotency_key}".encode("utf-8")
    return "wrun-" + hashlib.sha256(material).hexdigest()[:32]


def _text(value):
    return str(value or "").strip()


def normalize_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("runner manifest must be an object")
    schema = manifest.get("schema_version", MANIFEST_SCHEMA_VERSION)
    if schema != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported runner manifest schema")
    max_steps = manifest.get("max_steps")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= MAX_STEPS:
        raise ValueError(f"runner max_steps must be between 1 and {MAX_STEPS}")
    events = manifest.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError(f"runner events must be an array with at most {MAX_EVENTS} items")
    normalized_events = []
    seen_ids = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError("runner event must be an object")
        event_id = _text(event.get("event_id"))
        if not event_id or len(event_id) > 128 or event_id in seen_ids:
            raise ValueError("runner event_id must be unique and at most 128 characters")
        seen_ids.add(event_id)
        proposal = event.get("proposal")
        materialization = event.get("materialization", {})
        if not isinstance(proposal, dict) or not isinstance(materialization, dict):
            raise ValueError("runner event requires proposal and materialization objects")
        trigger_kind = _text(proposal.get("trigger_kind"))
        trigger_ref = _text(proposal.get("trigger_ref"))
        disposition = _text(proposal.get("disposition"))
        if not trigger_kind or not trigger_ref or not disposition:
            raise ValueError("runner proposal requires trigger kind, trigger ref, and disposition")
        normalized_events.append(
            {
                "event_id": event_id,
                "index": index,
                "proposal": {
                    "trigger_kind": trigger_kind,
                    "trigger_ref": trigger_ref,
                    "disposition": disposition,
                    "target_ref": _text(proposal.get("target_ref")) or None,
                    "candidate_target_ref": _text(proposal.get("candidate_target_ref")) or None,
                    "branches": list(dict.fromkeys(_text(value) for value in proposal.get("branches", []) if _text(value))),
                    "death_line_matches": list(
                        dict.fromkeys(
                            _text(value)
                            for value in proposal.get("death_line_matches", [])
                            if _text(value)
                        )
                    ),
                    "changed_assumption": _text(proposal.get("changed_assumption")),
                },
                "materialization": {
                    "level": _text(materialization.get("level")) or "L2",
                    "result_branch": _text(materialization.get("result_branch")),
                    "problem": _text(materialization.get("problem")),
                    "success_criteria": _text(materialization.get("success_criteria")),
                    "budget": _text(materialization.get("budget")),
                    "why_reasonable": _text(materialization.get("why_reasonable")),
                    "next_expected_evidence": _text(
                        materialization.get("next_expected_evidence")
                    ),
                    "death_line": _text(materialization.get("death_line")),
                    "evidence_refs": sorted(
                        {
                            _text(value)
                            for value in materialization.get("evidence_refs", [])
                            if _text(value)
                        }
                    ),
                    "once_reasonable": _text(materialization.get("once_reasonable")),
                    "invalidating_evidence": _text(
                        materialization.get("invalidating_evidence")
                    ),
                    "reusable_results": _text(materialization.get("reusable_results")),
                    "forbidden_assumption": _text(
                        materialization.get("forbidden_assumption")
                    ),
                },
            }
        )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "max_steps": max_steps,
        "events": normalized_events,
        "bounds": {
            "foreground_only": True,
            "background_allowed": False,
            "heartbeat_allowed": False,
            "unsupplied_event_generation_allowed": False,
        },
    }


def runner_directory(root, workspace_key, scope_key):
    return Path(root) / "memory" / "runs" / "workspaces" / workspace_key / scope_key


def runner_paths(root, workspace_key, scope_key):
    directory = runner_directory(root, workspace_key, scope_key)
    return sorted(directory.glob("*.jsonl")) if directory.exists() else []


def raw_read_jsonl(path):
    records = []
    if not Path(path).exists():
        return records
    for number, offset, raw, terminated in iter_ledger_lines(path):
        if not raw.strip():
            continue
        try:
            records.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if not terminated:
                warn_torn_tail(path, number)
                continue
            if is_quarantined_torn_fragment(path, offset, raw):
                continue
            raise ValueError(f"{Path(path).name}:{number}: {exc}") from exc
    return records


def load_run_records(root, workspace_key, scope_key):
    records = []
    for path in runner_paths(root, workspace_key, scope_key):
        records.extend(raw_read_jsonl(path))
    return records


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with path.open("a+b") as handle:
        terminate_torn_tail(path, handle)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def try_scope_claim(path):
    path = Path(path)
    claim_key = str(path.resolve())
    if claim_key in _LOCAL_CLAIMS:
        raise RunnerConflict("runner_scope_claim_conflict")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        acquired = False
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                _LOCAL_CLAIMS.add(claim_key)
            except OSError as exc:
                raise RunnerConflict("runner_scope_claim_conflict") from exc
            try:
                yield
            finally:
                if acquired:
                    _LOCAL_CLAIMS.discard(claim_key)
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                _LOCAL_CLAIMS.add(claim_key)
            except OSError as exc:
                raise RunnerConflict("runner_scope_claim_conflict") from exc
            try:
                yield
            finally:
                if acquired:
                    _LOCAL_CLAIMS.discard(claim_key)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reduce_runs(records):
    issues = []
    runs = {}
    idempotency = {}
    seen_event_ids = set()
    expected_sequence = 1
    head_event_id = None
    ledger_identity = None
    for number, record in enumerate(records, 1):
        label = f"runner record {number}"
        if record.get("schema_version") != RUNNER_SCHEMA_VERSION:
            issues.append(f"{label}: unsupported schema_version")
            continue
        identity = (
            str(record.get("workspace") or "").casefold(),
            record.get("workspace_key"),
            str(record.get("scope") or "").casefold(),
            record.get("scope_key"),
        )
        if any(not value for value in identity):
            issues.append(f"{label}: incomplete workspace or scope identity")
        elif ledger_identity is None:
            ledger_identity = identity
        elif identity != ledger_identity:
            issues.append(f"{label}: workspace or scope identity changed")
        sequence = record.get("sequence")
        if sequence != expected_sequence:
            issues.append(f"{label}: expected sequence {expected_sequence}, found {sequence}")
            expected_sequence = sequence + 1 if isinstance(sequence, int) else expected_sequence + 1
        else:
            expected_sequence += 1
        event_id = record.get("event_id")
        if not event_id or event_id in seen_event_ids:
            issues.append(f"{label}: missing or duplicate event_id")
        seen_event_ids.add(event_id)
        event_type = record.get("event_type")
        run_id = record.get("run_id")
        data = record.get("data")
        if event_type not in EVENT_TYPES or not run_id or not isinstance(data, dict):
            issues.append(f"{label}: invalid runner event envelope")
            continue
        current = runs.get(run_id)
        if event_type == "run_started":
            if current:
                issues.append(f"{label}: run already started")
                continue
            key = data.get("idempotency_key")
            try:
                manifest = normalize_manifest(data.get("manifest"))
            except ValueError as exc:
                issues.append(f"{label}: {exc}")
                continue
            manifest_hash = stable_hash(manifest)
            if data.get("manifest_hash") != manifest_hash:
                issues.append(f"{label}: manifest hash mismatch")
            if not key or key in idempotency:
                issues.append(f"{label}: missing or duplicate idempotency key")
            if key and run_id_for(record.get("workspace_key"), record.get("scope_key"), key) != run_id:
                issues.append(f"{label}: run id does not match idempotency identity")
            idempotency[key] = run_id
            current = {
                "run_id": run_id,
                "idempotency_key": key,
                "manifest": manifest,
                "manifest_hash": manifest_hash,
                "initial_contract_hash": data.get("initial_contract_hash"),
                "initial_contract_status": data.get("initial_contract_status"),
                "status": "RUNNING",
                "step_count": 0,
                "next_event_index": 0,
                "claimed": None,
                "steps": [],
                "stop_reason": None,
                "final_contract_hash": None,
                "final_contract_status": None,
                "receipt": None,
                "started_event_id": event_id,
            }
            runs[run_id] = current
        elif not current:
            issues.append(f"{label}: run has not started")
            continue
        elif event_type == "event_claimed":
            index = data.get("event_index")
            events = current["manifest"]["events"]
            if current["status"] != "RUNNING" or current["claimed"] is not None:
                issues.append(f"{label}: run cannot claim another event")
            elif index != current["next_event_index"] or index >= len(events):
                issues.append(f"{label}: event claim is out of order")
            elif data.get("event_id") != events[index]["event_id"]:
                issues.append(f"{label}: event claim identity mismatch")
            else:
                current["claimed"] = {
                    "event_index": index,
                    "event_id": data.get("event_id"),
                    "before_contract_hash": data.get("before_contract_hash"),
                    "claim_event_id": event_id,
                }
        elif event_type == "step_committed":
            claimed = current.get("claimed")
            if current["status"] != "RUNNING" or not claimed:
                issues.append(f"{label}: step has no active claim")
            elif (
                data.get("event_index") != claimed["event_index"]
                or data.get("event_id") != claimed["event_id"]
                or not data.get("transaction_id")
                or not data.get("receipt_hash")
                or not data.get("after_contract_hash")
            ):
                issues.append(f"{label}: committed step does not match its claim")
            else:
                current["steps"].append(
                    {
                        "event_index": data.get("event_index"),
                        "event_id": data.get("event_id"),
                        "transaction_id": data.get("transaction_id"),
                        "receipt_hash": data.get("receipt_hash"),
                        "before_contract_hash": claimed["before_contract_hash"],
                        "after_contract_hash": data.get("after_contract_hash"),
                        "after_contract_status": data.get("after_contract_status"),
                        "successor_frame_id": data.get("successor_frame_id"),
                        "step_event_id": event_id,
                    }
                )
                current["step_count"] += 1
                current["next_event_index"] += 1
                current["claimed"] = None
        elif event_type == "run_stopped":
            if current["status"] != "RUNNING":
                issues.append(f"{label}: run is already stopped")
            elif data.get("reason") not in STOP_REASONS:
                issues.append(f"{label}: unsupported stop reason")
            else:
                current["status"] = "STOPPED"
                current["stop_reason"] = data.get("reason")
                current["final_contract_hash"] = data.get("final_contract_hash")
                current["final_contract_status"] = data.get("final_contract_status")
                current["stop_detail"] = data.get("detail", "")
                current["claimed"] = None
                current["stopped_event_id"] = event_id
        elif event_type == "run_receipted":
            if current["status"] != "STOPPED" or current["receipt"] is not None:
                issues.append(f"{label}: receipt requires one stopped run")
            else:
                body = data.get("receipt")
                if not isinstance(body, dict) or data.get("receipt_hash") != stable_hash(body):
                    issues.append(f"{label}: invalid runner receipt")
                elif body != _receipt_body(current):
                    issues.append(f"{label}: runner receipt does not match replayed state")
                else:
                    current["receipt"] = {
                        "receipt_event_id": event_id,
                        "receipt_hash": data.get("receipt_hash"),
                        **body,
                    }
        head_event_id = event_id
    return {
        "valid": not issues,
        "issues": issues,
        "head_sequence": expected_sequence - 1,
        "head_event_id": head_event_id,
        "runs": runs,
        "idempotency": idempotency,
    }


def _journal_path(root, workspace_key, scope_key):
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return runner_directory(root, workspace_key, scope_key) / f"{date_dir}.jsonl"


def _append_event(root, workspace, scope, workspace_key, scope_key, state, run_id, event_type, data):
    record = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "sequence": state["head_sequence"] + 1,
        "workspace": workspace,
        "workspace_key": workspace_key,
        "scope": scope,
        "scope_key": scope_key,
        "run_id": run_id,
        "event_type": event_type,
        "data": data,
        "authority": "explicit_foreground_runner_never_activation_authority",
    }
    append_jsonl(_journal_path(root, workspace_key, scope_key), record)
    return record


def _receipt_body(run):
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": run["run_id"],
        "idempotency_key": run["idempotency_key"],
        "manifest_hash": run["manifest_hash"],
        "initial_contract_hash": run["initial_contract_hash"],
        "initial_contract_status": run["initial_contract_status"],
        "final_contract_hash": run["final_contract_hash"],
        "final_contract_status": run["final_contract_status"],
        "stop_reason": run["stop_reason"],
        "step_count": run["step_count"],
        "max_steps": run["manifest"]["max_steps"],
        "event_count": len(run["manifest"]["events"]),
        "next_event_index": run["next_event_index"],
        "steps": list(run["steps"]),
        "background_process_started": False,
        "heartbeat_used": False,
        "authority": "run_receipt_is_execution_evidence_not_activation_authority",
    }


def _append_receipt(root, workspace, scope, workspace_key, scope_key, records, state, run):
    body = _receipt_body(run)
    record = _append_event(
        root,
        workspace,
        scope,
        workspace_key,
        scope_key,
        state,
        run["run_id"],
        "run_receipted",
        {"receipt_hash": stable_hash(body), "receipt": body},
    )
    records.append(record)
    state = reduce_runs(records)
    return state["runs"][run["run_id"]], state


def _append_stop(
    root,
    workspace,
    scope,
    workspace_key,
    scope_key,
    records,
    state,
    run,
    reason,
    contract,
    detail="",
):
    record = _append_event(
        root,
        workspace,
        scope,
        workspace_key,
        scope_key,
        state,
        run["run_id"],
        "run_stopped",
        {
            "reason": reason,
            "final_contract_hash": contract.get("contract_hash"),
            "final_contract_status": contract.get("status"),
            "detail": _text(detail)[:512],
        },
    )
    records.append(record)
    state = reduce_runs(records)
    run = state["runs"][run["run_id"]]
    return run, state


def _status_stop_reason(status):
    return {
        "QUIESCENT": "QUIESCENT",
        "AWAITING_FRAME_CLOSE": "AWAITING_FRAME_CLOSE",
        "AUDIT_BLOCKED": "AUDIT_BLOCKED",
        "CONTROL_BLOCKED": "CONTROL_BLOCKED",
        "INVALID": "INVALID",
    }.get(status)


def runner_summary(run, state):
    return {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "run_id": run["run_id"],
        "idempotency_key": run["idempotency_key"],
        "manifest_hash": run["manifest_hash"],
        "status": run["status"],
        "stop_reason": run["stop_reason"],
        "step_count": run["step_count"],
        "max_steps": run["manifest"]["max_steps"],
        "event_count": len(run["manifest"]["events"]),
        "next_event_index": run["next_event_index"],
        "claimed": run["claimed"],
        "steps": run["steps"],
        "receipt": run["receipt"],
        "journal_head": {
            "event_id": state["head_event_id"],
            "sequence": state["head_sequence"],
        },
        "foreground_only": True,
        "background_process_started": False,
        "heartbeat_used": False,
    }


def execute_run(
    root,
    *,
    workspace,
    scope,
    workspace_key,
    scope_key,
    idempotency_key,
    manifest,
    contract_loader,
    materialize,
    reconcile_materialization=None,
    fault_at=None,
    fault_step=0,
):
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 256:
        raise ValueError("runner idempotency key must be a non-empty string up to 256 characters")
    idempotency_key = idempotency_key.strip()
    manifest = normalize_manifest(manifest)
    manifest_hash = stable_hash(manifest)
    run_id = run_id_for(workspace_key, scope_key, idempotency_key)
    directory = runner_directory(root, workspace_key, scope_key)
    with try_scope_claim(directory / ".runner.lock"):
        records = load_run_records(root, workspace_key, scope_key)
        state = reduce_runs(records)
        if state["issues"]:
            raise ValueError("runner journal is invalid: " + "; ".join(state["issues"]))
        existing_id = state["idempotency"].get(idempotency_key)
        if existing_id:
            run = state["runs"][existing_id]
            if run["manifest_hash"] != manifest_hash:
                raise ValueError("runner_idempotency_conflict")
        else:
            contract = contract_loader()
            started = _append_event(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                run_id,
                "run_started",
                {
                    "idempotency_key": idempotency_key,
                    "manifest": manifest,
                    "manifest_hash": manifest_hash,
                    "initial_contract_hash": contract.get("contract_hash"),
                    "initial_contract_status": contract.get("status"),
                },
            )
            records.append(started)
            state = reduce_runs(records)
            run = state["runs"][run_id]
            if fault_at == "after_run_started":
                raise RuntimeError("injected_runner_failure_after_run_started")
        if run["status"] == "STOPPED":
            if run["receipt"] is None:
                run, state = _append_receipt(
                    root, workspace, scope, workspace_key, scope_key, records, state, run
                )
            return run, state

        for _ in range(manifest["max_steps"] - run["step_count"] + 1):
            contract = contract_loader()
            if run["claimed"] is None:
                expected_head = (
                    run["steps"][-1]["after_contract_hash"]
                    if run["steps"]
                    else run["initial_contract_hash"]
                )
                if contract.get("contract_hash") != expected_head:
                    run, state = _append_stop(
                        root,
                        workspace,
                        scope,
                        workspace_key,
                        scope_key,
                        records,
                        state,
                        run,
                        "CONFLICT",
                        contract,
                        "contract_head_changed_between_runner_steps",
                    )
                    break
                reason = _status_stop_reason(contract.get("status"))
                if reason:
                    run, state = _append_stop(
                        root,
                        workspace,
                        scope,
                        workspace_key,
                        scope_key,
                        records,
                        state,
                        run,
                        reason,
                        contract,
                    )
                    break
                if run["step_count"] >= manifest["max_steps"]:
                    run, state = _append_stop(
                        root,
                        workspace,
                        scope,
                        workspace_key,
                        scope_key,
                        records,
                        state,
                        run,
                        "STEP_BUDGET_EXHAUSTED",
                        contract,
                    )
                    break
                if run["next_event_index"] >= len(manifest["events"]):
                    run, state = _append_stop(
                        root,
                        workspace,
                        scope,
                        workspace_key,
                        scope_key,
                        records,
                        state,
                        run,
                        "EVENTS_EXHAUSTED",
                        contract,
                    )
                    break
                event = manifest["events"][run["next_event_index"]]
                claimed = _append_event(
                    root,
                    workspace,
                    scope,
                    workspace_key,
                    scope_key,
                    state,
                    run_id,
                    "event_claimed",
                    {
                        "event_index": event["index"],
                        "event_id": event["event_id"],
                        "before_contract_hash": contract.get("contract_hash"),
                    },
                )
                records.append(claimed)
                state = reduce_runs(records)
                run = state["runs"][run_id]
                if fault_at == "after_event_claimed" and run["step_count"] == fault_step:
                    raise RuntimeError("injected_runner_failure_after_event_claimed")
            claimed = run["claimed"]
            event = manifest["events"][claimed["event_index"]]
            step_key = f"runner:{run_id}:{event['event_id']}"
            result = None
            try:
                result = materialize(event, step_key, claimed["before_contract_hash"])
            except Exception as exc:
                recovery = (
                    reconcile_materialization(
                        event, step_key, claimed["before_contract_hash"], exc
                    )
                    if reconcile_materialization
                    else None
                )
                if recovery and recovery.get("state") == "COMMITTED":
                    result = recovery
                else:
                    current = contract_loader()
                    message = str(exc)
                    reason = (
                        "CONFLICT"
                        if isinstance(exc, (ContractConflict, RunnerConflict))
                        or any(
                            token in message
                            for token in (
                                "contract_head_changed",
                                "idempotency_conflict",
                                "branch head conflict",
                                "runner_scope_claim_conflict",
                            )
                        )
                        else "INVALID_EVENT"
                    )
                    if reason != "CONFLICT" and recovery and recovery.get("state") == "NOT_STARTED":
                        # the materialization was rejected before any durable
                        # write; if the scope is now paused/blocked, that is the
                        # true stop cause, not an invalid event
                        status_reason = _status_stop_reason(current.get("status"))
                        if status_reason:
                            run, state = _append_stop(
                                root,
                                workspace,
                                scope,
                                workspace_key,
                                scope_key,
                                records,
                                state,
                                run,
                                status_reason,
                                current,
                                "materialization_blocked_before_start",
                            )
                            break
                    if (
                        recovery
                        and recovery.get("state") in {"PREPARING", "PREPARED"}
                        and reason != "CONFLICT"
                    ):
                        # The transaction outcome is not terminal.  Preserve the
                        # claimed event so a later explicit invocation can resume it.
                        raise
                    if recovery is None and reason != "CONFLICT":
                        # Without durable transaction evidence the commit
                        # outcome is unknown.  Never turn that uncertainty into
                        # a terminal INVALID_EVENT; keep the claim replayable.
                        raise
                    detail = (
                        "contract_or_idempotency_conflict"
                        if reason == "CONFLICT"
                        else "materialization_rejected_before_commit"
                    )
                    run, state = _append_stop(
                        root,
                        workspace,
                        scope,
                        workspace_key,
                        scope_key,
                        records,
                        state,
                        run,
                        reason,
                        current,
                        detail,
                    )
                    break
            if not result.get("materialized") or result.get("state") != "COMMITTED":
                current = contract_loader()
                run, state = _append_stop(
                    root,
                    workspace,
                    scope,
                    workspace_key,
                    scope_key,
                    records,
                    state,
                    run,
                    "INVALID_EVENT",
                    current,
                    "0.7c step did not commit",
                )
                break
            if fault_at == "after_materialize" and run["step_count"] == fault_step:
                raise RuntimeError("injected_runner_failure_after_materialize")
            after = contract_loader()
            committed_after_hash = result.get("contract_after_hash") or after.get(
                "contract_hash"
            )
            committed_after_status = result.get("contract_after_status") or after.get(
                "status"
            )
            receipt = result.get("receipt") or {}
            step = _append_event(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                run_id,
                "step_committed",
                {
                    "event_index": claimed["event_index"],
                    "event_id": claimed["event_id"],
                    "transaction_id": result.get("transaction_id"),
                    "receipt_hash": receipt.get("receipt_hash"),
                    "after_contract_hash": committed_after_hash,
                    "after_contract_status": committed_after_status,
                    "successor_frame_id": result.get("successor_frame_id"),
                },
            )
            records.append(step)
            state = reduce_runs(records)
            run = state["runs"][run_id]
            if fault_at == "after_step_committed" and run["step_count"] - 1 == fault_step:
                raise RuntimeError("injected_runner_failure_after_step_committed")

        if run["status"] == "RUNNING":
            contract = contract_loader()
            reason = _status_stop_reason(contract.get("status"))
            if not reason:
                if run["step_count"] >= manifest["max_steps"]:
                    reason = "STEP_BUDGET_EXHAUSTED"
                elif run["next_event_index"] >= len(manifest["events"]):
                    reason = "EVENTS_EXHAUSTED"
                else:
                    reason = "INVALID"
            stopped = _append_event(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                run_id,
                "run_stopped",
                {
                    "reason": reason,
                    "final_contract_hash": contract.get("contract_hash"),
                    "final_contract_status": contract.get("status"),
                    "detail": "bounded runner reached its foreground termination gate",
                },
            )
            records.append(stopped)
            state = reduce_runs(records)
            run = state["runs"][run_id]
        if fault_at == "after_run_stopped" and run["receipt"] is None:
            raise RuntimeError("injected_runner_failure_after_run_stopped")
        if run["receipt"] is None:
            run, state = _append_receipt(
                root, workspace, scope, workspace_key, scope_key, records, state, run
            )
        return run, state
