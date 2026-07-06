# WeiLan transition materialization 0.7c

Memory 0.7c converts one current, admitted 0.7a Transition Proposal（转移提案） into one deterministic event plan and may materialize it through exactly one 0.7b transaction. It automates event construction for collapse, regroup, completion, blocking, testing, and successor Frame creation. It does not schedule itself or run a background loop.

## Authority boundary

1. User instructions and the Control Ledger remain the only activation authority.
2. The 0.7a contract and proposal determine whether one transition is structurally admissible.
3. The 0.7c plan is read-only and cannot authorize or commit.
4. `metabolic-materialize` is a direct caller invocation under an explicitly active scope. It may commit at most one 0.7b transaction.
5. The command cannot create Control events, semantic memory, top-level goals, new scopes, expanded budgets, timers, workers, schedulers, or subsequent invocations.

“Automatic” in 0.7c means deterministic construction and atomic materialization of one already admitted transition. It does not mean autonomous selection of goals, candidates, evidence, permissions, or repeated execution.

## Transition Plan（转移计划）

`metabolic-plan-transition` derives a `weilan_transition_plan_v0.7c` document without writing method state.

The plan binds:

- the current 0.7a contract and proposal hashes;
- a caller-stable idempotency key and request hash;
- deterministic transition, event, lineage, and successor Frame identities;
- current control, branch, governance, evidence, pressure, and audit provenance;
- one normalized 0.7b participant intent bundle;
- a maximum of one transaction and at most one successor Frame.

Event timestamps are derived from current causal heads rather than a heartbeat. Repeating the same request against unchanged heads produces the same plan.

## Disposition mapping

```text
continue  -> one Frame + lineage continuation
test      -> one discriminating-test Frame + lineage continuation
fork      -> one Frame on an explicitly named new branch
join      -> one Frame joining admitted closed branch heads
collapse  -> one Governance target_collapsed event with complete Trace; no Frame
regroup   -> one Frame selecting an admitted active candidate and recording changed assumption
complete  -> one Governance target_completed event; no Frame
block     -> one Governance target_blocked event; no Frame
```

Successor Frames contain `frame_opened`, `candidate_admitted`, and `holder_selected`. Regroup also contains `candidates_regrouped`. The holder rationale, next expected evidence, and death line are explicit bounded inputs.

Collapse uses only active adverse pressure: contradiction, risk, cost, or staleness. Support pressure cannot authorize collapse. The collapse transaction requires a declared death-line match and the complete Trace contract:

```text
once_reasonable
invalidating_evidence
reusable_results
forbidden_assumption
reentry_condition
```

Collapse never creates a successor. Regroup is a later, separate invocation and requires:

- the original target is `COLLAPSED`;
- a distinct candidate is currently `ACTIVE`;
- one current closed audited branch;
- a changed assumption that does not repeat the collapse trace's forbidden assumption.

If no candidate passes admission, no transaction is created. The caller may explicitly block a still-valid governing target or yield; the planner does not invent a route.

## Budget and scope

A successor inherits the selected GovernanceTarget budget. A caller-supplied budget override is accepted only when it is exactly the declared target budget; an unverifiable expansion is rejected.

Fork may create one branch inside the current scope. No disposition can create another scope or a top-level GovernanceTarget.

## Explicit materialization

Plan only:

```powershell
python scripts/weilan_trace.py metabolic-plan-transition `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --idempotency-key "<stable-key>" `
  --trigger-kind frame_closed `
  --trigger-ref "frame:<id>" `
  --disposition continue `
  --target-ref "goal:<id>" `
  --branch main `
  --problem "<next bounded problem>" `
  --success "<success criteria>" `
  --why-reasonable "<evidence-based rationale>" `
  --next-expected-evidence "<expected evidence>"
```

Plan and commit one transaction:

```powershell
python scripts/weilan_trace.py metabolic-materialize `
  <the same bounded transition arguments>
```

`metabolic-materialize` performs:

```text
current contract
  -> proposal admission
  -> deterministic plan
  -> participant replay validation
  -> 0.7b PREPARE
  -> stale-head comparison
  -> 0.7b COMMIT + RECEIPT
  -> derived-cache refresh
  -> stop
```

It never calls itself. It never consumes the resulting Frame as a new trigger.

## Retry and recovery

The idempotency request hash covers both proposal fields and materialization fields. Reusing a key with changed disposition, target, candidate, branch, trace, evidence, or Frame envelope fails with `idempotency_conflict`.

If the original call crashed during PREPARE, repeating the same explicit command may finish staging and commit the originally requested transaction. If it already committed, the original receipt and successor Frame identity are returned without another transaction. An aborted transaction remains aborted.

## Completion boundary

0.7c is complete when:

- plan generation is deterministic and read-only;
- every disposition maps to a replay-valid participant bundle;
- one explicit invocation commits at most one transaction;
- retries cannot duplicate transactions, governance transitions, Frames, or receipts;
- collapse cannot force regroup or successor creation;
- regroup cannot invent a candidate or inherit the forbidden assumption;
- paused scopes, stale contracts, invalid branch relations, and budget expansion are rejected;
- complete and block preserve their distinct terminal/non-terminal meanings;
- no scheduler, timer, heartbeat, worker, bounded runner, or background autonomous loop exists.

Memory 0.7d remains responsible for any future bounded Runner（运行器）, concurrency-conflict policy, repeated event consumption, and loop-level fault injection.

Memory 0.7d implements that foreground-only bounded layer in [runner-system.md](runner-system.md). It consumes finite caller-supplied manifests and does not change 0.7c plan or authority rules.
