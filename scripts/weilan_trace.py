"""Frame/Trace recorder and federated workspace memory for solve-with-weilan."""

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from runtime_core import (
    canonical_workspace,
    exclusive_file_lock,
    file_sha256,
    normalize_branch,
    normalize_scope,
    normalized_workspace,
    scope_key,
    state_root,
    utc_now,
    workspace_key,
    write_event_file_atomic,
    write_json_atomic,
)
from prospective import (
    EVENT_KINDS as PROSPECTIVE_EVENT_KINDS,
    GOAL_STATES as PROSPECTIVE_GOAL_STATES,
    SCHEMA_VERSION as PROSPECTIVE_SCHEMA_VERSION,
    event_matches as prospective_event_matches,
    normalize_condition as normalize_prospective_condition,
    plan_cycle as plan_prospective_cycle,
    reduce_events as reduce_prospective_events,
)

from governance import (
    EVENT_TYPES as GOVERNANCE_EVENT_TYPES,
    PRESSURE_KINDS,
    SCALE_RANK,
    SCALES as GOVERNANCE_SCALES,
    SCHEMA_VERSION as GOVERNANCE_SCHEMA_VERSION,
    STRENGTH_RANK,
    STRENGTHS as PRESSURE_STRENGTHS,
    TARGET_KINDS as GOVERNANCE_TARGET_KINDS,
    TERMINAL_TARGET_STATES,
    reduce_events as reduce_governance_events,
)
from metabolism import (
    DISPOSITIONS as METABOLIC_DISPOSITIONS,
    TRIGGER_KINDS as METABOLIC_TRIGGER_KINDS,
    build_contract as build_metabolic_contract,
    propose_transition as propose_metabolic_transition,
)
from transaction import (
    TRANSACTION_SCHEMA_VERSION,
    abort_transaction,
    commit_transaction,
    contract_fence,
    load_transaction_records,
    normalize_intents as normalize_transaction_intents,
    prepare_transaction,
    recover_transaction,
    reduce_transactions,
    reset_visibility_snapshot,
    resolve_pending_record,
    transaction_visibility_scope,
    workspace_contract_fence,
)
from transition_planner import (
    PLAN_SCHEMA_VERSION as TRANSITION_PLAN_SCHEMA_VERSION,
    build_transition_plan,
    normalize_request as normalize_transition_request,
    transition_request_hash,
)
from runner import (
    MANIFEST_SCHEMA_VERSION as RUNNER_MANIFEST_SCHEMA_VERSION,
    RUNNER_SCHEMA_VERSION,
    RunnerConflict,
    execute_run,
    load_run_records,
    normalize_manifest as normalize_runner_manifest,
    reduce_runs,
    runner_summary,
)


SCHEMA_VERSION = "weilan_method_event_v0.1"
CONTROL_SCHEMA_VERSION = "weilan_memory_control_v0.2"
PROJECTION_SCHEMA_VERSION = "weilan_workspace_projection_v0.2"
LEGACY_PROJECTION_SCHEMA_VERSION = "weilan_workspace_projection_v0.1"
SEMANTIC_SCHEMA_VERSION = "weilan_semantic_memory_v0.3"
INDEX_SCHEMA_VERSION = "weilan_semantic_index_v0.3"
LINEAGE_SCHEMA_VERSION = "weilan_frame_lineage_v0.4"
LINEAGE_HEAD_SCHEMA_VERSION = "weilan_frame_lineage_heads_v0.4"
EVIDENCE_SCHEMA_VERSION = "weilan_conversation_evidence_v0.5"
PROMOTION_SCHEMA_VERSION = "weilan_promotion_gate_v0.5"
EVIDENCE_LIFECYCLE_SCHEMA_VERSION = "weilan_evidence_lifecycle_v0.5.1"
PERSISTENCE_AUDIT_SCHEMA_VERSION = "weilan_persistence_audit_v0.5.1"
FRAME_REPAIR_SCHEMA_VERSION = "weilan_frame_repair_v0.1"
EPISODE_INDEX_SCHEMA_VERSION = "weilan_episode_index_v0.2"
SEMANTIC_DISPOSITION_SCHEMA_VERSION = "weilan_semantic_disposition_v0.3"
TRANSACTION_PARTICIPANTS = (
    "frame",
    "lineage",
    "governance",
    "evidence",
    "evidence_lifecycle",
    "persistence_audit",
)
CONTROL_STATES = ("active", "paused", "blocked", "closed")
ACTIVATION_STATES = ("ACTIVE", "PAUSED", "CONFIRM_REQUIRED", "STALE", "NO_CONTEXT")
MAX_PROJECTION_BYTES = 16 * 1024
MAX_SEMANTIC_ENTRY_BYTES = 16 * 1024
SEMANTIC_KINDS = ("decision", "constraint", "fact", "lesson", "procedure", "open_question")
CAUSAL_RELATIONS = ("root", "continue", "fork", "join")
EVIDENCE_SIGNALS = (
    "explicit_user_directive",
    "architectural_decision",
    "durable_constraint",
    "verified_result",
    "repeated_preference",
    "unresolved_risk",
    "casual_chat",
    "transient_status",
    "social_acknowledgement",
)
PERSISTABLE_EVIDENCE_SIGNALS = {
    "explicit_user_directive",
    "architectural_decision",
    "durable_constraint",
    "verified_result",
    "repeated_preference",
    "unresolved_risk",
}
PROMOTABLE_KINDS_BY_SIGNAL = {
    "explicit_user_directive": {"decision", "constraint", "procedure"},
    "architectural_decision": {"decision", "constraint", "lesson"},
    "durable_constraint": {"constraint"},
    "verified_result": {"fact", "lesson"},
    "repeated_preference": {"constraint", "lesson"},
    "unresolved_risk": {"open_question"},
}
MAX_EVIDENCE_BYTES = 4 * 1024
EVIDENCE_TERMINAL_STATES = ("withdrawn", "expired", "superseded")
SEMANTIC_DISPOSITIONS = ("active", "dormant", "retired")
PERSISTENCE_AUDIT_TRIGGERS = ("round_end", "route_change", "version_switch")
ALLOWED_LEVELS = ("L2", "L3")
ALLOWED_EVENTS = {
    "frame_opened",
    "candidate_admitted",
    "holder_selected",
    "action_started",
    "evidence_observed",
    "holder_warned",
    "holder_probation_started",
    "discriminating_test_executed",
    "minimal_unit_collapsed",
    "trace_emitted",
    "candidates_regrouped",
    "route_reentered",
    "frame_blocked",
    "frame_closed",
}
REQUIRED_DATA_FIELDS = {
    "frame_opened": {"problem", "success_criteria", "budget"},
    "holder_selected": {
        "why_reasonable",
        "next_expected_evidence",
        "death_line",
    },
    "minimal_unit_collapsed": {
        "scope",
        "former_holder",
        "invalidating_evidence",
    },
    "trace_emitted": {
        "once_reasonable",
        "invalidating_evidence",
        "reusable_results",
        "forbidden_assumption",
        "reentry_condition",
    },
    "frame_closed": {"outcome", "verdict"},
}


def parse_fields(items):
    data = {}
    for item in items or ():
        if "=" not in item:
            raise ValueError("--field must use key=value")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("--field key cannot be empty")
        if key in data:
            if not isinstance(data[key], list):
                data[key] = [data[key]]
            data[key].append(value)
        else:
            data[key] = value
    return data


def find_frame(frame_id):
    candidates = list((state_root() / "frames").glob(f"*/{frame_id}.jsonl"))
    matches = [path for path in candidates if read_events(path)]
    if not matches:
        raise FileNotFoundError(f"frame not found: {frame_id}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple frame files found: {frame_id}")
    return matches[0]


def frame_repair_path(frame_id):
    return state_root() / "memory" / "frame-repairs" / f"{frame_id}.jsonl"


def read_events_raw(path):
    events = []
    warnings = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                resolved = resolve_pending_record(
                    state_root(), record, warnings, source_path=path
                )
                if resolved is not None:
                    events.append(resolved)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {number}: {exc}") from exc
    if warnings:
        raise ValueError("invalid pending transaction envelope: " + "; ".join(warnings))
    return events


def load_frame_repairs(frame_id, warnings=None):
    warnings = warnings if warnings is not None else []
    path = frame_repair_path(frame_id)
    repairs = []
    if not path.exists():
        return repairs
    for record in read_jsonl_records(path, warnings):
        if record.get("schema_version") != FRAME_REPAIR_SCHEMA_VERSION:
            warnings.append(f"{path.name}: unsupported frame repair schema")
            continue
        if record.get("frame_id") != frame_id:
            warnings.append(f"{path.name}: frame repair id mismatch")
            continue
        repairs.append(record)
    return repairs


def apply_frame_repairs(events, repairs, warnings=None):
    warnings = warnings if warnings is not None else []
    repaired = [
        {
            **event,
            "data": dict(event.get("data", {})),
        }
        for event in events
    ]
    by_event_id = {event.get("event_id"): index for index, event in enumerate(repaired)}
    for repair in repairs:
        target = repair.get("target_event_id")
        if target not in by_event_id:
            warnings.append(f"{repair.get('repair_id')}: target event not found")
            continue
        overlay = repair.get("data_overlay")
        if not isinstance(overlay, dict):
            warnings.append(f"{repair.get('repair_id')}: data_overlay must be an object")
            continue
        index = by_event_id[target]
        repaired[index]["data"].update(overlay)
    return repaired


def read_events(path):
    events = read_events_raw(path)
    if not events:
        return events
    warnings = []
    repairs = load_frame_repairs(events[0].get("frame_id"), warnings)
    repaired = apply_frame_repairs(events, repairs, warnings)
    if warnings:
        raise ValueError("invalid frame repair ledger: " + "; ".join(warnings))
    return repaired


def append_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def make_event(frame_id, event_type, level, workspace, data):
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "frame_id": frame_id,
        "timestamp_utc": utc_now(),
        "event_type": event_type,
        "level": level,
        "workspace": workspace,
        "data": data,
    }


def lineage_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "lineage"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def lineage_paths(workspace, scope):
    root = lineage_directory(workspace, scope)
    return sorted(root.glob("*.jsonl")) if root.exists() else []


def load_lineage_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    records = []
    for path in lineage_paths(workspace, scope):
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != LINEAGE_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported lineage schema")
                continue
            if normalized_workspace(record.get("workspace", "")) != normalized_workspace(workspace):
                warnings.append(f"{path.name}: lineage workspace mismatch")
                continue
            if normalize_scope(record.get("scope")).casefold() != normalize_scope(scope).casefold():
                warnings.append(f"{path.name}: lineage scope mismatch")
                continue
            records.append(record)
    return records


def derive_lineage_state(records):
    branches = {}
    frames = {}
    issues = []
    for index, record in enumerate(records, 1):
        label = f"record {index}"
        frame_id = record.get("frame_id")
        branch = record.get("branch_id")
        relation = record.get("relation")
        parents = record.get("parent_frame_ids")
        if not frame_id or not branch or relation not in CAUSAL_RELATIONS or not isinstance(parents, list):
            issues.append(f"{label}: missing or invalid lineage fields")
            continue
        if frame_id in frames:
            issues.append(f"{label}: duplicate frame_id {frame_id}")
            continue
        if len(parents) != len(set(parents)):
            issues.append(f"{label}: duplicate parent frame ids")
            continue
        if frame_id in parents:
            issues.append(f"{label}: frame cannot parent itself")
            continue

        transition_errors = []
        joined_branches = []
        if relation == "root":
            if records[: index - 1] or branches:
                transition_errors.append("root is allowed only as the first lineage record")
            if parents:
                transition_errors.append("root cannot have parents")
            if branch in branches:
                transition_errors.append("root branch already exists")
        elif relation == "continue":
            if len(parents) != 1:
                transition_errors.append("continue requires exactly one parent")
            if not branches:
                if branch in branches:
                    transition_errors.append("bootstrap branch already exists")
            else:
                branch_state = branches.get(branch)
                if not branch_state:
                    transition_errors.append("continue requires an existing branch")
                elif branch_state["status"] != "active":
                    transition_errors.append("joined branch cannot continue")
                elif parents != [branch_state["head_frame_id"]]:
                    transition_errors.append("continue parent must equal the current branch head")
        elif relation == "fork":
            if len(parents) != 1:
                transition_errors.append("fork requires exactly one parent")
            if branch in branches:
                transition_errors.append("fork target branch already exists")
            matching = [
                branch_id
                for branch_id, state in branches.items()
                if state["status"] == "active" and parents == [state["head_frame_id"]]
            ]
            if len(matching) != 1:
                transition_errors.append("fork parent must be one active branch head")
        elif relation == "join":
            if len(parents) < 2:
                transition_errors.append("join requires at least two parents")
            target = branches.get(branch)
            if not target or target["status"] != "active":
                transition_errors.append("join target must be an active branch")
            parent_branches = {
                branch_id: state["head_frame_id"]
                for branch_id, state in branches.items()
                if state["status"] == "active" and state["head_frame_id"] in parents
            }
            if set(parent_branches.values()) != set(parents):
                transition_errors.append("every join parent must be a distinct active branch head")
            if branch not in parent_branches:
                transition_errors.append("join parents must include the target branch head")
            joined_branches = sorted(item for item in parent_branches if item != branch)
            recorded_joined = sorted(record.get("joined_branch_ids", []))
            if recorded_joined != joined_branches:
                transition_errors.append("joined_branch_ids do not match join parents")

        if transition_errors:
            issues.extend(f"{label}: {message}" for message in transition_errors)
            continue

        frames[frame_id] = {
            "branch_id": branch,
            "relation": relation,
            "parent_frame_ids": parents,
            "event_id": record.get("event_id"),
        }
        if relation == "join":
            for joined in joined_branches:
                branches[joined] = {
                    **branches[joined],
                    "status": "joined",
                    "joined_into": branch,
                    "joined_by_frame_id": frame_id,
                }
        branches[branch] = {
            "head_frame_id": frame_id,
            "status": "active",
            "joined_into": None,
        }
    return {"frames": frames, "branches": branches, "issues": issues}


def lineage_heads_path(workspace, scope):
    return (
        state_root()
        / "memory"
        / "lineage"
        / "heads"
        / workspace_key(workspace)
        / f"{scope_key(scope)}.json"
    )


def write_lineage_heads(workspace, scope, records, state):
    value = {
        "schema_version": LINEAGE_HEAD_SCHEMA_VERSION,
        "authority": "derived_branch_heads_never_authorize_continuation",
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "generated_at_utc": utc_now(),
        "record_count": len(records),
        "head_event_id": records[-1].get("event_id") if records else None,
        "branches": state["branches"],
    }
    write_json_atomic(lineage_heads_path(workspace, scope), value)
    return value


def assert_closed_parent(frame_id, workspace, scope):
    path = find_frame(frame_id)
    events = read_events(path)
    if not events or events[-1].get("event_type") != "frame_closed":
        raise ValueError(f"causal parent must be closed: {frame_id}")
    validation_errors = validate_events(events, require_closed=True)
    if validation_errors:
        raise ValueError(
            f"causal parent frame is invalid: {frame_id}: {'; '.join(validation_errors)}"
        )
    if normalized_workspace(events[0].get("workspace", "")) != normalized_workspace(workspace):
        raise ValueError(f"causal parent belongs to another workspace: {frame_id}")
    causal = events[0].get("data", {}).get("causal")
    if causal and normalize_scope(causal.get("scope")).casefold() != scope.casefold():
        raise ValueError(f"causal parent belongs to another scope: {frame_id}")
    return events


