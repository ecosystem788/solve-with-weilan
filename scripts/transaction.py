"""Append-only cross-ledger transaction journal for WeiLan Memory 0.7b.

The journal is the commit coordinator. Participant ledgers receive pending
envelopes before COMMIT; readers unwrap them only when the coordinator reports
the transaction as committed. No worker, timer, or background loop is used.
"""

import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


TRANSACTION_SCHEMA_VERSION = "weilan_metabolic_transaction_v0.7b"
PENDING_SCHEMA_VERSION = "weilan_pending_event_v0.7b"
RECEIPT_SCHEMA_VERSION = "weilan_metabolic_receipt_v0.7b"
EVENT_TYPES = (
    "transaction_prepare_started",
    "transaction_prepared",
    "transaction_committed",
    "transaction_aborted",
    "transaction_receipted",
)
TERMINAL_STATES = {"COMMITTED", "ABORTED"}
MAX_INTENTS = 64
_VISIBILITY_CACHE = ContextVar("weilan_transaction_visibility_cache", default=None)


class ContractConflict(ValueError):
    """A retryable head/idempotency conflict, distinct from invalid input.

    Callers (the bounded runner in particular) classify stops by exception
    type instead of matching message substrings.
    """


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def transaction_id_for(workspace_key, scope_key, idempotency_key):
    material = f"{workspace_key}\n{scope_key}\n{idempotency_key}".encode("utf-8")
    return "wtx-" + hashlib.sha256(material).hexdigest()[:32]


def normalize_relative_path(value):
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("transaction target path must be a safe relative path")
    return path.as_posix()


def resolve_relative(root, value):
    relative = normalize_relative_path(value)
    root = Path(root).resolve()
    candidate = (root / Path(*PurePosixPath(relative).parts)).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("transaction target escapes method-state root")
    return candidate


def transaction_directory(root, workspace_key, scope_key):
    return (
        Path(root)
        / "memory"
        / "transactions"
        / "workspaces"
        / workspace_key
        / scope_key
    )


def transaction_paths(root, workspace_key, scope_key):
    directory = transaction_directory(root, workspace_key, scope_key)
    return sorted(directory.glob("*.jsonl")) if directory.exists() else []


def torn_tail_quarantine_path(path):
    return Path(path).with_name(Path(path).name + ".torn")


def iter_ledger_lines(path):
    """Yield (number, offset, line_bytes, terminated) for each physical ledger line.

    Lines are split on b"\\n" without decoding, so readers can enforce strict
    UTF-8 per line and address quarantined fragments by byte identity. By
    construction only the final physical line can be unterminated.
    """

    data = Path(path).read_bytes()
    offset = 0
    number = 0
    chunks = data.split(b"\n")
    for chunk in chunks[:-1]:
        number += 1
        yield number, offset, chunk, True
        offset += len(chunk) + 1
    tail = chunks[-1]
    if tail:
        yield number + 1, offset, tail, False


def is_quarantined_torn_fragment(path, offset, line_bytes):
    """Return True only for the exact quarantined fragment at this byte range.

    Matching is by offset, length, and SHA-256 of the fragment bytes, never by
    text, so a later unrelated bad line with identical content cannot be
    silently suppressed.
    """

    quarantine = torn_tail_quarantine_path(path)
    if not quarantine.exists():
        return False
    digest = hashlib.sha256(line_bytes).hexdigest()
    try:
        raw_records = quarantine.read_bytes().split(b"\n")
    except OSError:
        return False
    for raw in raw_records:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            record.get("fragment_offset") == offset
            and record.get("fragment_length") == len(line_bytes)
            and record.get("fragment_sha256") == digest
        ):
            return True
    return False


def warn_torn_tail(path, number):
    sys.stderr.write(
        f"weilan warning: ignored torn unterminated line {number} in {Path(path).name}; "
        "the next append will quarantine it\n"
    )


