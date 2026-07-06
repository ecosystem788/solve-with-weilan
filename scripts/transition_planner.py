"""Pure one-step transition-plan generation for WeiLan Memory 0.7c."""

import hashlib
import json
import uuid


PLAN_SCHEMA_VERSION = "weilan_transition_plan_v0.7c"
REQUEST_SCHEMA_VERSION = "weilan_transition_request_v0.7c"
FRAME_SCHEMA_VERSION = "weilan_method_event_v0.1"
LINEAGE_SCHEMA_VERSION = "weilan_frame_lineage_v0.4"
GOVERNANCE_SCHEMA_VERSION = "weilan_governance_event_v0.6"
SUCCESSOR_DISPOSITIONS = {"continue", "test", "fork", "join", "regroup"}
TERMINAL_DISPOSITIONS = {"collapse", "complete", "block"}
COLLAPSE_PRESSURE_KINDS = {"contradiction", "risk", "cost", "staleness"}


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def stable_uuid(seed):
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return str(uuid.UUID(digest))


def _text(value):
    return str(value or "").strip()


def normalize_request(request):
    if not isinstance(request, dict):
        raise ValueError("transition request must be an object")
    idempotency_key = _text(request.get("idempotency_key"))
    if not idempotency_key or len(idempotency_key) > 256:
        raise ValueError("transition request requires an idempotency key up to 256 characters")
    level = _text(request.get("level")) or "L2"
    if level not in {"L2", "L3"}:
        raise ValueError("transition Frame level must be L2 or L3")
    evidence_refs = sorted(
        {str(value).strip() for value in request.get("evidence_refs", []) if str(value).strip()}
    )
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "idempotency_key": idempotency_key,
        "level": level,
        "result_branch": _text(request.get("result_branch")),
        "problem": _text(request.get("problem")),
        "success_criteria": _text(request.get("success_criteria")),
        "budget": _text(request.get("budget")),
        "why_reasonable": _text(request.get("why_reasonable")),
        "next_expected_evidence": _text(request.get("next_expected_evidence")),
        "death_line": _text(request.get("death_line")),
        "evidence_refs": evidence_refs,
        "once_reasonable": _text(request.get("once_reasonable")),
        "invalidating_evidence": _text(request.get("invalidating_evidence")),
        "reusable_results": _text(request.get("reusable_results")),
        "forbidden_assumption": _text(request.get("forbidden_assumption")),
    }


def proposal_request_view(proposal):
    return {
        "trigger": dict(proposal.get("trigger") or {}),
        "disposition": proposal.get("disposition"),
        "target_ref": proposal.get("target_ref"),
        "candidate_target_ref": proposal.get("candidate_target_ref"),
        "branches": list(proposal.get("branches", [])),
        "death_line_matches": list(proposal.get("death_line_matches", [])),
        "changed_assumption": _text(proposal.get("changed_assumption")),
    }


def transition_request_hash(proposal, request):
    normalized = normalize_request(request)
    return stable_hash(
        {
            "proposal_request": proposal_request_view(proposal),
            "materialization_request": normalized,
        }
    )


def _record_identity(context, transition_id, role):
    return stable_uuid(f"{transition_id}:{role}")


def _frame_record(context, transition_id, frame_id, event_type, data, role):
    return {
        "schema_version": FRAME_SCHEMA_VERSION,
        "event_id": _record_identity(context, transition_id, role),
        "frame_id": frame_id,
        "timestamp_utc": context["timestamp_utc"],
        "event_type": event_type,
        "level": context["request"]["level"],
        "workspace": context["workspace"],
        "data": data,
    }


