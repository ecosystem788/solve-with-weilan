# WeiLan metabolic transactions 0.7b

Memory 0.7b adds an explicitly invoked Metabolic Transaction（代谢事务） layer for one admitted 0.7a proposal. It provides cross-ledger logical atomicity, crash recovery, idempotency, and a durable receipt. It does not select a proposal, create authority, or run a background agent loop.

## Authority boundary

1. The current user instruction and Control Ledger remain the only activation authority.
2. A current admissible 0.7a Transition Proposal（转移提案） defines the permitted structural transition but does not authorize execution by itself.
3. `metabolic-prepare`, `metabolic-commit`, `metabolic-abort`, and `metabolic-recover` require an explicitly active scope and a direct caller invocation.
4. A transaction cannot write Control events, expand scope or budget, create an undeclared top-level goal, or change the proposal disposition.
5. Recovery may finish staging or regenerate a missing receipt. It never chooses COMMIT（提交） instead of ABORT（中止）.

## Storage and participants

The coordinator journal is append-only and scoped:

```text
memory/transactions/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
```

Participant ledgers remain their normal append-only files. 0.7b supports only `frame`, `lineage`, `governance`, `evidence`, `evidence_lifecycle`, and `persistence_audit`. Control, semantic memory, projections, indexes, and arbitrary file paths are not transaction participants.

## Protocol

```text
PREPARE_STARTED
  -> append pending envelopes to every participant ledger
  -> PREPARED
  -> COMMITTED or ABORTED
  -> RECEIPTED
```

`transaction_prepare_started` stores the complete normalized intent bundle before participant staging begins. This makes a mid-staging crash replayable.

Every participant receives a `weilan_pending_event_v0.7b` envelope containing the transaction id, intent id, bundle hash, participant, payload hash, and original event payload. Reducers ignore the envelope until the coordinator journal contains a valid `transaction_committed` event for the same complete bundle.

COMMIT is appended only after every pending envelope is present and hash-valid. Therefore:

- a crash before COMMIT leaves all intents invisible;
- a crash after COMMIT leaves the complete bundle visible;
- a missing receipt does not undo a committed bundle;
- ABORT leaves staged envelopes permanently invisible without deleting history.

The contract check and coordinator COMMIT share one Contract Commit Fence（契约提交栅栏）. Lock order is workspace fence, scope fence, then transaction lock. Control, lineaged Frame, Governance, Evidence Lifecycle, Persistence Audit, Evidence Promotion, and semantic consolidation writers use the same workspace/scope fence. PREPARE and recovery staging hold that fence in the same order before the transaction lock, because staging appends pending envelopes into the same participant ledgers that direct writers append to. The current contract loader runs only after those fences are held, so an internal contract-head mutation is serialized entirely before the check or after COMMIT; a caller-supplied stale hash is never trusted as the current state.

## Durability degradation boundaries

An interrupted append can leave one torn unterminated final line in a ledger. Readers skip exactly that torn tail, and the next append seals it: the fragment's byte identity (offset, length, SHA-256) is preserved in an append-only `<ledger>.torn` quarantine sidecar, the fragment becomes an isolated non-record line, and the new record is appended after it. Ledgers are never truncated. Ledger lines are decoded as strict UTF-8; a newline-terminated line with invalid UTF-8 or invalid JSON remains a hard validation error unless its exact byte range is quarantined in the sidecar.

Journal identity replay compares workspace and scope case-insensitively; raw casing differences of the same scope key are not an identity change.

When the coordinator journal itself fails replay, readers do not fail every ledger and frame read in the workspace. Pending envelopes stay invisible — the same conservative treatment as an uncommitted transaction — and one process-level warning names the broken journal until `metabolic-recover` repairs it. An envelope that fails its own integrity validation is still a hard read error for the ledger that contains it.

One top-level CLI command pins a transaction visibility snapshot. A concurrent COMMIT cannot cause that command to observe part of the bundle before the boundary and another part after it.