def terminate_torn_tail(path, handle):
    """Seal a torn trailing fragment left by an interrupted append.

    The fragment was never a durably committed record. Never truncate the
    ledger: record the fragment's byte identity (offset, length, SHA-256) in
    an append-only `.torn` sidecar, then write one newline so the fragment
    becomes an isolated line that readers skip only at that exact byte range.
    A concurrent complete append at worst turns this newline into a blank
    line, which every reader already ignores.
    """

    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return
    handle.seek(size - 1)
    if handle.read(1) == b"\n":
        return
    handle.seek(0)
    data = handle.read()
    cut = data.rfind(b"\n") + 1
    fragment = data[cut:]
    quarantine_record = {
        "quarantined_at_utc": utc_now(),
        "source": Path(path).name,
        "reason": "torn_tail_from_interrupted_append",
        "fragment_offset": cut,
        "fragment_length": len(fragment),
        "fragment_sha256": hashlib.sha256(fragment).hexdigest(),
        "fragment_preview": fragment.decode("utf-8", "replace"),
    }
    quarantine = torn_tail_quarantine_path(path)
    with quarantine.open("ab") as sidecar:
        sidecar.write(
            (json.dumps(quarantine_record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        )
        sidecar.flush()
        os.fsync(sidecar.fileno())
    handle.write(b"\n")
    handle.flush()
    os.fsync(handle.fileno())


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


def load_transaction_records(root, workspace_key, scope_key):
    records = []
    for path in transaction_paths(root, workspace_key, scope_key):
        records.extend(raw_read_jsonl(path))
    return records


@contextmanager
def transaction_visibility_scope():
    """Pin transaction decisions for one caller-visible command snapshot."""

    token = _VISIBILITY_CACHE.set({})
    try:
        yield
    finally:
        _VISIBILITY_CACHE.reset(token)


def reset_visibility_snapshot():
    if _VISIBILITY_CACHE.get() is not None:
        _VISIBILITY_CACHE.set({})


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
def exclusive_file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            import time

            # msvcrt LK_LOCK gives up after ~10 one-second retries with a bare
            # "Permission denied"; keep retrying to a configurable deadline and
            # then name the real cause so dual-session contention is diagnosable
            timeout_s = float(os.environ.get("WEILAN_LOCK_TIMEOUT_S", "120"))
            deadline = time.monotonic() + timeout_s
            # each msvcrt LK_LOCK call blocks for up to ~10 one-second retries,
            # so a bounded number of calls covers the whole deadline window
            attempts = max(1, int(timeout_s / 10) + 1)
            last_error = None
            for _ in range(attempts):
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    last_error = None
                    break
                except OSError as exc:
                    last_error = exc
                    if time.monotonic() >= deadline:
                        break
            if last_error is not None:
                raise RuntimeError(
                    f"lock timeout on {path.name}; another weilan session holds it"
                ) from last_error
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def contract_fence_directory(root, workspace_key):
    return Path(root) / "memory" / "metabolism" / "fences" / "workspaces" / workspace_key


@contextmanager
def workspace_contract_fence(root, workspace_key):
    """Serialize workspace-wide control changes against every scoped commit."""

    directory = contract_fence_directory(root, workspace_key)
    with exclusive_file_lock(directory / ".workspace-contract.lock"):
        yield


@contextmanager
def contract_fence(root, workspace_key, scope_key):
    """Fence one contract snapshot and COMMIT against contract-affecting writers.

    The workspace lock is always acquired before the scope lock.  Direct writers
    use the same order, so a workspace control change cannot race a scoped commit.
    """

    directory = contract_fence_directory(root, workspace_key)
    with workspace_contract_fence(root, workspace_key):
        with exclusive_file_lock(directory / "scopes" / scope_key / ".scope-contract.lock"):
            yield


@contextmanager
def staging_fence(root, workspace_key, scope_key, directory):
    """Serialize participant-ledger staging against direct ledger writers.

    Staging appends pending envelopes into the same daily JSONL files that
    direct writers (control, frame, governance, evidence) append to under the
    contract fence, so it must hold the same fence. Lock order stays
    workspace fence, scope fence, then transaction lock, matching COMMIT.
    """

    with contract_fence(root, workspace_key, scope_key):
        with exclusive_file_lock(directory / ".transaction.lock"):
            yield


def normalize_intents(intents):
    if not isinstance(intents, list) or not intents:
        raise ValueError("transaction requires at least one intent")
    if len(intents) > MAX_INTENTS:
        raise ValueError(f"transaction cannot exceed {MAX_INTENTS} intents")
    normalized = []
    seen_ids = set()
    for index, value in enumerate(intents, 1):
        if not isinstance(value, dict):
            raise ValueError("each transaction intent must be an object")
        participant = str(value.get("participant", "")).strip()
        target_relpath = normalize_relative_path(value.get("target_relpath", ""))
        payload = value.get("payload")
        if not participant or not isinstance(payload, dict):
            raise ValueError("intent requires participant and object payload")
        payload_hash = stable_hash(payload)
        intent_id = value.get("intent_id") or f"i{index:04d}-{payload_hash[:16]}"
        if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 128:
            raise ValueError("intent_id must be a non-empty string up to 128 characters")
        if intent_id in seen_ids:
            raise ValueError(f"duplicate transaction intent_id: {intent_id}")
        seen_ids.add(intent_id)
        normalized.append(
            {
                "intent_id": intent_id,
                "order": index,
                "participant": participant,
                "target_relpath": target_relpath,
                "payload_hash": payload_hash,
                "payload": payload,
            }
        )
    return normalized


def bundle_hash_for(intents):
    return stable_hash(normalize_intents(intents))


def reduce_transactions(records):
    issues = []
    transactions = {}
    idempotency = {}
    seen_event_ids = set()
    expected_sequence = 1
    head_event_id = None
    ledger_identity = None
    for index, record in enumerate(records, 1):
        label = f"transaction record {index}"
        if record.get("schema_version") != TRANSACTION_SCHEMA_VERSION:
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
        if event_type not in EVENT_TYPES:
            issues.append(f"{label}: unsupported event_type")
            continue
        transaction_id = record.get("transaction_id")
        data = record.get("data")
        if not transaction_id or not isinstance(data, dict):
            issues.append(f"{label}: missing transaction_id or data")
            continue
        current = transactions.get(transaction_id)
        if event_type == "transaction_prepare_started":
            if current:
                issues.append(f"{label}: transaction already started")
                continue
            key = data.get("idempotency_key")
            intents = data.get("intents")
            try:
                normalized = normalize_intents(intents)
            except ValueError as exc:
                issues.append(f"{label}: {exc}")
                continue
            if data.get("bundle_hash") != stable_hash(normalized):
                issues.append(f"{label}: bundle hash mismatch")
            if not key or key in idempotency:
                issues.append(f"{label}: missing or duplicate idempotency key")
            if key and transaction_id_for(record.get("workspace_key"), record.get("scope_key"), key) != transaction_id:
                issues.append(f"{label}: transaction id does not match idempotency identity")
            idempotency[key] = transaction_id
            current = {
                "transaction_id": transaction_id,
                "idempotency_key": key,
                "contract_hash": data.get("contract_hash"),
                "proposal_hash": data.get("proposal_hash"),
                "proposal": data.get("proposal"),
                "request_hash": data.get("request_hash"),
                "plan_hash": data.get("plan_hash"),
                "bundle_hash": data.get("bundle_hash"),
                "intents": normalized,
                "state": "PREPARING",
                "prepare_event_id": event_id,
                "prepared_event_id": None,
                "terminal_event_id": None,
                "receipt": None,
            }
            transactions[transaction_id] = current
        elif not current:
            issues.append(f"{label}: transaction has not started")
            continue
        elif event_type == "transaction_prepared":
            if current["state"] != "PREPARING":
                issues.append(f"{label}: prepare completion requires PREPARING")
            elif data.get("bundle_hash") != current["bundle_hash"]:
                issues.append(f"{label}: prepared bundle hash mismatch")
            else:
                current["state"] = "PREPARED"
                current["prepared_event_id"] = event_id
        elif event_type == "transaction_committed":
            if current["state"] != "PREPARED":
                issues.append(f"{label}: commit requires PREPARED")
            elif data.get("bundle_hash") != current["bundle_hash"]:
                issues.append(f"{label}: committed bundle hash mismatch")
            else:
                current["state"] = "COMMITTED"
                current["terminal_event_id"] = event_id
        elif event_type == "transaction_aborted":
            if current["state"] not in {"PREPARING", "PREPARED"}:
                issues.append(f"{label}: abort requires PREPARING or PREPARED")
            else:
                current["state"] = "ABORTED"
                current["terminal_event_id"] = event_id
                current["abort_reason"] = data.get("reason", "")
        elif event_type == "transaction_receipted":
            if current["state"] not in TERMINAL_STATES:
                issues.append(f"{label}: receipt requires a terminal decision")
            elif current["receipt"] is not None:
                issues.append(f"{label}: duplicate receipt")
            elif data.get("outcome") != current["state"]:
                issues.append(f"{label}: receipt outcome mismatch")
            else:
                receipt_body = data.get("receipt")
                if not isinstance(receipt_body, dict) or data.get("receipt_hash") != stable_hash(
                    receipt_body
                ):
                    issues.append(f"{label}: invalid receipt hash")
                elif receipt_body != _receipt_body(current):
                    issues.append(f"{label}: receipt body does not match transaction")
                else:
                    current["receipt"] = {
                        "receipt_event_id": event_id,
                        "receipt_hash": data.get("receipt_hash"),
                        **receipt_body,
                    }
        head_event_id = event_id
    return {
        "valid": not issues,
        "issues": issues,
        "head_sequence": expected_sequence - 1,
        "head_event_id": head_event_id,
        "transactions": transactions,
        "idempotency": idempotency,
    }


def _journal_path(root, workspace_key, scope_key):
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return transaction_directory(root, workspace_key, scope_key) / f"{date_dir}.jsonl"


def _append_journal(root, workspace, scope, workspace_key, scope_key, state, event_type, transaction_id, data):
    record = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "sequence": state["head_sequence"] + 1,
        "workspace": workspace,
        "workspace_key": workspace_key,
        "scope": scope,
        "scope_key": scope_key,
        "transaction_id": transaction_id,
        "event_type": event_type,
        "data": data,
        "authority": "explicit_caller_invocation_with_active_control",
    }
    append_jsonl(_journal_path(root, workspace_key, scope_key), record)
    return record


def _pending_envelope(workspace, scope, workspace_key, scope_key, transaction, intent):
    return {
        "schema_version": PENDING_SCHEMA_VERSION,
        "pending_event_id": stable_hash(
            {"transaction_id": transaction["transaction_id"], "intent_id": intent["intent_id"]}
        ),
        "timestamp_utc": utc_now(),
        "workspace": workspace,
        "workspace_key": workspace_key,
        "scope": scope,
        "scope_key": scope_key,
        "transaction_id": transaction["transaction_id"],
        "bundle_hash": transaction["bundle_hash"],
        "intent_id": intent["intent_id"],
        "participant": intent["participant"],
        "payload_hash": intent["payload_hash"],
        "payload": intent["payload"],
        "visibility": "pending_until_coordinator_commit",
    }


def _stage_intent(root, workspace, scope, workspace_key, scope_key, transaction, intent):
    target = resolve_relative(root, intent["target_relpath"])
    matches = [
        record
        for record in raw_read_jsonl(target)
        if record.get("schema_version") == PENDING_SCHEMA_VERSION
        and record.get("transaction_id") == transaction["transaction_id"]
        and record.get("intent_id") == intent["intent_id"]
    ]
    envelope = _pending_envelope(
        workspace, scope, workspace_key, scope_key, transaction, intent
    )
    if matches:
        comparable = dict(envelope)
        comparable["timestamp_utc"] = matches[0].get("timestamp_utc")
        if len(matches) != 1 or matches[0] != comparable:
            raise ValueError(f"conflicting pending envelope: {intent['intent_id']}")
        return False
    append_jsonl(target, envelope)
    return True


def _all_intents_staged(root, transaction):
    for intent in transaction["intents"]:
        target = resolve_relative(root, intent["target_relpath"])
        matches = [
            record
            for record in raw_read_jsonl(target)
            if record.get("schema_version") == PENDING_SCHEMA_VERSION
            and record.get("transaction_id") == transaction["transaction_id"]
            and record.get("intent_id") == intent["intent_id"]
            and record.get("bundle_hash") == transaction["bundle_hash"]
            and record.get("payload_hash") == intent["payload_hash"]
        ]
        if len(matches) != 1:
            return False
    return True


def _receipt_body(transaction):
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "transaction_id": transaction["transaction_id"],
        "idempotency_key": transaction["idempotency_key"],
        "outcome": transaction["state"],
        "contract_hash": transaction["contract_hash"],
        "proposal_hash": transaction["proposal_hash"],
        "request_hash": transaction.get("request_hash"),
        "plan_hash": transaction.get("plan_hash"),
        "bundle_hash": transaction["bundle_hash"],
        "participant_count": len({intent["participant"] for intent in transaction["intents"]}),
        "intent_count": len(transaction["intents"]),
        "intent_refs": [
            {
                "intent_id": intent["intent_id"],
                "participant": intent["participant"],
                "target_relpath": intent["target_relpath"],
                "payload_hash": intent["payload_hash"],
            }
            for intent in transaction["intents"]
        ],
        "authority": "receipt_is_evidence_of_decision_not_activation_authority",
    }


