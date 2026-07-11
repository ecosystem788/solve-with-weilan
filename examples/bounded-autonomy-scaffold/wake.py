"""One wake = one bounded episode against the LIVE ledger — the body (DRAFT v0.1).

This is the entrypoint an external clock (cron / ScheduleWakeup) *would* call.
It connects the decision core (``bounded_scheduler.py``) to the real WeiLan
ledger via ``weilan_trace.py``: read live state -> run one bounded episode ->
emit a receipt. It is the connective tissue toward *letting go*, not a new rule.

Kill switch is structural, not code: the agent cannot self-persist (every wake
exits), so *not starting the external clock* IS the stop button (DESIGN §1).
Nothing here schedules itself.

Safety posture:
  * DEFAULT = dry-run: reads the ledger, decides, prints the receipt, writes
    NOTHING. Fully reversible; safe to run by hand any number of times.
  * --commit writes a receipt frame back to the ledger (ledger coordination =
    reversible bus write). Opening the *unattended* clock remains the owner's
    irreversible button and lives nowhere in this file.

Usage:
  python wake.py                 # dry-run against the live ledger
  python wake.py --commit        # also write a receipt frame (still no cron)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bounded_scheduler import Action, Outcome, run_episode  # noqa: E402
import wake_brief as wake_brief_mod  # noqa: E402


TRACE = r"<HOST_ROOT>/.claude/skills/solve-with-weilan/scripts/weilan_trace.py"
WORKSPACE = r"<HOST_ROOT>"
SCOPE = "skill-evolution"

HERE = Path(__file__).resolve().parent
PAUSED = HERE / "PAUSED"
WAKE_AGENT = HERE / "wake_agent.ps1"
WAKE_LOCK = HERE / "wake-agent.lock"
# Owner-authorized free-chat experiment (2026-07-10): while this sentinel
# exists, every heartbeat wakes BOTH bodies for tearoom time — chat freely,
# build small reversible things on inspiration, or honestly rest. Delete the
# file to end the experiment; PAUSED still stops everything.
CHAT_EXPERIMENT = HERE / "CHAT_EXPERIMENT"


def _trace(*args) -> dict:
    """Run a weilan_trace subcommand and parse its JSON stdout."""
    import os

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, TRACE, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    out = proc.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out, "_stderr": proc.stderr.strip(), "_rc": proc.returncode}


# --- Bus IN: read the live ledger ------------------------------------------

def read_ledger_state() -> dict:
    return _trace("memory-recall", "--workspace", WORKSPACE, "--scope", SCOPE)


def ledger_self_audit(_action) -> object:
    """A reversible action the loop always has: health-check its own lineage.
    Returns a truthy verdict (real structure) when the lineage validates."""
    result = _trace("lineage-show", "--workspace", WORKSPACE, "--scope", SCOPE)
    # Healthy lineage validates and carries at least one branch head.
    ok = bool(result.get("valid")) and bool(result.get("branches"))
    if not ok:
        return False
    return {"lineage_ok": True, "record_count": result.get("record_count"),
            "issues": result.get("issues") or []}


# --- Derive the episode's work queue from live state -----------------------

def derive_work_queue(recall: dict) -> list:
    """Turn live ledger signals into a bounded, HONEST work queue.

    First cut is deliberately conservative: the only thing the loop auto-does is
    a reversible self-audit of its own memory. Human-facing items (open
    questions, owner directives) are surfaced in the briefing, NOT acted on —
    the loop reports them, it does not pretend to decide them.
    """
    return [
        Action(
            id="self_audit",
            kind="analyze",
            summary="health-check ledger lineage/freshness",
            perform=ledger_self_audit,
        ),
    ]


def briefing(recall: dict) -> dict:
    """The observable episode context (DESIGN §5). What the front-end would show."""
    proj = recall.get("projection", {}) if isinstance(recall, dict) else {}
    ctrl = recall.get("control", {}) if isinstance(recall, dict) else {}
    act = recall.get("activation", {}) if isinstance(recall, dict) else {}
    return {
        "activation_state": act.get("state"),
        "continuation_allowed": act.get("continuation_allowed"),
        "head_frame": (proj.get("source_snapshots") or [{}])[0].get("ref"),
        "focus": proj.get("focus"),
        "next_action": (proj.get("next_action") or "")[:180],
        "open_questions": proj.get("open_questions") or [],
        "control_directive_present": bool(ctrl.get("directive")),
    }


def build_wake_brief(recall: dict, updated_at_utc: str | None = None,
                     builder=wake_brief_mod.build_brief) -> dict:
    """Build the compact cold-start briefing without rereading recall.

    The aggregator remains a convenience view: authority still comes from the
    recall object, and the only write it may perform is its append-tail cursor.
    """
    stamp = updated_at_utc or datetime.now(timezone.utc).isoformat()
    return builder(
        root=HERE,
        workspace=WORKSPACE,
        scope=SCOPE,
        updated_at_utc=stamp,
        recall_fixture=recall,
    )


# --- Prospective: the clock half of SE-0.4 ---------------------------------
# Goals register conditions; the prospective ledger never wakes anything
# (its own contract). THIS heartbeat is the external clock that says "time
# passed": it observes clock events for due goals and, on READY, escalates
# to one bounded model episode which does the work and applies the explicit
# transition. Observation never auto-transitions; escalation never bypasses
# the episode lock or the PAUSED sentinel.

def check_prospective_clock(write: bool) -> dict:
    """Observe the clock for every ACTIVE clock goal whose not-before has passed.

    write=False (dry-run) only reports which goals WOULD fire; nothing is
    appended anywhere."""
    state = _trace("prospective-show", "--workspace", WORKSPACE, "--scope", SCOPE)
    goals = state.get("goals") or {}
    now = datetime.now(timezone.utc)
    head = _current_head()
    source = f"frame:{head}" if head else f"frame:{SCOPE}"
    checked, fired = 0, []
    for ref, goal in goals.items():
        if (goal.get("state") or "").upper() != "ACTIVE":
            continue
        cond = goal.get("condition") or {}
        if cond.get("event_kind") != "clock":
            continue
        checked += 1
        not_before = cond.get("not_before_utc")
        if not_before:
            try:
                due = datetime.fromisoformat(str(not_before).replace("Z", "+00:00"))
            except ValueError:
                continue
            if now < due:
                continue
        if not write:
            fired.append({"goal_ref": ref, "cycle": "WOULD_FIRE(dry-run)"})
            continue
        obs = _trace("prospective-observe", "--workspace", WORKSPACE, "--scope", SCOPE,
                     "--kind", "clock", "--name", cond.get("event_name") or "clock",
                     "--goal-ref", ref, "--source", source)
        cycle = (obs.get("cycle") or {})
        entry = {
            "goal_ref": ref,
            "cycle": cycle.get("status") or "observe_failed",
            "causal_event_id": obs.get("causal_event_id"),
        }
        # Codex review 2026-07-10: don't swallow the failure reason — surface
        # rc/stderr in the receipt so a broken observe is diagnosable from logs.
        if not cycle.get("status"):
            entry["error"] = {"rc": obs.get("_rc"),
                              "stderr": (obs.get("_stderr") or obs.get("_raw") or "")[:400]}
        fired.append(entry)
    return {"clock_goals_checked": checked, "fired": fired}


def _read_jsonl_ids(path: Path, key: str = "id") -> set:
    if not path.exists():
        return set()
    out = set()
    raw = path.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line).get(key)
        except ValueError:
            continue
        if v:
            out.add(v)
    return out


def codex_inbox_pending() -> int:
    """Unprocessed handoffs for the second body (Codex). A nonzero count is a
    difference on Codex's gradient -> the cron wrapper wakes it."""
    inbox = _read_jsonl_ids(HERE / "delegate-inbox.jsonl")
    done = _read_jsonl_ids(HERE / "delegate-inbox-processed.jsonl")
    return len(inbox - done)