## Idempotency

The transaction id is derived from the workspace key, scope key, and caller idempotency key. Repeating PREPARE with the same key, proposal, contract, and normalized bundle returns the existing transaction without appending duplicates. Reusing the key with different content fails with `idempotency_conflict`.

Repeating COMMIT after success returns the original receipt even though the transaction's own committed events changed the current contract heads. COMMIT of a still-uncommitted transaction always rejects a stale contract.

## Proposal and participant validation

PREPARE regenerates the supplied 0.7a proposal against current heads and requires the same deterministic proposal hash.

Successor dispositions (`continue`, `test`, `fork`, `join`, and `regroup`) require exactly one matching `frame_opened` event and one matching lineage event whose relation and parents equal the admitted successor preview.

`collapse`, `complete`, and `block` require exactly one matching Governance target transition and cannot create a successor frame. Collapse still does not imply regroup.

Before staging, the transaction layer simulates participant replay:

- Frame events must satisfy Frame/Trace validation and required Persistence Audit（持久化审计） coverage.
- Lineage must remain a valid branch DAG and match the Frame causal envelope.
- Governance must pass deterministic sequence and lifecycle replay.
- Evidence identities and lifecycle transitions must remain scoped and unique.
- Persistence Audit identities, triggers, decisions, and Frame references must be valid.

## CLI

The proposal and plan are explicit JSON inputs. A plan contains an `intents` array; each item contains `participant` and `record`, with optional `intent_id`. Target paths are derived by the implementation and cannot be supplied by the caller.

```powershell
python scripts/weilan_trace.py metabolic-prepare `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --idempotency-key "<caller-stable-key>" `
  --proposal-file "<proposal.json>" `
  --plan-file "<transaction-plan.json>"

python scripts/weilan_trace.py metabolic-commit `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --transaction-id "<transaction-id>" `
  --expected-contract-hash "<contract-hash>"

python scripts/weilan_trace.py metabolic-abort `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --transaction-id "<transaction-id>" `
  --reason "<reason>"

python scripts/weilan_trace.py metabolic-recover `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --transaction-id "<transaction-id>"

python scripts/weilan_trace.py metabolic-transaction-show `
  --workspace "<cwd>" `
  --scope "memory-system"
```

## Receipt

The deterministic receipt body records the transaction outcome, contract hash, proposal hash, bundle hash, participant count, intent count, and payload hashes. The receipt proves the recorded transaction decision; it is not activation authority and does not prove the domain claims inside the payloads.

Replay requires the stored receipt body to equal the body deterministically derived from the reduced transaction, in addition to validating its hash. A self-consistent hash over fabricated receipt fields is invalid.

## Completion boundary

0.7b is complete when:

- PREPARE stages a complete replayable bundle while participant reducers see no pending events;
- COMMIT produces complete logical visibility from one coordinator decision;
- ABORT never exposes participant payloads;
- one read command cannot straddle a COMMIT boundary;
- repeated PREPARE, COMMIT, ABORT, and recovery do not duplicate logical events or receipts;
- a pre-commit contract-head change rejects COMMIT;
- contract validation and COMMIT are one fenced internal-state boundary;
- a receipt cannot replace derived transaction fields with self-hashed fabricated values;
- every crash boundary can recover without deleting or rewriting history;
- recovery never decides whether a prepared transaction should commit;
- no timer, heartbeat, worker, or background autonomous loop exists.

0.7b does not generate transition plans, automatically collapse targets, regroup candidates, or create the next Frame. Those behaviors remain outside this phase.

Memory 0.7c supplies the deterministic one-step plan and explicit materialization layer documented in [transition-planner-system.md](transition-planner-system.md). It reuses this transaction protocol and does not weaken its visibility, recovery, or idempotency rules.

Memory 0.7d may execute several supplied 0.7c steps inside one finite foreground run as documented in [runner-system.md](runner-system.md). Every step remains a separate 0.7b transaction.