def _append_receipt(root, workspace, scope, workspace_key, scope_key, state, transaction):
    body = _receipt_body(transaction)
    return _append_journal(
        root,
        workspace,
        scope,
        workspace_key,
        scope_key,
        state,
        "transaction_receipted",
        transaction["transaction_id"],
        {"outcome": transaction["state"], "receipt_hash": stable_hash(body), "receipt": body},
    )


def prepare_transaction(
    root,
    *,
    workspace,
    scope,
    workspace_key,
    scope_key,
    idempotency_key,
    contract_hash,
    proposal,
    intents,
    request_hash=None,
    plan_hash=None,
    fail_after_pending=None,
):
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 256:
        raise ValueError("idempotency key must be a non-empty string up to 256 characters")
    idempotency_key = idempotency_key.strip()
    intents = normalize_intents(intents)
    bundle_hash = stable_hash(intents)
    transaction_id = transaction_id_for(workspace_key, scope_key, idempotency_key)
    directory = transaction_directory(root, workspace_key, scope_key)
    with staging_fence(root, workspace_key, scope_key, directory):
        records = load_transaction_records(root, workspace_key, scope_key)
        state = reduce_transactions(records)
        if state["issues"]:
            raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
        existing_id = state["idempotency"].get(idempotency_key)
        if existing_id:
            transaction = state["transactions"][existing_id]
            if (
                transaction["bundle_hash"] != bundle_hash
                or transaction["contract_hash"] != contract_hash
                or transaction["proposal_hash"] != proposal.get("proposal_hash")
                or transaction["proposal"] != proposal
                or transaction.get("request_hash") != request_hash
                or transaction.get("plan_hash") != plan_hash
            ):
                raise ContractConflict("idempotency_conflict")
        else:
            started = _append_journal(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                "transaction_prepare_started",
                transaction_id,
                {
                    "idempotency_key": idempotency_key,
                    "contract_hash": contract_hash,
                    "proposal_hash": proposal.get("proposal_hash"),
                    "proposal": proposal,
                    "request_hash": request_hash,
                    "plan_hash": plan_hash,
                    "bundle_hash": bundle_hash,
                    "intents": intents,
                },
            )
            records.append(started)
            state = reduce_transactions(records)
            transaction = state["transactions"][transaction_id]
        if transaction["state"] == "PREPARING":
            staged = 0
            for intent in transaction["intents"]:
                if _stage_intent(
                    root, workspace, scope, workspace_key, scope_key, transaction, intent
                ):
                    staged += 1
                if fail_after_pending is not None and staged >= fail_after_pending:
                    raise RuntimeError("injected_failure_after_pending")
            if not _all_intents_staged(root, transaction):
                raise RuntimeError("transaction staging incomplete")
            prepared = _append_journal(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                "transaction_prepared",
                transaction_id,
                {"bundle_hash": transaction["bundle_hash"]},
            )
            records.append(prepared)
            state = reduce_transactions(records)
            transaction = state["transactions"][transaction_id]
        return transaction, state