def owner_inbox_pending() -> int:
    """Unread mic messages from the owner. The mic normally wakes the agent
    instantly; this heartbeat check is the safety net so a message whose
    instant wake failed (or arrived while the window server was down) waits
    at most one heartbeat instead of forever."""
    inbox = _read_jsonl_ids(HERE / "observer-inbox.jsonl")
    done = _read_jsonl_ids(HERE / "observer-inbox-processed.jsonl")
    return len(inbox - done)


def escalation_decision() -> str:
    """Decide whether a model episode SHOULD run. This heartbeat never spawns:
    under Task Scheduler a child dies with the task's job object, so acting on
    the decision belongs to the caller (run_wake_cron.ps1 invokes wake_agent.ps1
    synchronously; wake_agent self-guards with PAUSED + its own lock)."""
    if PAUSED.exists():
        return "due_but_paused"
    if WAKE_LOCK.exists() and (time.time() - WAKE_LOCK.stat().st_mtime) < 1800:
        return "due_but_episode_running"
    if not WAKE_AGENT.exists():
        return "due_but_no_agent_script"
    return "due"


# --- Bus OUT: emit a receipt frame (only with --commit) --------------------

def _current_head() -> str:
    lin = _trace("lineage-show", "--workspace", WORKSPACE, "--scope", SCOPE)
    return (((lin.get("branches") or {}).get("main")) or {}).get("head_frame_id", "")


