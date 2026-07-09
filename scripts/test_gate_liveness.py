"""Regression tests for barrel-fix-v5: making the collapse gate live.

Covers:
- M4: collapse-trace registry from both planes (frame episodes + governance
  targets), advisory matching, candidate_admitted / lineaged-open hooks, and
  the standalone trace-check command;
- M3: governance-pressure-derive suggestions (repeated failures -> contradiction,
  stale target sources -> staleness, orphan failures -> informational), the
  ready command's end-to-end validity, and the no-write invariant.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_core
import weilan_trace


WORKSPACE = r"D:\GateLivenessTest"
SCOPE = "gate-liveness"
FORBIDDEN = "wall clock timestamps decide cache invalidation ordering"


@pytest.fixture()
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "method-state"
    monkeypatch.setenv("WEILAN_METHOD_HOME", str(home))
    monkeypatch.setattr(weilan_trace, "assert_governance_write_allowed", lambda w, s: None)
    return home


def frame_event(frame_id, event_type, data):
    return weilan_trace.make_event(frame_id, event_type, "L3", WORKSPACE, data)


def write_collapsed_frame(frame_id, outcome="failed", forbidden=FORBIDDEN,
                          problem="cache invalidation ordering keeps failing"):
    path = runtime_core.state_root() / "frames" / "2026-01-01" / f"{frame_id}.jsonl"
    events = [
        frame_event(frame_id, "frame_opened", {
            "problem": problem,
            "success_criteria": "stable cache invalidation ordering",
            "budget": "bounded",
            "causal": {"scope": SCOPE},
        }),
        frame_event(frame_id, "candidate_admitted", {
            "candidate_id": "c1",
            "summary": "order cache invalidation by wall clock timestamps",
        }),
        frame_event(frame_id, "holder_selected", {
            "candidate_id": "c1",
            "why_reasonable": "clock ordering looked simplest",
            "next_expected_evidence": "replay test passes",
            "death_line": "two replay failures",
        }),
        frame_event(frame_id, "discriminating_test_executed", {
            "test": "replay ordering test",
            "result": "failed",
        }),
        frame_event(frame_id, "minimal_unit_collapsed", {
            "scope": "assumption",
            "former_holder": "c1",
            "invalidating_evidence": "replay reordering under clock skew",
        }),
        frame_event(frame_id, "trace_emitted", {
            "once_reasonable": "single writer made clock order look safe",
            "invalidating_evidence": "clock skew reorders replay",
            "reusable_results": "replay harness",
            "forbidden_assumption": forbidden,
            "reentry_condition": "monotonic sequence numbers proven under skew",
        }),
        frame_event(frame_id, "frame_closed", {
            "outcome": outcome,
            "verdict": "clock-order route collapsed",
        }),
    ]
    for event in events:
        weilan_trace.append_event(path, event)
    return path


def write_open_frame(frame_id, problem="new route attempt"):
    path = runtime_core.state_root() / "frames" / "2026-01-02" / f"{frame_id}.jsonl"
    weilan_trace.append_event(path, frame_event(frame_id, "frame_opened", {
        "problem": problem,
        "success_criteria": "stable ordering",
        "budget": "bounded",
        "causal": {"scope": SCOPE},
    }))
    return path


def test_trace_check_matches_and_misses(state_home, capsys):
    write_collapsed_frame("frame-collapse-1")
    args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE,
        text="retry with wall clock timestamps for cache invalidation",
    )
    code = weilan_trace.command_trace_check(args)
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["trace_count"] == 1
    assert output["matches"][0]["forbidden_assumption"] == FORBIDDEN
    assert output["matches"][0]["reentry_condition"]

    args.text = "completely unrelated database schema migration topic"
    code = weilan_trace.command_trace_check(args)
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["matches"] == []


def test_candidate_admitted_carries_advisory(state_home, capsys):
    write_collapsed_frame("frame-collapse-2")
    write_open_frame("frame-open-2")
    args = argparse.Namespace(
        frame_id="frame-open-2", type="candidate_admitted",
        field=["candidate_id=c2",
               "summary=reuse wall clock timestamps for invalidation ordering"],
    )
    weilan_trace.command_event(args)
    output = json.loads(capsys.readouterr().out)
    assert output["trace_advisories"][0]["forbidden_assumption"] == FORBIDDEN

    args = argparse.Namespace(
        frame_id="frame-open-2", type="candidate_admitted",
        field=["candidate_id=c3", "summary=monotonic sequence numbers instead"],
    )
    weilan_trace.command_event(args)
    output = json.loads(capsys.readouterr().out)
    assert "trace_advisories" not in output


def test_candidate_advisory_error_is_observable(state_home, capsys):
    write_open_frame("frame-open-bad-ledger")
    governance_dir = weilan_trace.governance_directory(WORKSPACE, SCOPE)
    governance_dir.mkdir(parents=True, exist_ok=True)
    (governance_dir / "2026-01-01.jsonl").write_text("{bad json\n", encoding="utf-8")
    args = argparse.Namespace(
        frame_id="frame-open-bad-ledger", type="candidate_admitted",
        field=["candidate_id=c-bad",
               "summary=reuse wall clock timestamps for invalidation ordering"],
    )
    weilan_trace.command_event(args)
    output = json.loads(capsys.readouterr().out)
    assert output["event_type"] == "candidate_admitted"
    assert output["trace_advisory_error"]["authority"] == (
        "advisory_failure_never_blocks_or_authorizes"
    )
    assert "collapse trace governance registry is invalid" in (
        output["trace_advisory_error"]["message"]
    )


def test_lineaged_open_carries_advisory(state_home, capsys):
    write_collapsed_frame("frame-collapse-3")
    args = argparse.Namespace(
        scope=SCOPE, workspace=WORKSPACE, level="L2", branch="main",
        relation="root", parent=[],
        problem="try wall clock timestamps for cache invalidation again",
        success="ordering stays stable", budget="bounded",
    )
    weilan_trace.command_open(args)
    output = json.loads(capsys.readouterr().out)
    assert output["trace_advisories"][0]["forbidden_assumption"] == FORBIDDEN


def governance_append(event_type, data):
    return weilan_trace.append_governance_event(WORKSPACE, SCOPE, event_type, data)


def register_target(tmp_path, target_ref="goal:cache-order",
                    death_lines=("cache invalidation ordering failure",)):
    source = tmp_path / "target-source.md"
    source.write_text("cache ordering requirement\n", encoding="utf-8")
    snapshots = weilan_trace.source_snapshots([str(source)], WORKSPACE)
    governance_append("target_registered", {
        "target_ref": target_ref,
        "target_kind": "goal",
        "scale": "task",
        "death_lines": list(death_lines),
        "source_refs": [str(source)],
        "source_snapshots": snapshots,
    })
    return target_ref, source


def test_governance_collapse_trace_joins_registry(state_home, tmp_path, capsys):
    target_ref, source = register_target(tmp_path)
    frame_path = write_collapsed_frame("frame-collapse-4")
    snapshots = weilan_trace.source_snapshots(["frame:frame-collapse-4"], WORKSPACE)
    governance_append("pressure_recorded", {
        "pressure_id": "p1",
        "target_ref": target_ref,
        "kind": "contradiction",
        "strength": "strong",
        "scale": "task",
        "evidence_refs": ["frame:frame-collapse-4"],
        "source_snapshots": snapshots,
        "source_pressure_ids": [],
        "required_change": "abandon clock ordering",
        "causal_frame_id": "frame-collapse-4",
    })
    governance_append("target_collapsed", {
        "target_ref": target_ref,
        "from_state": "ACTIVE",
        "to_state": "COLLAPSED",
        "pressure_ids": ["p1"],
        "evidence_refs": ["frame:frame-collapse-4"],
        "source_snapshots": snapshots,
        "causal_frame_id": "frame-collapse-4",
        "matched_death_lines": ["cache invalidation ordering failure"],
        "trace": {
            "once_reasonable": "single writer",
            "invalidating_evidence": "clock skew",
            "reusable_results": "replay harness",
            "forbidden_assumption": "governance plane forbids wall clock ordering",
            "reentry_condition": "sequence numbers proven",
        },
    })
    registry = weilan_trace.collapse_trace_registry(WORKSPACE, SCOPE)
    sources = {item["source"] for item in registry}
    assert "frame:frame-collapse-4" in sources
    assert target_ref in sources


def test_pressure_derive_suggests_contradiction_and_command_is_valid(
    state_home, tmp_path, capsys
):
    target_ref, source = register_target(
        tmp_path, death_lines=("cache invalidation ordering failure",)
    )
    write_collapsed_frame("frame-fail-1")
    write_collapsed_frame("frame-fail-2")

    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    code = weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    contradiction = next(s for s in output["suggestions"] if s["kind"] == "contradiction")
    assert contradiction["target_ref"] == target_ref
    assert contradiction["strength"] == "medium"
    assert len(contradiction["evidence_refs"]) == 2
    assert "governance-pressure-record" in contradiction["ready_command"]

    # third failure upgrades strength
    write_collapsed_frame("frame-fail-3")
    weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    contradiction = next(s for s in output["suggestions"] if s["kind"] == "contradiction")
    assert contradiction["strength"] == "strong"

    # the suggestion is executable end-to-end via the real record command
    record_args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, target_ref=target_ref,
        kind="contradiction", strength=contradiction["strength"],
        evidence=contradiction["evidence_refs"],
        required_change="abandon the clock-order assumption",
        frame_id="frame-fail-3", persistence_audit_id=None,
    )
    weilan_trace.command_governance_pressure_record(record_args)
    recorded = json.loads(capsys.readouterr().out)
    assert recorded.get("saved", True)

    # once the suggested pressure is recorded, derive should not keep emitting
    # the same unresolved contradiction forever.
    code = weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["suggestions"] == []


def test_pressure_derive_flags_stale_target_sources(state_home, tmp_path, capsys):
    target_ref, source = register_target(tmp_path)
    write_open_frame("frame-stale-current")
    source.write_text("changed requirement\n", encoding="utf-8")
    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    staleness = [s for s in output["suggestions"] if s["kind"] == "staleness"]
    assert staleness and staleness[0]["target_ref"] == target_ref
    assert staleness[0]["recordable"] is True
    assert "ready_command" in staleness[0]
    assert "<current-frame>" not in staleness[0]["ready_command"]
    assert '"..."' not in staleness[0]["ready_command"]


def test_pressure_derive_deleted_stale_source_has_no_fake_ready_command(
    state_home, tmp_path, capsys
):
    target_ref, source = register_target(tmp_path)
    source.unlink()
    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    staleness = [s for s in output["suggestions"] if s["kind"] == "staleness"]
    assert staleness and staleness[0]["target_ref"] == target_ref
    assert staleness[0]["recordable"] is False
    assert "ready_command" not in staleness[0]
    assert staleness[0]["rejected_evidence_refs"]


def test_pressure_derive_reports_orphan_failures(state_home, capsys):
    write_collapsed_frame("frame-orphan-1")
    write_collapsed_frame("frame-orphan-2")
    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    assert output["suggestions"] == []
    assert output["informational"][0]["kind"] == "unregistered_risk"


def state_digest(home):
    digest = hashlib.sha256()
    for path in sorted(p for p in home.rglob("*") if p.is_file()):
        digest.update(path.as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_derive_and_check_write_nothing(state_home, tmp_path, capsys):
    register_target(tmp_path)
    write_collapsed_frame("frame-ro-1")
    write_collapsed_frame("frame-ro-2")
    # settle derived caches first so the no-write check sees steady state
    weilan_trace.command_governance_pressure_derive(
        argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    )
    weilan_trace.command_trace_check(
        argparse.Namespace(workspace=WORKSPACE, scope=SCOPE, text="wall clock cache")
    )
    capsys.readouterr()
    before = state_digest(state_home)
    weilan_trace.command_governance_pressure_derive(
        argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    )
    weilan_trace.command_trace_check(
        argparse.Namespace(workspace=WORKSPACE, scope=SCOPE, text="wall clock cache")
    )
    capsys.readouterr()
    assert state_digest(state_home) == before


def test_suppressed_contradiction_does_not_hide_staleness(state_home, tmp_path, capsys):
    """A covered contradiction on a target must not skip its staleness derivation."""

    target_ref, source = register_target(
        tmp_path, death_lines=("cache invalidation ordering failure",)
    )
    write_collapsed_frame("frame-both-1")
    write_collapsed_frame("frame-both-2")

    # record the contradiction pressure exactly as derive suggests -> covered
    args = argparse.Namespace(workspace=WORKSPACE, scope=SCOPE)
    weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    contradiction = next(s for s in output["suggestions"] if s["kind"] == "contradiction")
    record_args = argparse.Namespace(
        workspace=WORKSPACE, scope=SCOPE, target_ref=target_ref,
        kind="contradiction", strength=contradiction["strength"],
        evidence=contradiction["evidence_refs"],
        required_change="address repeated failures",
        frame_id="frame-both-2", persistence_audit_id=None,
    )
    weilan_trace.command_governance_pressure_record(record_args)
    capsys.readouterr()

    # now the same target's sources also go stale
    source.write_text("changed requirement" + chr(10), encoding="utf-8")
    weilan_trace.command_governance_pressure_derive(args)
    output = json.loads(capsys.readouterr().out)
    kinds = {s["kind"] for s in output["suggestions"]}
    assert "contradiction" not in kinds  # covered by the recorded pressure
    assert "staleness" in kinds          # must still surface for the same target


def main():
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