def _successor_intents(context, proposal, request, transition_id):
    if not request["problem"] or not request["success_criteria"]:
        raise ValueError("successor generation requires problem and success criteria")
    if not request["why_reasonable"] or not request["next_expected_evidence"]:
        raise ValueError("successor generation requires holder rationale and expected evidence")
    target = context["targets"].get(
        proposal.get("candidate_target_ref")
        if proposal.get("disposition") in {"fork", "regroup"}
        else proposal.get("target_ref")
    )
    if not target:
        raise ValueError("successor holder target is unavailable")
    target_budget = _text(target.get("budget")) or "proportional"
    if request["budget"] and request["budget"] != target_budget:
        raise ValueError("successor budget override is not provably within the target budget")
    successor_budget = target_budget
    death_line = request["death_line"]
    if not death_line:
        declared = list(target.get("death_lines", []))
        death_line = declared[0] if declared else "explicit evidence removes governing authority"

    disposition = proposal["disposition"]
    relation = (proposal.get("successor_preview") or {}).get("relation")
    parents = sorted((proposal.get("successor_preview") or {}).get("parent_frame_ids", []))
    branches = list(proposal.get("branches", []))
    current_branches = context.get("branches", {})
    if disposition == "fork":
        branch_id = request["result_branch"]
        if not branch_id:
            raise ValueError("fork requires a distinct result branch")
        if len(branch_id) > 128:
            raise ValueError("fork result branch cannot exceed 128 characters")
        if branch_id in current_branches:
            raise ValueError("fork result branch already exists")
    elif disposition == "join":
        branch_id = request["result_branch"]
        if not branch_id or branch_id not in branches:
            raise ValueError("join result branch must be one admitted parent branch")
    else:
        if len(branches) != 1:
            raise ValueError(f"{disposition} requires exactly one current branch")
        branch_id = branches[0]
        if request["result_branch"] and request["result_branch"] != branch_id:
            raise ValueError("result branch would change an admitted continuation")
    joined = sorted(value for value in branches if disposition == "join" and value != branch_id)
    frame_id = "wf-mc7c-" + hashlib.sha256(
        f"{transition_id}:frame".encode("utf-8")
    ).hexdigest()[:20]
    lineage_event_id = _record_identity(context, transition_id, "lineage")
    causal = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "lineage_event_id": lineage_event_id,
        "scope": context["scope"],
        "branch_id": branch_id,
        "relation": relation,
        "parent_frame_ids": parents,
        "joined_branch_ids": joined,
    }
    frame_opened = _frame_record(
        context,
        transition_id,
        frame_id,
        "frame_opened",
        {
            "problem": request["problem"],
            "success_criteria": request["success_criteria"],
            "budget": successor_budget,
            "causal": causal,
            "persistence_audit_required": True,
            "control_heads_at_open": {
                "workspace": context.get("workspace_control_head"),
                "scope": context.get("scope_control_head"),
            },
            "scoped_control_count_at_open": context.get("scoped_control_count", 0),
            "metabolic_transition": {
                "transition_id": transition_id,
                "proposal_hash": proposal["proposal_hash"],
                "disposition": disposition,
                "holder_target_ref": target["target_ref"],
            },
        },
        "frame_opened",
    )
    candidate = _frame_record(
        context,
        transition_id,
        frame_id,
        "candidate_admitted",
        {
            "candidate_id": target["target_ref"],
            "source": "admitted_metabolic_transition",
        },
        "candidate_admitted",
    )
    holder = _frame_record(
        context,
        transition_id,
        frame_id,
        "holder_selected",
        {
            "candidate_id": target["target_ref"],
            "why_reasonable": request["why_reasonable"],
            "next_expected_evidence": request["next_expected_evidence"],
            "death_line": death_line,
        },
        "holder_selected",
    )
    frame_records = [frame_opened, candidate, holder]
    if disposition == "regroup":
        frame_records.append(
            _frame_record(
                context,
                transition_id,
                frame_id,
                "candidates_regrouped",
                {
                    "collapsed_target_ref": proposal["target_ref"],
                    "selected_candidate_ref": proposal["candidate_target_ref"],
                    "changed_assumption": proposal["changed_assumption"],
                },
                "candidates_regrouped",
            )
        )
    lineage = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "event_id": lineage_event_id,
        "timestamp_utc": context["timestamp_utc"],
        "workspace": context["workspace"],
        "workspace_key": context["workspace_key"],
        "scope": context["scope"],
        "scope_key": context["scope_key"],
        "frame_id": frame_id,
        "branch_id": branch_id,
        "relation": relation,
        "parent_frame_ids": parents,
        "joined_branch_ids": joined,
    }
    intents = [
        *({"participant": "frame", "record": record} for record in frame_records),
        {"participant": "lineage", "record": lineage},
    ]
    return intents, frame_id, branch_id