def commit_transaction(
    root,
    *,
    workspace,
    scope,
    workspace_key,
    scope_key,
    transaction_id,
    expected_contract_hash,
    current_contract_loader,
    fail_before_receipt=False,
):
    if not callable(current_contract_loader):
        raise ValueError("commit requires a current contract loader")
    directory = transaction_directory(root, workspace_key, scope_key)
    with contract_fence(root, workspace_key, scope_key):
        with exclusive_file_lock(directory / ".transaction.lock"):
            records = load_transaction_records(root, workspace_key, scope_key)
            state = reduce_transactions(records)
            if state["issues"]:
                raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
            transaction = state["transactions"].get(transaction_id)
            if not transaction:
                raise ValueError("transaction not found")
            if transaction["state"] == "ABORTED":
                raise ValueError("aborted transaction cannot commit")
            if transaction["contract_hash"] != expected_contract_hash:
                raise ContractConflict("expected contract does not match prepared transaction")
            if transaction["state"] != "COMMITTED":
                current_contract_hash = current_contract_loader()
                if current_contract_hash != transaction["contract_hash"]:
                    raise ContractConflict("contract_head_changed")
            if transaction["state"] == "PREPARING":
                raise ValueError("transaction is not fully prepared; recover it first")
            if not _all_intents_staged(root, transaction):
                raise ValueError("transaction staging is incomplete")
            if transaction["state"] == "PREPARED":
                committed = _append_journal(
                    root,
                    workspace,
                    scope,
                    workspace_key,
                    scope_key,
                    state,
                    "transaction_committed",
                    transaction_id,
                    {"bundle_hash": transaction["bundle_hash"]},
                )
                records.append(committed)
                state = reduce_transactions(records)
                transaction = state["transactions"][transaction_id]
            if fail_before_receipt and transaction["receipt"] is None:
                raise RuntimeError("injected_failure_before_receipt")
            if transaction["receipt"] is None:
                receipted = _append_receipt(
                    root, workspace, scope, workspace_key, scope_key, state, transaction
                )
                records.append(receipted)
                state = reduce_transactions(records)
                transaction = state["transactions"][transaction_id]
            return transaction, state


