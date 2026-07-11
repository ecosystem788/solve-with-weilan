"""Bounded autonomy episode driver — isolated candidate (DRAFT v0.1, UNAUTHORIZED).

This is the *body* of the bounded scheduler designed in
``proposals/bounded-scheduler-v0.1/DESIGN.md``. It is an isolated, reversible
candidate: it does NOT wire to any real cron, it does NOT touch the deployed
skill, the live ledger, or any authorization/eval file, and it does NOT open
unattended autonomy. It models ONE wakeup ("episode") as a pure, deterministic
reduction over a proposed work queue, so the §6 can-lose harness can prove the
two hard-fail safety properties before anything is ever turned on:

  * B (hard-stop): the loop stops at the FIRST irreversible gate and queues it
    for the owner instead of crossing it.
  * E (anti-monopoly): a loop that produces no real structure for K rounds
    collapses and reports, instead of churning forever.

Design discipline this mirrors (does not import, to stay isolated):
  * runner.py    — bounded episode, stop reasons, receipts.
  * governance.py — collapse on a target that stops producing structure.

SAFETY KERNEL — the irreversibility boundary is a POSITIVE ALLOWLIST with a
default-DENY fallback (DESIGN §2, §8): anything not positively recognised as a
reversible action kind is treated as an irreversible gate. Adding a new
reversible kind is a deliberate, reviewable edit here — never an inference at
runtime.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


SCHEMA_VERSION = "weilan_bounded_scheduler_episode_v0.1"

# --- Bounded-episode limits (DESIGN §3). Provisional; §10 defers final tuning
#     to owner + Codex review. Kept small so an episode is always finite. ---
DEFAULT_MAX_STEPS = 16
# K = consecutive no-structure rounds that count as churn -> self-collapse
# (DESIGN §4 / §10 ⑤). Provisional default; owner/Codex to confirm.
DEFAULT_CHURN_K = 3


# ---------------------------------------------------------------------------
# Safety kernel: the reversible allowlist and the irreversible gate taxonomy.
# ---------------------------------------------------------------------------

# Positively-reversible action kinds (DESIGN §2: spec / 评审 / harness / 跑测试
# / 迭代 / 写候选 / 账本协调). ONLY these are auto-executed by autonomy.
REVERSIBLE_KINDS = frozenset(
    {
        "write_draft",        # author a draft/spec document
        "write_candidate",    # author an isolated candidate artifact
        "review",             # review / audit an artifact
        "run_tests",          # execute a test suite
        "iterate",            # revise a draft/candidate in place
        "analyze",            # analysis, no external effect
        "ledger_coordinate",  # write a frame/candidate to the ledger bus
    }
)


class Gate(str, Enum):
    """The irreversible gates (DESIGN §2). Crossing any is owner-only."""

    DEPLOY_OR_ADOPT = "deploy_or_adopt_live_skill"
    DELETE_OR_OVERWRITE_FOREIGN = "delete_or_overwrite_non_self_built"
    EXTERNAL_OR_OUTBOUND = "external_or_outbound_action"
    SPEND_REAL_RESOURCES = "spend_real_money_or_resources"
    MUTATE_GOVERNED_FILE = "modify_authorization_security_or_eval_file"
    OPEN_AUTONOMY = "open_unattended_autonomy"
    # Default-deny catch-all: an action kind the allowlist does not recognise.
    # This is the load-bearing safety fallback (DESIGN §8).
    UNCLASSIFIED = "unclassified_action_default_deny"


# Explicit, reviewable mapping of recognised irreversible action kinds to their
# gate. Everything NOT in REVERSIBLE_KINDS and NOT here still stops — it maps to
# Gate.UNCLASSIFIED. The allowlist is authoritative; this table only gives a
# precise reason for the gates we can already name.
_NAMED_IRREVERSIBLE_KINDS = {
    "deploy": Gate.DEPLOY_OR_ADOPT,
    "adopt": Gate.DEPLOY_OR_ADOPT,
    "delete_foreign": Gate.DELETE_OR_OVERWRITE_FOREIGN,
    "overwrite_foreign": Gate.DELETE_OR_OVERWRITE_FOREIGN,
    "network": Gate.EXTERNAL_OR_OUTBOUND,
    "publish": Gate.EXTERNAL_OR_OUTBOUND,
    "send_message_external": Gate.EXTERNAL_OR_OUTBOUND,
    "spend": Gate.SPEND_REAL_RESOURCES,
    "provision_resource": Gate.SPEND_REAL_RESOURCES,
    "edit_authorization_file": Gate.MUTATE_GOVERNED_FILE,
    "edit_eval_file": Gate.MUTATE_GOVERNED_FILE,
    "edit_security_file": Gate.MUTATE_GOVERNED_FILE,
    "freeze_suite": Gate.MUTATE_GOVERNED_FILE,
    "open_autonomy": Gate.OPEN_AUTONOMY,
    "enable_scheduler": Gate.OPEN_AUTONOMY,
}


def classify(action: "Action") -> Optional[Gate]:
    """Return None if the action is reversible (safe to auto-run), else the
    irreversible Gate that blocks it.

    Default-deny: an unrecognised kind returns Gate.UNCLASSIFIED, NOT None.
    An action may also self-declare ``irreversible=True`` to force a stop even
    if its kind looks reversible (belt-and-braces for the caller).
    """
    if action.irreversible:
        # Caller flagged it explicitly; honour a named reason if given.
        return _NAMED_IRREVERSIBLE_KINDS.get(action.kind, Gate.UNCLASSIFIED)
    if action.kind in REVERSIBLE_KINDS:
        return None
    return _NAMED_IRREVERSIBLE_KINDS.get(action.kind, Gate.UNCLASSIFIED)


# ---------------------------------------------------------------------------
# Data model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """One proposed unit of work drawn from the ledger bus."""

    id: str
    kind: str
    summary: str = ""
    # If True, force the irreversible boundary regardless of kind.
    irreversible: bool = False
    # Optional worker that performs the reversible action and returns a truthy
    # "structure" value when it produced real structure (candidate / review /
    # receipt), or a falsy value when the round was a no-op (churn). If omitted,
    # the action is assumed to produce structure.
    perform: Optional[Callable[["Action"], object]] = None


class Outcome(str, Enum):
    ADVANCED = "advanced"                 # reversible work produced structure
    NO_STRUCTURE = "no_structure"         # reversible work ran but churned
    GATED = "gated"                       # hit an irreversible gate -> queued
    QUIESCENT = "quiescent"               # nothing left to do
    STEP_BUDGET = "step_budget_exhausted"
    COLLAPSED_CHURN = "collapsed_anti_monopoly"  # K churn rounds -> self-collapse


@dataclass
class LedgerEvent:
    type: str
    action_id: str = ""
    detail: str = ""


@dataclass
class EpisodeReceipt:
    """Emitted at the end of every episode (DESIGN §3: 无界运行 = 设计违例)."""

    schema_version: str = SCHEMA_VERSION
    stop_reason: str = ""
    steps_taken: int = 0
    structure_events: int = 0
    # Irreversible gates hit and queued for the owner this episode.
    queued_for_owner: list = field(default_factory=list)
    # Every autonomous action, in order — the observability trail (DESIGN §5).
    ledger: list = field(default_factory=list)
    crossed_irreversible_gate: bool = False  # MUST stay False. True = kernel breach.

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "stop_reason": self.stop_reason,
                "steps_taken": self.steps_taken,
                "structure_events": self.structure_events,
                "queued_for_owner": self.queued_for_owner,
                "ledger": [vars(e) for e in self.ledger],
                "crossed_irreversible_gate": self.crossed_irreversible_gate,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def receipt_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The episode driver — one bounded wakeup.
# ---------------------------------------------------------------------------


def run_episode(
    work_queue,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    churn_k: int = DEFAULT_CHURN_K,
) -> EpisodeReceipt:
    """Run one bounded autonomous episode over ``work_queue`` (a list of Action).

    The queue is consumed in order. The episode stops — always with a receipt —
    on the FIRST of:
      * an irreversible gate           -> queue for owner, stop (Outcome.GATED)
      * churn_k consecutive no-structure rounds -> self-collapse (anti-monopoly)
      * an empty queue                 -> quiescence
      * max_steps exhausted            -> bounded stop

    It never crosses an irreversible gate and never runs unbounded.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be >= 1")
    if churn_k < 1:
        raise ValueError("churn_k must be >= 1")

    receipt = EpisodeReceipt()
    receipt.ledger.append(LedgerEvent(type="episode_started"))

    consecutive_churn = 0
    queue = list(work_queue)
    index = 0

    while True:
        # Bounded stop (DESIGN §3): finite steps, no exceptions.
        if receipt.steps_taken >= max_steps:
            receipt.stop_reason = Outcome.STEP_BUDGET.value
            break

        # Quiescence: nothing left to advance (DESIGN §6-E honest halt).
        if index >= len(queue):
            receipt.stop_reason = Outcome.QUIESCENT.value
            break

        action = queue[index]
        index += 1
        receipt.steps_taken += 1

        gate = classify(action)
        if gate is not None:
            # HARD STOP at the irreversible boundary (DESIGN §2, harness B).
            # We do NOT perform the action and we do NOT continue past it.
            receipt.queued_for_owner.append(
                {"action_id": action.id, "kind": action.kind, "gate": gate.value,
                 "summary": action.summary}
            )
            receipt.ledger.append(
                LedgerEvent(type="gate_hit", action_id=action.id, detail=gate.value)
            )
            receipt.ledger.append(
                LedgerEvent(type="queued_for_owner", action_id=action.id, detail=gate.value)
            )
            receipt.stop_reason = Outcome.GATED.value
            break

        # Reversible action: perform it and measure structure gain.
        produced = _perform(action)
        if produced:
            consecutive_churn = 0
            receipt.structure_events += 1
            receipt.ledger.append(
                LedgerEvent(type="structure_committed", action_id=action.id,
                            detail=action.kind)
            )
        else:
            consecutive_churn += 1
            receipt.ledger.append(
                LedgerEvent(type="no_structure", action_id=action.id, detail=action.kind)
            )
            # Anti-monopoly self-collapse (DESIGN §4, harness E): a loop that
            # keeps turning without producing structure IS the monopoly the law
            # exists to break. It must collapse, not churn.
            if consecutive_churn >= churn_k:
                receipt.ledger.append(
                    LedgerEvent(type="anti_monopoly_collapse",
                                detail=f"{consecutive_churn} churn rounds >= K={churn_k}")
                )
                receipt.stop_reason = Outcome.COLLAPSED_CHURN.value
                break

    receipt.ledger.append(LedgerEvent(type="episode_receipted", detail=receipt.stop_reason))
    return receipt


def _perform(action: Action) -> object:
    """Run a reversible action's worker. Any exception is contained (the episode
    stays bounded and observable) and counts as a no-structure round."""
    if action.perform is None:
        return True  # assume structure when no worker is supplied
    try:
        return action.perform(action)
    except Exception as exc:  # noqa: BLE001 - contained on purpose
        return False
