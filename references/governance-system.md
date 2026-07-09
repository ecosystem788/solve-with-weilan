# WeiLan governance system 0.6

Memory 0.6 governs how valid feedback changes the future eligibility of structures. It does not add another memory store and does not implement the automatic causal metabolism reserved for 0.7.

## Authority boundary

1. User instructions and the Control Ledger decide whether a scope may act.
2. Evidence Lifecycle decides whether a source remains valid.
3. Governance Ledger decides pressure and GovernanceTarget state inside an already active scope.
4. Frame/Trace events provide episode evidence and collapse traces.
5. Self Projection is a derived read-only view with no write or resume authority.

Governance writes require an explicitly active scope. Self Projection always reports `can_authorize_actions: false`; when control is paused it also reports `continuation_allowed: false`.

## Append-only storage

```text
memory/governance/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
```

Every event uses `weilan_governance_event_v0.6`, a UUID event id, and a monotonic scoped sequence assigned while holding the governance lock. State is rebuilt from the ledger; no mutable target or pressure record is authoritative.

Supported events:

```text
target_registered
pressure_recorded
pressure_invalidated
pressure_propagated
target_warned
probation_started
target_stabilized
target_paused
target_resumed
target_collapsed
target_completed
target_blocked
target_superseded
target_reentered
```

## GovernanceTarget（治理目标）

Target refs are typed identities:

```text
assumption:<id>
holder:<id>
goal:<id>
route:<id>
frame:<id>
```

Registration declares kind, scale, optional parent, budget, death-line predicates, re-entry condition, and source snapshots. A child may not be broader than its parent.

Lifecycle:

```text
ACTIVE → WARNED → PROBATION → ACTIVE
ACTIVE/WARNED/PROBATION → PAUSED → ACTIVE
ACTIVE/WARNED/PROBATION/PAUSED → BLOCKED → ACTIVE
ACTIVE/WARNED/PROBATION/PAUSED/BLOCKED → COLLAPSED
ACTIVE/WARNED/PROBATION/PAUSED/BLOCKED → COMPLETED / SUPERSEDED
COLLAPSED → ACTIVE through target_reentered
```

Warning, probation, and collapse require active pressure. Collapse must match a declared death line and emit a complete trace: once reasonable, invalidating evidence, reusable results, forbidden assumption, and re-entry condition. Re-entry requires evidence not used by the collapse. Collapsing a child never changes its parent implicitly.

Replay validates pressure eligibility at the transition point. A required pressure must already exist, target the same object, remain non-duplicate and non-invalidated in Governance sequence, and depend only on pressures that were themselves eligible at that point. Collapse accepts only adverse kinds: `contradiction`, `risk`, `cost`, or `staleness`; `support` can never authorize collapse. A later Evidence Lifecycle change still removes the pressure from the current projection, but does not retroactively corrupt a transition that was structurally valid before that external lifecycle change.

`PAUSED` preserves target validity while suspending its execution. `BLOCKED` preserves target validity while authority, resources, or evidence are unavailable; both recover through `target_resumed`. They are not terminal states. A target cannot become terminal while any child remains non-terminal, and a new child cannot be registered under a terminal parent.

## Feedback Pressure（反馈压力）

Pressure kinds are `support`, `contradiction`, `risk`, `cost`, and `staleness`. Strength is the finite ordinal band `weak`, `medium`, `strong`, or `critical`; there is no scalar reward total.

Each pressure names one registered target, causal frame, evidence refs, source snapshots, required future change, kind, strength, and scale. Pressure changes governance eligibility only. It cannot modify evidence, facts, target state, or control.

The reducer treats repeated events with the same target, kind, evidence set, and source-pressure set as duplicate contributions. The history remains, but only the first contributes active pressure. Support and contradiction remain separate vector components and never cancel into a net score.

Pressure is active only while:

- its target remains non-terminal;
- it is not explicitly invalidated;
- its evidence snapshots remain current and non-terminal;
- it is not a duplicate contribution;
- every source pressure of a propagated pressure remains active.

Withdrawing, expiring, or superseding evidence therefore removes every dependent pressure and propagated descendant during replay without deleting governance history.

## Multi-scale State（多尺度状态）

Scales are:

```text
local       assumption, candidate, holder
task        goal, route, frame
continuity  branch and scope continuity
workspace   workspace-wide governance
```