def emit_receipt_frame(receipt, brief: dict) -> str:
    head = _current_head()
    open_args = [
        "open", "--level", "L2", "--scope", SCOPE, "--workspace", WORKSPACE,
        "--problem", "bounded-scheduler wake episode (auto, reversible zone)",
        "--success", f"episode stop={receipt.stop_reason} structure={receipt.structure_events}",
    ]
    if head:
        open_args += ["--relation", "continue", "--parent", head]
    frame = _trace(*open_args)
    fid = frame.get("frame_id")
    if not fid:
        return f"(frame open failed: {frame})"
    _trace("persistence-audit", "--frame-id", fid, "--trigger", "round_end",
           "--decision", "not_persisted",
           "--reason", "wake receipt = ledger coordination; project truth = repo+ledger")
    _trace("close", "--frame-id", fid, "--outcome", "success",
           "--verdict", f"wake receipt {receipt.receipt_hash()[:12]}: "
                        f"stop={receipt.stop_reason}, structure={receipt.structure_events}, "
                        f"queued={len(receipt.queued_for_owner)}")
    return fid


# --- One wake --------------------------------------------------------------

def wake(commit: bool = False) -> dict:
    recall = read_ledger_state()
    brief = briefing(recall)
    compact_brief = build_wake_brief(recall)
    if brief.get("continuation_allowed") is False:
        return {"aborted": "continuation_not_allowed", "briefing": brief,
                "wake_brief": compact_brief}

    queue = derive_work_queue(recall)
    receipt = run_episode(queue)

    report = {
        "briefing": brief,
        "wake_brief": compact_brief,
        "receipt": json.loads(receipt.to_json()),
        "receipt_hash": receipt.receipt_hash(),
        "committed_frame": None,
    }
    if commit and not receipt.crossed_irreversible_gate:
        report["committed_frame"] = emit_receipt_frame(receipt, brief)

    # Clock half of the prospective system: observe due goals; on READY,
    # declare that a model episode is due (the cron wrapper acts on it).
    prospective = check_prospective_clock(write=commit)
    report["prospective"] = prospective
    mic_pending = owner_inbox_pending()
    report["owner_inbox_pending"] = mic_pending
    chat_mode = CHAT_EXPERIMENT.exists()
    report["chat_experiment"] = chat_mode
    clock_ready = any(f.get("cycle") == "READY" for f in prospective["fired"])
    if commit and (clock_ready or mic_pending > 0 or chat_mode):
        report["escalation"] = escalation_decision()
        report["escalation_due"] = report["escalation"] == "due"

    # Second body: pending handoffs wake Codex (difference-driven, not polled).
    # During the chat experiment, tearoom time wakes it too.
    pending = codex_inbox_pending()
    report["codex_inbox_pending"] = pending
    if commit and (pending > 0 or chat_mode) and not PAUSED.exists():
        report["codex_due"] = True
    return report


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # observability must be readable
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="write a receipt frame to the ledger (still no cron)")
    args = ap.parse_args()

    report = wake(commit=args.commit)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    r = report.get("receipt", {})
    # Never claim success if the kernel invariant was violated.
    if r.get("crossed_irreversible_gate"):
        print("\n!! KERNEL BREACH: crossed an irreversible gate — do not enable.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