def _governance_intent(context, proposal, request, transition_id):
    disposition = proposal["disposition"]
    target = context["targets"].get(proposal.get("target_ref"))
    if not target:
        raise ValueError("governance transition target is unavailable")
    event_types = {
        "collapse": ("target_collapsed", "COLLAPSED"),
        "complete": ("target_completed", "COMPLETED"),
        "block": ("target_blocked", "BLOCKED"),
    }
    event_type, destination = event_types[disposition]
    active_pressures = [
        value
        for value in context.get("pressures", {}).values()
        if value.get("active")
        and value.get("target_ref") == proposal.get("target_ref")
        and value.get("kind") in COLLAPSE_PRESSURE_KINDS
    ]
    pressure_ids = sorted(value["pressure_id"] for value in active_pressures)
    evidence_refs = sorted(
        set(request["evidence_refs"])
        | {
            ref
            for pressure in active_pressures
            for ref in pressure.get("evidence_refs", [])
        }
    )
    if disposition == "collapse" and not pressure_ids:
        raise ValueError("collapse materialization requires active adverse pressure")
    if not evidence_refs:
        raise ValueError(f"{disposition} materialization requires evidence")
    snapshots = context.get("transition_source_snapshots", [])
    if [value.get("ref") for value in snapshots] != evidence_refs:
        raise ValueError("transition evidence snapshots do not match evidence refs")
    data = {
        "target_ref": proposal["target_ref"],
        "from_state": target["state"],
        "to_state": destination,
        "pressure_ids": pressure_ids if disposition == "collapse" else [],
        "evidence_refs": evidence_refs,
        "source_snapshots": snapshots,
        "causal_frame_id": context["causal_frame_id"],
    }
    if disposition == "collapse":
        trace = {
            "once_reasonable": request["once_reasonable"],
            "invalidating_evidence": request["invalidating_evidence"],
            "reusable_results": request["reusable_results"],
            "forbidden_assumption": request["forbidden_assumption"],
            "reentry_condition": target.get("reentry_condition", ""),
        }
        if any(not value for value in trace.values()):
            raise ValueError("collapse materialization requires a complete trace")
        data["matched_death_lines"] = list(proposal.get("death_line_matches", []))
        data["trace"] = trace
    record = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "event_id": _record_identity(context, transition_id, event_type),
        "timestamp_utc": context["timestamp_utc"],
        "sequence": context["governance_head_sequence"] + 1,
        "workspace": context["workspace"],
        "workspace_key": context["workspace_key"],
        "scope": context["scope"],
        "scope_key": context["scope_key"],
        "event_type": event_type,
        "data": data,
    }
    return [{"participant": "governance", "record": record}]


def build_transition_plan(context, proposal, request):
    """Derive one deterministic transaction plan without writing state."""

    if not proposal.get("admissible") or proposal.get("disposition") == "yield":
        raise ValueError("0.7c requires one admissible non-yield proposal")
    disposition = proposal.get("disposition")
    if disposition not in SUCCESSOR_DISPOSITIONS | TERMINAL_DISPOSITIONS:
        raise ValueError("unsupported 0.7c disposition")
    request = normalize_request(request)
    request_hash = transition_request_hash(proposal, request)
    transition_id = "mtx7c-" + stable_hash(
        {
            "workspace_key": context["workspace_key"],
            "scope_key": context["scope_key"],
            "proposal_hash": proposal["proposal_hash"],
            "request_hash": request_hash,
        }
    )[:32]
    local_context = {**context, "request": request}
    if disposition in SUCCESSOR_DISPOSITIONS:
        intents, successor_frame_id, result_branch = _successor_intents(
            local_context, proposal, request, transition_id
        )
    else:
        intents = _governance_intent(local_context, proposal, request, transition_id)
        successor_frame_id = None
        result_branch = None
    body = {
        "transition_id": transition_id,
        "request_hash": request_hash,
        "proposal_hash": proposal["proposal_hash"],
        "contract_hash": proposal["contract_hash"],
        "disposition": disposition,
        "successor_frame_id": successor_frame_id,
        "result_branch": result_branch,
        "intents": intents,
        "bounds": {
            "max_transactions": 1,
            "max_successor_frames": 1 if disposition in SUCCESSOR_DISPOSITIONS else 0,
            "background_loop_allowed": False,
            "scheduler_allowed": False,
            "scope_expansion_allowed": False,
            "budget_expansion_allowed": False,
        },
    }
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_hash": stable_hash(body),
        "authority": "derived_transition_plan_never_activation_authority",
        "can_authorize_actions": False,
        "can_commit": False,
        "requires_transaction_phase": "Memory 0.7b",
        **body,
    }