Pressure never crosses scale implicitly. `pressure_propagated` must name active source pressure ids and a registered broader target. It preserves kind, takes the strongest ordinal band, deduplicates evidence, and records the explicit propagation reason.

Continuity and workspace pressure require promoted evidence or a promoted Persistence Audit. Candidate evidence may affect local and task governance but cannot silently reshape continuity or workspace state.

## Deterministic reducer（确定性归约器）

The reducer is a pure replay over sequence-ordered events plus current evidence snapshots. It uses no wall-clock time, background heartbeat, hidden model score, or mutable projection state.

It derives:

- target lifecycle and transition history;
- active/inactive pressures and reasons;
- typed pressure vectors by target;
- strongest band without cross-kind cancellation;
- target and pressure membership by scale;
- collapse traces and replacement relations;
- a governance head event and sequence.

The same ledger and dependency heads must produce byte-equivalent logical state. Replay rejects duplicate or non-contiguous sequences, duplicate event ids, invalid lifecycle edges, mismatched pressure targets, incomplete collapse traces, and active children under terminal parents. A supported event name never bypasses semantic validation.

## Self Projection（自我投影）

`self-project` combines current control, governance head, lineage branch heads, active targets, holders, unresolved pressure vectors, warned/probation targets, collapse traces, and scale summaries.

It does not persist a Self object. Its deterministic hash changes only when an input head or derived state changes. It answers what currently governs the system and why; it cannot authorize action, restore a paused scope, transition a target, propagate pressure, or write semantic memory.

## CLI

```powershell
python scripts/weilan_trace.py governance-target-register ...
python scripts/weilan_trace.py governance-pressure-record ...
python scripts/weilan_trace.py governance-pressure-propagate ...
python scripts/weilan_trace.py governance-pressure-invalidate ...
python scripts/weilan_trace.py governance-target-transition ...
python scripts/weilan_trace.py governance-show --workspace "<cwd>" --scope "..."
python scripts/weilan_trace.py self-project --workspace "<cwd>" --scope "..."
```

## Completion boundary

0.6 is complete when replay is deterministic, withdrawn evidence removes pressure, duplicates do not double count, local collapse does not kill parents, paused and blocked targets remain distinct and recoverable, terminal parents cannot retain active children, propagation is explicit, projection cannot acquire authority, collapse/re-entry are explainable, cross-workspace data is isolated, corrupt events are detected, and the system uses no wall-clock or background heartbeat.

0.6 does not automatically evaluate every response, schedule the next frame, or run feedback-pressure-collapse-regroup as a continuous loop. Memory 0.7a adds only the read-only contract and proposal layer documented in [metabolism-system.md](metabolism-system.md); transaction commits and any bounded runner remain later phases.

## Pressure Derivation（压力派生, read-only）

`governance-pressure-derive --workspace <cwd> --scope <scope>` derives pressure
suggestions from the scope's own evidence and records nothing:

- two or more failed/blocked episodes whose tokens overlap an ACTIVE/WARNED/PROBATION
  target suggest a `contradiction` pressure (three or more upgrade the suggested
  strength to strong), citing the failing frames as evidence;
- a target whose registered source snapshots no longer match their sources suggests a
  `staleness` pressure;
- failed episodes matching no registered target are reported as informational
  `unregistered_risk`, inviting an explicit `governance-target-register`.

Recordable suggestions carry a ready-to-run `governance-pressure-record` command line.
When the scope lacks a current causal frame or all stale source refs are no longer valid
governance evidence, the suggestion is still shown with `recordable: false` plus the
blocking reason instead of a fake command template. Derivation is a read-only projection
of evidence; recording pressure remains an explicit act, and nothing transitions
automatically. Exit code 2 signals suggestions exist.

## Re-entry Trace Advisories（重入痕迹提醒, read-only）

`trace-check --scope <scope> --text "<candidate description>"` matches text against every
collapse trace in the scope — frame `trace_emitted` events and governance
`collapse_trace` records alike — by token overlap with each `forbidden_assumption`.
`candidate_admitted`, `holder_selected`, `route_reentered` events and lineaged `open`
surface the same advisories automatically in their command output. Advisories never
block: the constitution's re-entry rule (new evidence satisfying the declared
`reentry_condition`, not a rename) stays a judgment the caller must make — the mechanism
only guarantees the trace is seen.