def abort_transaction(
    root,
    *,
    workspace,
    scope,
    workspace_key,
    scope_key,
    transaction_id,
    reason,
):
    if not str(reason).strip():
        raise ValueError("abort requires a reason")
    directory = transaction_directory(root, workspace_key, scope_key)
    with exclusive_file_lock(directory / ".transaction.lock"):
        records = load_transaction_records(root, workspace_key, scope_key)
        state = reduce_transactions(records)
        if state["issues"]:
            raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
        transaction = state["transactions"].get(transaction_id)
        if not transaction:
            raise ValueError("transaction not found")
        if transaction["state"] == "COMMITTED":
            raise ValueError("committed transaction cannot abort")
        if transaction["state"] != "ABORTED":
            aborted = _append_journal(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                "transaction_aborted",
                transaction_id,
                {"reason": str(reason).strip(), "bundle_hash": transaction["bundle_hash"]},
            )
            records.append(aborted)
            state = reduce_transactions(records)
            transaction = state["transactions"][transaction_id]
        if transaction["receipt"] is None:
            receipted = _append_receipt(
                root, workspace, scope, workspace_key, scope_key, state, transaction
            )
            records.append(receipted)
            state = reduce_transactions(records)
            transaction = state["transactions"][transaction_id]
        return transaction, state


