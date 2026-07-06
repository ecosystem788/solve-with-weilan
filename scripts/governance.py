"""Pure deterministic reducer for WeiLan Memory 0.6 governance events."""

from collections import defaultdict


SCHEMA_VERSION = "weilan_governance_event_v0.6"
TARGET_KINDS = ("assumption", "holder", "goal", "route", "frame")
SCALES = ("local", "task", "continuity", "workspace")
SCALE_RANK = {scale: index for index, scale in enumerate(SCALES)}
PRESSURE_KINDS = ("support", "contradiction", "risk", "cost", "staleness")
ADVERSE_PRESSURE_KINDS = {"contradiction", "risk", "cost", "staleness"}
STRENGTHS = ("weak", "medium", "strong", "critical")
STRENGTH_RANK = {strength: index for index, strength in enumerate(STRENGTHS)}
TARGET_STATES = (
    "ACTIVE",
    "WARNED",
    "PROBATION",
    "PAUSED",
    "COLLAPSED",
    "COMPLETED",
    "BLOCKED",
    "SUPERSEDED",
)
TERMINAL_TARGET_STATES = {"COLLAPSED", "COMPLETED", "SUPERSEDED"}
EVENT_TYPES = {
    "target_registered",
    "pressure_recorded",
    "pressure_invalidated",
    "pressure_propagated",
    "target_warned",
    "probation_started",
    "target_stabilized",
    "target_paused",
    "target_resumed",
    "target_collapsed",
    "target_completed",
    "target_blocked",
    "target_superseded",
    "target_reentered",
}

TRANSITION_RULES = {
    "target_warned": ({"ACTIVE"}, "WARNED", True),
    "probation_started": ({"WARNED"}, "PROBATION", True),
    "target_stabilized": ({"PROBATION"}, "ACTIVE", False),
    "target_paused": ({"ACTIVE", "WARNED", "PROBATION"}, "PAUSED", False),
    "target_resumed": ({"PAUSED", "BLOCKED"}, "ACTIVE", False),
    "target_collapsed": (
        {"ACTIVE", "WARNED", "PROBATION", "PAUSED", "BLOCKED"},
        "COLLAPSED",
        True,
    ),
    "target_completed": (
        {"ACTIVE", "WARNED", "PROBATION", "PAUSED", "BLOCKED"},
        "COMPLETED",
        False,
    ),
    "target_blocked": (
        {"ACTIVE", "WARNED", "PROBATION", "PAUSED"},
        "BLOCKED",
        False,
    ),
    "target_superseded": (
        {"ACTIVE", "WARNED", "PROBATION", "PAUSED", "BLOCKED"},
        "SUPERSEDED",
        False,
    ),
    "target_reentered": ({"COLLAPSED"}, "ACTIVE", False),
}


def pressure_signature(data):
    return (
        data.get("target_ref"),
        data.get("kind"),
        tuple(sorted(data.get("evidence_refs", []))),
        tuple(sorted(data.get("source_pressure_ids", []))),
    )


def _target_snapshot(data, event):
    return {
        "target_ref": data["target_ref"],
        "target_kind": data["target_kind"],
        "scale": data["scale"],
        "parent_target_ref": data.get("parent_target_ref"),
        "death_lines": list(data.get("death_lines", [])),
        "reentry_condition": data.get("reentry_condition", ""),
        "budget": data.get("budget", "proportional"),
        "source_refs": list(data.get("source_refs", [])),
        "source_snapshots": list(data.get("source_snapshots", [])),
        "state": "ACTIVE",
        "registered_event_id": event["event_id"],
        "last_event_id": event["event_id"],
        "transition_history": [],
        "matched_death_lines": [],
        "collapse_trace": None,
        "replacement_target_ref": None,
    }


def pressure_inactive_reasons(
    pressure,
    targets,
    pressures,
    source_fresh,
    seen=None,
    require_current_source=True,
):
    """Return reasons a pressure is ineligible at the current replay point."""

    seen = set(seen or ())
    pressure_id = pressure.get("pressure_id")
    if pressure_id in seen:
        return ["pressure_dependency_cycle"]
    seen.add(pressure_id)
    reasons = []
    target = targets.get(pressure.get("target_ref"))
    if not target or target["state"] in TERMINAL_TARGET_STATES:
        reasons.append("target_missing_or_terminal")
    if pressure.get("duplicate_of"):
        reasons.append("duplicate_evidence_contribution")
    if pressure.get("invalidated_event_id"):
        reasons.append("explicitly_invalidated")
    if require_current_source and not source_fresh(pressure.get("source_snapshots", [])):
        reasons.append("source_missing_stale_or_terminal")
    for source_pressure_id in pressure.get("source_pressure_ids", []):
        source_pressure = pressures.get(source_pressure_id)
        if not source_pressure or pressure_inactive_reasons(
            source_pressure,
            targets,
            pressures,
            source_fresh,
            seen,
            require_current_source=require_current_source,
        ):
            reasons.append(f"inactive_source_pressure:{source_pressure_id}")
    return reasons


