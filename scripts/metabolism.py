"""Pure read-only contract and proposal logic for WeiLan Memory 0.7a."""

import hashlib
import json


CONTRACT_SCHEMA_VERSION = "weilan_metabolic_contract_v0.7a"
PROPOSAL_SCHEMA_VERSION = "weilan_metabolic_proposal_v0.7a"
STRATEGY_VERSION = "memory-0.7a-read-only-v1"
DISPOSITIONS = (
    "continue",
    "test",
    "fork",
    "join",
    "collapse",
    "regroup",
    "yield",
    "complete",
    "block",
)
TRIGGER_KINDS = (
    "frame_closed",
    "evidence_changed",
    "pressure_changed",
    "control_changed",
    "discriminating_test_completed",
)
TERMINAL_TARGET_STATES = {"COLLAPSED", "COMPLETED", "SUPERSEDED"}
ADVERSE_PRESSURE_KINDS = {"contradiction", "risk", "cost", "staleness"}


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _target_view(target, pressure_vector):
    return {
        "target_kind": target.get("target_kind"),
        "scale": target.get("scale"),
        "parent_target_ref": target.get("parent_target_ref"),
        "state": target.get("state"),
        "budget": target.get("budget"),
        "death_lines": list(target.get("death_lines", [])),
        "reentry_condition": target.get("reentry_condition", ""),
        "collapse_trace": target.get("collapse_trace"),
        "pressure_vector": pressure_vector,
    }


def build_contract(snapshot):
    """Derive a deterministic, non-authoritative one-step contract."""

    issues = sorted(set(snapshot.get("issues", [])))
    control = dict(snapshot.get("control", {}))
    branches = {
        branch_id: dict(value)
        for branch_id, value in sorted(snapshot.get("branches", {}).items())
    }
    targets = snapshot.get("targets", {})
    pressure_vectors = snapshot.get("pressure_vectors", {})
    target_views = {
        ref: _target_view(target, pressure_vectors.get(ref, {}))
        for ref, target in sorted(targets.items())
    }
    active_targets = {
        ref: value
        for ref, value in target_views.items()
        if value["state"] not in TERMINAL_TARGET_STATES
    }
    collapsed_targets = {
        ref: value
        for ref, value in target_views.items()
        if value["state"] == "COLLAPSED"
    }
    active_branches = {
        ref: value for ref, value in branches.items() if value.get("status") == "active"
    }
    open_heads = sorted(
        ref for ref, value in active_branches.items() if not value.get("head_closed")
    )
    closed_heads = sorted(
        ref
        for ref, value in active_branches.items()
        if value.get("head_closed") and value.get("audit_valid")
    )
    audit_blocked = sorted(
        ref
        for ref, value in active_branches.items()
        if value.get("head_closed") and not value.get("audit_valid")
    )

    if issues:
        status = "INVALID"
    elif not control.get("continuation_allowed"):
        status = "CONTROL_BLOCKED"
    elif open_heads:
        status = "AWAITING_FRAME_CLOSE"
    elif audit_blocked:
        status = "AUDIT_BLOCKED"
    elif not active_targets or not closed_heads:
        status = "QUIESCENT"
    else:
        status = "READY"

    eligible_triggers = []
    control_event_id = control.get("event_id")
    if control_event_id:
        eligible_triggers.append(
            {"kind": "control_changed", "ref": f"control:{control_event_id}"}
        )
    for branch_id in closed_heads:
        frame_id = active_branches[branch_id].get("head_frame_id")
        eligible_triggers.append({"kind": "frame_closed", "ref": f"frame:{frame_id}"})
        if active_branches[branch_id].get("discriminating_test_completed"):
            eligible_triggers.append(
                {"kind": "discriminating_test_completed", "ref": f"frame:{frame_id}"}
            )
    for pressure_id, pressure in sorted(snapshot.get("pressures", {}).items()):
        if not pressure.get("active"):
            continue
        eligible_triggers.append(
            {"kind": "pressure_changed", "ref": f"pressure:{pressure_id}"}
        )
        for evidence_ref in pressure.get("evidence_refs", []):
            eligible_triggers.append({"kind": "evidence_changed", "ref": evidence_ref})
    eligible_triggers = sorted(
        {json.dumps(item, sort_keys=True): item for item in eligible_triggers}.values(),
        key=lambda item: (item["kind"], item["ref"]),
    )

    allowed_dispositions = ["yield"]
    if status == "READY":
        allowed_dispositions.extend(["continue", "test", "fork", "complete", "block"])
        if len(closed_heads) >= 2:
            allowed_dispositions.append("join")
        collapse_ready = any(
            any(
                kind in ADVERSE_PRESSURE_KINDS
                and (
                    target["state"] in {"WARNED", "PROBATION"}
                    or component.get("strongest") == "critical"
                )
                for kind, component in target.get("pressure_vector", {}).items()
            )
            for target in active_targets.values()
        )
        if collapse_ready:
            allowed_dispositions.append("collapse")
        if collapsed_targets and active_targets:
            allowed_dispositions.append("regroup")

    body = {
        "workspace": snapshot.get("workspace"),
        "scope": snapshot.get("scope"),
        "strategy_version": STRATEGY_VERSION,
        "status": status,
        "input_heads": {
            "control": control.get("event_id"),
            "lineage": snapshot.get("lineage_head"),
            "governance": snapshot.get("governance_head"),
            "self_projection": snapshot.get("self_projection_hash"),
            "evidence_dependencies": list(snapshot.get("evidence_heads", [])),
            "persistence_audits": list(snapshot.get("audit_heads", [])),
        },
        "control": control,
        "branches": branches,
        "targets": target_views,
        "active_target_refs": sorted(active_targets),
        "collapsed_target_refs": sorted(collapsed_targets),
        "eligible_triggers": eligible_triggers,
        "allowed_dispositions": sorted(set(allowed_dispositions)),
        "bounds": {
            "max_steps": 1,
            "writes_allowed": False,
            "background_loop_allowed": False,
            "scope_expansion_allowed": False,
            "top_level_goal_creation_allowed": False,
            "budget_expansion_allowed": False,
        },
        "issues": issues,
    }
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_hash": stable_hash(body),
        "authority": "derived_read_only_contract_never_action_authority",
        "can_authorize_actions": False,
        "can_commit": False,
        **body,
    }