def command_open_lineaged(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    with contract_fence(
        state_root(), workspace_key(workspace), scope_key(scope)
    ):
        return command_open_lineaged_fenced(args, workspace, scope)


def command_open_lineaged_fenced(args, workspace, scope):
    branch = normalize_branch(args.branch)
    relation = args.relation
    parents = list(dict.fromkeys(args.parent))
    if len(parents) != len(args.parent):
        raise ValueError("duplicate --parent values are not allowed")
    lock_path = lineage_directory(workspace, scope) / ".lineage.lock"
    with exclusive_file_lock(lock_path):
        warnings = []
        records = load_lineage_records(workspace, scope, warnings)
        current = derive_lineage_state(records)
        if warnings or current["issues"]:
            details = warnings + current["issues"]
            raise ValueError("lineage ledger is invalid: " + "; ".join(details))
        for parent in parents:
            assert_closed_parent(parent, workspace, scope)

        branches = current["branches"]
        joined_branches = []
        if relation == "root":
            if records or parents:
                raise ValueError("root requires an empty lineage and no parents")
        elif relation == "continue":
            if len(parents) != 1:
                raise ValueError("continue requires exactly one parent")
            if records:
                state = branches.get(branch)
                if not state:
                    raise ValueError("continue requires an existing branch")
                if state["status"] != "active":
                    raise ValueError("joined branch cannot continue")
                if parents != [state["head_frame_id"]]:
                    raise ValueError(
                        f"branch head conflict: expected {state['head_frame_id']}, received {parents[0]}"
                    )
        elif relation == "fork":
            if len(parents) != 1:
                raise ValueError("fork requires exactly one parent")
            if branch in branches:
                raise ValueError("fork target branch already exists")
            matches = [
                branch_id
                for branch_id, state in branches.items()
                if state["status"] == "active" and parents == [state["head_frame_id"]]
            ]
            if len(matches) != 1:
                raise ValueError("fork parent must be one active branch head")
        elif relation == "join":
            if len(parents) < 2:
                raise ValueError("join requires at least two parents")
            target = branches.get(branch)
            if not target or target["status"] != "active":
                raise ValueError("join target must be an active branch")
            parent_branches = {
                branch_id: state["head_frame_id"]
                for branch_id, state in branches.items()
                if state["status"] == "active" and state["head_frame_id"] in parents
            }
            if set(parent_branches.values()) != set(parents):
                raise ValueError("every join parent must be a distinct active branch head")
            if branch not in parent_branches:
                raise ValueError("join parents must include the target branch head")
            joined_branches = sorted(item for item in parent_branches if item != branch)
        else:
            raise ValueError(f"unsupported causal relation: {relation}")

        frame_id = "wf-{}-{}".format(
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
            uuid.uuid4().hex[:6],
        )
        lineage_event_id = str(uuid.uuid4())
        causal = {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "lineage_event_id": lineage_event_id,
            "scope": scope,
            "branch_id": branch,
            "relation": relation,
            "parent_frame_ids": parents,
            "joined_branch_ids": joined_branches,
        }
        controls, _ = load_control_records()
        workspace_control = latest_control(controls, workspace, "workspace")
        scoped_control = latest_control(controls, workspace, scope)
        data = {
            "problem": args.problem,
            "success_criteria": args.success,
            "budget": args.budget or "proportional",
            "causal": causal,
            "persistence_audit_required": True,
            "control_heads_at_open": {
                "workspace": workspace_control.get("event_id") if workspace_control else None,
                "scope": scoped_control.get("event_id") if scoped_control else None,
            },
            "scoped_control_count_at_open": len(
                [
                    record
                    for record in controls
                    if normalized_workspace(record.get("workspace", ""))
                    == normalized_workspace(workspace)
                    and normalize_scope(record.get("scope")).casefold() == scope.casefold()
                ]
            ),
        }
        frame_event = make_event(frame_id, "frame_opened", args.level, workspace, data)
        date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        frame_path = state_root() / "frames" / date_dir / f"{frame_id}.jsonl"
        lineage_path = lineage_directory(workspace, scope) / f"{date_dir}.jsonl"
        lineage_record = {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "event_id": lineage_event_id,
            "timestamp_utc": utc_now(),
            "workspace": workspace,
            "workspace_key": workspace_key(workspace),
            "scope": scope,
            "scope_key": scope_key(scope),
            "frame_id": frame_id,
            "branch_id": branch,
            "relation": relation,
            "parent_frame_ids": parents,
            "joined_branch_ids": joined_branches,
        }
        try:
            write_event_file_atomic(frame_path, frame_event)
            append_event(lineage_path, lineage_record)
        except Exception:
            if frame_path.exists():
                frame_path.unlink()
            raise
        updated_records = records + [lineage_record]
        updated_state = derive_lineage_state(updated_records)
        if updated_state["issues"]:
            raise RuntimeError("created invalid lineage transition: " + "; ".join(updated_state["issues"]))
        heads = write_lineage_heads(workspace, scope, updated_records, updated_state)
        print(
            json.dumps(
                {
                    "frame_id": frame_id,
                    "path": str(frame_path),
                    "causal": causal,
                    "branch_heads_path": str(lineage_heads_path(workspace, scope)),
                    "branch_heads": heads["branches"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def command_lineage_show(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    records = load_lineage_records(workspace, scope, warnings)
    state = derive_lineage_state(records)
    issues = list(warnings) + list(state["issues"])
    for record in records:
        frame_id = record.get("frame_id")
        try:
            events = read_events(find_frame(frame_id))
        except (OSError, ValueError, RuntimeError) as exc:
            issues.append(f"frame {frame_id}: {exc}")
            continue
        causal = events[0].get("data", {}).get("causal", {}) if events else {}
        if causal.get("lineage_event_id") != record.get("event_id"):
            issues.append(f"frame {frame_id}: lineage event id mismatch")
        if causal.get("parent_frame_ids") != record.get("parent_frame_ids"):
            issues.append(f"frame {frame_id}: parent list mismatch")
    result = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "workspace": workspace,
        "scope": scope,
        "valid": not issues,
        "record_count": len(records),
        "branches": state["branches"],
        "frames": state["frames"],
        "issues": issues,
        "authority": "causal_evidence_and_branch_governance_never_activation_authority",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if issues else 0


def command_open(args):
    if args.scope is not None:
        return command_open_lineaged(args)
    frame_id = "wf-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        uuid.uuid4().hex[:6],
    )
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = state_root() / "frames" / date_dir / f"{frame_id}.jsonl"
    data = {
        "problem": args.problem,
        "success_criteria": args.success,
        "budget": args.budget or "proportional",
    }
    event = make_event(frame_id, "frame_opened", args.level, args.workspace, data)
    append_event(path, event)
    print(json.dumps({"frame_id": frame_id, "path": str(path)}, ensure_ascii=False))


def frame_contract_identity(events):
    if not events:
        return None
    first = events[0]
    scope = first.get("data", {}).get("causal", {}).get("scope")
    workspace = canonical_workspace(first["workspace"])
    return workspace, normalize_scope(scope) if scope else None


def command_event(args):
    path = find_frame(args.frame_id)
    events = read_events(path)
    identity = frame_contract_identity(events)
    if identity:
        workspace, scope = identity
        fence = (
            contract_fence(
                state_root(), workspace_key(workspace), scope_key(scope)
            )
            if scope
            else workspace_contract_fence(state_root(), workspace_key(workspace))
        )
        with fence:
            return command_event_fenced(args, path)
    return command_event_fenced(args, path)


def command_event_fenced(args, path):
    events = read_events(path)
    if events and events[-1].get("event_type") == "frame_closed":
        raise ValueError("cannot append to a closed frame")
    first = events[0]
    data = parse_fields(args.field)
    event = make_event(
        args.frame_id,
        args.type,
        first["level"],
        first["workspace"],
        data,
    )
    field_errors = validate_event_required_fields(event, len(events) + 1)
    if field_errors:
        raise ValueError("invalid frame event: " + "; ".join(field_errors))
    append_event(path, event)
    print(json.dumps({"frame_id": args.frame_id, "event_type": args.type}, ensure_ascii=False))


def required_persistence_audit_triggers(events):
    if not events or not events[0].get("data", {}).get("persistence_audit_required"):
        return set()
    required = {"round_end"}
    event_types = {event.get("event_type") for event in events}
    if event_types.intersection({"minimal_unit_collapsed", "candidates_regrouped", "route_reentered"}):
        required.add("route_change")
    first = events[0]
    causal = first.get("data", {}).get("causal", {})
    scope = causal.get("scope")
    if scope:
        controls, _ = load_control_records()
        scoped_controls = [
            record
            for record in controls
            if normalized_workspace(record.get("workspace", ""))
            == normalized_workspace(first.get("workspace", ""))
            and normalize_scope(record.get("scope")).casefold()
            == normalize_scope(scope).casefold()
        ]
        opened_head = first.get("data", {}).get("control_heads_at_open", {}).get("scope")
        opened_count = first.get("data", {}).get("scoped_control_count_at_open")
        if isinstance(opened_count, int):
            later_controls = scoped_controls[opened_count:]
        elif opened_head:
            head_indexes = [
                index
                for index, record in enumerate(scoped_controls)
                if record.get("event_id") == opened_head
            ]
            later_controls = scoped_controls[head_indexes[-1] + 1 :] if head_indexes else scoped_controls
        else:
            later_controls = [
                record
                for record in scoped_controls
                if record.get("timestamp_utc", "") > first.get("timestamp_utc", "")
            ]
        closed_at = next(
            (
                event.get("timestamp_utc")
                for event in reversed(events)
                if event.get("event_type") == "frame_closed"
            ),
            None,
        )
        if closed_at:
            later_controls = [
                record
                for record in later_controls
                if record.get("timestamp_utc", "") <= closed_at
            ]
        for record in later_controls:
            reason = record.get("reason", "").casefold()
            if "version" in reason or "scope_redirection" in reason:
                required.add("version_switch")
    return required


def command_close(args):
    path = find_frame(args.frame_id)
    events = read_events(path)
    identity = frame_contract_identity(events)
    if identity:
        workspace, scope = identity
        fence = (
            contract_fence(
                state_root(), workspace_key(workspace), scope_key(scope)
            )
            if scope
            else workspace_contract_fence(state_root(), workspace_key(workspace))
        )
        with fence:
            return command_close_fenced(args, path)
    return command_close_fenced(args, path)


def command_close_fenced(args, path):
    events = read_events(path)
    if events and events[-1].get("event_type") == "frame_closed":
        raise ValueError("frame is already closed")
    first = events[0]
    required_audits = required_persistence_audit_triggers(events)
    if required_audits:
        scope = first.get("data", {}).get("causal", {}).get("scope")
        warnings = []
        audits = [
            record
            for record in load_persistence_audit_records(first["workspace"], scope, warnings)
            if record.get("frame_id") == args.frame_id
            and record.get("decision") in {"PROMOTED", "NOT_PERSISTED"}
        ]
        completed_triggers = {record.get("trigger") for record in audits}
        missing = sorted(required_audits - completed_triggers)
        if warnings:
            raise ValueError("persistence audit ledger is invalid: " + "; ".join(warnings))
        if missing:
            raise ValueError(
                "persistence audit required before frame close; missing triggers: "
                + ", ".join(missing)
            )
    data = {"outcome": args.outcome, "verdict": args.verdict}
    event = make_event(
        args.frame_id,
        "frame_closed",
        first["level"],
        first["workspace"],
        data,
    )
    validation_errors = validate_events(events + [event], require_closed=True)
    if validation_errors:
        raise ValueError(
            "frame validation failed before close: " + "; ".join(validation_errors)
        )
    append_event(path, event)
    print(json.dumps({"frame_id": args.frame_id, "closed": True}, ensure_ascii=False))


def validate_event_required_fields(event, index):
    errors = []
    data = event.get("data")
    if not isinstance(data, dict):
        return [f"line {index}: data must be an object"]
    required = REQUIRED_DATA_FIELDS.get(event.get("event_type"), set())
    missing = sorted(field for field in required if not data.get(field))
    if missing:
        errors.append(f"line {index}: missing data fields: {', '.join(missing)}")
    if event.get("event_type") == "holder_selected" and not (
        data.get("candidate_id") or data.get("holder")
    ):
        errors.append(f"line {index}: holder_selected requires candidate_id or holder")
    return errors


def validate_events(events, require_closed=False):
    errors = []
    if not events:
        return ["frame has no events"]
    types = [event.get("event_type") for event in events]
    if types[0] != "frame_opened":
        errors.append("first event must be frame_opened")
    if types.count("frame_opened") != 1:
        errors.append("frame_opened must be unique")
    if types.count("frame_closed") > 1:
        errors.append("frame_closed must be unique")
    if "frame_closed" in types and types[-1] != "frame_closed":
        errors.append("frame_closed must be last")
    if require_closed and types[-1] != "frame_closed":
        errors.append("frame must be closed")
    if "minimal_unit_collapsed" in types:
        collapse_index = types.index("minimal_unit_collapsed")
        trace_indices = [index for index, item in enumerate(types) if item == "trace_emitted"]
        if not trace_indices or max(trace_indices) < collapse_index:
            errors.append("collapse requires a following trace_emitted")
    for probation_index, item in enumerate(types):
        if item != "holder_probation_started":
            continue
        resolution_types = {
            "discriminating_test_executed",
            "minimal_unit_collapsed",
            "frame_blocked",
        }
        if not any(candidate in resolution_types for candidate in types[probation_index + 1 :]):
            errors.append("probation requires a following discriminating test, collapse, or block")
    frame_ids = {event.get("frame_id") for event in events}
    if len(frame_ids) != 1:
        errors.append("all events must share one frame_id")
    event_ids = [event.get("event_id") for event in events]
    if len(event_ids) != len(set(event_ids)):
        errors.append("event_id values must be unique")
    for index, event in enumerate(events, 1):
        if event.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"line {index}: unsupported schema_version")
        if event.get("event_type") not in ALLOWED_EVENTS:
            errors.append(f"line {index}: unsupported event_type")
        if event.get("level") not in ALLOWED_LEVELS:
            errors.append(f"line {index}: unsupported level")
        data = event.get("data")
        if not isinstance(data, dict):
            errors.append(f"line {index}: data must be an object")
            continue
        errors.extend(validate_event_required_fields(event, index))
        if event.get("event_type") == "frame_opened" and data.get("causal") is not None:
            causal = data.get("causal")
            if not isinstance(causal, dict):
                errors.append(f"line {index}: causal metadata must be an object")
            else:
                required_causal = {
                    "schema_version",
                    "lineage_event_id",
                    "scope",
                    "branch_id",
                    "relation",
                    "parent_frame_ids",
                    "joined_branch_ids",
                }
                missing_causal = sorted(field for field in required_causal if field not in causal)
                if missing_causal:
                    errors.append(
                        f"line {index}: missing causal fields: {', '.join(missing_causal)}"
                    )
                if causal.get("schema_version") != LINEAGE_SCHEMA_VERSION:
                    errors.append(f"line {index}: unsupported causal schema_version")
                if causal.get("relation") not in CAUSAL_RELATIONS:
                    errors.append(f"line {index}: unsupported causal relation")
                if not isinstance(causal.get("parent_frame_ids"), list):
                    errors.append(f"line {index}: parent_frame_ids must be a list")
    return errors


def command_validate(args):
    path = find_frame(args.frame_id)
    events = read_events(path)
    errors = validate_events(events, require_closed=args.require_closed)
    result = {
        "frame_id": args.frame_id,
        "valid": not errors,
        "event_count": len(events),
        "errors": errors,
        "path": str(path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        return 1
    return 0


def command_show(args):
    path = find_frame(args.frame_id)
    print(json.dumps(read_events(path), ensure_ascii=False, indent=2))


def command_frame_repair(args):
    path = find_frame(args.frame_id)
    raw_events = read_events_raw(path)
    target = next(
        (event for event in raw_events if event.get("event_id") == args.target_event_id),
        None,
    )
    if target is None:
        raise ValueError(f"target event not found: {args.target_event_id}")
    repair = {
        "schema_version": FRAME_REPAIR_SCHEMA_VERSION,
        "repair_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "frame_id": args.frame_id,
        "target_event_id": args.target_event_id,
        "reason": args.reason,
        "data_overlay": parse_fields(args.field),
    }
    warnings = []
    existing_repairs = load_frame_repairs(args.frame_id, warnings)
    repaired = apply_frame_repairs(raw_events, existing_repairs + [repair], warnings)
    if warnings:
        raise ValueError("invalid frame repair proposal: " + "; ".join(warnings))
    validation_errors = validate_events(repaired, require_closed=args.require_closed)
    if validation_errors:
        raise ValueError(
            "frame repair does not produce a valid frame: " + "; ".join(validation_errors)
        )
    path = frame_repair_path(args.frame_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(repair, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(
        json.dumps(
            {
                "frame_id": args.frame_id,
                "repair_id": repair["repair_id"],
                "target_event_id": args.target_event_id,
                "path": str(path),
            },
            ensure_ascii=False,
        )
    )


def is_workspace_match(requested, candidate):
    requested_normalized = normalized_workspace(requested)
    candidate_normalized = normalized_workspace(candidate)
    if requested_normalized == candidate_normalized:
        return True
    prefix = candidate_normalized.rstrip("\\/") + os.sep
    return requested_normalized.startswith(prefix)


def read_jsonl_records(path, warnings):
    records = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    resolved = resolve_pending_record(
                        state_root(), record, warnings, source_path=path
                    )
                    if resolved is not None:
                        records.append(resolved)
                except json.JSONDecodeError as exc:
                    warnings.append(f"{path.name}:{number}: {exc}")
    except OSError as exc:
        warnings.append(f"{path.name}: {exc}")
    return records


def load_control_records():
    root = state_root() / "memory" / "control" / "workspaces"
    records = []
    warnings = []
    if root.exists():
        for path in sorted(root.glob("*/*.jsonl")):
            for record in read_jsonl_records(path, warnings):
                if record.get("schema_version") == CONTROL_SCHEMA_VERSION:
                    records.append(record)
    return records, warnings


def load_projection_records():
    root = state_root() / "memory" / "projections" / "workspaces"
    records = []
    warnings = []
    if root.exists():
        for path in sorted(root.rglob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    projection = json.load(handle)
                if projection.get("schema_version") in {
                    PROJECTION_SCHEMA_VERSION,
                    LEGACY_PROJECTION_SCHEMA_VERSION,
                }:
                    records.append(projection)
            except (OSError, ValueError, TypeError) as exc:
                warnings.append(f"{path.name}: {exc}")
    return records, warnings


def record_timestamp(record):
    return record.get("timestamp_utc") or record.get("generated_at_utc") or record.get("updated_at_utc") or ""


def latest_record(records):
    if not records:
        return None
    return max(
        enumerate(records),
        key=lambda item: (record_timestamp(item[1]), item[0]),
    )[1]


def latest_control(records, workspace, scope):
    normalized = normalized_workspace(workspace)
    wanted_scope = normalize_scope(scope).casefold()
    matches = [
        record
        for record in records
        if normalized_workspace(record.get("workspace", "")) == normalized
        and normalize_scope(record.get("scope")).casefold() == wanted_scope
    ]
    return latest_record(matches)


def evidence_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "evidence"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def promotion_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "evidence"
        / "promotions"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def evidence_lifecycle_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "evidence"
        / "lifecycle"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def persistence_audit_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "audits"
        / "persistence"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def load_evidence_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = evidence_directory(workspace, scope)
    records = []
    for path in sorted(root.glob("*.jsonl")) if root.exists() else []:
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported evidence schema")
                continue
            if normalized_workspace(record.get("workspace", "")) != normalized_workspace(workspace):
                warnings.append(f"{path.name}: evidence workspace mismatch")
                continue
            if normalize_scope(record.get("scope")).casefold() != normalize_scope(scope).casefold():
                warnings.append(f"{path.name}: evidence scope mismatch")
                continue
            records.append(record)
    return records


def load_promotion_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = promotion_directory(workspace, scope)
    records = []
    for path in sorted(root.glob("*.jsonl")) if root.exists() else []:
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != PROMOTION_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported promotion schema")
                continue
            records.append(record)
    return records


def load_evidence_lifecycle_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = evidence_lifecycle_directory(workspace, scope)
    records = []
    for path in sorted(root.glob("*.jsonl")) if root.exists() else []:
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != EVIDENCE_LIFECYCLE_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported evidence lifecycle schema")
                continue
            records.append(record)
    return records


def load_persistence_audit_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = persistence_audit_directory(workspace, scope)
    records = []
    for path in sorted(root.glob("*.jsonl")) if root.exists() else []:
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != PERSISTENCE_AUDIT_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported persistence audit schema")
                continue
            records.append(record)
    return records


def evidence_lifecycle_head(evidence, warnings=None):
    warnings = warnings if warnings is not None else []
    workspace = evidence["workspace"]
    scope = evidence["scope"]
    evidence_id = evidence["evidence_id"]
    lifecycle = [
        record
        for record in load_evidence_lifecycle_records(workspace, scope, warnings)
        if record.get("evidence_id") == evidence_id
    ]
    if lifecycle:
        latest = lifecycle[-1]
        return {
            "state": latest.get("state", "unknown").upper(),
            "head_event_id": latest.get("event_id"),
            "replacement_evidence_id": latest.get("replacement_evidence_id"),
        }
    return {
        "state": "ACTIVE",
        "head_event_id": None,
        "replacement_evidence_id": None,
    }


def evidence_state(evidence, warnings=None):
    warnings = warnings if warnings is not None else []
    workspace = evidence["workspace"]
    scope = evidence["scope"]
    evidence_id = evidence["evidence_id"]
    lifecycle = evidence_lifecycle_head(evidence, warnings)
    if lifecycle["state"] != "ACTIVE":
        return lifecycle
    promoted = [
        record
        for record in load_promotion_records(workspace, scope, warnings)
        if record.get("evidence_id") == evidence_id and record.get("decision") == "PROMOTED"
    ]
    if promoted:
        return {
            "state": "PROMOTED",
            "head_event_id": promoted[-1].get("promotion_id"),
            "replacement_evidence_id": None,
        }
    return {
        "state": "CANDIDATE",
        "head_event_id": evidence.get("evidence_id"),
        "replacement_evidence_id": None,
    }


def find_evidence(evidence_id):
    root = state_root() / "memory" / "evidence" / "workspaces"
    matches = []
    if root.exists():
        for path in root.glob("*/*/*.jsonl"):
            for record in read_jsonl_records(path, []):
                if (
                    record.get("schema_version") == EVIDENCE_SCHEMA_VERSION
                    and record.get("evidence_id") == evidence_id
                ):
                    matches.append(record)
    if not matches:
        raise FileNotFoundError(f"evidence not found: {evidence_id}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple evidence records found: {evidence_id}")
    return matches[0]


def parse_conversation_source(value):
    if not value.startswith("conversation:"):
        return None
    locator = value.split(":", 1)[1]
    if "#" not in locator:
        return None
    thread_id, turn_id = (part.strip() for part in locator.split("#", 1))
    if not thread_id or not turn_id:
        return None
    return thread_id, turn_id


def valid_conversation_source(value):
    return parse_conversation_source(value) is not None


def codex_sessions_home():
    explicit = os.environ.get("WEILAN_CODEX_SESSIONS_HOME")
    if explicit:
        return Path(explicit)
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "sessions"
    return Path.home() / ".codex" / "sessions"


def conversation_session_candidates(root, thread_id):
    if not root.exists():
        return []
    candidates = sorted(root.rglob(f"*{thread_id}*.jsonl"))
    if candidates:
        return candidates
    return sorted(root.rglob("*.jsonl"))


def append_text_parts(parts, value):
    if isinstance(value, str) and value:
        parts.append(value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                append_text_parts(parts, item)
            elif isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                append_text_parts(parts, item.get("text"))


def public_message_text(message):
    parts = []
    if isinstance(message, str):
        append_text_parts(parts, message)
    elif isinstance(message, dict):
        append_text_parts(parts, message.get("content"))
    return "\n".join(parts)


def codex_turn_messages(path, thread_id, turn_id):
    messages = []
    current_turn = None
    session_id = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            payload = record.get("payload", {})
            record_type = record.get("type")
            if record_type == "session_meta":
                session_id = payload.get("id") or payload.get("session_id")
            if record_type == "turn_context" and payload.get("turn_id"):
                current_turn = payload["turn_id"]
            if record_type == "event_msg" and payload.get("type") == "task_started":
                current_turn = payload.get("turn_id") or current_turn
            if record_type != "event_msg" or current_turn != turn_id:
                continue
            event_type = payload.get("type")
            if event_type == "user_message" and payload.get("message"):
                messages.append({"role": "user", "message": payload["message"]})
            elif event_type == "agent_message" and payload.get("message"):
                messages.append({"role": "assistant", "message": payload["message"]})
    return messages if session_id == thread_id else []


def claude_turn_messages(path, thread_id, turn_id):
    messages = []
    active = False
    session_id = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            session_id = record.get("sessionId") or session_id
            record_type = record.get("type")
            if record_type not in {"user", "assistant"}:
                continue
            message = record.get("message")
            text = public_message_text(message)
            record_id = record.get("uuid")
            if isinstance(message, dict):
                record_id = record_id or message.get("id")
            if not active and record_id != turn_id:
                continue
            if not active:
                active = True
            elif record_type == "user" and record_id != turn_id and text:
                break
            if record_type == "user" and not text:
                continue
            if text:
                messages.append({"role": record_type, "message": text})
    return messages if session_id == thread_id else []


def conversation_turn_snapshot(source):
    parsed = parse_conversation_source(source)
    if not parsed:
        return {"kind": "conversation_turn", "ref": source, "exists": False, "reason": "invalid_locator"}
    thread_id, turn_id = parsed
    root = codex_sessions_home()
    candidates = conversation_session_candidates(root, thread_id)
    matches = []
    for path in candidates:
        try:
            codex_messages = codex_turn_messages(path, thread_id, turn_id)
            if codex_messages:
                matches.append((path, codex_messages, "codex_jsonl"))
                continue
            claude_messages = claude_turn_messages(path, thread_id, turn_id)
            if claude_messages:
                matches.append((path, claude_messages, "claude_transcript"))
        except (OSError, ValueError, TypeError):
            continue
    if len(matches) != 1:
        if os.environ.get("WEILAN_ALLOW_UNRESOLVED_CONVERSATION") == "1":
            return {"kind": "opaque", "ref": source}
        return {
            "kind": "conversation_turn",
            "ref": source,
            "exists": False,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "reason": "not_found" if not matches else "ambiguous",
        }
    path, messages, session_format = matches[0]
    content_hash = hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "kind": "conversation_turn",
        "ref": source,
        "exists": True,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "message_count": len(messages),
        "content_hash": content_hash,
        "session_file": str(path.relative_to(root)),
        "session_format": session_format,
    }


def sensitive_material_reason(value):
    patterns = (
        (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private_key_material"),
        (r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+", "credential_like_material"),
        (r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b", "token_like_material"),
    )
    for pattern, reason in patterns:
        if re.search(pattern, value):
            return reason
    return None


def source_snapshot(source, workspace):
    if source.startswith("conversation:"):
        return conversation_turn_snapshot(source)
    if source.startswith("evidence:"):
        evidence_id = source.split(":", 1)[1]
        try:
            record = find_evidence(evidence_id)
        except (OSError, ValueError, RuntimeError):
            return {"kind": "conversation_evidence", "ref": source, "exists": False}
        content_hash = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        lifecycle = evidence_lifecycle_head(record)
        return {
            "kind": "conversation_evidence",
            "ref": source,
            "exists": True,
            "evidence_id": evidence_id,
            "content_hash": content_hash,
            "timestamp_utc": record.get("timestamp_utc"),
            "lifecycle_state": lifecycle["state"],
            "lifecycle_head_event_id": lifecycle["head_event_id"],
        }
    if source.startswith("frame:"):
        frame_id = source.split(":", 1)[1]
        try:
            path = find_frame(frame_id)
            events = read_events(path)
            last = events[-1] if events else {}
            return {
                "kind": "frame",
                "ref": source,
                "exists": True,
                "head_event_id": last.get("event_id"),
                "head_timestamp_utc": last.get("timestamp_utc"),
            }
        except (OSError, ValueError, RuntimeError):
            return {"kind": "frame", "ref": source, "exists": False}
    if source.startswith("memory:"):
        memory_id = source.split(":", 1)[1]
        try:
            record = find_semantic_memory(memory_id)
        except (OSError, ValueError, RuntimeError):
            return {"kind": "semantic_memory", "ref": source, "exists": False}
        return {
            "kind": "semantic_memory",
            "ref": source,
            "exists": True,
            "memory_id": memory_id,
            "content_hash": hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
    if ":" in source and not Path(source).is_absolute():
        return {"kind": "opaque", "ref": source}
    candidate = Path(os.path.expandvars(os.path.expanduser(source)))
    if not candidate.is_absolute():
        candidate = Path(workspace) / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        return {"kind": "file", "ref": source, "path": str(candidate), "exists": False}
    stat = candidate.stat()
    return {
        "kind": "file",
        "ref": source,
        "path": str(candidate),
        "exists": True,
        "size": stat.st_size,
        "sha256": file_sha256(candidate),
    }


def source_snapshots(sources, workspace):
    return [source_snapshot(source, workspace) for source in sources]


def semantic_paths(workspace, scope):
    root = (
        state_root()
        / "memory"
        / "semantic"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
    )
    return sorted(root.glob("*.jsonl")) if root.exists() else []


def semantic_source_state(paths):
    root = state_root()
    state = []
    for path in paths:
        stat = path.stat()
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = str(path)
        state.append(
            {
                "path": relative,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return state


def load_semantic_entries(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    entries = []
    for path in semantic_paths(workspace, scope):
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != SEMANTIC_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported semantic schema")
                continue
            if normalized_workspace(record.get("workspace", "")) != normalized_workspace(workspace):
                warnings.append(f"{path.name}: workspace mismatch")
                continue
            if normalize_scope(record.get("scope")).casefold() != normalize_scope(scope).casefold():
                warnings.append(f"{path.name}: scope mismatch")
                continue
            entries.append(record)
    return entries


def find_semantic_memory(memory_id):
    root = state_root() / "memory" / "semantic" / "workspaces"
    matches = []
    if root.exists():
        for path in root.glob("*/*/*.jsonl"):
            matches.extend(
                record
                for record in read_jsonl_records(path, [])
                if record.get("schema_version") == SEMANTIC_SCHEMA_VERSION
                and record.get("memory_id") == memory_id
            )
    if not matches:
        raise FileNotFoundError(f"semantic memory not found: {memory_id}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple semantic memories found: {memory_id}")
    return matches[0]


def semantic_disposition_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "semantic-dispositions"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def load_semantic_dispositions(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = semantic_disposition_directory(workspace, scope)
    records = []
    if root.exists():
        for path in sorted(root.glob("*.jsonl")):
            for record in read_jsonl_records(path, warnings):
                if record.get("schema_version") == SEMANTIC_DISPOSITION_SCHEMA_VERSION:
                    records.append(record)
                else:
                    warnings.append(f"{path.name}: unsupported semantic disposition schema")
    return records


def semantic_disposition_heads(workspace, scope, warnings=None):
    heads = {}
    for record in load_semantic_dispositions(workspace, scope, warnings):
        heads[record.get("memory_id")] = record
    return heads


def active_semantic_entries(entries, workspace=None, scope=None, warnings=None):
    superseded = {
        memory_id
        for entry in entries
        for memory_id in entry.get("supersedes", [])
    }
    dispositions = (
        semantic_disposition_heads(workspace, scope, warnings)
        if workspace is not None and scope is not None
        else {}
    )
    active = []
    for entry in entries:
        if entry.get("memory_id") in superseded:
            continue
        disposition = dispositions.get(entry.get("memory_id"), {}).get("state", "active")
        if disposition != "active":
            continue
        evidence_id = entry.get("promotion", {}).get("evidence_id")
        if evidence_id:
            try:
                evidence = find_evidence(evidence_id)
                if evidence_state(evidence)["state"] != "PROMOTED":
                    continue
            except (OSError, ValueError, RuntimeError):
                continue
        active.append(entry)
    return active


def semantic_evidence_dependency_state(entries):
    state = {}
    for entry in entries:
        evidence_id = entry.get("promotion", {}).get("evidence_id")
        if not evidence_id:
            continue
        try:
            evidence = find_evidence(evidence_id)
            lifecycle = evidence_state(evidence)
            state[evidence_id] = {
                "state": lifecycle["state"],
                "head_event_id": lifecycle["head_event_id"],
            }
        except (OSError, ValueError, RuntimeError):
            state[evidence_id] = {"state": "MISSING", "head_event_id": None}
    return dict(sorted(state.items()))


def semantic_tokens(value):
    tokens = set()
    for part in re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", value.casefold()):
        tokens.add(part)
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            tokens.update(part)
            if len(part) > 1:
                tokens.update(part[index : index + 2] for index in range(len(part) - 1))
    return sorted(tokens)


def semantic_index_path(workspace, scope):
    return (
        state_root()
        / "memory"
        / "indexes"
        / "workspaces"
        / workspace_key(workspace)
        / f"{scope_key(scope)}.json"
    )


def build_semantic_index_value(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    paths = semantic_paths(workspace, scope)
    all_entries = load_semantic_entries(workspace, scope, warnings)
    entries = active_semantic_entries(all_entries, workspace, scope, warnings)
    indexed_entries = {}
    terms = {}
    for entry in entries:
        memory_id = entry["memory_id"]
        indexed_entries[memory_id] = entry
        text = " ".join(
            [
                entry.get("kind", ""),
                entry.get("summary", ""),
                entry.get("detail", ""),
                " ".join(entry.get("tags", [])),
            ]
        )
        for token in semantic_tokens(text):
            terms.setdefault(token, []).append(memory_id)
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "authority": "derived_rebuildable_index",
        "workspace": canonical_workspace(workspace),
        "workspace_key": workspace_key(workspace),
        "scope": normalize_scope(scope),
        "scope_key": scope_key(scope),
        "source_state": semantic_source_state(paths),
        "evidence_dependency_state": semantic_evidence_dependency_state(all_entries),
        "disposition_state": {
            memory_id: record.get("event_id")
            for memory_id, record in semantic_disposition_heads(workspace, scope, warnings).items()
        },
        "active_entry_count": len(indexed_entries),
        "entries": indexed_entries,
        "terms": {token: sorted(ids) for token, ids in sorted(terms.items())},
    }


def rebuild_semantic_index(workspace, scope, warnings=None):
    index = build_semantic_index_value(workspace, scope, warnings)
    write_json_atomic(semantic_index_path(workspace, scope), index)
    return index


def load_semantic_index(workspace, scope):
    path = semantic_index_path(workspace, scope)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if index.get("schema_version") != INDEX_SCHEMA_VERSION:
        return None
    return index


def semantic_index_is_fresh(index, workspace, scope):
    if not index or index.get("source_state") != semantic_source_state(
        semantic_paths(workspace, scope)
    ):
        return False
    warnings = []
    entries = load_semantic_entries(workspace, scope, warnings)
    current_dispositions = {
        memory_id: record.get("event_id")
        for memory_id, record in semantic_disposition_heads(workspace, scope, warnings).items()
    }
    return (
        not warnings
        and index.get("evidence_dependency_state", {}) == semantic_evidence_dependency_state(entries)
        and index.get("disposition_state", {}) == current_dispositions
    )


def append_semantic_memory(
    workspace,
    scope,
    kind,
    summary,
    detail,
    tags,
    sources,
    supersedes,
    conflicts_with=None,
    promotion=None,
):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    if not summary.strip():
        raise ValueError("semantic summary cannot be empty")
    warnings = []
    existing = load_semantic_entries(workspace, scope, warnings)
    existing_ids = {entry.get("memory_id") for entry in existing}
    unknown = sorted(set(supersedes) - existing_ids)
    if unknown:
        raise ValueError(f"cannot supersede unknown memory ids: {', '.join(unknown)}")
    conflicts_with = conflicts_with or []
    unknown_conflicts = sorted(set(conflicts_with) - existing_ids)
    if unknown_conflicts:
        raise ValueError(
            f"cannot conflict with unknown memory ids: {', '.join(unknown_conflicts)}"
        )
    normalized_tags = sorted({tag.strip().casefold() for tag in tags if tag.strip()})
    unique_sources = list(dict.fromkeys(sources))
    entry = {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "memory_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "authority": "derived_semantic_memory_never_authorizes_continuation",
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "kind": kind,
        "summary": summary.strip(),
        "detail": detail.strip(),
        "tags": normalized_tags,
        "sources": unique_sources,
        "source_snapshots": source_snapshots(unique_sources, workspace),
        "supersedes": sorted(set(supersedes)),
        "conflicts_with": sorted(set(conflicts_with)),
    }
    if promotion:
        entry["promotion"] = promotion
    encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_SEMANTIC_ENTRY_BYTES:
        raise ValueError(f"semantic entry exceeds {MAX_SEMANTIC_ENTRY_BYTES} bytes")
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = (
        state_root()
        / "memory"
        / "semantic"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
        / f"{date_dir}.jsonl"
    )
    append_event(path, entry)
    index = rebuild_semantic_index(workspace, scope, warnings)
    result = {
        "saved": True,
        "memory_id": entry["memory_id"],
        "workspace": workspace,
        "scope": scope,
        "path": str(path),
        "index_path": str(semantic_index_path(workspace, scope)),
        "active_entry_count": index["active_entry_count"],
    }
    if warnings:
        result["warnings"] = warnings
    return result, entry


def command_memory_consolidate(args):
    prohibited = [
        source for source in args.source
        if source.startswith("conversation:") or source.startswith("evidence:")
    ]
    if prohibited:
        raise ValueError(
            "conversation-derived semantic memory must pass evidence-capture and evidence-promote"
        )
    result, _ = append_semantic_memory(
        args.workspace,
        args.scope,
        args.kind,
        args.summary,
        args.detail,
        args.tag,
        args.source,
        args.supersedes,
        args.conflicts_with,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_evidence_capture(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    claim = args.claim.strip()
    sources = list(dict.fromkeys(args.source))
    if args.signal not in PERSISTABLE_EVIDENCE_SIGNALS:
        print(
            json.dumps(
                {
                    "saved": False,
                    "decision": "REJECTED",
                    "reason_codes": ["non_durable_conversation_signal"],
                    "signal": args.signal,
                    "persistence": "none",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not claim:
        raise ValueError("--claim cannot be empty")
    conversation_sources = [source for source in sources if valid_conversation_source(source)]
    if not conversation_sources:
        raise ValueError(
            "conversation evidence requires --source conversation:<thread-id>#<exact-turn-id>"
        )
    if len(claim.splitlines()) > 6:
        print(
            json.dumps(
                {
                    "saved": False,
                    "decision": "REJECTED",
                    "reason_codes": ["claim_exceeds_six_line_fragment_boundary"],
                    "persistence": "none",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    sensitive_reason = sensitive_material_reason(
        "\n".join([claim, *sources, *args.tag])
    )
    if sensitive_reason:
        print(
            json.dumps(
                {
                    "saved": False,
                    "decision": "REJECTED",
                    "reason_codes": [sensitive_reason],
                    "persistence": "none",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    unresolved = [
        snapshot
        for snapshot in (conversation_turn_snapshot(source) for source in conversation_sources)
        if snapshot.get("kind") == "conversation_turn" and not snapshot.get("exists")
    ]
    if unresolved:
        reasons = sorted({snapshot.get("reason", "not_found") for snapshot in unresolved})
        raise ValueError(
            "conversation turn provenance is not resolvable: " + ", ".join(reasons)
        )
    evidence_id = str(uuid.uuid4())
    entry = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "timestamp_utc": utc_now(),
        "authority": "conversation_evidence_never_semantic_or_activation_authority",
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "signal": args.signal,
        "claim": claim,
        "claim_hash": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
        "tags": sorted({tag.strip().casefold() for tag in args.tag if tag.strip()}),
        "sources": sources,
        "source_snapshots": source_snapshots(sources, workspace),
        "privacy_scan": "passed_automated_pattern_check",
    }
    encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_EVIDENCE_BYTES:
        print(
            json.dumps(
                {
                    "saved": False,
                    "decision": "REJECTED",
                    "reason_codes": ["evidence_fragment_exceeds_4096_bytes"],
                    "persistence": "none",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = evidence_directory(workspace, scope) / f"{date_dir}.jsonl"
    append_event(path, entry)
    print(
        json.dumps(
            {
                "saved": True,
                "decision": "CAPTURED",
                "evidence_id": evidence_id,
                "workspace": workspace,
                "scope": scope,
                "signal": args.signal,
                "path": str(path),
                "semantic_memory_written": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def append_promotion_record(workspace, scope, record):
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = promotion_directory(workspace, scope) / f"{date_dir}.jsonl"
    append_event(path, record)
    return path


def command_evidence_promote(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    evidence_matches = [
        record
        for record in load_evidence_records(workspace, scope, warnings)
        if record.get("evidence_id") == args.evidence_id
    ]
    if len(evidence_matches) != 1:
        raise ValueError(f"expected one scoped evidence record, found {len(evidence_matches)}")
    evidence = evidence_matches[0]
    prior_promotions = [
        record
        for record in load_promotion_records(workspace, scope, warnings)
        if record.get("evidence_id") == args.evidence_id and record.get("decision") == "PROMOTED"
    ]
    prior_semantic_entries = [
        entry
        for entry in load_semantic_entries(workspace, scope, warnings)
        if entry.get("promotion", {}).get("evidence_id") == args.evidence_id
    ]
    if prior_promotions or prior_semantic_entries:
        raise ValueError(f"evidence is already promoted: {args.evidence_id}")
    current_evidence_state = evidence_state(evidence, warnings)
    if current_evidence_state["state"] != "CANDIDATE":
        raise ValueError(
            f"evidence is not promotable in state {current_evidence_state['state']}: {args.evidence_id}"
        )

    reasons = []
    if not args.stable:
        reasons.append("stability_not_confirmed")
    if not args.reusable:
        reasons.append("cross_task_reuse_not_confirmed")
    if not args.privacy_reviewed:
        reasons.append("privacy_review_not_confirmed")
    allowed_kinds = PROMOTABLE_KINDS_BY_SIGNAL.get(evidence.get("signal"), set())
    if args.kind not in allowed_kinds:
        reasons.append("semantic_kind_not_allowed_for_signal")
    if not args.summary.strip():
        reasons.append("semantic_summary_empty")
    if sensitive_material_reason(
        "\n".join([args.summary, args.detail, *args.tag, *args.source])
    ):
        reasons.append("semantic_content_failed_sensitive_material_check")
    current_sources = source_snapshots(evidence.get("sources", []), workspace)
    if current_sources != evidence.get("source_snapshots", []):
        reasons.append("evidence_sources_stale")
    claim_tokens = set(semantic_tokens(evidence.get("claim", "")))
    summary_tokens = set(semantic_tokens(args.summary))
    if not claim_tokens.intersection(summary_tokens):
        reasons.append("semantic_summary_not_grounded_in_evidence_claim")

    promotion_id = str(uuid.uuid4())
    now = utc_now()
    summary_hash = hashlib.sha256(args.summary.strip().encode("utf-8")).hexdigest()
    record = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "promotion_id": promotion_id,
        "timestamp_utc": now,
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "evidence_id": args.evidence_id,
        "requested_kind": args.kind,
        "summary_hash": summary_hash,
        "gate_checks": {
            "source_backed": bool(evidence.get("sources")),
            "sources_fresh": current_sources == evidence.get("source_snapshots", []),
            "stable": bool(args.stable),
            "reusable": bool(args.reusable),
            "privacy_reviewed": bool(args.privacy_reviewed),
            "signal_kind_allowed": args.kind in allowed_kinds,
            "claim_summary_overlap": bool(claim_tokens.intersection(summary_tokens)),
        },
        "reason_codes": reasons,
        "authority": "promotion_audit_never_activation_authority",
    }
    if reasons:
        record["decision"] = "REJECTED"
        path = append_promotion_record(workspace, scope, record)
        result = {
            "promoted": False,
            "decision": "REJECTED",
            "promotion_id": promotion_id,
            "evidence_id": args.evidence_id,
            "reason_codes": reasons,
            "semantic_memory_written": False,
            "audit_path": str(path),
        }
        if warnings:
            result["warnings"] = warnings
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    sources = [f"evidence:{args.evidence_id}", *evidence.get("sources", []), *args.source]
    semantic_result, semantic_entry = append_semantic_memory(
        workspace,
        scope,
        args.kind,
        args.summary,
        args.detail,
        args.tag,
        sources,
        args.supersedes,
        args.conflicts_with,
        promotion={
            "schema_version": PROMOTION_SCHEMA_VERSION,
            "promotion_id": promotion_id,
            "evidence_id": args.evidence_id,
        },
    )
    record["decision"] = "PROMOTED"
    record["semantic_memory_id"] = semantic_entry["memory_id"]
    path = append_promotion_record(workspace, scope, record)
    rebuilt_index = rebuild_semantic_index(workspace, scope, warnings)
    result = {
        "promoted": True,
        "decision": "PROMOTED",
        "promotion_id": promotion_id,
        "evidence_id": args.evidence_id,
        "semantic_memory_id": semantic_entry["memory_id"],
        "semantic_memory_written": True,
        "audit_path": str(path),
        "index_path": semantic_result["index_path"],
        "active_semantic_entry_count": rebuilt_index["active_entry_count"],
    }
    if warnings or semantic_result.get("warnings"):
        result["warnings"] = warnings + semantic_result.get("warnings", [])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_evidence_disposition(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    with contract_fence(
        state_root(), workspace_key(workspace), scope_key(scope)
    ):
        return command_evidence_disposition_fenced(args, workspace, scope)


def command_evidence_disposition_fenced(args, workspace, scope):
    warnings = []
    evidence_matches = [
        record
        for record in load_evidence_records(workspace, scope, warnings)
        if record.get("evidence_id") == args.evidence_id
    ]
    if len(evidence_matches) != 1:
        raise ValueError(f"expected one scoped evidence record, found {len(evidence_matches)}")
    evidence = evidence_matches[0]
    current = evidence_state(evidence, warnings)
    if current["state"] in {state.upper() for state in EVIDENCE_TERMINAL_STATES}:
        raise ValueError(f"evidence already has terminal state {current['state']}")
    reason = args.reason.strip()
    if not reason:
        raise ValueError("--reason cannot be empty")
    if len(reason) > 512:
        raise ValueError("--reason cannot exceed 512 characters")
    if sensitive_material_reason(reason):
        raise ValueError("--reason contains sensitive material")

    replacement = args.replacement_evidence_id
    if args.state == "superseded":
        if not replacement:
            raise ValueError("superseded evidence requires --replacement-evidence-id")
        if replacement == args.evidence_id:
            raise ValueError("evidence cannot supersede itself")
        replacement_matches = [
            record
            for record in load_evidence_records(workspace, scope, warnings)
            if record.get("evidence_id") == replacement
        ]
        if len(replacement_matches) != 1:
            raise ValueError("replacement evidence must exist in the same workspace and scope")
        replacement_state = evidence_state(replacement_matches[0], warnings)["state"]
        if replacement_state in {state.upper() for state in EVIDENCE_TERMINAL_STATES}:
            raise ValueError("replacement evidence must not be terminal")
    elif replacement:
        raise ValueError("--replacement-evidence-id is only valid for superseded state")

    event = {
        "schema_version": EVIDENCE_LIFECYCLE_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "evidence_id": args.evidence_id,
        "previous_state": current["state"],
        "state": args.state,
        "replacement_evidence_id": replacement,
        "reason": reason,
        "authority": "evidence_lifecycle_never_activation_authority",
    }
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = evidence_lifecycle_directory(workspace, scope) / f"{date_dir}.jsonl"
    append_event(path, event)
    index = rebuild_semantic_index(workspace, scope, warnings)
    result = {
        "saved": True,
        "evidence_id": args.evidence_id,
        "previous_state": current["state"],
        "state": args.state.upper(),
        "replacement_evidence_id": replacement,
        "history_deleted": False,
        "path": str(path),
        "active_semantic_entry_count": index["active_entry_count"],
    }
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_persistence_audit(args):
    frame_path = find_frame(args.frame_id)
    events = read_events(frame_path)
    if not events:
        raise ValueError("frame has no events")
    first = events[0]
    workspace = canonical_workspace(first["workspace"])
    scope = first.get("data", {}).get("causal", {}).get("scope") or "workspace"
    scope = normalize_scope(scope)
    with contract_fence(
        state_root(), workspace_key(workspace), scope_key(scope)
    ):
        return command_persistence_audit_fenced(
            args, frame_path, workspace, scope
        )


def command_persistence_audit_fenced(args, frame_path, workspace, scope):
    events = read_events(frame_path)
    if not events:
        raise ValueError("frame has no events")
    if events[-1].get("event_type") == "frame_closed":
        raise ValueError("cannot audit a closed frame")
    warnings = []
    existing = [
        record
        for record in load_persistence_audit_records(workspace, scope, warnings)
        if record.get("frame_id") == args.frame_id and record.get("trigger") == args.trigger
    ]
    if warnings:
        raise ValueError("persistence audit ledger is invalid: " + "; ".join(warnings))
    if existing:
        raise ValueError(f"persistence audit trigger already recorded: {args.trigger}")

    evidence_id = args.evidence_id
    semantic_memory_id = None
    reason = args.reason.strip()
    if args.decision == "promoted":
        if not evidence_id:
            raise ValueError("promoted audit requires --evidence-id")
        evidence_matches = [
            record
            for record in load_evidence_records(workspace, scope, warnings)
            if record.get("evidence_id") == evidence_id
        ]
        if len(evidence_matches) != 1:
            raise ValueError("promoted audit evidence must exist in the frame workspace and scope")
        if evidence_state(evidence_matches[0], warnings)["state"] != "PROMOTED":
            raise ValueError("persistence audit evidence has not passed Promotion Gate")
        semantic_matches = [
            entry
            for entry in load_semantic_entries(workspace, scope, warnings)
            if entry.get("promotion", {}).get("evidence_id") == evidence_id
        ]
        if len(semantic_matches) != 1:
            raise ValueError("promoted audit requires exactly one semantic memory entry")
        semantic_memory_id = semantic_matches[0]["memory_id"]
        reason = ""
    else:
        if evidence_id:
            raise ValueError("not_persisted audit must not provide --evidence-id")
        if not reason:
            raise ValueError("not_persisted audit requires an explicit --reason")
        if len(reason) > 512:
            raise ValueError("--reason cannot exceed 512 characters")
        if sensitive_material_reason(reason):
            raise ValueError("--reason contains sensitive material")

    record = {
        "schema_version": PERSISTENCE_AUDIT_SCHEMA_VERSION,
        "audit_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "frame_id": args.frame_id,
        "trigger": args.trigger,
        "decision": args.decision.upper(),
        "evidence_id": evidence_id,
        "semantic_memory_id": semantic_memory_id,
        "reason": reason,
        "authority": "persistence_audit_never_activation_authority",
    }
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = persistence_audit_directory(workspace, scope) / f"{date_dir}.jsonl"
    append_event(path, record)
    print(
        json.dumps(
            {
                "saved": True,
                "audit_id": record["audit_id"],
                "frame_id": args.frame_id,
                "trigger": args.trigger,
                "decision": record["decision"],
                "evidence_id": evidence_id,
                "semantic_memory_id": semantic_memory_id,
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_persistence_audit_show(args):
    events = read_events(find_frame(args.frame_id))
    if not events:
        raise ValueError("frame has no events")
    first = events[0]
    workspace = canonical_workspace(first["workspace"])
    scope = first.get("data", {}).get("causal", {}).get("scope") or "workspace"
    warnings = []
    records = [
        record
        for record in load_persistence_audit_records(workspace, scope, warnings)
        if record.get("frame_id") == args.frame_id
    ]
    required = sorted(required_persistence_audit_triggers(events))
    completed = sorted({record.get("trigger") for record in records})
    result = {
        "frame_id": args.frame_id,
        "workspace": workspace,
        "scope": scope,
        "required_triggers": required,
        "completed_triggers": completed,
        "missing_triggers": sorted(set(required) - set(completed)),
        "records": records,
        "valid": not warnings and set(required).issubset(set(completed)),
        "authority": "persistence_audit_never_activation_authority",
    }
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if warnings else 0


def command_evidence_show(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    evidence = load_evidence_records(workspace, scope, warnings)
    promotions = load_promotion_records(workspace, scope, warnings)
    promoted_by_evidence = {
        record.get("evidence_id"): record
        for record in promotions
        if record.get("decision") == "PROMOTED"
    }
    results = []
    for entry in evidence:
        if args.evidence_id and entry.get("evidence_id") != args.evidence_id:
            continue
        item = dict(entry)
        item["sources_fresh"] = source_snapshots(entry.get("sources", []), workspace) == entry.get(
            "source_snapshots", []
        )
        lifecycle = evidence_state(entry, warnings)
        promotion = promoted_by_evidence.get(entry.get("evidence_id"))
        item["lifecycle_state"] = lifecycle["state"]
        item["lifecycle_head_event_id"] = lifecycle["head_event_id"]
        item["replacement_evidence_id"] = lifecycle["replacement_evidence_id"]
        item["promotion_status"] = lifecycle["state"]
        if promotion:
            item["semantic_memory_id"] = promotion.get("semantic_memory_id")
            item["promotion_id"] = promotion.get("promotion_id")
        results.append(item)
    output = {
        "workspace": workspace,
        "scope": scope,
        "evidence_count": len(results),
        "results": results,
        "promotion_audit_count": len(promotions),
        "authority": "evidence_and_promotion_status_never_activation_authority",
    }
    if warnings:
        output["warnings"] = warnings
    print(json.dumps(output, ensure_ascii=False, indent=2))


def assert_scope_write_allowed(workspace, scope, operation):
    controls, warnings = load_control_records()
    if warnings:
        raise ValueError("control ledger is invalid: " + "; ".join(warnings))
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    if workspace_control and workspace_control.get("state") == "paused":
        raise ValueError("workspace control is paused")
    if not scoped_control or scoped_control.get("state") != "active":
        raise ValueError(f"{operation} requires an explicitly active scope")
    if scoped_control.get("resume_requires_confirmation"):
        raise ValueError("scope control still requires confirmation")


def prospective_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "prospective"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def load_prospective_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = prospective_directory(workspace, scope)
    records = []
    for path in sorted(root.glob("*.jsonl")) if root.exists() else []:
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != PROSPECTIVE_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported prospective schema")
                continue
            if normalized_workspace(record.get("workspace", "")) != normalized_workspace(workspace):
                warnings.append(f"{path.name}: prospective workspace mismatch")
                continue
            if normalize_scope(record.get("scope")).casefold() != normalize_scope(scope).casefold():
                warnings.append(f"{path.name}: prospective scope mismatch")
                continue
            records.append(record)
    return records


def reduce_prospective(workspace, scope, warnings=None):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    warnings = warnings if warnings is not None else []
    records = load_prospective_records(workspace, scope, warnings)
    return records, reduce_prospective_events(records)


def append_prospective_event(workspace, scope, event_type, data):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    with contract_fence(state_root(), workspace_key(workspace), scope_key(scope)):
        assert_scope_write_allowed(workspace, scope, "prospective-memory writes")
        lock_path = prospective_directory(workspace, scope) / ".prospective.lock"
        with exclusive_file_lock(lock_path):
            warnings = []
            records, state = reduce_prospective(workspace, scope, warnings)
            if warnings or state["issues"]:
                raise ValueError(
                    "prospective ledger is invalid: " + "; ".join(warnings + state["issues"])
                )
            event = {
                "schema_version": PROSPECTIVE_SCHEMA_VERSION,
                "event_id": str(uuid.uuid4()),
                "timestamp_utc": utc_now(),
                "sequence": state["head_sequence"] + 1,
                "workspace": workspace,
                "workspace_key": workspace_key(workspace),
                "scope": scope,
                "scope_key": scope_key(scope),
                "event_type": event_type,
                "data": data,
            }
            updated = reduce_prospective_events(records + [event])
            if updated["issues"]:
                raise ValueError(
                    "prospective event is not admissible: " + "; ".join(updated["issues"])
                )
            date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = prospective_directory(workspace, scope) / f"{date_dir}.jsonl"
            append_event(path, event)
            return event, updated, path


def command_prospective_register(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    if not args.goal_ref or len(args.goal_ref) > 256 or " " in args.goal_ref:
        raise ValueError("goal ref must be a non-empty space-free value up to 256 characters")
    condition = normalize_prospective_condition(
        args.event_kind, args.event_name, args.not_before
    )
    sources, snapshots = governance_evidence_refs(args.source, workspace, scope)
    event, state, path = append_prospective_event(
        workspace,
        scope,
        "goal_registered",
        {
            "goal_ref": args.goal_ref,
            "description": args.description.strip(),
            "condition": condition,
            "budget": args.budget,
            "death_line": args.death_line.strip(),
            "sources": sources,
            "source_snapshots": snapshots,
        },
    )
    print(json.dumps({
        "saved": True,
        "event_id": event["event_id"],
        "goal": state["goals"][args.goal_ref],
        "path": str(path),
        "authority": "prospective_condition_never_activation_or_action_authority",
    }, ensure_ascii=False, indent=2))


def command_prospective_observe(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    sources, snapshots = governance_evidence_refs(args.source, workspace, scope)
    observed = {
        "causal_event_id": str(uuid.uuid4()),
        "event_kind": args.kind,
        "event_name": args.name,
        "observed_at_utc": utc_now(),
        "payload_hash": args.payload_hash,
        "sources": sources,
        "source_snapshots": snapshots,
    }
    if args.kind == "clock":
        if not args.goal_ref:
            raise ValueError("clock observation requires --goal-ref")
        warnings = []
        _, state = reduce_prospective(workspace, scope, warnings)
        if warnings or state["issues"]:
            raise ValueError("prospective ledger is invalid")
        goal = state["goals"].get(args.goal_ref)
        matches = [
            goal_ref
            for goal_ref, candidate in state["goals"].items()
            if prospective_event_matches(candidate, observed)
        ]
        if not goal or args.goal_ref not in matches:
            raise ValueError("clock condition is not currently true for the named active goal")
        if len(matches) != 1:
            raise ValueError("clock event is ambiguous across active goals")
    event, state, path = append_prospective_event(
        workspace, scope, "causal_event_observed", observed
    )
    cycle = plan_prospective_cycle(state, observed["causal_event_id"])
    print(json.dumps({
        "saved": True,
        "event_id": event["event_id"],
        "causal_event_id": observed["causal_event_id"],
        "cycle": cycle,
        "path": str(path),
        "authority": "observed_event_never_auto_transitions_or_authorizes_action",
    }, ensure_ascii=False, indent=2))


def command_prospective_cycle(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    _, state = reduce_prospective(workspace, scope, warnings)
    if warnings or state["issues"]:
        raise ValueError("prospective ledger is invalid: " + "; ".join(warnings + state["issues"]))
    print(json.dumps({
        "workspace": workspace,
        "scope": scope,
        "cycle": plan_prospective_cycle(state, args.causal_event_id),
        "authority": "read_only_cycle_plan_requires_explicit_transition",
    }, ensure_ascii=False, indent=2))


def command_prospective_transition(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    sources, snapshots = governance_evidence_refs(args.source, workspace, scope)
    event, state, path = append_prospective_event(
        workspace,
        scope,
        "goal_transitioned",
        {
            "goal_ref": args.goal_ref,
            "state": args.state.upper(),
            "causal_event_id": args.causal_event_id,
            "replacement_goal_ref": args.replacement_goal_ref,
            "reason": args.reason.strip(),
            "sources": sources,
            "source_snapshots": snapshots,
        },
    )
    print(json.dumps({
        "saved": True,
        "event_id": event["event_id"],
        "goal": state["goals"][args.goal_ref],
        "path": str(path),
        "history_preserved": True,
    }, ensure_ascii=False, indent=2))


def command_prospective_show(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    records, state = reduce_prospective(workspace, scope, warnings)
    print(json.dumps({
        "workspace": workspace,
        "scope": scope,
        "record_count": len(records),
        "goals": state["goals"],
        "causal_events": state["causal_events"],
        "issues": state["issues"],
        "warnings": warnings,
        "authority": "prospective_state_never_activation_or_action_authority",
    }, ensure_ascii=False, indent=2))


def governance_directory(workspace, scope):
    return (
        state_root()
        / "memory"
        / "governance"
        / "workspaces"
        / workspace_key(workspace)
        / scope_key(scope)
    )


def load_governance_records(workspace, scope, warnings=None):
    warnings = warnings if warnings is not None else []
    root = governance_directory(workspace, scope)
    records = []
    for path in sorted(root.glob("*.jsonl")) if root.exists() else []:
        for record in read_jsonl_records(path, warnings):
            if record.get("schema_version") != GOVERNANCE_SCHEMA_VERSION:
                warnings.append(f"{path.name}: unsupported governance schema")
                continue
            if normalized_workspace(record.get("workspace", "")) != normalized_workspace(workspace):
                warnings.append(f"{path.name}: governance workspace mismatch")
                continue
            if normalize_scope(record.get("scope")).casefold() != normalize_scope(scope).casefold():
                warnings.append(f"{path.name}: governance scope mismatch")
                continue
            records.append(record)
    return records


def governance_snapshots_fresh(stored_snapshots, workspace):
    refs = [snapshot.get("ref") for snapshot in stored_snapshots]
    if not refs or any(not ref for ref in refs):
        return False
    current = source_snapshots(refs, workspace)
    if current != stored_snapshots:
        return False
    return all(snapshot.get("exists", True) for snapshot in current)


def reduce_governance(workspace, scope, warnings=None):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    warnings = warnings if warnings is not None else []
    records = load_governance_records(workspace, scope, warnings)
    state = reduce_governance_events(
        records,
        lambda snapshots: governance_snapshots_fresh(snapshots, workspace),
    )
    return records, state


def assert_governance_write_allowed(workspace, scope):
    assert_scope_write_allowed(workspace, scope, "governance writes")


def validate_governance_target_ref(target_ref, target_kind):
    if not target_ref or len(target_ref) > 256 or " " in target_ref:
        raise ValueError("target ref must be a non-empty space-free value up to 256 characters")
    prefix = target_ref.split(":", 1)[0]
    if prefix != target_kind:
        raise ValueError(f"target ref must start with {target_kind}:")


def governance_evidence_refs(refs, workspace, scope=None):
    unique = list(dict.fromkeys(refs))
    if not unique:
        raise ValueError("governance event requires at least one evidence ref")
    snapshots = source_snapshots(unique, workspace)
    for ref, snapshot in zip(unique, snapshots):
        if ref.startswith("evidence:"):
            evidence = find_evidence(ref.split(":", 1)[1])
            if normalized_workspace(evidence.get("workspace", "")) != normalized_workspace(workspace):
                raise ValueError(f"evidence belongs to another workspace: {ref}")
            if scope and normalize_scope(evidence.get("scope")).casefold() != normalize_scope(scope).casefold():
                raise ValueError(f"evidence belongs to another scope: {ref}")
            lifecycle = evidence_state(evidence)["state"]
            if lifecycle in {state.upper() for state in EVIDENCE_TERMINAL_STATES}:
                raise ValueError(f"terminal evidence cannot govern pressure: {ref}")
        elif ref.startswith("frame:"):
            if not snapshot.get("exists"):
                raise ValueError(f"frame evidence does not exist: {ref}")
        else:
            candidate = Path(os.path.expandvars(os.path.expanduser(ref)))
            if not candidate.is_absolute():
                candidate = Path(workspace) / candidate
            if not candidate.resolve().is_file():
                raise ValueError(
                    "governance evidence must be evidence:<id>, frame:<id>, or an existing file"
                )
    return unique, snapshots


def governance_high_scale_authorized(workspace, scope, evidence_refs, audit_id=None):
    for ref in evidence_refs:
        if not ref.startswith("evidence:"):
            continue
        evidence = find_evidence(ref.split(":", 1)[1])
        if (
            normalized_workspace(evidence.get("workspace", "")) == normalized_workspace(workspace)
            and normalize_scope(evidence.get("scope")).casefold() == normalize_scope(scope).casefold()
            and evidence_state(evidence)["state"] == "PROMOTED"
        ):
            return True
    if audit_id:
        audits = load_persistence_audit_records(workspace, scope, [])
        return any(
            record.get("audit_id") == audit_id and record.get("decision") == "PROMOTED"
            for record in audits
        )
    return False


def append_governance_event(workspace, scope, event_type, data):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    with contract_fence(
        state_root(), workspace_key(workspace), scope_key(scope)
    ):
        assert_governance_write_allowed(workspace, scope)
        lock_path = governance_directory(workspace, scope) / ".governance.lock"
        with exclusive_file_lock(lock_path):
            warnings = []
            records, state = reduce_governance(workspace, scope, warnings)
            if warnings or state["issues"]:
                raise ValueError(
                    "governance ledger is invalid: " + "; ".join(warnings + state["issues"])
                )
            event = {
                "schema_version": GOVERNANCE_SCHEMA_VERSION,
                "event_id": str(uuid.uuid4()),
                "timestamp_utc": utc_now(),
                "sequence": state["head_sequence"] + 1,
                "workspace": workspace,
                "workspace_key": workspace_key(workspace),
                "scope": scope,
                "scope_key": scope_key(scope),
                "event_type": event_type,
                "data": data,
            }
            date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = governance_directory(workspace, scope) / f"{date_dir}.jsonl"
            append_event(path, event)
            updated_state = reduce_governance_events(
                records + [event],
                lambda snapshots: governance_snapshots_fresh(snapshots, workspace),
            )
            if updated_state["issues"]:
                raise RuntimeError(
                    "governance event produced invalid replay: " + "; ".join(updated_state["issues"])
                )
            return event, updated_state, path


def command_governance_target_register(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    validate_governance_target_ref(args.target_ref, args.target_kind)
    warnings = []
    _, state = reduce_governance(workspace, scope, warnings)
    if warnings or state["issues"]:
        raise ValueError("governance ledger is invalid")
    if args.target_ref in state["targets"]:
        raise ValueError(f"target already exists: {args.target_ref}")
    if args.parent_target_ref:
        parent = state["targets"].get(args.parent_target_ref)
        if not parent:
            raise ValueError("parent target must already exist")
        if parent["state"] in TERMINAL_TARGET_STATES:
            raise ValueError("parent target is terminal")
        if SCALE_RANK[parent["scale"]] < SCALE_RANK[args.scale]:
            raise ValueError("parent target cannot be at a narrower scale than its child")
    source_refs, snapshots = governance_evidence_refs(args.source, workspace, scope)
    data = {
        "target_ref": args.target_ref,
        "target_kind": args.target_kind,
        "scale": args.scale,
        "parent_target_ref": args.parent_target_ref,
        "death_lines": list(dict.fromkeys(args.death_line)),
        "reentry_condition": args.reentry_condition.strip(),
        "budget": args.budget,
        "source_refs": source_refs,
        "source_snapshots": snapshots,
    }
    event, updated, path = append_governance_event(
        workspace, scope, "target_registered", data
    )
    print(
        json.dumps(
            {
                "saved": True,
                "event_id": event["event_id"],
                "target": updated["targets"][args.target_ref],
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_governance_pressure_record(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    _, state = reduce_governance(workspace, scope, warnings)
    target = state["targets"].get(args.target_ref)
    if not target:
        raise ValueError("pressure target must already exist")
    if target["state"] in TERMINAL_TARGET_STATES:
        raise ValueError("cannot record pressure against a terminal target")
    evidence_refs, snapshots = governance_evidence_refs(args.evidence, workspace, scope)
    if target["scale"] in {"continuity", "workspace"} and not governance_high_scale_authorized(
        workspace, scope, evidence_refs, args.persistence_audit_id
    ):
        raise ValueError(
            "continuity/workspace pressure requires promoted evidence or a promoted persistence audit"
        )
    parent_events = read_events(find_frame(args.frame_id))
    if normalized_workspace(parent_events[0].get("workspace", "")) != normalized_workspace(workspace):
        raise ValueError("causal frame belongs to another workspace")
    pressure_id = str(uuid.uuid4())
    data = {
        "pressure_id": pressure_id,
        "target_ref": args.target_ref,
        "kind": args.kind,
        "strength": args.strength,
        "scale": target["scale"],
        "evidence_refs": evidence_refs,
        "source_snapshots": snapshots,
        "source_pressure_ids": [],
        "required_change": args.required_change.strip(),
        "causal_frame_id": args.frame_id,
        "persistence_audit_id": args.persistence_audit_id,
    }
    event, updated, path = append_governance_event(
        workspace, scope, "pressure_recorded", data
    )
    pressure = updated["pressures"][pressure_id]
    print(
        json.dumps(
            {
                "saved": True,
                "event_id": event["event_id"],
                "pressure": pressure,
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_governance_pressure_invalidate(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    _, state = reduce_governance(workspace, scope, [])
    pressure = state["pressures"].get(args.pressure_id)
    if not pressure:
        raise ValueError("unknown pressure")
    if pressure["invalidated_event_id"]:
        raise ValueError("pressure is already explicitly invalidated")
    reason = args.reason.strip()
    if not reason:
        raise ValueError("--reason cannot be empty")
    event, updated, path = append_governance_event(
        workspace,
        scope,
        "pressure_invalidated",
        {"pressure_id": args.pressure_id, "reason": reason},
    )
    print(
        json.dumps(
            {
                "saved": True,
                "event_id": event["event_id"],
                "pressure": updated["pressures"][args.pressure_id],
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_governance_pressure_propagate(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    _, state = reduce_governance(workspace, scope, [])
    target = state["targets"].get(args.target_ref)
    if not target:
        raise ValueError("propagation target must already exist")
    sources = []
    for pressure_id in list(dict.fromkeys(args.source_pressure_id)):
        pressure = state["pressures"].get(pressure_id)
        if not pressure or not pressure["active"]:
            raise ValueError(f"source pressure is missing or inactive: {pressure_id}")
        if SCALE_RANK[target["scale"]] <= SCALE_RANK[pressure["scale"]]:
            raise ValueError("pressure propagation must move to a broader scale")
        sources.append(pressure)
    kinds = {pressure["kind"] for pressure in sources}
    if len(kinds) != 1:
        raise ValueError("one propagated pressure cannot combine different pressure kinds")
    evidence_refs = sorted(
        {ref for pressure in sources for ref in pressure["evidence_refs"]}
    )
    evidence_refs, snapshots = governance_evidence_refs(evidence_refs, workspace, scope)
    if target["scale"] in {"continuity", "workspace"} and not governance_high_scale_authorized(
        workspace, scope, evidence_refs, args.persistence_audit_id
    ):
        raise ValueError(
            "continuity/workspace propagation requires promoted evidence or promoted audit"
        )
    strength = max(sources, key=lambda item: STRENGTH_RANK[item["strength"]])["strength"]
    pressure_id = str(uuid.uuid4())
    data = {
        "pressure_id": pressure_id,
        "target_ref": args.target_ref,
        "kind": next(iter(kinds)),
        "strength": strength,
        "scale": target["scale"],
        "evidence_refs": evidence_refs,
        "source_snapshots": snapshots,
        "source_pressure_ids": [pressure["pressure_id"] for pressure in sources],
        "required_change": args.reason.strip(),
        "causal_frame_id": args.frame_id,
        "persistence_audit_id": args.persistence_audit_id,
    }
    event, updated, path = append_governance_event(
        workspace, scope, "pressure_propagated", data
    )
    print(
        json.dumps(
            {
                "saved": True,
                "event_id": event["event_id"],
                "pressure": updated["pressures"][pressure_id],
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_governance_target_transition(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    _, state = reduce_governance(workspace, scope, [])
    target = state["targets"].get(args.target_ref)
    if not target:
        raise ValueError("unknown governance target")
    current = target["state"]
    transition_map = {
        "warn": ("target_warned", {"ACTIVE"}, "WARNED"),
        "probation": ("probation_started", {"WARNED"}, "PROBATION"),
        "stabilize": ("target_stabilized", {"PROBATION"}, "ACTIVE"),
        "pause": (
            "target_paused",
            {"ACTIVE", "WARNED", "PROBATION"},
            "PAUSED",
        ),
        "resume": ("target_resumed", {"PAUSED", "BLOCKED"}, "ACTIVE"),
        "collapse": (
            "target_collapsed",
            {"ACTIVE", "WARNED", "PROBATION", "PAUSED", "BLOCKED"},
            "COLLAPSED",
        ),
        "complete": (
            "target_completed",
            {"ACTIVE", "WARNED", "PROBATION", "PAUSED", "BLOCKED"},
            "COMPLETED",
        ),
        "block": (
            "target_blocked",
            {"ACTIVE", "WARNED", "PROBATION", "PAUSED"},
            "BLOCKED",
        ),
        "supersede": (
            "target_superseded",
            {"ACTIVE", "WARNED", "PROBATION", "PAUSED", "BLOCKED"},
            "SUPERSEDED",
        ),
        "reenter": ("target_reentered", {"COLLAPSED"}, "ACTIVE"),
    }
    event_type, allowed, destination = transition_map[args.transition]
    if current not in allowed:
        raise ValueError(f"transition {args.transition} is invalid from {current}")
    if destination in TERMINAL_TARGET_STATES:
        active_children = sorted(
            ref
            for ref, child in state["targets"].items()
            if child.get("parent_target_ref") == args.target_ref
            and child["state"] not in TERMINAL_TARGET_STATES
        )
        if active_children:
            raise ValueError(
                "terminal target transition requires terminal children: "
                + ", ".join(active_children)
            )
    pressure_ids = list(dict.fromkeys(args.pressure_id))
    active_pressures = []
    for pressure_id in pressure_ids:
        pressure = state["pressures"].get(pressure_id)
        if not pressure or not pressure["active"] or pressure["target_ref"] != args.target_ref:
            raise ValueError(f"transition pressure is inactive or targets another object: {pressure_id}")
        active_pressures.append(pressure)
    evidence_refs = list(dict.fromkeys(args.evidence))
    if pressure_ids:
        evidence_refs = sorted(
            set(evidence_refs)
            | {ref for pressure in active_pressures for ref in pressure["evidence_refs"]}
        )
    evidence_refs, snapshots = governance_evidence_refs(evidence_refs, workspace, scope)
    if args.transition in {"warn", "probation", "collapse"} and not pressure_ids:
        raise ValueError(f"{args.transition} requires at least one active pressure")

    data = {
        "target_ref": args.target_ref,
        "from_state": current,
        "to_state": destination,
        "pressure_ids": pressure_ids,
        "evidence_refs": evidence_refs,
        "source_snapshots": snapshots,
        "causal_frame_id": args.frame_id,
    }
    if args.transition == "collapse":
        matched = list(dict.fromkeys(args.death_line_match))
        if not matched or any(item not in target["death_lines"] for item in matched):
            raise ValueError("collapse must match at least one declared death line")
        trace_fields = {
            "once_reasonable": args.once_reasonable.strip(),
            "invalidating_evidence": args.invalidating_evidence.strip(),
            "reusable_results": args.reusable_results.strip(),
            "forbidden_assumption": args.forbidden_assumption.strip(),
            "reentry_condition": target["reentry_condition"],
        }
        if any(not value for value in trace_fields.values()):
            raise ValueError("collapse requires a complete trace and declared reentry condition")
        data["matched_death_lines"] = matched
        data["trace"] = trace_fields
    elif args.transition == "supersede":
        replacement = state["targets"].get(args.replacement_target_ref)
        if not replacement or replacement["state"] in TERMINAL_TARGET_STATES:
            raise ValueError("supersede requires an active replacement target")
        data["replacement_target_ref"] = args.replacement_target_ref
    elif args.transition == "reenter":
        if not target["reentry_condition"]:
            raise ValueError("collapsed target has no declared reentry condition")
        collapsed_evidence = {
            ref
            for item in target["transition_history"]
            if item["event_type"] == "target_collapsed"
            for ref in item.get("evidence_refs", [])
        }
        if not set(evidence_refs) - collapsed_evidence:
            raise ValueError("reentry requires evidence not used by the collapse")
        data["reentry_condition"] = target["reentry_condition"]

    event, updated, path = append_governance_event(
        workspace, scope, event_type, data
    )
    print(
        json.dumps(
            {
                "saved": True,
                "event_id": event["event_id"],
                "target": updated["targets"][args.target_ref],
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_governance_show(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    records, state = reduce_governance(workspace, scope, warnings)
    result = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "workspace": workspace,
        "scope": scope,
        "valid": not warnings and not state["issues"],
        "event_count": len(records),
        "head_event_id": state["head_event_id"],
        "head_sequence": state["head_sequence"],
        "targets": state["targets"],
        "pressures": state["pressures"],
        "pressure_vectors": state["pressure_vectors"],
        "by_scale": state["by_scale"],
        "issues": warnings + state["issues"],
        "authority": "governance_state_within_active_scope_never_control_authority",
    }
    if args.target_ref:
        result["targets"] = {
            args.target_ref: state["targets"][args.target_ref]
        } if args.target_ref in state["targets"] else {}
        result["pressures"] = {
            pressure_id: pressure
            for pressure_id, pressure in state["pressures"].items()
            if pressure["target_ref"] == args.target_ref
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["issues"] else 0


def build_self_projection(workspace, scope):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    warnings = []
    records, state = reduce_governance(workspace, scope, warnings)
    controls, control_warnings = load_control_records()
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    control_active = bool(
        scoped_control
        and scoped_control.get("state") == "active"
        and not scoped_control.get("resume_requires_confirmation")
        and not (workspace_control and workspace_control.get("state") == "paused")
    )
    lineage_warnings = []
    lineage_records = load_lineage_records(workspace, scope, lineage_warnings)
    lineage = derive_lineage_state(lineage_records)
    active_targets = {
        ref: target
        for ref, target in state["targets"].items()
        if target["state"] not in TERMINAL_TARGET_STATES
    }
    holders = {
        ref: target
        for ref, target in active_targets.items()
        if target["target_kind"] == "holder"
    }
    near_death_line = {
        ref: {
            "state": target["state"],
            "declared_death_lines": target["death_lines"],
            "pressure_vector": state["pressure_vectors"].get(ref, {}),
        }
        for ref, target in active_targets.items()
        if target["state"] in {"WARNED", "PROBATION"}
    }
    collapsed_traces = {
        ref: target["collapse_trace"]
        for ref, target in state["targets"].items()
        if target["state"] == "COLLAPSED" and target["collapse_trace"]
    }
    projection_body = {
        "workspace": workspace,
        "scope": scope,
        "control": {
            "state": scoped_control.get("state") if scoped_control else None,
            "event_id": scoped_control.get("event_id") if scoped_control else None,
            "continuation_allowed": control_active,
        },
        "governance_head": {
            "event_id": state["head_event_id"],
            "sequence": state["head_sequence"],
        },
        "branch_heads": lineage["branches"],
        "active_targets": active_targets,
        "holders": holders,
        "unresolved_pressure_vectors": state["pressure_vectors"],
        "near_death_line": near_death_line,
        "collapsed_traces": collapsed_traces,
        "by_scale": state["by_scale"],
        "issues": warnings + control_warnings + lineage_warnings + state["issues"] + lineage["issues"],
    }
    projection_hash = hashlib.sha256(
        json.dumps(projection_body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    result = {
        "schema_version": "weilan_self_projection_v0.6",
        "projection_hash": projection_hash,
        "authority": "derived_read_only_projection_never_control_authority",
        "can_authorize_actions": False,
        **projection_body,
    }
    return result


def command_self_project(args):
    result = build_self_projection(args.workspace, args.scope)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["issues"] else 0


def current_metabolic_contract(workspace, scope):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    projection = build_self_projection(workspace, scope)
    warnings = list(projection.get("issues", []))
    controls, control_warnings = load_control_records()
    warnings.extend(control_warnings)
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    continuation_allowed = bool(
        scoped_control
        and scoped_control.get("state") == "active"
        and not scoped_control.get("resume_requires_confirmation")
        and not (workspace_control and workspace_control.get("state") == "paused")
    )

    lineage_warnings = []
    lineage_records = load_lineage_records(workspace, scope, lineage_warnings)
    warnings.extend(lineage_warnings)
    lineage = derive_lineage_state(lineage_records)
    warnings.extend(lineage["issues"])
    audit_records = load_persistence_audit_records(workspace, scope, warnings)
    branches = {}
    audit_heads = []
    lineage_head = lineage_records[-1].get("event_id") if lineage_records else None
    for branch_id, branch in sorted(lineage["branches"].items()):
        head_frame_id = branch.get("head_frame_id")
        head_closed = False
        audit_valid = False
        completed_test = False
        required = set()
        completed = set()
        if head_frame_id:
            try:
                frame_events = read_events(find_frame(head_frame_id))
            except (FileNotFoundError, RuntimeError) as exc:
                warnings.append(str(exc))
                frame_events = []
            if frame_events:
                head_closed = frame_events[-1].get("event_type") == "frame_closed"
                completed_test = any(
                    event.get("event_type") == "discriminating_test_executed"
                    for event in frame_events
                )
                required = required_persistence_audit_triggers(frame_events)
                completed = {
                    record.get("trigger")
                    for record in audit_records
                    if record.get("frame_id") == head_frame_id
                    and record.get("decision") in {"PROMOTED", "NOT_PERSISTED"}
                }
                audit_valid = head_closed and required.issubset(completed)
                matching_audits = [
                    record
                    for record in audit_records
                    if record.get("frame_id") == head_frame_id
                ]
                audit_heads.append(
                    {
                        "branch_id": branch_id,
                        "frame_id": head_frame_id,
                        "required": sorted(required),
                        "completed": sorted(completed),
                        "valid": audit_valid,
                        "head_audit_id": matching_audits[-1].get("audit_id")
                        if matching_audits
                        else None,
                    }
                )
        branches[branch_id] = {
            **branch,
            "head_closed": head_closed,
            "audit_valid": audit_valid,
            "discriminating_test_completed": completed_test,
        }

    governance_warnings = []
    _, governance = reduce_governance(workspace, scope, governance_warnings)
    warnings.extend(governance_warnings)
    warnings.extend(governance["issues"])
    evidence_heads = {}
    for pressure in governance["pressures"].values():
        if not pressure.get("active"):
            continue
        for snapshot in pressure.get("source_snapshots", []):
            ref = snapshot.get("ref")
            if ref:
                evidence_heads[ref] = {
                    "ref": ref,
                    "content_hash": snapshot.get("content_hash"),
                    "lifecycle_state": snapshot.get("lifecycle_state"),
                    "lifecycle_head_event_id": snapshot.get("lifecycle_head_event_id"),
                }

    snapshot = {
        "workspace": workspace,
        "scope": scope,
        "control": {
            "state": scoped_control.get("state") if scoped_control else None,
            "event_id": scoped_control.get("event_id") if scoped_control else None,
            "continuation_allowed": continuation_allowed,
        },
        "lineage_head": lineage_head,
        "branches": branches,
        "governance_head": {
            "event_id": governance["head_event_id"],
            "sequence": governance["head_sequence"],
        },
        "targets": governance["targets"],
        "pressures": governance["pressures"],
        "pressure_vectors": governance["pressure_vectors"],
        "self_projection_hash": projection["projection_hash"],
        "evidence_heads": sorted(evidence_heads.values(), key=lambda item: item["ref"]),
        "audit_heads": audit_heads,
        "issues": warnings,
    }
    return build_metabolic_contract(snapshot)


def assert_transaction_write_allowed(workspace, scope):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    controls, warnings = load_control_records()
    if warnings:
        raise ValueError("control ledger is invalid: " + "; ".join(warnings))
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    if workspace_control and workspace_control.get("state") == "paused":
        raise ValueError("workspace control is paused")
    if (
        not scoped_control
        or scoped_control.get("state") != "active"
        or scoped_control.get("resume_requires_confirmation")
    ):
        raise ValueError("transaction writes require an explicitly active scope")


def load_json_object(path, label):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def validate_current_metabolic_proposal(workspace, scope, proposal):
    if proposal.get("schema_version") != "weilan_metabolic_proposal_v0.7a":
        raise ValueError("unsupported metabolic proposal schema")
    contract = current_metabolic_contract(workspace, scope)
    regenerated = propose_metabolic_transition(
        contract,
        expected_contract_hash=proposal.get("contract_hash"),
        trigger_kind=(proposal.get("trigger") or {}).get("kind"),
        trigger_ref=(proposal.get("trigger") or {}).get("ref"),
        disposition=proposal.get("disposition"),
        target_ref=proposal.get("target_ref"),
        candidate_target_ref=proposal.get("candidate_target_ref"),
        branches=proposal.get("branches", []),
        death_line_matches=proposal.get("death_line_matches", []),
        changed_assumption=proposal.get("changed_assumption", ""),
    )
    if regenerated.get("proposal_hash") != proposal.get("proposal_hash"):
        raise ValueError("proposal_hash_mismatch")
    if not regenerated.get("admissible"):
        raise ValueError(
            "proposal is not admissible against current heads: "
            + ", ".join(regenerated.get("reasons", []))
        )
    if proposal.get("disposition") == "yield":
        raise ValueError("yield has no writable transaction")
    if proposal.get("can_authorize_actions") or proposal.get("can_commit"):
        raise ValueError("0.7a proposal cannot claim authority or commit power")
    return contract, regenerated


def transaction_record_date(record):
    timestamp = str(record.get("timestamp_utc", ""))
    if not re.match(r"^\d{4}-\d{2}-\d{2}T", timestamp):
        raise ValueError("transaction participant record requires ISO timestamp_utc")
    return timestamp[:10]


def transaction_target_relpath(participant, record, workspace, scope):
    date_dir = transaction_record_date(record)
    wk = workspace_key(workspace)
    sk = scope_key(scope)
    if participant == "frame":
        frame_id = record.get("frame_id")
        if not frame_id:
            raise ValueError("frame intent requires frame_id")
        try:
            path = find_frame(frame_id)
            return path.relative_to(state_root()).as_posix()
        except FileNotFoundError:
            return f"frames/{date_dir}/{frame_id}.jsonl"
    directories = {
        "lineage": f"memory/lineage/workspaces/{wk}/{sk}",
        "governance": f"memory/governance/workspaces/{wk}/{sk}",
        "evidence": f"memory/evidence/workspaces/{wk}/{sk}",
        "evidence_lifecycle": f"memory/evidence/lifecycle/{wk}/{sk}",
        "persistence_audit": f"memory/audits/persistence/{wk}/{sk}",
    }
    if participant not in directories:
        raise ValueError(f"unsupported transaction participant: {participant}")
    return f"{directories[participant]}/{date_dir}.jsonl"


def validate_transaction_record_scope(participant, record, workspace, scope):
    schemas = {
        "frame": SCHEMA_VERSION,
        "lineage": LINEAGE_SCHEMA_VERSION,
        "governance": GOVERNANCE_SCHEMA_VERSION,
        "evidence": EVIDENCE_SCHEMA_VERSION,
        "evidence_lifecycle": EVIDENCE_LIFECYCLE_SCHEMA_VERSION,
        "persistence_audit": PERSISTENCE_AUDIT_SCHEMA_VERSION,
    }
    if record.get("schema_version") != schemas[participant]:
        raise ValueError(f"{participant} intent has unsupported schema_version")
    if normalized_workspace(record.get("workspace", "")) != normalized_workspace(workspace):
        raise ValueError(f"{participant} intent belongs to another workspace")
    if participant == "frame":
        causal = record.get("data", {}).get("causal", {})
        if record.get("event_type") == "frame_opened" and causal:
            record_scope = causal.get("scope")
            if normalize_scope(record_scope).casefold() != normalize_scope(scope).casefold():
                raise ValueError("frame intent belongs to another scope")
    elif normalize_scope(record.get("scope")).casefold() != normalize_scope(scope).casefold():
        raise ValueError(f"{participant} intent belongs to another scope")


def validate_transaction_intents(intents, workspace, scope):
    by_participant = {name: [] for name in TRANSACTION_PARTICIPANTS}
    seen_payload_ids = set()
    for intent in intents:
        participant = intent["participant"]
        if participant not in by_participant:
            raise ValueError(f"unsupported transaction participant: {participant}")
        record = intent["payload"]
        validate_transaction_record_scope(participant, record, workspace, scope)
        identity = (
            record.get("event_id")
            or record.get("audit_id")
            or record.get("evidence_id")
            or record.get("lineage_event_id")
        )
        if identity and identity in seen_payload_ids:
            raise ValueError(f"duplicate participant record identity: {identity}")
        if identity:
            seen_payload_ids.add(identity)
        by_participant[participant].append(intent)

    transaction_audits = [value["payload"] for value in by_participant["persistence_audit"]]
    transaction_frames = [value["payload"] for value in by_participant["frame"]]
    frame_groups = {}
    for event in transaction_frames:
        frame_groups.setdefault(event.get("frame_id"), []).append(event)
    for frame_id, additions in frame_groups.items():
        try:
            existing = read_events(find_frame(frame_id))
        except FileNotFoundError:
            existing = []
        combined = existing + additions
        errors = validate_events(combined)
        if errors:
            raise ValueError(f"frame {frame_id} transaction replay invalid: " + "; ".join(errors))
        if combined[-1].get("event_type") == "frame_closed":
            required = required_persistence_audit_triggers(combined)
            completed = {
                record.get("trigger")
                for record in [
                    *load_persistence_audit_records(workspace, scope, []),
                    *transaction_audits,
                ]
                if record.get("frame_id") == frame_id
                and record.get("decision") in {"PROMOTED", "NOT_PERSISTED"}
            }
            missing = sorted(required - completed)
            if missing:
                raise ValueError(
                    f"frame {frame_id} transaction lacks persistence audit: "
                    + ", ".join(missing)
                )

    if by_participant["lineage"]:
        additions = [value["payload"] for value in by_participant["lineage"]]
        current = load_lineage_records(workspace, scope, [])
        replay = derive_lineage_state(current + additions)
        if replay["issues"]:
            raise ValueError("lineage transaction replay invalid: " + "; ".join(replay["issues"]))
        frames_by_id = {
            event.get("frame_id"): event
            for event in transaction_frames
            if event.get("event_type") == "frame_opened"
        }
        for lineage_record in additions:
            frame_event = frames_by_id.get(lineage_record.get("frame_id"))
            if not frame_event:
                try:
                    frame_event = read_events(find_frame(lineage_record.get("frame_id")))[0]
                except (FileNotFoundError, IndexError):
                    raise ValueError("lineage intent requires a matching frame")
            causal = frame_event.get("data", {}).get("causal", {})
            if (
                causal.get("lineage_event_id") != lineage_record.get("event_id")
                or causal.get("branch_id") != lineage_record.get("branch_id")
                or causal.get("relation") != lineage_record.get("relation")
                or causal.get("parent_frame_ids", []) != lineage_record.get("parent_frame_ids", [])
            ):
                raise ValueError("lineage and frame causal envelopes do not match")

    if by_participant["governance"]:
        additions = [value["payload"] for value in by_participant["governance"]]
        records = load_governance_records(workspace, scope, [])
        replay = reduce_governance_events(
            records + additions,
            lambda snapshots: governance_snapshots_fresh(snapshots, workspace),
        )
        if replay["issues"]:
            raise ValueError("governance transaction replay invalid: " + "; ".join(replay["issues"]))

    current_evidence = load_evidence_records(workspace, scope, [])
    transaction_evidence = [value["payload"] for value in by_participant["evidence"]]
    evidence_ids = [record.get("evidence_id") for record in current_evidence + transaction_evidence]
    if any(not value for value in evidence_ids) or len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence transaction contains missing or duplicate evidence_id")

    lifecycle = [
        *load_evidence_lifecycle_records(workspace, scope, []),
        *[value["payload"] for value in by_participant["evidence_lifecycle"]],
    ]
    known_evidence = set(evidence_ids)
    terminal_by_evidence = {}
    for record in lifecycle:
        evidence_id = record.get("evidence_id")
        if evidence_id not in known_evidence:
            raise ValueError("evidence lifecycle intent references unknown evidence")
        if record.get("state") not in EVIDENCE_TERMINAL_STATES:
            raise ValueError("unsupported evidence lifecycle state")
        if evidence_id in terminal_by_evidence:
            raise ValueError("evidence cannot have multiple terminal lifecycle events")
        terminal_by_evidence[evidence_id] = record.get("state")

    audits = [*load_persistence_audit_records(workspace, scope, []), *transaction_audits]
    audit_ids = [record.get("audit_id") for record in audits]
    if any(not value for value in audit_ids) or len(audit_ids) != len(set(audit_ids)):
        raise ValueError("persistence audit transaction contains missing or duplicate audit_id")
    known_frames = set(frame_groups)
    for record in transaction_audits:
        frame_id = record.get("frame_id")
        if frame_id not in known_frames:
            try:
                find_frame(frame_id)
            except FileNotFoundError:
                raise ValueError("persistence audit intent references unknown frame")
        if record.get("trigger") not in PERSISTENCE_AUDIT_TRIGGERS:
            raise ValueError("persistence audit intent has unsupported trigger")
        if record.get("decision") not in {"PROMOTED", "NOT_PERSISTED"}:
            raise ValueError("persistence audit intent has unsupported decision")


def validate_proposal_transaction_shape(proposal, intents):
    disposition = proposal.get("disposition")
    frame_events = [
        value["payload"]
        for value in intents
        if value["participant"] == "frame"
        and value["payload"].get("event_type") == "frame_opened"
    ]
    lineage_events = [
        value["payload"] for value in intents if value["participant"] == "lineage"
    ]
    successor_dispositions = {"continue", "test", "fork", "join", "regroup"}
    if disposition in successor_dispositions:
        if len(frame_events) != 1 or len(lineage_events) != 1:
            raise ValueError(
                f"{disposition} transaction requires exactly one frame_opened and one lineage intent"
            )
        preview = proposal.get("successor_preview") or {}
        frame_causal = frame_events[0].get("data", {}).get("causal", {})
        lineage = lineage_events[0]
        if (
            frame_causal.get("relation") != preview.get("relation")
            or lineage.get("relation") != preview.get("relation")
            or sorted(frame_causal.get("parent_frame_ids", []))
            != sorted(preview.get("parent_frame_ids", []))
            or sorted(lineage.get("parent_frame_ids", []))
            != sorted(preview.get("parent_frame_ids", []))
        ):
            raise ValueError("transaction successor does not match admitted proposal preview")
    elif frame_events or lineage_events:
        raise ValueError(f"{disposition} transaction must not create a successor frame")

    required_governance_events = {
        "collapse": "target_collapsed",
        "complete": "target_completed",
        "block": "target_blocked",
    }
    required = required_governance_events.get(disposition)
    if required:
        matches = [
            value["payload"]
            for value in intents
            if value["participant"] == "governance"
            and value["payload"].get("event_type") == required
            and value["payload"].get("data", {}).get("target_ref")
            == proposal.get("target_ref")
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{disposition} transaction requires exactly one matching {required} intent"
            )


def normalize_transaction_plan(plan, workspace, scope, proposal, validate=True):
    values = plan.get("intents")
    if not isinstance(values, list):
        raise ValueError("transaction plan requires an intents array")
    prepared = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("transaction plan intent must be an object")
        participant = str(value.get("participant", "")).strip()
        if participant not in TRANSACTION_PARTICIPANTS:
            raise ValueError(f"unsupported transaction participant: {participant}")
        record = value.get("record")
        if not isinstance(record, dict):
            raise ValueError("transaction plan intent requires an object record")
        prepared.append(
            {
                "intent_id": value.get("intent_id"),
                "participant": participant,
                "target_relpath": transaction_target_relpath(
                    participant, record, workspace, scope
                ),
                "payload": record,
            }
        )
    normalized = normalize_transaction_intents(prepared)
    validate_proposal_transaction_shape(proposal, normalized)
    if validate:
        validate_transaction_intents(normalized, workspace, scope)
    return normalized


def transaction_summary(transaction, state):
    return {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction["transaction_id"],
        "idempotency_key": transaction["idempotency_key"],
        "state": transaction["state"],
        "contract_hash": transaction["contract_hash"],
        "proposal_hash": transaction["proposal_hash"],
        "request_hash": transaction.get("request_hash"),
        "plan_hash": transaction.get("plan_hash"),
        "bundle_hash": transaction["bundle_hash"],
        "intent_count": len(transaction["intents"]),
        "participants": sorted({value["participant"] for value in transaction["intents"]}),
        "receipt": transaction["receipt"],
        "journal_head": {
            "event_id": state["head_event_id"],
            "sequence": state["head_sequence"],
        },
        "pending_events_visible": transaction["state"] == "COMMITTED",
        "background_loop_started": False,
        "authority": "explicit_caller_transaction_never_expands_control_authority",
    }


def refresh_transaction_derived_views(workspace, scope, transaction):
    participants = {value["participant"] for value in transaction["intents"]}
    refreshed = []
    if transaction["state"] == "COMMITTED" and "lineage" in participants:
        warnings = []
        records = load_lineage_records(workspace, scope, warnings)
        replay = derive_lineage_state(records)
        if warnings or replay["issues"]:
            raise ValueError(
                "committed lineage cannot refresh derived heads: "
                + "; ".join(warnings + replay["issues"])
            )
        write_lineage_heads(workspace, scope, records, replay)
        refreshed.append("lineage_heads")
    return refreshed


def command_metabolic_prepare(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    assert_transaction_write_allowed(workspace, scope)
    proposal = load_json_object(args.proposal_file, "proposal file")
    plan = load_json_object(args.plan_file, "transaction plan")
    records = load_transaction_records(
        state_root(), workspace_key(workspace), scope_key(scope)
    )
    transaction_state = reduce_transactions(records)
    if transaction_state["issues"]:
        raise ValueError(
            "transaction journal is invalid: " + "; ".join(transaction_state["issues"])
        )
    existing_id = transaction_state["idempotency"].get(args.idempotency_key.strip())
    if existing_id:
        contract_hash = proposal.get("contract_hash")
        intents = normalize_transaction_plan(
            plan, workspace, scope, proposal, validate=False
        )
    else:
        contract, proposal = validate_current_metabolic_proposal(workspace, scope, proposal)
        contract_hash = contract["contract_hash"]
        intents = normalize_transaction_plan(plan, workspace, scope, proposal)
    transaction, state = prepare_transaction(
        state_root(),
        workspace=workspace,
        scope=scope,
        workspace_key=workspace_key(workspace),
        scope_key=scope_key(scope),
        idempotency_key=args.idempotency_key,
        contract_hash=contract_hash,
        proposal=proposal,
        intents=intents,
    )
    print(json.dumps(transaction_summary(transaction, state), ensure_ascii=False, indent=2))


def command_metabolic_commit(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    assert_transaction_write_allowed(workspace, scope)
    transaction, state = commit_transaction(
        state_root(),
        workspace=workspace,
        scope=scope,
        workspace_key=workspace_key(workspace),
        scope_key=scope_key(scope),
        transaction_id=args.transaction_id,
        expected_contract_hash=args.expected_contract_hash,
        current_contract_loader=lambda: current_metabolic_contract(workspace, scope)[
            "contract_hash"
        ],
    )
    reset_visibility_snapshot()
    result = transaction_summary(transaction, state)
    result["refreshed_views"] = refresh_transaction_derived_views(
        workspace, scope, transaction
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_metabolic_abort(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    assert_transaction_write_allowed(workspace, scope)
    transaction, state = abort_transaction(
        state_root(),
        workspace=workspace,
        scope=scope,
        workspace_key=workspace_key(workspace),
        scope_key=scope_key(scope),
        transaction_id=args.transaction_id,
        reason=args.reason,
    )
    print(json.dumps(transaction_summary(transaction, state), ensure_ascii=False, indent=2))


def command_metabolic_recover(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    assert_transaction_write_allowed(workspace, scope)
    transaction, state = recover_transaction(
        state_root(),
        workspace=workspace,
        scope=scope,
        workspace_key=workspace_key(workspace),
        scope_key=scope_key(scope),
        transaction_id=args.transaction_id,
    )
    reset_visibility_snapshot()
    result = transaction_summary(transaction, state)
    result["refreshed_views"] = refresh_transaction_derived_views(
        workspace, scope, transaction
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_metabolic_transaction_show(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    records = load_transaction_records(
        state_root(), workspace_key(workspace), scope_key(scope)
    )
    state = reduce_transactions(records)
    if state["issues"]:
        raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
    transactions = state["transactions"]
    if args.transaction_id:
        transaction = transactions.get(args.transaction_id)
        if not transaction:
            raise ValueError("transaction not found")
        result = transaction_summary(transaction, state)
    else:
        result = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "workspace": workspace,
            "scope": scope,
            "transaction_count": len(transactions),
            "transactions": [
                transaction_summary(value, state)
                for _, value in sorted(transactions.items())
            ],
            "journal_head": {
                "event_id": state["head_event_id"],
                "sequence": state["head_sequence"],
            },
            "background_loop_started": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def transition_proposal_from_args(contract, args):
    return propose_metabolic_transition(
        contract,
        expected_contract_hash=contract["contract_hash"],
        trigger_kind=args.trigger_kind,
        trigger_ref=args.trigger_ref,
        disposition=args.disposition,
        target_ref=args.target_ref,
        candidate_target_ref=args.candidate_target_ref,
        branches=args.branch,
        death_line_matches=args.death_line_match,
        changed_assumption=args.changed_assumption,
    )


def transition_proposal_request_from_args(args):
    return {
        "trigger": {"kind": args.trigger_kind, "ref": args.trigger_ref},
        "disposition": args.disposition,
        "target_ref": args.target_ref,
        "candidate_target_ref": args.candidate_target_ref,
        "branches": list(args.branch),
        "death_line_matches": list(args.death_line_match),
        "changed_assumption": args.changed_assumption.strip(),
    }


def transition_materialization_request_from_args(args):
    return {
        "idempotency_key": args.idempotency_key,
        "level": args.level,
        "result_branch": args.result_branch,
        "problem": args.problem,
        "success_criteria": args.success,
        "budget": args.budget,
        "why_reasonable": args.why_reasonable,
        "next_expected_evidence": args.next_expected_evidence,
        "death_line": args.next_death_line,
        "evidence_refs": list(args.evidence),
        "once_reasonable": args.once_reasonable,
        "invalidating_evidence": args.invalidating_evidence,
        "reusable_results": args.reusable_results,
        "forbidden_assumption": args.forbidden_assumption,
    }


def transition_trigger_timestamp(workspace, scope, proposal, governance_records, governance):
    trigger = proposal.get("trigger") or {}
    kind = trigger.get("kind")
    ref = trigger.get("ref", "")
    if ref.startswith("frame:"):
        frame_id = ref.split(":", 1)[1]
        events = read_events(find_frame(frame_id))
        if kind == "discriminating_test_completed":
            matches = [
                event
                for event in events
                if event.get("event_type") == "discriminating_test_executed"
            ]
            if matches:
                return matches[-1].get("timestamp_utc", "")
        return events[-1].get("timestamp_utc", "") if events else ""
    if ref.startswith("control:"):
        event_id = ref.split(":", 1)[1]
        controls, _ = load_control_records()
        match = next((value for value in controls if value.get("event_id") == event_id), None)
        return match.get("timestamp_utc", "") if match else ""
    if ref.startswith("pressure:"):
        pressure_id = ref.split(":", 1)[1]
        pressure = governance["pressures"].get(pressure_id)
        event_id = pressure.get("recorded_event_id") if pressure else None
        match = next(
            (value for value in governance_records if value.get("event_id") == event_id),
            None,
        )
        return match.get("timestamp_utc", "") if match else ""
    if ref.startswith("evidence:"):
        evidence = find_evidence(ref.split(":", 1)[1])
        return evidence.get("timestamp_utc", "")
    pressure_matches = [
        value
        for value in governance["pressures"].values()
        if value.get("active") and ref in value.get("evidence_refs", [])
    ]
    if pressure_matches:
        event_ids = {value.get("recorded_event_id") for value in pressure_matches}
        timestamps = [
            value.get("timestamp_utc", "")
            for value in governance_records
            if value.get("event_id") in event_ids
        ]
        return max(timestamps, default="")
    return ""


def build_transition_planner_context(workspace, scope, contract, proposal, request):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    warnings = []
    governance_records, governance = reduce_governance(workspace, scope, warnings)
    if warnings or governance["issues"]:
        raise ValueError(
            "governance state is invalid: " + "; ".join(warnings + governance["issues"])
        )
    normalized_request = normalize_transition_request(request)
    adverse_kinds = {"contradiction", "risk", "cost", "staleness"}
    transition_pressures = [
        value
        for value in governance["pressures"].values()
        if value.get("active")
        and value.get("target_ref") == proposal.get("target_ref")
        and value.get("kind") in adverse_kinds
    ]
    evidence_refs = set(normalized_request["evidence_refs"])
    if proposal.get("disposition") == "collapse":
        evidence_refs.update(
            ref
            for pressure in transition_pressures
            for ref in pressure.get("evidence_refs", [])
        )
    evidence_refs = sorted(evidence_refs)
    transition_source_snapshots = []
    if evidence_refs:
        _, transition_source_snapshots = governance_evidence_refs(
            evidence_refs, workspace, scope
        )

    controls, control_warnings = load_control_records()
    if control_warnings:
        raise ValueError("control ledger is invalid: " + "; ".join(control_warnings))
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    scoped_control_count = len(
        [
            record
            for record in controls
            if normalized_workspace(record.get("workspace", ""))
            == normalized_workspace(workspace)
            and normalize_scope(record.get("scope")).casefold() == scope.casefold()
        ]
    )

    causal_frame_id = None
    trigger_ref = (proposal.get("trigger") or {}).get("ref", "")
    if trigger_ref.startswith("frame:"):
        causal_frame_id = trigger_ref.split(":", 1)[1]
    elif trigger_ref.startswith("pressure:"):
        pressure = governance["pressures"].get(trigger_ref.split(":", 1)[1])
        causal_frame_id = pressure.get("causal_frame_id") if pressure else None
    parent_frame_ids = list((proposal.get("successor_preview") or {}).get("parent_frame_ids", []))
    if not causal_frame_id and parent_frame_ids:
        causal_frame_id = parent_frame_ids[0]
    if not causal_frame_id:
        active_heads = sorted(
            value.get("head_frame_id")
            for value in contract.get("branches", {}).values()
            if value.get("status") == "active" and value.get("head_frame_id")
        )
        causal_frame_id = active_heads[0] if active_heads else None
    if not causal_frame_id:
        raise ValueError("transition has no causal Frame provenance")

    timestamp_candidates = [
        transition_trigger_timestamp(
            workspace, scope, proposal, governance_records, governance
        ),
        scoped_control.get("timestamp_utc", "") if scoped_control else "",
        governance_records[-1].get("timestamp_utc", "") if governance_records else "",
    ]
    for frame_id in parent_frame_ids:
        events = read_events(find_frame(frame_id))
        if events:
            timestamp_candidates.append(events[-1].get("timestamp_utc", ""))
    timestamp_utc = max((value for value in timestamp_candidates if value), default="")
    if not re.match(r"^\d{4}-\d{2}-\d{2}T", timestamp_utc):
        raise ValueError("transition cannot resolve a deterministic causal timestamp")
    return {
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "timestamp_utc": timestamp_utc,
        "workspace_control_head": workspace_control.get("event_id")
        if workspace_control
        else None,
        "scope_control_head": scoped_control.get("event_id") if scoped_control else None,
        "scoped_control_count": scoped_control_count,
        "governance_head_sequence": governance["head_sequence"],
        "targets": governance["targets"],
        "pressures": governance["pressures"],
        "branches": contract["branches"],
        "causal_frame_id": causal_frame_id,
        "transition_source_snapshots": transition_source_snapshots,
    }


def derive_transition_plan_from_args(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    contract = current_metabolic_contract(workspace, scope)
    proposal = transition_proposal_from_args(contract, args)
    if not proposal["admissible"]:
        return contract, proposal, None
    request = transition_materialization_request_from_args(args)
    context = build_transition_planner_context(
        workspace, scope, contract, proposal, request
    )
    plan = build_transition_plan(context, proposal, request)
    normalize_transaction_plan(plan, workspace, scope, proposal)
    return contract, proposal, plan


def command_metabolic_plan_transition(args):
    contract, proposal, plan = derive_transition_plan_from_args(args)
    result = {
        "schema_version": TRANSITION_PLAN_SCHEMA_VERSION,
        "contract_hash": contract["contract_hash"],
        "proposal": proposal,
        "plan": plan,
        "read_only": True,
        "background_loop_started": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if plan else 2


def materialize_transition_args(args, expected_contract_hash=None):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    assert_transaction_write_allowed(workspace, scope)
    request = transition_materialization_request_from_args(args)
    proposal_request = transition_proposal_request_from_args(args)
    caller_request_hash = transition_request_hash(proposal_request, request)
    records = load_transaction_records(
        state_root(), workspace_key(workspace), scope_key(scope)
    )
    state = reduce_transactions(records)
    if state["issues"]:
        raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
    existing_id = state["idempotency"].get(args.idempotency_key.strip())
    plan = None
    if existing_id:
        transaction = state["transactions"][existing_id]
        if transaction.get("request_hash") != caller_request_hash:
            raise ValueError("idempotency_conflict")
        if expected_contract_hash and transaction.get("contract_hash") != expected_contract_hash:
            raise ValueError("contract_head_changed")
        if transaction["state"] == "PREPARING":
            transaction, state = recover_transaction(
                state_root(),
                workspace=workspace,
                scope=scope,
                workspace_key=workspace_key(workspace),
                scope_key=scope_key(scope),
                transaction_id=existing_id,
            )
        if transaction["state"] == "PREPARED":
            transaction, state = commit_transaction(
                state_root(),
                workspace=workspace,
                scope=scope,
                workspace_key=workspace_key(workspace),
                scope_key=scope_key(scope),
                transaction_id=existing_id,
                expected_contract_hash=transaction["contract_hash"],
                current_contract_loader=lambda: current_metabolic_contract(
                    workspace, scope
                )["contract_hash"],
            )
        elif transaction["state"] in {"COMMITTED", "ABORTED"} and not transaction["receipt"]:
            transaction, state = recover_transaction(
                state_root(),
                workspace=workspace,
                scope=scope,
                workspace_key=workspace_key(workspace),
                scope_key=scope_key(scope),
                transaction_id=existing_id,
            )
    else:
        contract, proposal, plan = derive_transition_plan_from_args(args)
        if expected_contract_hash and contract["contract_hash"] != expected_contract_hash:
            raise ValueError("contract_head_changed")
        if not plan:
            return {
                "materialized": False,
                "proposal": proposal,
                "background_loop_started": False,
            }
        intents = normalize_transaction_plan(plan, workspace, scope, proposal)
        transaction, state = prepare_transaction(
            state_root(),
            workspace=workspace,
            scope=scope,
            workspace_key=workspace_key(workspace),
            scope_key=scope_key(scope),
            idempotency_key=args.idempotency_key,
            contract_hash=contract["contract_hash"],
            proposal=proposal,
            intents=intents,
            request_hash=plan["request_hash"],
            plan_hash=plan["plan_hash"],
        )
        transaction, state = commit_transaction(
            state_root(),
            workspace=workspace,
            scope=scope,
            workspace_key=workspace_key(workspace),
            scope_key=scope_key(scope),
            transaction_id=transaction["transaction_id"],
            expected_contract_hash=contract["contract_hash"],
            current_contract_loader=lambda: current_metabolic_contract(
                workspace, scope
            )["contract_hash"],
        )
    reset_visibility_snapshot()
    result = transaction_summary(transaction, state)
    refreshed_views = refresh_transaction_derived_views(workspace, scope, transaction)
    contract_after = current_metabolic_contract(workspace, scope)
    result.update(
        {
            "materialized": transaction["state"] == "COMMITTED",
            "successor_frame_id": (
                plan.get("successor_frame_id")
                if plan
                else next(
                    (
                        intent["payload"].get("frame_id")
                        for intent in transaction["intents"]
                        if intent["participant"] == "frame"
                        and intent["payload"].get("event_type") == "frame_opened"
                    ),
                    None,
                )
            ),
            "refreshed_views": refreshed_views,
            "contract_after_hash": contract_after["contract_hash"],
            "contract_after_status": contract_after["status"],
            "transactions_committed_this_invocation": 1
            if transaction["state"] == "COMMITTED" and not existing_id
            else 0,
            "background_loop_started": False,
        }
    )
    return result


def command_metabolic_materialize(args):
    result = materialize_transition_args(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("materialized") else 2


def validate_runner_manifest_domain(manifest):
    for event in manifest["events"]:
        proposal = event["proposal"]
        if proposal["trigger_kind"] not in METABOLIC_TRIGGER_KINDS:
            raise ValueError(f"unsupported runner trigger: {proposal['trigger_kind']}")
        if proposal["disposition"] not in METABOLIC_DISPOSITIONS or proposal["disposition"] == "yield":
            raise ValueError(f"unsupported runner disposition: {proposal['disposition']}")


def runner_step_namespace(workspace, scope, event, idempotency_key):
    proposal = event["proposal"]
    materialization = event["materialization"]
    return argparse.Namespace(
        workspace=workspace,
        scope=scope,
        idempotency_key=idempotency_key,
        trigger_kind=proposal["trigger_kind"],
        trigger_ref=proposal["trigger_ref"],
        disposition=proposal["disposition"],
        target_ref=proposal.get("target_ref"),
        candidate_target_ref=proposal.get("candidate_target_ref"),
        branch=list(proposal.get("branches", [])),
        death_line_match=list(proposal.get("death_line_matches", [])),
        changed_assumption=proposal.get("changed_assumption", ""),
        result_branch=materialization.get("result_branch", ""),
        level=materialization.get("level", "L2"),
        problem=materialization.get("problem", ""),
        success=materialization.get("success_criteria", ""),
        budget=materialization.get("budget", ""),
        why_reasonable=materialization.get("why_reasonable", ""),
        next_expected_evidence=materialization.get("next_expected_evidence", ""),
        next_death_line=materialization.get("death_line", ""),
        evidence=list(materialization.get("evidence_refs", [])),
        once_reasonable=materialization.get("once_reasonable", ""),
        invalidating_evidence=materialization.get("invalidating_evidence", ""),
        reusable_results=materialization.get("reusable_results", ""),
        forbidden_assumption=materialization.get("forbidden_assumption", ""),
    )


def reconcile_runner_materialization(workspace, scope, step_key):
    """Recover a 0.7c result from the durable transaction journal.

    This path deliberately avoids derived-cache refresh.  A committed
    coordinator decision is sufficient to journal the Runner step; caches can
    be rebuilt later without changing logical visibility.
    """

    records = load_transaction_records(
        state_root(), workspace_key(workspace), scope_key(scope)
    )
    state = reduce_transactions(records)
    if state["issues"]:
        raise ValueError("transaction journal is invalid: " + "; ".join(state["issues"]))
    transaction_id = state["idempotency"].get(step_key)
    if not transaction_id:
        return {"state": "NOT_STARTED", "materialized": False}
    transaction = state["transactions"][transaction_id]
    if transaction["state"] == "COMMITTED" and not transaction.get("receipt"):
        transaction, state = recover_transaction(
            state_root(),
            workspace=workspace,
            scope=scope,
            workspace_key=workspace_key(workspace),
            scope_key=scope_key(scope),
            transaction_id=transaction_id,
        )
    if transaction["state"] != "COMMITTED":
        return {
            "state": transaction["state"],
            "transaction_id": transaction_id,
            "materialized": False,
        }
    reset_visibility_snapshot()
    contract_after = current_metabolic_contract(workspace, scope)
    result = transaction_summary(transaction, state)
    result.update(
        {
            "materialized": True,
            "successor_frame_id": next(
                (
                    intent["payload"].get("frame_id")
                    for intent in transaction["intents"]
                    if intent["participant"] == "frame"
                    and intent["payload"].get("event_type") == "frame_opened"
                ),
                None,
            ),
            "contract_after_hash": contract_after["contract_hash"],
            "contract_after_status": contract_after["status"],
            "transactions_committed_this_invocation": 0,
            "recovered_after_materialization_exception": True,
            "background_loop_started": False,
        }
    )
    return result


def command_metabolic_run(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    assert_transaction_write_allowed(workspace, scope)
    manifest = normalize_runner_manifest(
        load_json_object(args.manifest_file, "runner manifest")
    )
    validate_runner_manifest_domain(manifest)

    def contract_loader():
        return current_metabolic_contract(workspace, scope)

    def materialize(event, step_key, expected_contract_hash):
        namespace = runner_step_namespace(workspace, scope, event, step_key)
        return materialize_transition_args(
            namespace, expected_contract_hash=expected_contract_hash
        )

    def reconcile(event, step_key, expected_contract_hash, exception):
        return reconcile_runner_materialization(workspace, scope, step_key)

    try:
        run, state = execute_run(
            state_root(),
            workspace=workspace,
            scope=scope,
            workspace_key=workspace_key(workspace),
            scope_key=scope_key(scope),
            idempotency_key=args.idempotency_key,
            manifest=manifest,
            contract_loader=contract_loader,
            materialize=materialize,
            reconcile_materialization=reconcile,
        )
    except RunnerConflict:
        print(
            json.dumps(
                {
                    "schema_version": RUNNER_SCHEMA_VERSION,
                    "started": False,
                    "status": "CONFLICT",
                    "reason": "runner_scope_claim_conflict",
                    "foreground_only": True,
                    "background_process_started": False,
                    "heartbeat_used": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3
    print(json.dumps(runner_summary(run, state), ensure_ascii=False, indent=2))


def command_metabolic_run_show(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    records = load_run_records(
        state_root(), workspace_key(workspace), scope_key(scope)
    )
    state = reduce_runs(records)
    if state["issues"]:
        raise ValueError("runner journal is invalid: " + "; ".join(state["issues"]))
    if args.run_id:
        run = state["runs"].get(args.run_id)
        if not run:
            raise ValueError("runner run not found")
        result = runner_summary(run, state)
    else:
        result = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "workspace": workspace,
            "scope": scope,
            "run_count": len(state["runs"]),
            "runs": [runner_summary(value, state) for _, value in sorted(state["runs"].items())],
            "journal_head": {
                "event_id": state["head_event_id"],
                "sequence": state["head_sequence"],
            },
            "foreground_only": True,
            "background_process_started": False,
            "heartbeat_used": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_metabolic_contract(args):
    result = current_metabolic_contract(args.workspace, args.scope)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["issues"] else 0


def command_metabolic_propose(args):
    contract = current_metabolic_contract(args.workspace, args.scope)
    result = propose_metabolic_transition(
        contract,
        expected_contract_hash=args.expected_contract_hash,
        trigger_kind=args.trigger_kind,
        trigger_ref=args.trigger_ref,
        disposition=args.disposition,
        target_ref=args.target_ref,
        candidate_target_ref=args.candidate_target_ref,
        branches=args.branch,
        death_line_matches=args.death_line_match,
        changed_assumption=args.changed_assumption,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["admissible"] else 2


def command_memory_index(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    index = rebuild_semantic_index(workspace, scope, warnings)
    result = {
        "rebuilt": True,
        "workspace": workspace,
        "scope": scope,
        "active_entry_count": index["active_entry_count"],
        "term_count": len(index["terms"]),
        "path": str(semantic_index_path(workspace, scope)),
    }
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_memory_disposition(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    entries = load_semantic_entries(workspace, scope, [])
    if args.memory_id not in {entry.get("memory_id") for entry in entries}:
        raise ValueError(f"unknown scoped semantic memory: {args.memory_id}")
    event = {
        "schema_version": SEMANTIC_DISPOSITION_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "authority": "explicit_semantic_retention_control_never_activation_authority",
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "memory_id": args.memory_id,
        "state": args.state,
        "reason": args.reason.strip(),
        "source": args.source,
    }
    path = semantic_disposition_directory(workspace, scope) / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    append_event(path, event)
    index = rebuild_semantic_index(workspace, scope, [])
    print(json.dumps({
        "saved": True,
        "event_id": event["event_id"],
        "memory_id": args.memory_id,
        "state": args.state,
        "history_preserved": True,
        "active_entry_count": index["active_entry_count"],
        "path": str(path),
    }, ensure_ascii=False, indent=2))


def command_memory_retention_plan(args):
    if args.max_active < 0:
        raise ValueError("--max-active cannot be negative")
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    entries = active_semantic_entries(
        load_semantic_entries(workspace, scope, warnings), workspace, scope, warnings
    )
    protected_kinds = {"constraint", "open_question"}
    protected = [entry for entry in entries if entry.get("kind") in protected_kinds or "critical" in entry.get("tags", [])]
    candidates = [entry for entry in entries if entry not in protected]
    overflow = max(0, len(entries) - args.max_active)
    proposed = candidates[:overflow]
    print(json.dumps({
        "workspace": workspace,
        "scope": scope,
        "mode": "plan_only_no_state_changed",
        "max_active": args.max_active,
        "active_count": len(entries),
        "protected_count": len(protected),
        "proposed_dormant": [
            {"memory_id": entry["memory_id"], "kind": entry["kind"], "summary": entry["summary"]}
            for entry in proposed
        ],
        "bounded": len(entries) - len(proposed) <= max(args.max_active, len(protected)),
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


def command_memory_conflicts(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    warnings = []
    entries = active_semantic_entries(
        load_semantic_entries(workspace, scope, warnings), workspace, scope, warnings
    )
    active_ids = {entry.get("memory_id") for entry in entries}
    conflicts = []
    seen = set()
    for entry in entries:
        for target in entry.get("conflicts_with", []):
            if target not in active_ids:
                continue
            pair = tuple(sorted((entry["memory_id"], target)))
            if pair in seen:
                continue
            seen.add(pair)
            conflicts.append({"memory_ids": list(pair), "declared_by": entry["memory_id"]})
    print(json.dumps({
        "workspace": workspace,
        "scope": scope,
        "unresolved_conflicts": conflicts,
        "authority": "explicit_conflict_evidence_never_activation_authority",
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


def frame_scope(events):
    if not events:
        return "workspace"
    return normalize_scope(events[0].get("data", {}).get("causal", {}).get("scope"))


def scoped_frame_paths(workspace, scope):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    paths = []
    root = state_root() / "frames"
    if not root.exists():
        return paths
    for path in sorted(root.glob("*/*.jsonl")):
        events = read_events(path)
        if not events:
            continue
        if normalized_workspace(events[0].get("workspace", "")) != normalized_workspace(workspace):
            continue
        if frame_scope(events).casefold() != scope.casefold():
            continue
        paths.append(path)
    return paths


def frame_source_state(paths):
    root = state_root()
    result = []
    for path in paths:
        stat = path.stat()
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = str(path)
        result.append({"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return result


def summarize_episode(events):
    first = events[0]
    opened = first.get("data", {})
    candidates = []
    holders = []
    evidence = []
    collapses = []
    traces = []
    tests = []
    for event in events[1:]:
        data = event.get("data", {})
        event_type = event.get("event_type")
        if event_type == "candidate_admitted":
            candidates.append(data)
        elif event_type == "holder_selected":
            holders.append(data)
        elif event_type == "evidence_observed":
            evidence.append(data)
        elif event_type == "discriminating_test_executed":
            tests.append(data)
        elif event_type == "minimal_unit_collapsed":
            collapses.append(data)
        elif event_type == "trace_emitted":
            traces.append(data)
    closed = next((event.get("data", {}) for event in reversed(events) if event.get("event_type") == "frame_closed"), None)
    text_parts = [
        str(opened.get("problem", "")),
        str(opened.get("success_criteria", "")),
        json.dumps(candidates, ensure_ascii=False),
        json.dumps(holders, ensure_ascii=False),
        json.dumps(evidence, ensure_ascii=False),
        json.dumps(tests, ensure_ascii=False),
        json.dumps(collapses, ensure_ascii=False),
        json.dumps(traces, ensure_ascii=False),
        json.dumps(closed or {}, ensure_ascii=False),
    ]
    causal = opened.get("causal", {})
    return {
        "frame_id": first.get("frame_id"),
        "workspace": first.get("workspace"),
        "scope": frame_scope(events),
        "level": first.get("level"),
        "branch_id": causal.get("branch_id"),
        "relation": causal.get("relation"),
        "parent_frame_ids": causal.get("parent_frame_ids", []),
        "problem": opened.get("problem"),
        "success_criteria": opened.get("success_criteria"),
        "candidates": candidates,
        "holders": holders,
        "tests": tests,
        "evidence": evidence,
        "collapses": collapses,
        "traces": traces,
        "outcome": (closed or {}).get("outcome", "open"),
        "verdict": (closed or {}).get("verdict", ""),
        "opened_at_utc": first.get("timestamp_utc"),
        "head_timestamp_utc": events[-1].get("timestamp_utc"),
        "source": f"frame:{first.get('frame_id')}",
        "tokens": semantic_tokens(" ".join(text_parts)),
    }


def episode_index_path(workspace, scope):
    return (
        state_root() / "memory" / "episode-indexes" / "workspaces"
        / workspace_key(workspace) / f"{scope_key(scope)}.json"
    )


def build_episode_index_value(workspace, scope):
    workspace = canonical_workspace(workspace)
    scope = normalize_scope(scope)
    paths = scoped_frame_paths(workspace, scope)
    episodes = [summarize_episode(read_events(path)) for path in paths]
    return {
        "schema_version": EPISODE_INDEX_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "authority": "derived_rebuildable_episode_index_never_activation_authority",
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "source_state": frame_source_state(paths),
        "episode_count": len(episodes),
        "episodes": episodes,
    }


def rebuild_episode_index(workspace, scope):
    value = build_episode_index_value(workspace, scope)
    write_json_atomic(episode_index_path(workspace, scope), value)
    return value


def load_episode_index(workspace, scope):
    path = episode_index_path(workspace, scope)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    return value if value.get("schema_version") == EPISODE_INDEX_SCHEMA_VERSION else None


def episode_index_is_fresh(index, workspace, scope):
    return bool(index) and index.get("source_state") == frame_source_state(scoped_frame_paths(workspace, scope))


def command_episode_index(args):
    index = rebuild_episode_index(args.workspace, args.scope)
    print(json.dumps({
        "rebuilt": True,
        "workspace": index["workspace"],
        "scope": index["scope"],
        "episode_count": index["episode_count"],
        "path": str(episode_index_path(args.workspace, args.scope)),
    }, ensure_ascii=False, indent=2))


def command_episode_search(args):
    if args.limit < 1 or args.limit > 100:
        raise ValueError("--limit must be between 1 and 100")
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    index = load_episode_index(workspace, scope)
    index_state = "FRESH"
    if not episode_index_is_fresh(index, workspace, scope):
        index = build_episode_index_value(workspace, scope)
        index_state = "STALE_FALLBACK_SCAN"
    query_tokens = set(semantic_tokens(args.query))
    results = []
    for episode in index["episodes"]:
        overlap = sorted(query_tokens.intersection(episode.get("tokens", [])))
        haystack = " ".join([
            str(episode.get("problem", "")), str(episode.get("verdict", "")),
            json.dumps(episode.get("evidence", []), ensure_ascii=False),
            json.dumps(episode.get("collapses", []), ensure_ascii=False),
        ]).casefold()
        score = len(overlap) + (2 if args.query.casefold() in haystack else 0)
        if score:
            item = dict(episode)
            item.pop("tokens", None)
            item["score"] = score
            item["matched_tokens"] = overlap
            item["source_snapshot"] = source_snapshot(item["source"], workspace)
            results.append(item)
    results.sort(key=lambda item: (item["score"], item.get("head_timestamp_utc", "")), reverse=True)
    print(json.dumps({
        "workspace": workspace,
        "scope": scope,
        "query": args.query,
        "index_state": index_state,
        "results": results[:args.limit],
        "authority": "episodic_recall_evidence_only_never_activation_authority",
    }, ensure_ascii=False, indent=2))


def command_memory_search(args):
    requested = canonical_workspace(args.workspace)
    if args.limit < 1 or args.limit > 100:
        raise ValueError("--limit must be between 1 and 100")
    semantic_root = state_root() / "memory" / "semantic" / "workspaces"
    workspace = None
    requested_path = Path(requested)
    for candidate_path in (requested_path, *requested_path.parents):
        candidate_root = semantic_root / workspace_key(str(candidate_path))
        first_file = next(iter(sorted(candidate_root.glob("*/*.jsonl"))), None)
        if not first_file:
            continue
        records = read_jsonl_records(first_file, [])
        if records:
            workspace = records[0].get("workspace") or canonical_workspace(str(candidate_path))
            break
    if not workspace:
        print(
            json.dumps(
                {
                    "matched": False,
                    "requested_workspace": requested,
                    "query": args.query,
                    "results": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    workspace_root = semantic_root / workspace_key(workspace)
    scopes = [normalize_scope(args.scope)] if args.scope else []
    if not scopes and workspace_root.exists():
        for directory in workspace_root.iterdir():
            if not directory.is_dir():
                continue
            first_file = next(iter(sorted(directory.glob("*.jsonl"))), None)
            records = read_jsonl_records(first_file, []) if first_file else []
            if records:
                scopes.append(normalize_scope(records[0].get("scope")))
    query_tokens = set(semantic_tokens(args.query))
    results = []
    index_states = {}
    warnings = []
    for scope in sorted(set(scopes), key=str.casefold):
        index = load_semantic_index(workspace, scope)
        if semantic_index_is_fresh(index, workspace, scope):
            index_state = "FRESH"
        else:
            index = build_semantic_index_value(workspace, scope, warnings)
            index_state = "STALE_FALLBACK_SCAN"
        index_states[scope] = index_state
        candidate_ids = set()
        for token in query_tokens:
            candidate_ids.update(index["terms"].get(token, []))
        for memory_id in candidate_ids:
            entry = index["entries"][memory_id]
            haystack = " ".join(
                [entry.get("summary", ""), entry.get("detail", ""), " ".join(entry.get("tags", []))]
            ).casefold()
            entry_tokens = set(semantic_tokens(haystack))
            overlap = sorted(query_tokens & entry_tokens)
            score = len(overlap)
            if args.query.casefold() in haystack:
                score += 2
            if args.query.casefold() in {tag.casefold() for tag in entry.get("tags", [])}:
                score += 3
            current_sources = source_snapshots(entry.get("sources", []), workspace)
            result = dict(entry)
            result["score"] = score
            result["matched_tokens"] = overlap
            result["sources_fresh"] = current_sources == entry.get("source_snapshots", [])
            results.append(result)
    results.sort(key=lambda item: (item["score"], item.get("timestamp_utc", "")), reverse=True)
    output = {
        "matched": True,
        "requested_workspace": requested,
        "matched_workspace": workspace,
        "query": args.query,
        "query_tokens": sorted(query_tokens),
        "index_states": index_states,
        "results": results[: args.limit],
        "authority": "recall_evidence_only_never_authorizes_continuation",
    }
    if warnings:
        output["warnings"] = warnings
    print(json.dumps(output, ensure_ascii=False, indent=2))


def command_memory_archive_plan(args):
    workspace = canonical_workspace(args.workspace)
    try:
        cutoff = datetime.strptime(args.before, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("--before must use YYYY-MM-DD") from exc
    controls, _ = load_control_records()
    projections, _ = load_projection_records()
    referenced_frames = {
        source.split(":", 1)[1]
        for projection in projections
        if normalized_workspace(projection.get("workspace", "")) == normalized_workspace(workspace)
        for source in projection.get("sources", [])
        if source.startswith("frame:")
    }
    semantic_root = state_root() / "memory" / "semantic" / "workspaces" / workspace_key(workspace)
    if semantic_root.exists():
        for directory in semantic_root.iterdir():
            if not directory.is_dir():
                continue
            records = []
            for path in sorted(directory.glob("*.jsonl")):
                records.extend(read_jsonl_records(path, []))
            for entry in active_semantic_entries(records):
                referenced_frames.update(
                    source.split(":", 1)[1]
                    for source in entry.get("sources", [])
                    if source.startswith("frame:")
                )
    eligible = []
    blocked = []
    for path in sorted((state_root() / "frames").glob("*/*.jsonl")):
        try:
            shard_date = datetime.strptime(path.parent.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if shard_date >= cutoff:
            continue
        events = read_events(path)
        if not events or normalized_workspace(events[0].get("workspace", "")) != normalized_workspace(workspace):
            continue
        frame_id = events[0].get("frame_id")
        reasons = []
        if events[-1].get("event_type") != "frame_closed":
            reasons.append("frame_not_closed")
        if frame_id in referenced_frames:
            reasons.append("referenced_by_current_projection_or_active_semantic_memory")
        item = {"frame_id": frame_id, "path": str(path)}
        if reasons:
            item["reasons"] = reasons
            blocked.append(item)
        else:
            eligible.append(item)
    print(
        json.dumps(
            {
                "workspace": workspace,
                "before": args.before,
                "mode": "plan_only_no_files_moved_or_deleted",
                "eligible_closed_frames": eligible,
                "blocked_frames": blocked,
                "control_record_count": len(
                    [
                        record
                        for record in controls
                        if normalized_workspace(record.get("workspace", "")) == normalized_workspace(workspace)
                    ]
                ),
                "policy": "control shards and referenced evidence remain hot until an integrity-manifest archive reader exists",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_memory_control(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    key = workspace_key(workspace)
    event = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        "workspace": workspace,
        "workspace_key": key,
        "scope": scope,
        "scope_key": scope_key(scope),
        "state": args.state,
        "directive": args.directive,
        "reason": args.reason or "explicit_user_direction",
        "resume_requires_confirmation": bool(
            args.resume_requires_confirmation or args.state != "active"
        ),
        "sources": args.source,
    }
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = state_root() / "memory" / "control" / "workspaces" / key / f"{date_dir}.jsonl"
    if scope.casefold() == "workspace":
        fence = workspace_contract_fence(state_root(), key)
    else:
        fence = contract_fence(state_root(), key, scope_key(scope))
    with fence:
        append_event(path, event)
    print(
        json.dumps(
            {
                "saved": True,
                "event_id": event["event_id"],
                "workspace": workspace,
                "scope": scope,
                "state": event["state"],
                "path": str(path),
            },
            ensure_ascii=False,
        )
    )


def projection_path(workspace, scope):
    return (
        state_root() / "memory" / "projections" / "workspaces"
        / workspace_key(workspace) / f"{scope_key(scope)}.json"
    )


def save_projection(projection):
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_PROJECTION_BYTES:
        raise ValueError(
            f"projection exceeds {MAX_PROJECTION_BYTES} bytes; reduce decisions, questions, or sources"
        )
    path = projection_path(projection["workspace"], projection["scope"])
    write_json_atomic(path, projection)
    return path


def command_projection_rebuild(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    controls, warnings = load_control_records()
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    effective_control = scoped_control or (workspace_control if scope == "workspace" else None)
    if not effective_control:
        raise ValueError("cannot rebuild projection without a scoped control head")

    episode_index = rebuild_episode_index(workspace, scope)
    episodes = episode_index.get("episodes", [])
    lineage_records = load_lineage_records(workspace, scope, warnings)
    lineage_state = derive_lineage_state(lineage_records)
    if lineage_state["issues"]:
        raise ValueError("cannot rebuild projection from invalid lineage: " + "; ".join(lineage_state["issues"]))
    active_branches = {
        branch: state for branch, state in lineage_state["branches"].items()
        if state.get("status") == "active"
    }
    selected_frame_id = None
    if args.branch:
        branch_state = active_branches.get(normalize_branch(args.branch))
        if not branch_state:
            raise ValueError(f"projection branch is not an active lineage head: {args.branch}")
        selected_frame_id = branch_state["head_frame_id"]
    elif len(active_branches) > 1:
        raise ValueError(
            "multiple active lineage branches require explicit --branch: "
            + ", ".join(sorted(active_branches))
        )
    elif len(active_branches) == 1:
        selected_frame_id = next(iter(active_branches.values()))["head_frame_id"]
    current_episode = next(
        (episode for episode in episodes if episode.get("frame_id") == selected_frame_id),
        None,
    )
    if current_episode is None and not active_branches:
        current_episode = max(episodes, key=lambda item: item.get("head_timestamp_utc", ""), default=None)
    semantic_warnings = []
    semantic_entries = active_semantic_entries(
        load_semantic_entries(workspace, scope, semantic_warnings),
        workspace,
        scope,
        semantic_warnings,
    )
    decisions = [entry["summary"] for entry in semantic_entries if entry.get("kind") in {"decision", "constraint", "fact", "lesson"}][-8:]
    open_questions = [entry["summary"] for entry in semantic_entries if entry.get("kind") == "open_question"][-8:]
    holder = (current_episode or {}).get("holders", [])
    latest_holder = holder[-1] if holder else {}
    if current_episode and current_episode.get("outcome") == "open":
        next_action = latest_holder.get("next_expected_evidence") or effective_control.get("directive")
        status = "open_frame"
    else:
        next_action = effective_control.get("directive") or "await explicit user task"
        status = effective_control.get("state", "unknown")
    focus = (current_episode or {}).get("problem") or effective_control.get("directive") or scope
    sources = []
    if current_episode:
        sources.append(current_episode["source"])
    sources.extend(f"memory:{entry['memory_id']}" for entry in semantic_entries[-12:])
    projection = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "projection_id": str(uuid.uuid4()),
        "authority": "derived_projection_verify_against_current_sources",
        "derivation": "deterministic_reducer_from_control_frames_and_active_semantic_memory",
        "workspace": workspace,
        "workspace_key": workspace_key(workspace),
        "scope": scope,
        "scope_key": scope_key(scope),
        "generated_at_utc": utc_now(),
        "focus": focus,
        "status": status,
        "next_action": next_action,
        "decisions": decisions,
        "open_questions": open_questions,
        "sources": sources,
        "source_snapshots": source_snapshots(sources, workspace),
        "control_heads": {
            "workspace": workspace_control.get("event_id") if workspace_control else None,
            "scope": scoped_control.get("event_id") if scoped_control else None,
        },
    }
    path = save_projection(projection)
    warnings.extend(semantic_warnings)
    print(json.dumps({
        "rebuilt": True,
        "projection_id": projection["projection_id"],
        "workspace": workspace,
        "scope": scope,
        "path": str(path),
        "source_count": len(sources),
        "episode_count": episode_index["episode_count"],
        "semantic_entry_count": len(semantic_entries),
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


def command_memory_update(args):
    workspace = canonical_workspace(args.workspace)
    scope = normalize_scope(args.scope)
    key = workspace_key(workspace)
    controls, _ = load_control_records()
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    projection = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "projection_id": str(uuid.uuid4()),
        "authority": "derived_projection_verify_against_current_sources",
        "workspace": workspace,
        "workspace_key": key,
        "scope": scope,
        "scope_key": scope_key(scope),
        "generated_at_utc": utc_now(),
        "focus": args.focus,
        "status": args.status,
        "next_action": args.next_action,
        "decisions": args.decision,
        "open_questions": args.open_question,
        "sources": args.source,
        "source_snapshots": source_snapshots(args.source, workspace),
        "control_heads": {
            "workspace": workspace_control.get("event_id") if workspace_control else None,
            "scope": scoped_control.get("event_id") if scoped_control else None,
        },
    }
    path = save_projection(projection)
    print(
        json.dumps(
            {
                "saved": True,
                "projection_id": projection["projection_id"],
                "workspace": workspace,
                "scope": scope,
                "path": str(path),
            },
            ensure_ascii=False,
        )
    )


def projection_freshness(projection, controls):
    if not projection:
        return {"fresh": False, "reason_codes": ["projection_missing"]}
    if projection.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        return {"fresh": False, "reason_codes": ["legacy_projection_schema"]}
    workspace = projection["workspace"]
    scope = projection["scope"]
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    current_heads = {
        "workspace": workspace_control.get("event_id") if workspace_control else None,
        "scope": scoped_control.get("event_id") if scoped_control else None,
    }
    reasons = []
    if projection.get("control_heads") != current_heads:
        reasons.append("control_head_changed")
    current_sources = source_snapshots(projection.get("sources", []), workspace)
    if projection.get("source_snapshots") != current_sources:
        reasons.append("source_changed")
    return {
        "fresh": not reasons,
        "reason_codes": reasons,
        "current_control_heads": current_heads,
    }


def evaluate_activation(workspace, scope, projection, controls):
    workspace_control = latest_control(controls, workspace, "workspace")
    scoped_control = latest_control(controls, workspace, scope)
    effective_control = scoped_control or (workspace_control if scope == "workspace" else None)
    freshness = projection_freshness(projection, controls)
    reasons = list(freshness["reason_codes"])

    if workspace_control and workspace_control.get("state") == "paused":
        state = "PAUSED"
        reasons.insert(0, "workspace_control_paused")
    elif scoped_control and scoped_control.get("state") == "paused":
        state = "PAUSED"
        reasons.insert(0, "scope_control_paused")
    elif not projection and not workspace_control and not scoped_control:
        state = "NO_CONTEXT"
        reasons = ["no_workspace_memory"]
    elif not effective_control:
        state = "CONFIRM_REQUIRED"
        reasons.insert(0, "scope_control_missing")
    elif effective_control.get("state") in {"blocked", "closed"}:
        state = "CONFIRM_REQUIRED"
        reasons.insert(0, f"scope_control_{effective_control['state']}")
    elif effective_control.get("resume_requires_confirmation"):
        state = "CONFIRM_REQUIRED"
        reasons.insert(0, "resume_requires_confirmation")
    elif not freshness["fresh"]:
        state = "STALE"
    else:
        state = "ACTIVE"

    instructions = {
        "ACTIVE": "Continuation is allowed after verifying task-relevant sources.",
        "PAUSED": "Report recalled state only. Do not execute until the user explicitly resumes this scope.",
        "CONFIRM_REQUIRED": "Do not continue prior work. Ask for a specific confirmation or direction.",
        "STALE": "Do not continue from this projection. Rebuild it from current controls and sources first.",
        "NO_CONTEXT": "Do not claim prior project context. Work only from the current user request.",
    }
    return {
        "scope": scope,
        "state": state,
        "continuation_allowed": state == "ACTIVE",
        "reason_codes": list(dict.fromkeys(reasons)),
        "instruction": instructions[state],
        "control": effective_control,
        "workspace_control": workspace_control,
        "projection": projection,
        "freshness": freshness,
    }


def command_memory_recall(args):
    requested = canonical_workspace(args.workspace)
    controls, control_warnings = load_control_records()
    projections, projection_warnings = load_projection_records()
    warnings = control_warnings + projection_warnings
    workspace_candidates = {
        record.get("workspace")
        for record in controls + projections
        if record.get("workspace") and is_workspace_match(requested, record["workspace"])
    }
    if not workspace_candidates:
        result = {
            "matched": False,
            "requested_workspace": requested,
            "activation": {
                "state": "NO_CONTEXT",
                "continuation_allowed": False,
                "reason_codes": ["no_workspace_memory"],
                "instruction": "Do not claim prior project context. Work only from the current user request.",
            },
            "projection": None,
        }
        if warnings:
            result["warnings"] = warnings
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    workspace = max(workspace_candidates, key=lambda item: len(normalized_workspace(item)))
    workspace_controls = [
        record for record in controls if normalized_workspace(record["workspace"]) == normalized_workspace(workspace)
    ]
    workspace_projections = [
        record
        for record in projections
        if normalized_workspace(record["workspace"]) == normalized_workspace(workspace)
    ]
    current_projections = [
        record for record in workspace_projections if record.get("schema_version") == PROJECTION_SCHEMA_VERSION
    ]
    projection_pool = current_projections or workspace_projections

    if args.scope:
        scopes = [normalize_scope(args.scope)]
    else:
        scopes = sorted(
            {
                normalize_scope(record.get("scope"))
                for record in projection_pool
            }
            | {
                normalize_scope(record.get("scope"))
                for record in workspace_controls
                if normalize_scope(record.get("scope")) != "workspace"
            }
        )
        if not scopes:
            scopes = ["workspace"]

    evaluations = []
    for scope in scopes:
        scoped_projections = [
            record
            for record in projection_pool
            if normalize_scope(record.get("scope")).casefold() == scope.casefold()
            or (
                record.get("schema_version") == LEGACY_PROJECTION_SCHEMA_VERSION
                and scope == "workspace"
            )
        ]
        projection = latest_record(scoped_projections)
        evaluations.append(evaluate_activation(workspace, scope, projection, workspace_controls))

    if args.scope or len(evaluations) == 1:
        selected = evaluations[0]
    else:
        active = [item for item in evaluations if item["state"] == "ACTIVE"]
        selected = active[0] if len(active) == 1 else None

    if selected:
        activation = {
            key: selected[key]
            for key in ("scope", "state", "continuation_allowed", "reason_codes", "instruction")
        }
        projection = selected["projection"]
        control = selected["control"]
        freshness = selected["freshness"]
    else:
        activation = {
            "state": "CONFIRM_REQUIRED",
            "continuation_allowed": False,
            "reason_codes": ["multiple_or_ambiguous_scopes"],
            "instruction": "Do not continue prior work. Ask which scope the user wants to resume.",
        }
        projection = None
        control = None
        freshness = None

    result = {
        "matched": True,
        "requested_workspace": requested,
        "matched_workspace": workspace,
        "activation": activation,
        "control": control,
        "projection": projection,
        "freshness": freshness,
        "scope_candidates": [
            {
                "scope": item["scope"],
                "state": item["state"],
                "continuation_allowed": item["continuation_allowed"],
                "reason_codes": item["reason_codes"],
            }
            for item in evaluations
        ],
    }
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open", help="open an L2/L3 frame")
    open_parser.add_argument("--level", choices=ALLOWED_LEVELS, required=True)
    open_parser.add_argument("--problem", required=True)
    open_parser.add_argument("--success", required=True)
    open_parser.add_argument("--budget")
    open_parser.add_argument("--workspace", default=os.getcwd())
    open_parser.add_argument(
        "--scope",
        help="enable Memory 0.4 causal lineage for this scope; omit for a legacy standalone frame",
    )
    open_parser.add_argument("--branch", default="main")
    open_parser.add_argument("--relation", choices=CAUSAL_RELATIONS)
    open_parser.add_argument("--parent", action="append", default=[])
    open_parser.set_defaults(func=command_open)

    lineage_show_parser = subparsers.add_parser(
        "lineage-show", help="validate and show causal frame lineage plus concurrent branch heads"
    )
    lineage_show_parser.add_argument("--workspace", default=os.getcwd())
    lineage_show_parser.add_argument("--scope", required=True)
    lineage_show_parser.set_defaults(func=command_lineage_show)

    prospective_register_parser = subparsers.add_parser(
        "prospective-register",
        help="register one bounded future goal and its causal event condition",
    )
    prospective_register_parser.add_argument("--workspace", default=os.getcwd())
    prospective_register_parser.add_argument("--scope", required=True)
    prospective_register_parser.add_argument("--goal-ref", required=True)
    prospective_register_parser.add_argument("--description", required=True)
    prospective_register_parser.add_argument(
        "--event-kind", choices=PROSPECTIVE_EVENT_KINDS, required=True
    )
    prospective_register_parser.add_argument("--event-name", required=True)
    prospective_register_parser.add_argument("--not-before")
    prospective_register_parser.add_argument("--budget", default="one explicit transition")
    prospective_register_parser.add_argument("--death-line", required=True)
    prospective_register_parser.add_argument("--source", action="append", required=True)
    prospective_register_parser.set_defaults(func=command_prospective_register)

    prospective_observe_parser = subparsers.add_parser(
        "prospective-observe",
        help="append one user, tool, environment, or authorized clock causal event",
    )
    prospective_observe_parser.add_argument("--workspace", default=os.getcwd())
    prospective_observe_parser.add_argument("--scope", required=True)
    prospective_observe_parser.add_argument(
        "--kind", choices=PROSPECTIVE_EVENT_KINDS, required=True
    )
    prospective_observe_parser.add_argument("--name", required=True)
    prospective_observe_parser.add_argument("--goal-ref")
    prospective_observe_parser.add_argument("--payload-hash", default="")
    prospective_observe_parser.add_argument("--source", action="append", required=True)
    prospective_observe_parser.set_defaults(func=command_prospective_observe)

    prospective_cycle_parser = subparsers.add_parser(
        "prospective-cycle",
        help="derive one read-only bounded cycle plan from an observed causal event",
    )
    prospective_cycle_parser.add_argument("--workspace", default=os.getcwd())
    prospective_cycle_parser.add_argument("--scope", required=True)
    prospective_cycle_parser.add_argument("--causal-event-id", required=True)
    prospective_cycle_parser.set_defaults(func=command_prospective_cycle)

    prospective_transition_parser = subparsers.add_parser(
        "prospective-transition",
        help="explicitly satisfy, supersede, or collapse one active future goal",
    )
    prospective_transition_parser.add_argument("--workspace", default=os.getcwd())
    prospective_transition_parser.add_argument("--scope", required=True)
    prospective_transition_parser.add_argument("--goal-ref", required=True)
    prospective_transition_parser.add_argument(
        "--state",
        choices=tuple(state.casefold() for state in PROSPECTIVE_GOAL_STATES[1:]),
        required=True,
    )
    prospective_transition_parser.add_argument("--causal-event-id")
    prospective_transition_parser.add_argument("--replacement-goal-ref")
    prospective_transition_parser.add_argument("--reason", default="")
    prospective_transition_parser.add_argument("--source", action="append", required=True)
    prospective_transition_parser.set_defaults(func=command_prospective_transition)

    prospective_show_parser = subparsers.add_parser(
        "prospective-show", help="replay and show scoped future goals and causal events"
    )
    prospective_show_parser.add_argument("--workspace", default=os.getcwd())
    prospective_show_parser.add_argument("--scope", required=True)
    prospective_show_parser.set_defaults(func=command_prospective_show)

    governance_target_parser = subparsers.add_parser(
        "governance-target-register",
        help="register one source-backed governance target in the append-only ledger",
    )
    governance_target_parser.add_argument("--workspace", default=os.getcwd())
    governance_target_parser.add_argument("--scope", required=True)
    governance_target_parser.add_argument("--target-ref", required=True)
    governance_target_parser.add_argument(
        "--target-kind", choices=GOVERNANCE_TARGET_KINDS, required=True
    )
    governance_target_parser.add_argument("--scale", choices=GOVERNANCE_SCALES, required=True)
    governance_target_parser.add_argument("--parent-target-ref")
    governance_target_parser.add_argument("--death-line", action="append", default=[])
    governance_target_parser.add_argument("--reentry-condition", default="")
    governance_target_parser.add_argument("--budget", default="proportional")
    governance_target_parser.add_argument("--source", action="append", required=True)
    governance_target_parser.set_defaults(func=command_governance_target_register)

    governance_pressure_parser = subparsers.add_parser(
        "governance-pressure-record",
        help="record typed source-backed feedback pressure against a governance target",
    )
    governance_pressure_parser.add_argument("--workspace", default=os.getcwd())
    governance_pressure_parser.add_argument("--scope", required=True)
    governance_pressure_parser.add_argument("--target-ref", required=True)
    governance_pressure_parser.add_argument("--kind", choices=PRESSURE_KINDS, required=True)
    governance_pressure_parser.add_argument(
        "--strength", choices=PRESSURE_STRENGTHS, required=True
    )
    governance_pressure_parser.add_argument("--evidence", action="append", required=True)
    governance_pressure_parser.add_argument("--required-change", required=True)
    governance_pressure_parser.add_argument("--frame-id", required=True)
    governance_pressure_parser.add_argument("--persistence-audit-id")
    governance_pressure_parser.set_defaults(func=command_governance_pressure_record)

    governance_invalidate_parser = subparsers.add_parser(
        "governance-pressure-invalidate",
        help="append an explicit pressure invalidation without deleting history",
    )
    governance_invalidate_parser.add_argument("--workspace", default=os.getcwd())
    governance_invalidate_parser.add_argument("--scope", required=True)
    governance_invalidate_parser.add_argument("--pressure-id", required=True)
    governance_invalidate_parser.add_argument("--reason", required=True)
    governance_invalidate_parser.set_defaults(func=command_governance_pressure_invalidate)

    governance_propagate_parser = subparsers.add_parser(
        "governance-pressure-propagate",
        help="explicitly propagate active pressure to a broader registered target scale",
    )
    governance_propagate_parser.add_argument("--workspace", default=os.getcwd())
    governance_propagate_parser.add_argument("--scope", required=True)
    governance_propagate_parser.add_argument("--source-pressure-id", action="append", required=True)
    governance_propagate_parser.add_argument("--target-ref", required=True)
    governance_propagate_parser.add_argument("--reason", required=True)
    governance_propagate_parser.add_argument("--frame-id", required=True)
    governance_propagate_parser.add_argument("--persistence-audit-id")
    governance_propagate_parser.set_defaults(func=command_governance_pressure_propagate)

    governance_transition_parser = subparsers.add_parser(
        "governance-target-transition",
        help="append a validated governance target lifecycle transition",
    )
    governance_transition_parser.add_argument("--workspace", default=os.getcwd())
    governance_transition_parser.add_argument("--scope", required=True)
    governance_transition_parser.add_argument("--target-ref", required=True)
    governance_transition_parser.add_argument(
        "--transition",
        choices=(
            "warn",
            "probation",
            "stabilize",
            "pause",
            "resume",
            "collapse",
            "complete",
            "block",
            "supersede",
            "reenter",
        ),
        required=True,
    )
    governance_transition_parser.add_argument("--pressure-id", action="append", default=[])
    governance_transition_parser.add_argument("--evidence", action="append", required=True)
    governance_transition_parser.add_argument("--frame-id", required=True)
    governance_transition_parser.add_argument("--death-line-match", action="append", default=[])
    governance_transition_parser.add_argument("--once-reasonable", default="")
    governance_transition_parser.add_argument("--invalidating-evidence", default="")
    governance_transition_parser.add_argument("--reusable-results", default="")
    governance_transition_parser.add_argument("--forbidden-assumption", default="")
    governance_transition_parser.add_argument("--replacement-target-ref")
    governance_transition_parser.set_defaults(func=command_governance_target_transition)

    governance_show_parser = subparsers.add_parser(
        "governance-show", help="deterministically replay and show scoped Memory 0.6 governance state"
    )
    governance_show_parser.add_argument("--workspace", default=os.getcwd())
    governance_show_parser.add_argument("--scope", required=True)
    governance_show_parser.add_argument("--target-ref")
    governance_show_parser.set_defaults(func=command_governance_show)

    self_project_parser = subparsers.add_parser(
        "self-project", help="derive a read-only non-authoritative current governance projection"
    )
    self_project_parser.add_argument("--workspace", default=os.getcwd())
    self_project_parser.add_argument("--scope", required=True)
    self_project_parser.set_defaults(func=command_self_project)

    metabolic_contract_parser = subparsers.add_parser(
        "metabolic-contract",
        help="derive a deterministic read-only Memory 0.7a metabolic contract",
    )
    metabolic_contract_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_contract_parser.add_argument("--scope", required=True)
    metabolic_contract_parser.set_defaults(func=command_metabolic_contract)

    metabolic_propose_parser = subparsers.add_parser(
        "metabolic-propose",
        help="validate one read-only Memory 0.7a transition proposal without writing state",
    )
    metabolic_propose_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_propose_parser.add_argument("--scope", required=True)
    metabolic_propose_parser.add_argument("--expected-contract-hash", required=True)
    metabolic_propose_parser.add_argument(
        "--trigger-kind", choices=METABOLIC_TRIGGER_KINDS, required=True
    )
    metabolic_propose_parser.add_argument("--trigger-ref", required=True)
    metabolic_propose_parser.add_argument(
        "--disposition", choices=METABOLIC_DISPOSITIONS, required=True
    )
    metabolic_propose_parser.add_argument("--target-ref")
    metabolic_propose_parser.add_argument("--candidate-target-ref")
    metabolic_propose_parser.add_argument("--branch", action="append", default=[])
    metabolic_propose_parser.add_argument(
        "--death-line-match", action="append", default=[]
    )
    metabolic_propose_parser.add_argument("--changed-assumption", default="")
    metabolic_propose_parser.set_defaults(func=command_metabolic_propose)

    metabolic_prepare_parser = subparsers.add_parser(
        "metabolic-prepare",
        help="stage one explicit Memory 0.7b transaction as invisible pending events",
    )
    metabolic_prepare_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_prepare_parser.add_argument("--scope", required=True)
    metabolic_prepare_parser.add_argument("--idempotency-key", required=True)
    metabolic_prepare_parser.add_argument("--proposal-file", required=True)
    metabolic_prepare_parser.add_argument("--plan-file", required=True)
    metabolic_prepare_parser.set_defaults(func=command_metabolic_prepare)

    metabolic_commit_parser = subparsers.add_parser(
        "metabolic-commit",
        help="commit one fully prepared Memory 0.7b transaction against current heads",
    )
    metabolic_commit_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_commit_parser.add_argument("--scope", required=True)
    metabolic_commit_parser.add_argument("--transaction-id", required=True)
    metabolic_commit_parser.add_argument("--expected-contract-hash", required=True)
    metabolic_commit_parser.set_defaults(func=command_metabolic_commit)

    metabolic_abort_parser = subparsers.add_parser(
        "metabolic-abort",
        help="abort one uncommitted Memory 0.7b transaction and emit its receipt",
    )
    metabolic_abort_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_abort_parser.add_argument("--scope", required=True)
    metabolic_abort_parser.add_argument("--transaction-id", required=True)
    metabolic_abort_parser.add_argument("--reason", required=True)
    metabolic_abort_parser.set_defaults(func=command_metabolic_abort)

    metabolic_recover_parser = subparsers.add_parser(
        "metabolic-recover",
        help="explicitly recover staging or a missing receipt without deciding commit",
    )
    metabolic_recover_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_recover_parser.add_argument("--scope", required=True)
    metabolic_recover_parser.add_argument("--transaction-id", required=True)
    metabolic_recover_parser.set_defaults(func=command_metabolic_recover)

    metabolic_show_parser = subparsers.add_parser(
        "metabolic-transaction-show",
        help="replay and show the append-only Memory 0.7b transaction journal",
    )
    metabolic_show_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_show_parser.add_argument("--scope", required=True)
    metabolic_show_parser.add_argument("--transaction-id")
    metabolic_show_parser.set_defaults(func=command_metabolic_transaction_show)

    for command_name, help_text, handler in (
        (
            "metabolic-plan-transition",
            "derive one deterministic read-only Memory 0.7c transition event plan",
            command_metabolic_plan_transition,
        ),
        (
            "metabolic-materialize",
            "explicitly materialize one Memory 0.7c transition through one 0.7b transaction",
            command_metabolic_materialize,
        ),
    ):
        transition_parser = subparsers.add_parser(command_name, help=help_text)
        transition_parser.add_argument("--workspace", default=os.getcwd())
        transition_parser.add_argument("--scope", required=True)
        transition_parser.add_argument("--idempotency-key", required=True)
        transition_parser.add_argument(
            "--trigger-kind", choices=METABOLIC_TRIGGER_KINDS, required=True
        )
        transition_parser.add_argument("--trigger-ref", required=True)
        transition_parser.add_argument(
            "--disposition",
            choices=tuple(value for value in METABOLIC_DISPOSITIONS if value != "yield"),
            required=True,
        )
        transition_parser.add_argument("--target-ref")
        transition_parser.add_argument("--candidate-target-ref")
        transition_parser.add_argument("--branch", action="append", default=[])
        transition_parser.add_argument("--death-line-match", action="append", default=[])
        transition_parser.add_argument("--changed-assumption", default="")
        transition_parser.add_argument("--result-branch", default="")
        transition_parser.add_argument("--level", choices=ALLOWED_LEVELS, default="L2")
        transition_parser.add_argument("--problem", default="")
        transition_parser.add_argument("--success", default="")
        transition_parser.add_argument("--budget", default="")
        transition_parser.add_argument("--why-reasonable", default="")
        transition_parser.add_argument("--next-expected-evidence", default="")
        transition_parser.add_argument("--next-death-line", default="")
        transition_parser.add_argument("--evidence", action="append", default=[])
        transition_parser.add_argument("--once-reasonable", default="")
        transition_parser.add_argument("--invalidating-evidence", default="")
        transition_parser.add_argument("--reusable-results", default="")
        transition_parser.add_argument("--forbidden-assumption", default="")
        transition_parser.set_defaults(func=handler)

    metabolic_run_parser = subparsers.add_parser(
        "metabolic-run",
        help="execute one explicit foreground Memory 0.7d bounded event manifest",
    )
    metabolic_run_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_run_parser.add_argument("--scope", required=True)
    metabolic_run_parser.add_argument("--idempotency-key", required=True)
    metabolic_run_parser.add_argument("--manifest-file", required=True)
    metabolic_run_parser.set_defaults(func=command_metabolic_run)

    metabolic_run_show_parser = subparsers.add_parser(
        "metabolic-run-show",
        help="replay and show the append-only Memory 0.7d Run Journal",
    )
    metabolic_run_show_parser.add_argument("--workspace", default=os.getcwd())
    metabolic_run_show_parser.add_argument("--scope", required=True)
    metabolic_run_show_parser.add_argument("--run-id")
    metabolic_run_show_parser.set_defaults(func=command_metabolic_run_show)

    event_parser = subparsers.add_parser("event", help="append a lifecycle event")
    event_parser.add_argument("--frame-id", required=True)
    event_parser.add_argument("--type", choices=sorted(ALLOWED_EVENTS - {"frame_opened", "frame_closed"}), required=True)
    event_parser.add_argument("--field", action="append", default=[])
    event_parser.set_defaults(func=command_event)

    close_parser = subparsers.add_parser("close", help="close a frame")
    close_parser.add_argument("--frame-id", required=True)
    close_parser.add_argument("--outcome", choices=("success", "partial", "failed", "blocked"), required=True)
    close_parser.add_argument("--verdict", required=True)
    close_parser.set_defaults(func=command_close)

    validate_parser = subparsers.add_parser("validate", help="validate one frame")
    validate_parser.add_argument("--frame-id", required=True)
    validate_parser.add_argument("--require-closed", action="store_true")
    validate_parser.set_defaults(func=command_validate)

    show_parser = subparsers.add_parser("show", help="print one frame")
    show_parser.add_argument("--frame-id", required=True)
    show_parser.set_defaults(func=command_show)

    frame_repair_parser = subparsers.add_parser(
        "frame-repair", help="append a repair overlay for one historical frame event"
    )
    frame_repair_parser.add_argument("--frame-id", required=True)
    frame_repair_parser.add_argument("--target-event-id", required=True)
    frame_repair_parser.add_argument("--reason", required=True)
    frame_repair_parser.add_argument("--field", action="append", default=[])
    frame_repair_parser.add_argument("--require-closed", action="store_true")
    frame_repair_parser.set_defaults(func=command_frame_repair)

    memory_control_parser = subparsers.add_parser(
        "memory-control", help="append an authoritative workspace or scope control event"
    )
    memory_control_parser.add_argument("--workspace", default=os.getcwd())
    memory_control_parser.add_argument("--scope", default="workspace")
    memory_control_parser.add_argument("--state", choices=CONTROL_STATES, required=True)
    memory_control_parser.add_argument("--directive", required=True)
    memory_control_parser.add_argument("--reason")
    memory_control_parser.add_argument("--resume-requires-confirmation", action="store_true")
    memory_control_parser.add_argument("--source", action="append", default=[])
    memory_control_parser.set_defaults(func=command_memory_control)

    memory_update_parser = subparsers.add_parser(
        "memory-update", help="write a derived workspace projection for later recall"
    )
    memory_update_parser.add_argument("--workspace", default=os.getcwd())
    memory_update_parser.add_argument("--scope", default="workspace")
    memory_update_parser.add_argument("--focus", required=True)
    memory_update_parser.add_argument("--status", required=True)
    memory_update_parser.add_argument("--next", dest="next_action", required=True)
    memory_update_parser.add_argument("--decision", action="append", default=[])
    memory_update_parser.add_argument("--open-question", action="append", default=[])
    memory_update_parser.add_argument("--source", action="append", default=[])
    memory_update_parser.set_defaults(func=command_memory_update)

    projection_rebuild_parser = subparsers.add_parser(
        "projection-rebuild",
        help="rebuild a bounded current projection from controls, Frames, and active semantic memory",
    )
    projection_rebuild_parser.add_argument("--workspace", default=os.getcwd())
    projection_rebuild_parser.add_argument("--scope", required=True)
    projection_rebuild_parser.add_argument("--branch")
    projection_rebuild_parser.set_defaults(func=command_projection_rebuild)

    memory_recall_parser = subparsers.add_parser(
        "memory-recall", help="evaluate the activation gate for the nearest workspace memory"
    )
    memory_recall_parser.add_argument("--workspace", default=os.getcwd())
    memory_recall_parser.add_argument("--scope")
    memory_recall_parser.set_defaults(func=command_memory_recall)

    evidence_capture_parser = subparsers.add_parser(
        "evidence-capture",
        help="persist one selected source-backed conversation evidence fragment",
    )
    evidence_capture_parser.add_argument("--workspace", default=os.getcwd())
    evidence_capture_parser.add_argument("--scope", default="workspace")
    evidence_capture_parser.add_argument("--signal", choices=EVIDENCE_SIGNALS, required=True)
    evidence_capture_parser.add_argument("--claim", required=True)
    evidence_capture_parser.add_argument("--source", action="append", required=True)
    evidence_capture_parser.add_argument("--tag", action="append", default=[])
    evidence_capture_parser.set_defaults(func=command_evidence_capture)

    evidence_promote_parser = subparsers.add_parser(
        "evidence-promote",
        help="apply the audited promotion gate to one conversation evidence fragment",
    )
    evidence_promote_parser.add_argument("--workspace", default=os.getcwd())
    evidence_promote_parser.add_argument("--scope", default="workspace")
    evidence_promote_parser.add_argument("--evidence-id", required=True)
    evidence_promote_parser.add_argument("--kind", choices=SEMANTIC_KINDS, required=True)
    evidence_promote_parser.add_argument("--summary", required=True)
    evidence_promote_parser.add_argument("--detail", default="")
    evidence_promote_parser.add_argument("--tag", action="append", default=[])
    evidence_promote_parser.add_argument("--source", action="append", default=[])
    evidence_promote_parser.add_argument("--supersedes", action="append", default=[])
    evidence_promote_parser.add_argument("--conflicts-with", action="append", default=[])
    evidence_promote_parser.add_argument("--stable", action="store_true")
    evidence_promote_parser.add_argument("--reusable", action="store_true")
    evidence_promote_parser.add_argument("--privacy-reviewed", action="store_true")
    evidence_promote_parser.set_defaults(func=command_evidence_promote)

    evidence_disposition_parser = subparsers.add_parser(
        "evidence-disposition",
        help="append a terminal withdrawn, expired, or superseded evidence lifecycle event",
    )
    evidence_disposition_parser.add_argument("--workspace", default=os.getcwd())
    evidence_disposition_parser.add_argument("--scope", default="workspace")
    evidence_disposition_parser.add_argument("--evidence-id", required=True)
    evidence_disposition_parser.add_argument(
        "--state", choices=EVIDENCE_TERMINAL_STATES, required=True
    )
    evidence_disposition_parser.add_argument("--replacement-evidence-id")
    evidence_disposition_parser.add_argument("--reason", required=True)
    evidence_disposition_parser.set_defaults(func=command_evidence_disposition)

    evidence_show_parser = subparsers.add_parser(
        "evidence-show", help="show scoped conversation evidence and promotion status"
    )
    evidence_show_parser.add_argument("--workspace", default=os.getcwd())
    evidence_show_parser.add_argument("--scope", default="workspace")
    evidence_show_parser.add_argument("--evidence-id")
    evidence_show_parser.set_defaults(func=command_evidence_show)

    persistence_audit_parser = subparsers.add_parser(
        "persistence-audit",
        help="record promoted evidence or an explicit non-persistence reason before frame transitions",
    )
    persistence_audit_parser.add_argument("--frame-id", required=True)
    persistence_audit_parser.add_argument(
        "--trigger", choices=PERSISTENCE_AUDIT_TRIGGERS, required=True
    )
    persistence_audit_parser.add_argument(
        "--decision", choices=("promoted", "not_persisted"), required=True
    )
    persistence_audit_parser.add_argument("--evidence-id")
    persistence_audit_parser.add_argument("--reason", default="")
    persistence_audit_parser.set_defaults(func=command_persistence_audit)

    persistence_audit_show_parser = subparsers.add_parser(
        "persistence-audit-show",
        help="show required and completed persistence audit triggers for one frame",
    )
    persistence_audit_show_parser.add_argument("--frame-id", required=True)
    persistence_audit_show_parser.set_defaults(func=command_persistence_audit_show)

    memory_consolidate_parser = subparsers.add_parser(
        "memory-consolidate",
        help="append one source-backed semantic memory entry and rebuild its index",
    )
    memory_consolidate_parser.add_argument("--workspace", default=os.getcwd())
    memory_consolidate_parser.add_argument("--scope", default="workspace")
    memory_consolidate_parser.add_argument("--kind", choices=SEMANTIC_KINDS, required=True)
    memory_consolidate_parser.add_argument("--summary", required=True)
    memory_consolidate_parser.add_argument("--detail", default="")
    memory_consolidate_parser.add_argument("--tag", action="append", default=[])
    memory_consolidate_parser.add_argument("--source", action="append", required=True)
    memory_consolidate_parser.add_argument("--supersedes", action="append", default=[])
    memory_consolidate_parser.add_argument("--conflicts-with", action="append", default=[])
    memory_consolidate_parser.set_defaults(func=command_memory_consolidate)

    memory_index_parser = subparsers.add_parser(
        "memory-index", help="rebuild the replaceable semantic memory index"
    )
    memory_index_parser.add_argument("--workspace", default=os.getcwd())
    memory_index_parser.add_argument("--scope", default="workspace")
    memory_index_parser.set_defaults(func=command_memory_index)

    memory_disposition_parser = subparsers.add_parser(
        "memory-disposition",
        help="append an explicit active, dormant, or retired semantic-memory disposition",
    )
    memory_disposition_parser.add_argument("--workspace", default=os.getcwd())
    memory_disposition_parser.add_argument("--scope", required=True)
    memory_disposition_parser.add_argument("--memory-id", required=True)
    memory_disposition_parser.add_argument("--state", choices=SEMANTIC_DISPOSITIONS, required=True)
    memory_disposition_parser.add_argument("--reason", required=True)
    memory_disposition_parser.add_argument("--source", action="append", default=[])
    memory_disposition_parser.set_defaults(func=command_memory_disposition)

    memory_retention_parser = subparsers.add_parser(
        "memory-retention-plan",
        help="plan bounded semantic forgetting without deleting history",
    )
    memory_retention_parser.add_argument("--workspace", default=os.getcwd())
    memory_retention_parser.add_argument("--scope", required=True)
    memory_retention_parser.add_argument("--max-active", type=int, required=True)
    memory_retention_parser.set_defaults(func=command_memory_retention_plan)

    memory_conflicts_parser = subparsers.add_parser(
        "memory-conflicts", help="show unresolved explicitly declared semantic conflicts"
    )
    memory_conflicts_parser.add_argument("--workspace", default=os.getcwd())
    memory_conflicts_parser.add_argument("--scope", required=True)
    memory_conflicts_parser.set_defaults(func=command_memory_conflicts)

    memory_search_parser = subparsers.add_parser(
        "memory-search", help="search active semantic memory without changing activation authority"
    )
    memory_search_parser.add_argument("--workspace", default=os.getcwd())
    memory_search_parser.add_argument("--scope")
    memory_search_parser.add_argument("--query", required=True)
    memory_search_parser.add_argument("--limit", type=int, default=5)
    memory_search_parser.set_defaults(func=command_memory_search)

    episode_index_parser = subparsers.add_parser(
        "episode-index", help="rebuild the scoped episodic Frame index"
    )
    episode_index_parser.add_argument("--workspace", default=os.getcwd())
    episode_index_parser.add_argument("--scope", required=True)
    episode_index_parser.set_defaults(func=command_episode_index)

    episode_search_parser = subparsers.add_parser(
        "episode-search", help="search scoped Frames, tests, failures, collapses, and outcomes"
    )
    episode_search_parser.add_argument("--workspace", default=os.getcwd())
    episode_search_parser.add_argument("--scope", required=True)
    episode_search_parser.add_argument("--query", required=True)
    episode_search_parser.add_argument("--limit", type=int, default=5)
    episode_search_parser.set_defaults(func=command_episode_search)

    memory_archive_parser = subparsers.add_parser(
        "memory-archive-plan", help="plan safe cold-frame archival without moving or deleting files"
    )
    memory_archive_parser.add_argument("--workspace", default=os.getcwd())
    memory_archive_parser.add_argument("--before", required=True)
    memory_archive_parser.set_defaults(func=command_memory_archive_plan)

    return parser


def configure_console_output():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    configure_console_output()
    parser = build_parser()
    args = parser.parse_args()
    try:
        with transaction_visibility_scope():
            result = args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