def reduce_events(events, source_fresh):
    """Replay events in sequence order and derive governance state.

    source_fresh receives a stored source snapshot list and returns a boolean.
    The reducer performs no writes and uses no wall-clock time.
    """

    ordered = sorted(events, key=lambda event: (event.get("sequence", 0), event.get("event_id", "")))
    targets = {}
    pressures = {}
    issues = []
    signatures = {}
    seen_event_ids = set()
    seen_sequences = set()

    for expected_sequence, event in enumerate(ordered, start=1):
        event_id = event.get("event_id")
        sequence = event.get("sequence")
        if not isinstance(event_id, str) or not event_id:
            issues.append("governance event has no event id")
        elif event_id in seen_event_ids:
            issues.append(f"event {event_id}: duplicate event id")
        else:
            seen_event_ids.add(event_id)
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            issues.append(f"event {event_id}: invalid sequence")
        elif sequence in seen_sequences:
            issues.append(f"event {event_id}: duplicate sequence {sequence}")
        else:
            seen_sequences.add(sequence)
        if sequence != expected_sequence:
            issues.append(
                f"event {event_id}: non-contiguous sequence; "
                f"expected {expected_sequence}, received {sequence}"
            )

    for event in ordered:
        event_type = event.get("event_type")
        data = event.get("data", {})
        event_id = event.get("event_id")
        if event.get("schema_version") != SCHEMA_VERSION or event_type not in EVENT_TYPES:
            issues.append(f"event {event_id}: unsupported governance event")
            continue
        if not isinstance(data, dict):
            issues.append(f"event {event_id}: governance event data must be an object")
            continue

        if event_type == "target_registered":
            target_ref = data.get("target_ref")
            target_kind = data.get("target_kind")
            scale = data.get("scale")
            parent_ref = data.get("parent_target_ref")
            if (
                not isinstance(target_ref, str)
                or not target_ref
                or target_kind not in TARGET_KINDS
                or target_ref.split(":", 1)[0] != target_kind
                or scale not in SCALES
            ):
                issues.append(f"event {event_id}: invalid target registration")
                continue
            if target_ref in targets:
                issues.append(f"event {event_id}: duplicate target {target_ref}")
                continue
            if parent_ref:
                parent = targets.get(parent_ref)
                if not parent:
                    issues.append(f"event {event_id}: unknown parent target {parent_ref}")
                    continue
                if parent["state"] in TERMINAL_TARGET_STATES:
                    issues.append(f"event {event_id}: parent target is terminal")
                    continue
                if SCALE_RANK[parent["scale"]] < SCALE_RANK[scale]:
                    issues.append(f"event {event_id}: child is broader than parent")
                    continue
            if not data.get("source_refs") or not data.get("source_snapshots"):
                issues.append(f"event {event_id}: target registration has no source")
                continue
            targets[target_ref] = _target_snapshot(data, event)
            continue

        if event_type in {"pressure_recorded", "pressure_propagated"}:
            pressure_id = data.get("pressure_id")
            target_ref = data.get("target_ref")
            target = targets.get(target_ref)
            source_pressure_ids = list(data.get("source_pressure_ids", []))
            if not isinstance(pressure_id, str) or not pressure_id:
                issues.append(f"event {event_id}: invalid pressure id")
                continue
            if pressure_id in pressures:
                issues.append(f"event {event_id}: duplicate pressure {pressure_id}")
                continue
            if not target:
                issues.append(f"event {event_id}: unknown pressure target {target_ref}")
                continue
            if target["state"] in TERMINAL_TARGET_STATES:
                issues.append(f"event {event_id}: pressure target is terminal")
                continue
            if (
                data.get("kind") not in PRESSURE_KINDS
                or data.get("strength") not in STRENGTHS
                or data.get("scale") != target["scale"]
                or not data.get("evidence_refs")
                or not data.get("source_snapshots")
                or not data.get("required_change")
                or not data.get("causal_frame_id")
            ):
                issues.append(f"event {event_id}: invalid pressure payload")
                continue
            if event_type == "pressure_recorded" and source_pressure_ids:
                issues.append(f"event {event_id}: recorded pressure has source pressures")
                continue
            if event_type == "pressure_propagated":
                if not source_pressure_ids or len(source_pressure_ids) != len(set(source_pressure_ids)):
                    issues.append(f"event {event_id}: invalid propagated pressure sources")
                    continue
                source_pressures = [pressures.get(item) for item in source_pressure_ids]
                if any(item is None for item in source_pressures):
                    issues.append(f"event {event_id}: unknown propagated pressure source")
                    continue
                if any(item["kind"] != data.get("kind") for item in source_pressures):
                    issues.append(f"event {event_id}: propagated pressure mixes kinds")
                    continue
                if any(
                    SCALE_RANK[item["scale"]] >= SCALE_RANK[target["scale"]]
                    for item in source_pressures
                ):
                    issues.append(f"event {event_id}: pressure did not propagate upward")
                    continue
                inactive_sources = [
                    item["pressure_id"]
                    for item in source_pressures
                    if pressure_inactive_reasons(
                        item,
                        targets,
                        pressures,
                        source_fresh,
                        require_current_source=False,
                    )
                ]
                if inactive_sources:
                    issues.append(
                        f"event {event_id}: propagated pressure sources are inactive: "
                        + ", ".join(inactive_sources)
                    )
                    continue
            signature = pressure_signature(data)
            duplicate_of = signatures.get(signature)
            if not duplicate_of:
                signatures[signature] = pressure_id
            pressures[pressure_id] = {
                "pressure_id": pressure_id,
                "target_ref": target_ref,
                "kind": data.get("kind"),
                "strength": data.get("strength"),
                "scale": data.get("scale"),
                "evidence_refs": list(data.get("evidence_refs", [])),
                "source_snapshots": list(data.get("source_snapshots", [])),
                "source_pressure_ids": source_pressure_ids,
                "required_change": data.get("required_change", ""),
                "causal_frame_id": data.get("causal_frame_id"),
                "recorded_event_id": event_id,
                "sequence": event.get("sequence", 0),
                "invalidated_event_id": None,
                "duplicate_of": duplicate_of,
                "active": False,
                "inactive_reasons": [],
            }
            continue

        if event_type == "pressure_invalidated":
            pressure_id = data.get("pressure_id")
            pressure = pressures.get(pressure_id)
            if not pressure:
                issues.append(f"event {event_id}: unknown pressure {pressure_id}")
                continue
            if pressure["invalidated_event_id"]:
                issues.append(f"event {event_id}: pressure already invalidated")
                continue
            if not data.get("reason"):
                issues.append(f"event {event_id}: pressure invalidation has no reason")
                continue
            pressure["invalidated_event_id"] = event_id
            pressure["invalidation_reason"] = data.get("reason", "")
            continue

        target_ref = data.get("target_ref")
        target = targets.get(target_ref)
        if not target:
            issues.append(f"event {event_id}: unknown target {target_ref}")
            continue
        allowed_states, destination, pressure_required = TRANSITION_RULES[event_type]
        if data.get("from_state") != target["state"] or target["state"] not in allowed_states:
            issues.append(f"event {event_id}: invalid transition from {target['state']}")
            continue
        if data.get("to_state") != destination:
            issues.append(f"event {event_id}: invalid transition destination")
            continue
        pressure_ids = list(data.get("pressure_ids", []))
        evidence_refs = list(data.get("evidence_refs", []))
        if not evidence_refs or not data.get("source_snapshots") or not data.get("causal_frame_id"):
            issues.append(f"event {event_id}: transition has incomplete provenance")
            continue
        transition_pressures = [pressures.get(item) for item in pressure_ids]
        if pressure_required and not pressure_ids:
            issues.append(f"event {event_id}: transition requires pressure")
            continue
        if any(
            item is None or item["target_ref"] != target_ref
            for item in transition_pressures
        ):
            issues.append(f"event {event_id}: transition pressure targets another object")
            continue
        if pressure_required:
            inactive_pressures = [
                item["pressure_id"]
                for item in transition_pressures
                if pressure_inactive_reasons(
                    item,
                    targets,
                    pressures,
                    source_fresh,
                    require_current_source=False,
                )
            ]
            if inactive_pressures:
                issues.append(
                    f"event {event_id}: transition pressures are inactive: "
                    + ", ".join(inactive_pressures)
                )
                continue
        if event_type == "target_collapsed":
            non_adverse = [
                item["pressure_id"]
                for item in transition_pressures
                if item["kind"] not in ADVERSE_PRESSURE_KINDS
            ]
            if non_adverse:
                issues.append(
                    f"event {event_id}: collapse pressure is not adverse: "
                    + ", ".join(non_adverse)
                )
                continue
            matched = list(data.get("matched_death_lines", []))
            trace = data.get("trace")
            required_trace = {
                "once_reasonable",
                "invalidating_evidence",
                "reusable_results",
                "forbidden_assumption",
                "reentry_condition",
            }
            if not matched or any(item not in target["death_lines"] for item in matched):
                issues.append(f"event {event_id}: collapse did not match a death line")
                continue
            if (
                not isinstance(trace, dict)
                or not required_trace.issubset(trace)
                or any(not trace.get(item) for item in required_trace)
            ):
                issues.append(f"event {event_id}: collapse trace is incomplete")
                continue
        elif event_type == "target_superseded":
            replacement = targets.get(data.get("replacement_target_ref"))
            if not replacement or replacement["state"] in TERMINAL_TARGET_STATES:
                issues.append(f"event {event_id}: invalid replacement target")
                continue
        elif event_type == "target_reentered":
            collapsed_evidence = {
                ref
                for item in target["transition_history"]
                if item["event_type"] == "target_collapsed"
                for ref in item.get("evidence_refs", [])
            }
            if not target["reentry_condition"] or not set(evidence_refs) - collapsed_evidence:
                issues.append(f"event {event_id}: reentry has no new evidence")
                continue
        target["state"] = destination
        target["last_event_id"] = event_id
        transition = {
            "event_id": event_id,
            "event_type": event_type,
            "from_state": data.get("from_state"),
            "to_state": data.get("to_state"),
            "pressure_ids": pressure_ids,
            "evidence_refs": evidence_refs,
        }
        target["transition_history"].append(transition)
        if event_type == "target_collapsed":
            target["matched_death_lines"] = list(data.get("matched_death_lines", []))
            target["collapse_trace"] = data.get("trace")
        elif event_type == "target_superseded":
            target["replacement_target_ref"] = data.get("replacement_target_ref")
        elif event_type == "target_reentered":
            target["collapse_trace"] = None
            target["matched_death_lines"] = []

    for ref, target in targets.items():
        parent_ref = target.get("parent_target_ref")
        if (
            parent_ref
            and target["state"] not in TERMINAL_TARGET_STATES
            and targets.get(parent_ref, {}).get("state") in TERMINAL_TARGET_STATES
        ):
            issues.append(f"target {ref}: active child has terminal parent {parent_ref}")

    # Pressure validity is derived only after replay, so later evidence withdrawal
    # and explicit invalidation affect every scale deterministically.
    for pressure in sorted(
        pressures.values(), key=lambda item: (item["sequence"], item["recorded_event_id"])
    ):
        reasons = pressure_inactive_reasons(
            pressure, targets, pressures, source_fresh
        )
        pressure["inactive_reasons"] = reasons
        pressure["active"] = not reasons

    pressure_vectors = {}
    target_pressures = defaultdict(lambda: defaultdict(list))
    for pressure in pressures.values():
        if pressure["active"]:
            target_pressures[pressure["target_ref"]][pressure["kind"]].append(pressure)
    for target_ref, kinds in target_pressures.items():
        pressure_vectors[target_ref] = {}
        for kind, values in kinds.items():
            strongest = max(values, key=lambda item: STRENGTH_RANK[item["strength"]])
            pressure_vectors[target_ref][kind] = {
                "strongest": strongest["strength"],
                "pressure_ids": sorted(item["pressure_id"] for item in values),
                "independent_evidence_sets": sorted(
                    {tuple(sorted(item["evidence_refs"])) for item in values}
                ),
            }

    by_scale = {scale: {"targets": [], "active_pressures": []} for scale in SCALES}
    for target in targets.values():
        by_scale[target["scale"]]["targets"].append(target["target_ref"])
    for pressure in pressures.values():
        if pressure["active"]:
            by_scale[pressure["scale"]]["active_pressures"].append(pressure["pressure_id"])
    for value in by_scale.values():
        value["targets"].sort()
        value["active_pressures"].sort()

    return {
        "targets": targets,
        "pressures": pressures,
        "pressure_vectors": pressure_vectors,
        "by_scale": by_scale,
        "issues": issues,
        "event_count": len(ordered),
        "head_event_id": ordered[-1]["event_id"] if ordered else None,
        "head_sequence": ordered[-1].get("sequence", 0) if ordered else 0,
    }