def recover_transaction(
    root,
    *,
    workspace,
    scope,
    workspace_key,
    scope_key,
    transaction_id,
):
    directory = transaction_directory(root, workspace_key, scope_key)
    with staging_fence(root, workspace_key, scope_key, directory):
        records = load_transaction_records(root, workspace_key, scope_key)
        state = reduce_transactions(records)
        if state["issues"]:
            raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
        transaction = state["transactions"].get(transaction_id)
        if not transaction:
            raise ValueError("transaction not found")
        if transaction["state"] == "PREPARING":
            for intent in transaction["intents"]:
                _stage_intent(
                    root, workspace, scope, workspace_key, scope_key, transaction, intent
                )
            if not _all_intents_staged(root, transaction):
                raise RuntimeError("transaction recovery could not complete staging")
            prepared = _append_journal(
                root,
                workspace,
                scope,
                workspace_key,
                scope_key,
                state,
                "transaction_prepared",
                transaction_id,
                {"bundle_hash": transaction["bundle_hash"]},
            )
            records.append(prepared)
            state = reduce_transactions(records)
            transaction = state["transactions"][transaction_id]
        if transaction["state"] in TERMINAL_STATES and transaction["receipt"] is None:
            receipted = _append_receipt(
                root, workspace, scope, workspace_key, scope_key, state, transaction
            )
            records.append(receipted)
            state = reduce_transactions(records)
            transaction = state["transactions"][transaction_id]
        return transaction, state


