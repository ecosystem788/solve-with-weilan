"""Regression tests for lower-the-floor memory-note v0.1."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")
SCOPE = "lower-floor-tooth"


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


def read_jsonl(root, *parts):
    base = root.joinpath(*parts)
    if not base.exists():
        return []
    records = []
    for path in base.rglob("*.jsonl"):
        records.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return records


def counts(root):
    return {
        "evidence": len(read_jsonl(root, "memory", "evidence", "workspaces")),
        "semantic": len(read_jsonl(root, "memory", "semantic", "workspaces")),
        "promotions": len(read_jsonl(root, "memory", "evidence", "promotions")),
    }


def assert_counts_unchanged(root, before, label):
    after = counts(root)
    if after != before:
        raise AssertionError(f"{label} wrote unexpected records: before={before}, after={after}")


def memory_note_args(workspace, source, claim, summary, signal="architectural_decision", kind="decision"):
    return [
        "memory-note",
        "--workspace",
        workspace,
        "--scope",
        SCOPE,
        "--signal",
        signal,
        "--claim",
        claim,
        "--source",
        source,
        "--kind",
        kind,
        "--summary",
        summary,
        "--stable",
        "--reusable",
        "--privacy-reviewed",
    ]


def normalize_sources(values):
    normalized = []
    for value in values:
        if value.startswith("evidence:"):
            normalized.append("evidence:<normalized>")
        else:
            normalized.append(value)
    return normalized


def normalize_semantic(entry):
    return {
        "kind": entry["kind"],
        "summary": entry["summary"],
        "detail": entry["detail"],
        "tags": entry["tags"],
        "sources": normalize_sources(entry["sources"]),
        "supersedes": entry["supersedes"],
        "conflicts_with": entry["conflicts_with"],
        "promotion": {
            "schema_version": entry["promotion"]["schema_version"],
            "evidence_id": "<normalized>",
            "promotion_id": "<normalized>",
        },
    }


def normalize_promotion(record):
    return {
        "decision": record["decision"],
        "requested_kind": record["requested_kind"],
        "summary_hash": record["summary_hash"],
        "gate_checks": record["gate_checks"],
        "reason_codes": record["reason_codes"],
        "evidence_id": "<normalized>",
        "promotion_id": "<normalized>",
        "semantic_memory_id": "<normalized>",
    }


def case_environment(root, name, allow_unresolved=True, budget="2"):
    environment = os.environ.copy()
    environment["WEILAN_METHOD_HOME"] = str(root / f"method-state-{name}")
    environment["WEILAN_SEMANTIC_BUDGET_DEFAULT"] = str(budget)
    if allow_unresolved:
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
    else:
        environment.pop("WEILAN_ALLOW_UNRESOLVED_CONVERSATION", None)
    workspace = str(root / f"workspace-{name}")
    Path(workspace).mkdir()
    return workspace, environment


def capture_args(workspace, signal, claim, source, tags=None):
    arguments = [
        "evidence-capture",
        "--workspace",
        workspace,
        "--scope",
        SCOPE,
        "--signal",
        signal,
        "--claim",
        claim,
        "--source",
        source,
    ]
    for tag in tags or []:
        arguments.extend(["--tag", tag])
    return arguments


def note_args(
    workspace,
    source,
    claim,
    summary,
    signal="architectural_decision",
    kind="decision",
    tags=None,
    stable=True,
    reusable=True,
    privacy_reviewed=True,
):
    arguments = [
        "memory-note",
        "--workspace",
        workspace,
        "--scope",
        SCOPE,
        "--signal",
        signal,
        "--claim",
        claim,
        "--source",
        source,
        "--kind",
        kind,
        "--summary",
        summary,
    ]
    for tag in tags or []:
        arguments.extend(["--tag", tag])
    if stable:
        arguments.append("--stable")
    if reusable:
        arguments.append("--reusable")
    if privacy_reviewed:
        arguments.append("--privacy-reviewed")
    return arguments


def promote_args(
    workspace,
    evidence_id,
    kind,
    summary,
    tags=None,
    stable=True,
    reusable=True,
    privacy_reviewed=True,
):
    arguments = [
        "evidence-promote",
        "--workspace",
        workspace,
        "--scope",
        SCOPE,
        "--evidence-id",
        evidence_id,
        "--kind",
        kind,
        "--summary",
        summary,
    ]
    for tag in tags or []:
        arguments.extend(["--tag", tag])
    if stable:
        arguments.append("--stable")
    if reusable:
        arguments.append("--reusable")
    if privacy_reviewed:
        arguments.append("--privacy-reviewed")
    return arguments


def classify_result(result):
    if result.returncode:
        return {
            "category": "usage",
            "reason_text": result.stderr.strip().replace("error: ", "", 1),
            "reason_codes": [],
        }
    output = json.loads(result.stdout)
    if output.get("decision") == "REJECTED" or output.get("promoted") is False:
        return {
            "category": "policy",
            "reason_text": "",
            "reason_codes": output.get("reason_codes", []),
        }
    raise AssertionError(f"expected a rejection, got: {output}")


def assert_same_rejection(label, note_result, manual_result, expected_reason):
    note = classify_result(note_result)
    manual = classify_result(manual_result)
    if note["category"] != manual["category"]:
        raise AssertionError(f"{label}: category mismatch: note={note}, manual={manual}")
    if note["category"] == "policy":
        if note["reason_codes"] != manual["reason_codes"]:
            raise AssertionError(f"{label}: reason_codes mismatch: note={note}, manual={manual}")
        if expected_reason not in note["reason_codes"]:
            raise AssertionError(f"{label}: missing expected reason {expected_reason}: {note}")
    else:
        if expected_reason not in note["reason_text"] or expected_reason not in manual["reason_text"]:
            raise AssertionError(f"{label}: missing expected usage text {expected_reason}: note={note}, manual={manual}")


def assert_note_reject_atomic(label, root, before, result):
    classified = classify_result(result)
    if classified["category"] == "policy":
        data = json.loads(result.stdout)
        if data.get("evidence_written") or data.get("audit_written") or data.get("semantic_memory_written"):
            raise AssertionError(f"{label}: policy reject reported a write: {data}")
    assert_counts_unchanged(root, before, label)


def exercise_success(root, environment):
    workspace = str(root / "workspace-note")
    Path(workspace).mkdir()
    source = "conversation:thread-note#turn-1"
    claim = "Memory note keeps sourced conclusions gated"
    summary = "Memory note keeps sourced conclusions gated"
    result = run(memory_note_args(workspace, source, claim, summary), environment)
    if not result["saved"] or not result["promoted"]:
        raise AssertionError("memory-note did not promote a valid note")
    if not result["evidence_written"] or not result["audit_written"] or not result["semantic_memory_written"]:
        raise AssertionError("memory-note did not write the three success records")
    state = Path(environment["WEILAN_METHOD_HOME"])
    evidence = read_jsonl(state, "memory", "evidence", "workspaces")
    semantic = read_jsonl(state, "memory", "semantic", "workspaces")
    promotions = read_jsonl(state, "memory", "evidence", "promotions")
    if len(evidence) != 1 or len(semantic) != 1 or len(promotions) != 1:
        raise AssertionError("valid memory-note did not write exactly one evidence, semantic entry, and audit")
    if promotions[0].get("signal") is not None:
        raise AssertionError("promotion audit should not grow a signal field in this candidate")
    if promotions[0]["evidence_id"] != evidence[0]["evidence_id"]:
        raise AssertionError("promotion audit cannot join back to its evidence")
    if evidence[0]["signal"] != "architectural_decision":
        raise AssertionError("evidence signal was not preserved for audit join")
    if semantic[0]["promotion"]["promotion_id"] != promotions[0]["promotion_id"]:
        raise AssertionError("semantic entry lost promotion linkage")
    return result


def exercise_manual_equivalence(root, environment):
    note_workspace = str(root / "workspace-note")
    manual_workspace = str(root / "workspace-manual")
    Path(manual_workspace).mkdir()
    source = "conversation:thread-note#turn-2"
    claim = "Memory note parity keeps gate checks auditable"
    summary = "Memory note parity keeps gate checks auditable"
    before = counts(Path(environment["WEILAN_METHOD_HOME"]))
    note = run(memory_note_args(note_workspace, source, claim, summary), environment)
    captured = run(
        [
            "evidence-capture",
            "--workspace",
            manual_workspace,
            "--scope",
            SCOPE,
            "--signal",
            "architectural_decision",
            "--claim",
            claim,
            "--source",
            source,
        ],
        environment,
    )
    manual = run(
        [
            "evidence-promote",
            "--workspace",
            manual_workspace,
            "--scope",
            SCOPE,
            "--evidence-id",
            captured["evidence_id"],
            "--kind",
            "decision",
            "--summary",
            summary,
            "--stable",
            "--reusable",
            "--privacy-reviewed",
        ],
        environment,
    )
    state = Path(environment["WEILAN_METHOD_HOME"])
    semantic = read_jsonl(state, "memory", "semantic", "workspaces")
    promotions = [record for record in read_jsonl(state, "memory", "evidence", "promotions") if record["decision"] == "PROMOTED"]
    note_semantic = next(entry for entry in semantic if entry["promotion"]["evidence_id"] == note["evidence_id"])
    manual_semantic = next(entry for entry in semantic if entry["promotion"]["evidence_id"] == manual["evidence_id"])
    note_promotion = next(record for record in promotions if record["evidence_id"] == note["evidence_id"])
    manual_promotion = next(record for record in promotions if record["evidence_id"] == manual["evidence_id"])
    if normalize_semantic(note_semantic) != normalize_semantic(manual_semantic):
        raise AssertionError("memory-note semantic entry diverged from manual capture+promote")
    if normalize_promotion(note_promotion) != normalize_promotion(manual_promotion):
        raise AssertionError("memory-note promotion audit diverged from manual capture+promote")
    after = counts(state)
    expected_delta = {"evidence": 2, "semantic": 2, "promotions": 2}
    actual_delta = {key: after[key] - before[key] for key in before}
    if actual_delta != expected_delta:
        raise AssertionError(f"unexpected success-path record delta: {actual_delta}")


def exercise_rejections(root, environment):
    workspace = str(root / "workspace-note")
    state = Path(environment["WEILAN_METHOD_HOME"])
    source = "conversation:thread-note#turn-reject"

    before = counts(state)
    missing_flag = run(
        [
            "memory-note",
            "--workspace",
            workspace,
            "--scope",
            SCOPE,
            "--signal",
            "architectural_decision",
            "--claim",
            "Missing stability flag stays atomic",
            "--source",
            source,
            "--kind",
            "decision",
            "--summary",
            "Missing stability flag stays atomic",
            "--reusable",
            "--privacy-reviewed",
        ],
        environment,
    )
    if "stability_not_confirmed" not in missing_flag["reason_codes"]:
        raise AssertionError("missing stability flag did not hit the promotion gate")
    assert_counts_unchanged(state, before, "missing flag rejection")

    for signal in ("casual_chat", "social_acknowledgement", "transient_status"):
        before = counts(state)
        result = run(
            memory_note_args(
                workspace,
                source,
                "Non durable signal stays out of memory",
                "Non durable signal stays out of memory",
                signal=signal,
                kind="decision",
            ),
            environment,
        )
        if result["reason_codes"] != ["non_durable_conversation_signal"]:
            raise AssertionError(f"{signal} did not hit the non-durable signal gate")
        assert_counts_unchanged(state, before, f"{signal} rejection")

    before = counts(state)
    wrong_kind = run(
        memory_note_args(
            workspace,
            source,
            "Verified result cannot become a decision",
            "Verified result cannot become a decision",
            signal="verified_result",
            kind="decision",
        ),
        environment,
    )
    if "semantic_kind_not_allowed_for_signal" not in wrong_kind["reason_codes"]:
        raise AssertionError("wrong kind did not hit signal_kind_allowed")
    assert_counts_unchanged(state, before, "wrong kind rejection")

    before = counts(state)
    sensitive_capture = run(
        memory_note_args(
            workspace,
            source,
            "api_key=secret123",
            "api key secret123",
        ),
        environment,
    )
    if "credential_like_material" not in sensitive_capture["reason_codes"]:
        raise AssertionError("sensitive claim did not hit capture sensitive gate")
    assert_counts_unchanged(state, before, "capture sensitive rejection")

    before = counts(state)
    sensitive_promote = run(
        memory_note_args(
            workspace,
            source,
            "Sensitive summary gate should reject",
            "Sensitive summary gate should reject api_key=secret123",
        ),
        environment,
    )
    if "semantic_content_failed_sensitive_material_check" not in sensitive_promote["reason_codes"]:
        raise AssertionError("sensitive summary did not hit promote sensitive gate")
    assert_counts_unchanged(state, before, "promote sensitive rejection")

    before = counts(state)
    budget = run(
        memory_note_args(
            workspace,
            source,
            "Budget exhaustion stays atomic",
            "Budget exhaustion stays atomic",
        ),
        environment,
    )
    if "semantic_budget_exhausted" not in budget["reason_codes"]:
        raise AssertionError("full semantic budget did not reject memory-note")
    assert_counts_unchanged(state, before, "budget rejection")

    before = counts(state)
    bad_source = completed(
        memory_note_args(
            workspace,
            "frame:not-conversation",
            "Bad source is usage error",
            "Bad source is usage error",
        ),
        environment,
    )
    if bad_source.returncode == 0 or "conversation evidence requires" not in bad_source.stderr:
        raise AssertionError("non-conversation source did not fail as a usage error")
    assert_counts_unchanged(state, before, "bad source usage error")


def exercise_full_reject_equivalence(root):
    source = "conversation:thread-equiv#turn"
    cases = []
    for signal in ("casual_chat", "social_acknowledgement", "transient_status"):
        cases.append({
            "name": f"non_durable_{signal}",
            "phase": "capture",
            "signal": signal,
            "claim": "Non durable signal stays out",
            "summary": "Non durable signal stays out",
            "expected": "non_durable_conversation_signal",
        })
    cases.extend([
        {
            "name": "empty_claim",
            "phase": "capture",
            "claim": "",
            "summary": "Empty claim is invalid",
            "expected": "--claim cannot be empty",
        },
        {
            "name": "bad_source",
            "phase": "capture",
            "source": "frame:not-conversation",
            "claim": "Bad source is invalid",
            "summary": "Bad source is invalid",
            "expected": "conversation evidence requires --source",
        },
        {
            "name": "over_six_lines",
            "phase": "capture",
            "claim": "\n".join(f"line {index}" for index in range(7)),
            "summary": "line",
            "expected": "claim_exceeds_six_line_fragment_boundary",
        },
        {
            "name": "sensitive_capture",
            "phase": "capture",
            "claim": "api_key=secret123",
            "summary": "api key secret123",
            "expected": "credential_like_material",
        },
        {
            "name": "turn_unresolved",
            "phase": "capture",
            "allow_unresolved": False,
            "source": "conversation:not-present#turn",
            "claim": "Turn must resolve",
            "summary": "Turn must resolve",
            "expected": "conversation turn provenance is not resolvable",
        },
        {
            "name": "evidence_oversize",
            "phase": "capture",
            "claim": "Oversize evidence is rejected",
            "summary": "Oversize evidence is rejected",
            "tags": ["x" * 5000],
            "expected": "evidence_fragment_exceeds_4096_bytes",
        },
        {
            "name": "missing_stable",
            "phase": "promote",
            "stable": False,
            "expected": "stability_not_confirmed",
        },
        {
            "name": "missing_reusable",
            "phase": "promote",
            "reusable": False,
            "expected": "cross_task_reuse_not_confirmed",
        },
        {
            "name": "missing_privacy_reviewed",
            "phase": "promote",
            "privacy_reviewed": False,
            "expected": "privacy_review_not_confirmed",
        },
        {
            "name": "wrong_kind",
            "phase": "promote",
            "signal": "verified_result",
            "kind": "decision",
            "claim": "Verified result cannot become a decision",
            "summary": "Verified result cannot become a decision",
            "expected": "semantic_kind_not_allowed_for_signal",
        },
        {
            "name": "empty_summary",
            "phase": "promote",
            "summary": "",
            "expected": "semantic_summary_empty",
        },
        {
            "name": "sensitive_promote",
            "phase": "promote",
            "claim": "Sensitive summary gate should reject",
            "summary": "Sensitive summary gate should reject api_key=secret123",
            "expected": "semantic_content_failed_sensitive_material_check",
        },
        {
            "name": "ungrounded_summary",
            "phase": "promote",
            "claim": "AlphaOne",
            "summary": "BetaTwo",
            "expected": "semantic_summary_not_grounded_in_evidence_claim",
        },
        {
            "name": "budget_exhausted",
            "phase": "promote",
            "budget": "0",
            "claim": "Budget exhaustion rejects",
            "summary": "Budget exhaustion rejects",
            "expected": "semantic_budget_exhausted",
        },
    ])

    for case in cases:
        workspace, case_env = case_environment(
            root,
            case["name"],
            allow_unresolved=case.get("allow_unresolved", True),
            budget=case.get("budget", "2"),
        )
        state = Path(case_env["WEILAN_METHOD_HOME"])
        before = counts(state)
        case_source = case.get("source", source)
        signal = case.get("signal", "architectural_decision")
        kind = case.get("kind", "decision")
        claim = case.get("claim", "Memory note reject equivalence")
        summary = case.get("summary", claim)
        tags = case.get("tags", [])
        note_result = completed(
            note_args(
                workspace,
                case_source,
                claim,
                summary,
                signal=signal,
                kind=kind,
                tags=tags,
                stable=case.get("stable", True),
                reusable=case.get("reusable", True),
                privacy_reviewed=case.get("privacy_reviewed", True),
            ),
            case_env,
        )
        assert_note_reject_atomic(case["name"], state, before, note_result)
        if case["phase"] == "capture":
            manual_result = completed(
                capture_args(workspace, signal, claim, case_source, tags=tags),
                case_env,
            )
        else:
            captured = run(
                capture_args(workspace, signal, claim, case_source, tags=None),
                case_env,
            )
            manual_result = completed(
                promote_args(
                    workspace,
                    captured["evidence_id"],
                    kind,
                    summary,
                    tags=tags,
                    stable=case.get("stable", True),
                    reusable=case.get("reusable", True),
                    privacy_reviewed=case.get("privacy_reviewed", True),
                ),
                case_env,
            )
        assert_same_rejection(case["name"], note_result, manual_result, case["expected"])

    retry_workspace, retry_env = case_environment(root, "retry_no_dup")
    retry_state = Path(retry_env["WEILAN_METHOD_HOME"])
    retry_source = "conversation:thread-equiv#retry"
    before = counts(retry_state)
    rejected = completed(
        note_args(
            retry_workspace,
            retry_source,
            "Retry after reject should not duplicate",
            "Retry after reject should not duplicate",
            privacy_reviewed=False,
        ),
        retry_env,
    )
    assert_note_reject_atomic("retry missing privacy", retry_state, before, rejected)
    fixed = run(
        note_args(
            retry_workspace,
            retry_source,
            "Retry after reject should not duplicate",
            "Retry after reject should not duplicate",
        ),
        retry_env,
    )
    if not fixed["promoted"]:
        raise AssertionError("retry with fixed flags did not promote")
    expected = {"evidence": 1, "semantic": 1, "promotions": 1}
    actual = counts(retry_state)
    if actual != expected:
        raise AssertionError(f"retry created duplicate records: {actual}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-memory-note-test-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        environment["WEILAN_ALLOW_UNRESOLVED_CONVERSATION"] = "1"
        environment["WEILAN_SEMANTIC_BUDGET_DEFAULT"] = "2"
        exercise_success(root, environment)
        exercise_manual_equivalence(root, environment)
        exercise_rejections(root, environment)
        exercise_full_reject_equivalence(root)
        print(
            json.dumps(
                {
                    "valid": True,
                    "floor_lowered": "2_to_1",
                    "success_records": "evidence+semantic+promotion_audit",
                    "rejects_are_atomic": True,
                    "manual_equivalence": True,
                    "reject_equivalence_full": True,
                    "retry_no_duplicate": True,
                    "non_durable_signals_tested": 3,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
