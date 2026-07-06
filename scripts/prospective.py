"""Pure SE-0.4 prospective-memory reducer and bounded cycle planner."""

from datetime import datetime, timezone


SCHEMA_VERSION = "weilan_prospective_memory_v0.4"
EVENT_KINDS = ("user", "tool", "environment", "clock")
GOAL_STATES = ("ACTIVE", "SATISFIED", "SUPERSEDED", "COLLAPSED")
TERMINAL_GOAL_STATES = frozenset(GOAL_STATES[1:])
EVENT_TYPES = ("goal_registered", "causal_event_observed", "goal_transitioned")


def parse_timestamp(value):
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_condition(event_kind, event_name, not_before_utc=None):
    if event_kind not in EVENT_KINDS:
        raise ValueError(f"unsupported causal event kind: {event_kind}")
    name = (event_name or "").strip()
    if not name or len(name) > 128 or any(character.isspace() for character in name):
        raise ValueError("event name must be a non-empty space-free value up to 128 characters")
    if not_before_utc:
        parse_timestamp(not_before_utc)
        if event_kind != "clock":
            raise ValueError("not-before is allowed only for clock conditions")
    return {
        "event_kind": event_kind,
        "event_name": name,
        "not_before_utc": not_before_utc,
    }


def event_matches(goal, observed):
    if goal.get("state") != "ACTIVE":
        return False
    condition = goal.get("condition", {})
    if condition.get("event_kind") != observed.get("event_kind"):
        return False
    if condition.get("event_name") != observed.get("event_name"):
        return False
    not_before = condition.get("not_before_utc")
    if not_before:
        try:
            return parse_timestamp(observed.get("observed_at_utc")) >= parse_timestamp(not_before)
        except (TypeError, ValueError):
            return False
    return True


def reduce_events(records):
    goals = {}
    observed_events = {}
    issues = []
    head_sequence = 0
    seen_event_ids = set()
    for index, record in enumerate(records, 1):
        label = f"record {index}"
        if record.get("schema_version") != SCHEMA_VERSION:
            issues.append(f"{label}: unsupported schema")
            continue
        event_id = record.get("event_id")
        sequence = record.get("sequence")
        event_type = record.get("event_type")
        data = record.get("data")
        if not event_id or event_id in seen_event_ids:
            issues.append(f"{label}: missing or duplicate event_id")
            continue
        seen_event_ids.add(event_id)
        if sequence != head_sequence + 1:
            issues.append(f"{label}: sequence must be contiguous")
            continue
        if event_type not in EVENT_TYPES or not isinstance(data, dict):
            issues.append(f"{label}: invalid event type or data")
            continue
        head_sequence = sequence

        if event_type == "goal_registered":
            goal_ref = data.get("goal_ref")
            if not goal_ref or goal_ref in goals:
                issues.append(f"{label}: goal_ref missing or already registered")
                continue
            try:
                condition = normalize_condition(**data.get("condition", {}))
            except (TypeError, ValueError) as exc:
                issues.append(f"{label}: invalid condition: {exc}")
                continue
            goals[goal_ref] = {
                "goal_ref": goal_ref,
                "description": data.get("description", ""),
                "condition": condition,
                "budget": data.get("budget", "bounded"),
                "death_line": data.get("death_line", ""),
                "state": "ACTIVE",
                "registered_event_id": event_id,
                "head_event_id": event_id,
                "transition_reason": None,
                "causal_event_id": None,
                "replacement_goal_ref": None,
                "sources": data.get("sources", []),
                "source_snapshots": data.get("source_snapshots", []),
            }
        elif event_type == "causal_event_observed":
            causal_event_id = data.get("causal_event_id")
            if not causal_event_id or causal_event_id in observed_events:
                issues.append(f"{label}: causal_event_id missing or duplicate")
                continue
            if data.get("event_kind") not in EVENT_KINDS or not data.get("event_name"):
                issues.append(f"{label}: invalid observed causal event")
                continue
            observed_events[causal_event_id] = {
                **data,
                "ledger_event_id": event_id,
            }
        else:
            goal_ref = data.get("goal_ref")
            target_state = data.get("state")
            goal = goals.get(goal_ref)
            if not goal:
                issues.append(f"{label}: unknown goal_ref")
                continue
            if goal["state"] in TERMINAL_GOAL_STATES:
                issues.append(f"{label}: terminal goal cannot transition again")
                continue
            if target_state not in TERMINAL_GOAL_STATES:
                issues.append(f"{label}: invalid terminal goal state")
                continue
            causal_event_id = data.get("causal_event_id")
            replacement = data.get("replacement_goal_ref")
            if target_state == "SATISFIED":
                observed = observed_events.get(causal_event_id)
                if not observed or not event_matches(goal, observed):
                    issues.append(f"{label}: satisfaction requires a matching observed event")
                    continue
            elif target_state == "SUPERSEDED":
                replacement_goal = goals.get(replacement)
                if not replacement_goal or replacement_goal["state"] != "ACTIVE" or replacement == goal_ref:
                    issues.append(f"{label}: supersession requires a distinct active replacement")
                    continue
            elif not data.get("reason"):
                issues.append(f"{label}: collapse requires a reason")
                continue
            goal.update(
                {
                    "state": target_state,
                    "head_event_id": event_id,
                    "transition_reason": data.get("reason"),
                    "causal_event_id": causal_event_id,
                    "replacement_goal_ref": replacement,
                }
            )
    return {
        "head_sequence": head_sequence,
        "head_event_id": records[-1].get("event_id") if records else None,
        "goals": goals,
        "causal_events": observed_events,
        "issues": issues,
    }


def plan_cycle(state, causal_event_id):
    observed = state.get("causal_events", {}).get(causal_event_id)
    if not observed:
        raise ValueError(f"unknown causal event: {causal_event_id}")
    matches = sorted(
        goal_ref
        for goal_ref, goal in state.get("goals", {}).items()
        if event_matches(goal, observed)
    )
    if not matches:
        return {
            "status": "QUIESCENT",
            "stop_reason": "no_active_condition_matched",
            "causal_event_id": causal_event_id,
            "plans": [],
        }
    if len(matches) > 1:
        return {
            "status": "AMBIGUOUS",
            "stop_reason": "multiple_active_conditions_matched",
            "causal_event_id": causal_event_id,
            "matched_goal_refs": matches,
            "plans": [],
        }
    return {
        "status": "READY",
        "stop_reason": "explicit_transition_required",
        "causal_event_id": causal_event_id,
        "matched_goal_refs": matches,
        "plans": [
            {
                "goal_ref": matches[0],
                "transition": "SATISFIED",
                "causal_event_id": causal_event_id,
            }
        ],
    }
