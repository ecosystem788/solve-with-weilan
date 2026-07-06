# WeiLan causal frame metabolism 0.7a

Memory 0.7a adds a deterministic read-only Metabolic Contract（代谢契约） and Transition Proposal（转移提案） admission layer. It does not commit transactions, mutate any ledger, create a successor frame, or run a background agent loop.

## Authority boundary

1. User instructions and the Control Ledger remain the only activation authority.
2. Causal lineage defines valid branch heads and closed parents.
3. Persistence Audit determines whether a closed frame is eligible for succession.
4. Evidence Lifecycle and Governance determine active pressure and target eligibility.
5. Self Projection is an input head, never an authority source.
6. Metabolic Contract and Transition Proposal are derived read-only views. They always report `can_authorize_actions: false` and `can_commit: false`.

An admissible proposal means only that the proposed transition is structurally consistent with the current heads. It does not authorize or execute the transition.

## Metabolic Contract（代谢契约）

```powershell
python scripts/weilan_trace.py metabolic-contract `
  --workspace "<cwd>" `
  --scope "memory-system"
```

The contract binds:

- current control, lineage, governance, Self Projection, evidence-dependency, and persistence-audit heads;
- active and collapsed GovernanceTarget envelopes;
- active branch heads and their closed/audited state;
- currently eligible event triggers and dispositions;
- a fixed one-step execution budget;
- explicit prohibitions on writes, background loops, scope expansion, top-level goal creation, and budget expansion.

Contract states are:

```text
READY                 one read-only transition may be proposed
QUIESCENT             no active target or eligible closed head; yield only
CONTROL_BLOCKED       scope is not explicitly active; yield only
AWAITING_FRAME_CLOSE  an active branch head is still open; yield only
AUDIT_BLOCKED         a closed head lacks required Persistence Audit; yield only
INVALID               an input ledger or derived state is invalid
```

The contract has no wall-clock validity period. Its `contract_hash` is valid only while every input head remains unchanged.

## Event triggers

0.7a recognizes only source-resolved triggers:

```text
frame_closed
evidence_changed
pressure_changed
control_changed
discriminating_test_completed
```

A trigger must be present in the current contract's `eligible_triggers`. Control pause or close may produce a new contract head, but cannot authorize continuation. Evidence and pressure references must already resolve through the current Evidence and Governance layers.

## Transition Proposal（转移提案）

```powershell
python scripts/weilan_trace.py metabolic-propose `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --expected-contract-hash "<hash>" `
  --trigger-kind frame_closed `
  --trigger-ref "frame:<id>" `
  --disposition continue `
  --target-ref "goal:<id>" `
  --branch main
```

Supported dispositions are:

```text
continue  healthy ACTIVE target on one closed audited branch
test      WARNED or PROBATION target schedules a discriminating test proposal
fork      one closed audited branch plus a distinct active candidate
join      two or more closed audited active branch heads
collapse  declared death line plus WARNED/PROBATION or critical pressure
regroup   collapsed target, distinct active candidate, and changed assumption
yield     no successor and no write
complete  propose terminal completion of a non-terminal target
block     propose non-terminal blocking of a target
```

Collapse never implies regroup. A collapse proposal has no successor preview. Regroup must separately name an active candidate and an assumption changed from the collapsed trace; inheriting the forbidden assumption is rejected.

Model-generated proposals may be non-deterministic. Admission, proposal hashing, head comparison, and replay are deterministic. Repeating the same proposal against the same contract yields the same result.

## Read-only guarantee

0.7a writes no Frame, Control, Evidence, Audit, Governance, semantic-memory, projection, or metabolism ledger records. `writes_planned` is always empty. Non-yield dispositions identify `Memory 0.7b or later` as the required commit phase.

The first writable step belongs to 0.7b and requires a separate append-only PREPARE/COMMIT/ABORT/RECEIPT journal, idempotency, recovery, and pending-event invisibility. None of those write paths exist in 0.7a.

Memory 0.7b implements that explicit write boundary in [transaction-system.md](transaction-system.md). A 0.7a proposal remains read-only; only a separate caller-invoked 0.7b transaction may stage and commit its validated event bundle.

Memory 0.7c may derive and explicitly materialize one event plan for an admitted proposal as documented in [transition-planner-system.md](transition-planner-system.md). It does not change 0.7a admission semantics or turn the proposal into authority.

## Completion boundary

0.7a is complete when:

- contracts and proposals are deterministic;
- a changed input head rejects an old contract;
- paused scopes, open heads, missing audits, terminal targets, and no-gain states become non-advancing contracts;
- critical collapse can be admitted without forcing regroup;
- outputs cannot authorize or commit;
- contract and proposal commands make no method-state writes;
- no wall-clock heartbeat or background runner exists.