_WARNED_INVALID_JOURNALS = set()


def _warn_journal_invalid(key, issues):
    """Surface a broken transaction journal once per process without wedging readers.

    A corrupt journal must not make every frame or ledger read in the
    workspace raise; pending envelopes are simply kept invisible, which is the
    same conservative treatment as an uncommitted transaction.
    """

    if key in _WARNED_INVALID_JOURNALS:
        return
    _WARNED_INVALID_JOURNALS.add(key)
    summary = issues[0] if issues else "unknown issue"
    sys.stderr.write(
        f"weilan warning: transaction journal for workspace_key={key[1]} scope_key={key[2]} "
        f"is invalid ({len(issues)} issue(s); first: {summary}); pending envelopes stay hidden "
        "until metabolic-recover repairs the journal\n"
    )


def resolve_pending_record(root, record, warnings=None, source_path=None):
    """Return payload for committed pending records, None while invisible."""

    if record.get("schema_version") != PENDING_SCHEMA_VERSION:
        return record
    warnings = warnings if warnings is not None else []
    key = (
        str(Path(root).resolve()),
        record.get("workspace_key", ""),
        record.get("scope_key", ""),
    )
    try:
        cache = _VISIBILITY_CACHE.get()
        state = cache.get(key) if cache is not None else None
        if state is None:
            records = load_transaction_records(root, key[1], key[2])
            state = reduce_transactions(records)
            if cache is not None:
                cache[key] = state
        if state["issues"]:
            _warn_journal_invalid(key, state["issues"])
            return None
        transaction = state["transactions"].get(record.get("transaction_id"))
        if not transaction or transaction["state"] != "COMMITTED":
            return None
        intent = next(
            (
                value
                for value in transaction["intents"]
                if value["intent_id"] == record.get("intent_id")
            ),
            None,
        )
        actual_relpath = None
        if source_path is not None:
            actual_relpath = Path(source_path).resolve().relative_to(Path(root).resolve()).as_posix()
        if (
            not intent
            or transaction["bundle_hash"] != record.get("bundle_hash")
            or intent["participant"] != record.get("participant")
            or intent["payload_hash"] != record.get("payload_hash")
            or stable_hash(record.get("payload")) != record.get("payload_hash")
            or (actual_relpath is not None and intent["target_relpath"] != actual_relpath)
        ):
            warnings.append("pending transaction envelope failed integrity validation")
            return None
        return record["payload"]
    except (OSError, ValueError, TypeError) as exc:
        _warn_journal_invalid(key, [str(exc)])
        return None
