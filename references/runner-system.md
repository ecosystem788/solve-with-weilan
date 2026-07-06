# WeiLan bounded runner 0.7d

Memory 0.7d adds an explicitly invoked, foreground-only Bounded Runner（有界运行器）. One command consumes a caller-supplied finite event manifest, executes zero or more admitted 0.7c transitions within hard budgets, writes an append-only Run Journal（运行账本）, emits one deterministic receipt, and exits.

It is not a background autonomous Agent. It has no daemon, worker, timer, polling loop, heartbeat, wakeup service, or self-scheduling continuation.

## Authority boundary

1. User instructions and the Control Ledger remain the only activation authority.
2. `metabolic-run` requires an explicitly active scope and a direct foreground invocation.
3. The caller supplies the complete ordered event manifest. The Runner cannot invent triggers, dispositions, candidates, evidence, goals, permissions, or successor criteria.
4. Every event still passes 0.7a admission, 0.7c plan validation, and one 0.7b transaction boundary.
5. The Runner cannot expand scope or budgets, create top-level goals, wait for future events, or invoke itself.

The manifest authorizes only the listed bounded attempts. It does not grant authority beyond the current Control and Governance heads.

## Finite manifest

```json
{
  "schema_version": "weilan_runner_manifest_v0.7d",
  "max_steps": 2,
  "events": [
    {
      "event_id": "collapse-current-route",
      "proposal": {
        "trigger_kind": "pressure_changed",
        "trigger_ref": "pressure:<id>",
        "disposition": "collapse",
        "target_ref": "route:<id>",
        "death_line_matches": ["<declared death line>"]
      },
      "materialization": {
        "once_reasonable": "...",
        "invalidating_evidence": "...",
        "reusable_results": "...",
        "forbidden_assumption": "..."
      }
    }
  ]
}
```

Hard bounds:

```text
1 <= max_steps <= 16
0 <= event_count <= 32
```

Event order is preserved. Event ids must be unique. The Runner derives a separate 0.7c idempotency key from the run id and event id, so replay cannot duplicate an already committed step.

## Run Journal（运行账本）

The scoped append-only journal is stored at:

```text
memory/runs/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
```

Events are:

```text
run_started
event_claimed
step_committed
run_stopped
run_receipted
```

`run_started` stores the normalized manifest, its hash, the initial contract hash, and the initial contract status. `event_claimed` binds one supplied event to the exact pre-step contract hash. `step_committed` stores the 0.7b transaction and receipt hashes plus the post-step contract head. `run_stopped` records one deterministic reason. `run_receipted` summarizes the replayed run state.

The journal is operational evidence, never activation authority.

## Foreground cycle

```text
explicit command
  -> acquire non-blocking scope claim
  -> load or resume one run
  -> inspect current contract
  -> claim next supplied event
  -> execute one idempotent 0.7c materialization
  -> record step receipt
  -> evaluate stop gates
  -> repeat only while budget and supplied events remain
  -> stop + receipt
  -> release claim and exit process
```

The implementation uses a finite counted loop. It never waits for a Frame to close or for new evidence to arrive. A resulting open Frame causes an immediate stop.

## Stop reasons

```text
QUIESCENT                no advancing target or eligible transition
AWAITING_FRAME_CLOSE     a successor Frame is open
AUDIT_BLOCKED            a closed head lacks Persistence Audit
CONTROL_BLOCKED          scope is paused, blocked, closed, or confirmation-gated
INVALID                  an input ledger or derived contract is invalid
STEP_BUDGET_EXHAUSTED    max_steps reached while supplied work remains
EVENTS_EXHAUSTED         no supplied event remains while the contract is still READY
CONFLICT                 a contract, idempotency, branch, or scope claim changed
INVALID_EVENT            the next supplied event failed admission or materialization
ABORTED                  reserved terminal run outcome for an explicitly aborted workflow
```

These are stop conditions, not prompts to schedule another process. Resumption requires another explicit caller invocation with the same run key and unchanged manifest.

## Concurrency

One scope has one non-blocking foreground Runner claim. A concurrent Runner receives `runner_scope_claim_conflict`; it does not wait, steal the claim, or apply last-writer-wins.

The claim does not replace 0.7b and lineage locks. Every step also binds the claimed contract hash. External changes before a step, between steps, or during transaction admission become `CONFLICT` rather than silently rebasing the run.

Different scopes remain independent.

## Crash recovery and idempotency

The run id is derived from workspace, scope, and caller idempotency key. Reusing that key with a different manifest fails with `runner_idempotency_conflict`.

Recovery rules:

- after `run_started`: require the original initial contract head before the first claim;
- after `event_claimed`: retry the same supplied event and exact pre-step contract hash;
- after the 0.7c transaction committed but before `step_committed`: the derived step key returns the original transaction receipt, then the step is journaled once;
- after `step_committed`: continue only from the recorded post-step contract head;
- after `run_stopped` but before `run_receipted`: regenerate the deterministic receipt;
- after receipt: return the existing receipt without executing.

No recovery path chooses a new event, alters the manifest, or increases a budget.

An exception from materialization is not proof that the transaction failed. The Runner reconciles the derived step key against the durable transaction journal before choosing a stop reason. `COMMITTED` is reconstructed into `step_committed`; `PREPARING` or `PREPARED` leaves the claimed event resumable for a later explicit invocation unless the exception proves a non-committed contract conflict; `NOT_STARTED` or `ABORTED` may be classified as a rejected event. If no durable reconciliation result is available, the exception propagates and the claim remains replayable rather than being terminalized as `INVALID_EVENT`.

## CLI

```powershell
python scripts/weilan_trace.py metabolic-run `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --idempotency-key "<stable-run-key>" `
  --manifest-file "<finite-manifest.json>"

python scripts/weilan_trace.py metabolic-run-show `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --run-id "<optional-run-id>"
```

## Completion boundary

0.7d is complete when:

- quiescent input stops with zero steps;
- every run is finite by both event and step count;
- each step is one admitted, idempotent 0.7c transaction;
- open Frames, audit gaps, control changes, invalid events, conflicts, and exhausted budgets stop immediately;
- a scope permits only one foreground Runner claim;
- every crash boundary recovers without duplicate transactions, Frames, governance events, steps, or receipts;
- a post-COMMIT materialization exception cannot leave a terminal zero-step run;
- a changed manifest cannot reuse a run idempotency key;
- no unsupplied event can execute;
- the command releases its claim and exits after the receipt;
- no daemon, background process, timer, polling heartbeat, wakeup mechanism, or unbounded loop exists.

With this boundary, Memory 0.7 provides a complete event-driven causal metabolism path while keeping goal and permission authority external to the metabolism loop.