def _strongest_is_critical(target):
    return any(
        kind in ADVERSE_PRESSURE_KINDS and component.get("strongest") == "critical"
        for kind, component in target.get("pressure_vector", {}).items()
    )


def _has_adverse_pressure(target):
    return any(
        kind in ADVERSE_PRESSURE_KINDS
        for kind in target.get("pressure_vector", {})
    )


def propose_transition(
    contract,
    *,
    expected_contract_hash,
    trigger_kind,
    trigger_ref,
    disposition,
    target_ref=None,
    candidate_target_ref=None,
    branches=None,
    death_line_matches=None,
    changed_assumption="",
):
    """Validate one read-only proposal against a current contract."""

    branches = list(dict.fromkeys(branches or []))
    death_line_matches = list(dict.fromkeys(death_line_matches or []))
    reasons = []
    if expected_contract_hash != contract.get("contract_hash"):
        reasons.append("contract_head_changed")
    if trigger_kind not in TRIGGER_KINDS:
        reasons.append("unsupported_trigger")
    if {"kind": trigger_kind, "ref": trigger_ref} not in contract.get(
        "eligible_triggers", []
    ):
        reasons.append("trigger_not_current_or_eligible")
    if disposition not in DISPOSITIONS:
        reasons.append("unsupported_disposition")
    if disposition not in contract.get("allowed_dispositions", []):
        reasons.append(f"disposition_not_allowed_in_{contract.get('status', 'UNKNOWN').lower()}")

    targets = contract.get("targets", {})
    target = targets.get(target_ref) if target_ref else None
    candidate = targets.get(candidate_target_ref) if candidate_target_ref else None
    target_required = {"continue", "test", "fork", "join", "collapse", "regroup", "complete", "block"}
    if disposition in target_required and not target:
        reasons.append("target_required")

    branch_state = contract.get("branches", {})
    successor_dispositions = {"continue", "test", "fork", "regroup"}
    if disposition in successor_dispositions:
        if len(branches) != 1:
            reasons.append("one_branch_required")
        elif (
            branches[0] not in branch_state
            or branch_state[branches[0]].get("status") != "active"
            or not branch_state[branches[0]].get("head_closed")
            or not branch_state[branches[0]].get("audit_valid")
        ):
            reasons.append("branch_head_not_closed_and_audited")
    elif disposition == "join":
        if len(branches) < 2:
            reasons.append("join_requires_multiple_branches")
        elif any(
            branch not in branch_state
            or branch_state[branch].get("status") != "active"
            or not branch_state[branch].get("head_closed")
            or not branch_state[branch].get("audit_valid")
            for branch in branches
        ):
            reasons.append("join_branch_not_closed_and_audited")

    if target:
        state = target.get("state")
        if disposition == "continue" and state != "ACTIVE":
            reasons.append("continue_requires_active_target")
        elif disposition == "test" and state not in {"WARNED", "PROBATION"}:
            reasons.append("test_requires_warned_or_probation_target")
        elif disposition == "fork":
            if state not in {"ACTIVE", "WARNED", "PROBATION"}:
                reasons.append("fork_target_is_not_governable")
            if not candidate or candidate_target_ref == target_ref:
                reasons.append("fork_requires_distinct_active_candidate")
            elif candidate.get("state") != "ACTIVE":
                reasons.append("fork_candidate_is_not_active")
        elif disposition == "join" and state != "ACTIVE":
            reasons.append("join_requires_active_target")
        elif disposition == "collapse":
            if state not in {"ACTIVE", "WARNED", "PROBATION"}:
                reasons.append("collapse_target_state_not_eligible")
            if state == "ACTIVE" and not _strongest_is_critical(target):
                reasons.append("active_collapse_requires_critical_pressure")
            if not _has_adverse_pressure(target):
                reasons.append("collapse_requires_adverse_pressure")
            declared = set(target.get("death_lines", []))
            if not death_line_matches or any(item not in declared for item in death_line_matches):
                reasons.append("collapse_requires_declared_death_line")
        elif disposition == "regroup":
            if state != "COLLAPSED":
                reasons.append("regroup_requires_collapsed_target")
            if not candidate or candidate.get("state") != "ACTIVE":
                reasons.append("regroup_requires_active_candidate")
            if not changed_assumption.strip():
                reasons.append("regroup_requires_changed_assumption")
            forbidden = (target.get("collapse_trace") or {}).get(
                "forbidden_assumption", ""
            )
            if forbidden and changed_assumption.strip() == forbidden.strip():
                reasons.append("regroup_inherits_forbidden_assumption")
        elif disposition == "complete" and state in TERMINAL_TARGET_STATES:
            reasons.append("terminal_target_cannot_transition")
        elif disposition == "block" and state not in {"ACTIVE", "WARNED", "PROBATION", "PAUSED"}:
            reasons.append("block_target_state_not_eligible")

    successor_preview = None
    if disposition in {"continue", "test", "fork", "join", "regroup"} and not reasons:
        relation = "continue"
        if disposition == "fork":
            relation = "fork"
        elif disposition == "join":
            relation = "join"
        successor_preview = {
            "relation": relation,
            "parent_frame_ids": sorted(
                branch_state[branch].get("head_frame_id")
                for branch in branches
                if branch in branch_state
            ),
            "target_ref": candidate_target_ref if disposition in {"fork", "regroup"} else target_ref,
        }

    body = {
        "contract_hash": contract.get("contract_hash"),
        "trigger": {"kind": trigger_kind, "ref": trigger_ref},
        "disposition": disposition,
        "target_ref": target_ref,
        "candidate_target_ref": candidate_target_ref,
        "branches": branches,
        "death_line_matches": death_line_matches,
        "changed_assumption": changed_assumption.strip(),
        "admissible": not reasons,
        "reasons": sorted(set(reasons)),
        "successor_preview": successor_preview,
        "writes_planned": [],
        "requires_commit_phase": None if disposition == "yield" else "Memory 0.7b or later",
    }
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposal_hash": stable_hash(body),
        "authority": "read_only_proposal_never_action_authority",
        "can_authorize_actions": False,
        "can_commit": False,
        **body,
    }
