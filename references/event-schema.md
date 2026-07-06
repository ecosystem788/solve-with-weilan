# Frame and Trace Event Schema 0.1

Persist L2 and L3 frames as append-only UTF-8 JSONL. Derive current state from events; do not maintain an authoritative `current-self.json`.

Memory control events and replaceable scoped projections use the separate schema in [memory-system.md](memory-system.md). Do not encode pause or resume authority only inside a Frame/Trace event.

## Common fields

Every event contains:

```json
{
  "schema_version": "weilan_method_event_v0.1",
  "event_id": "uuid",
  "frame_id": "wf-...",
  "timestamp_utc": "ISO-8601",
  "event_type": "candidate_admitted",
  "level": "L2",
  "workspace": "D:\\project",
  "data": {}
}
```

## Memory 0.4 causal envelope

A lineaged `frame_opened` event adds a `causal` object inside `data`:

```json
{
  "schema_version": "weilan_frame_lineage_v0.4",
  "lineage_event_id": "uuid",
  "scope": "memory-system",
  "branch_id": "main",
  "relation": "continue",
  "parent_frame_ids": ["wf-parent"],
  "joined_branch_ids": []
}
```

The same transition is appended to the scoped lineage ledger. The envelope and ledger must agree on event id, frame id, branch, relation, parents, and joined branches. `lineage-show` validates this agreement.

Parent edges always point to already existing closed frames, so accepted transitions cannot create a causal cycle. `continue` and `join` compare their declared parents with current active branch heads while holding the scoped lineage lock. Lineage data records causal evidence and branch governance; it does not encode pause or resume authority.

Memory 0.5.1 lineaged frames also set `persistence_audit_required`, record scoped control heads/count at open, and enforce append-only Persistence Audit before close. Every such frame requires `round_end`; collapse/regroup/re-entry additionally requires `route_change`; a version/scope-redirection control between frame open and frame close requires `version_switch`. Controls written after closure never retroactively alter the closed frame's audit requirements. Missing audit triggers make `close` fail without modifying the frame.

Memory 0.6 governance events are intentionally not Frame events. They live in the separate append-only Governance Ledger described by `governance-system.md`; Frames provide causal provenance, while the governance reducer derives pressure, target lifecycle, multi-scale state, and the read-only Self Projection without changing Frame or control authority.

Memory 0.7a Metabolic Contracts and Transition Proposals are not events at all. They are deterministic read-only views described by `metabolism-system.md`; they cannot be appended to a Frame, used as evidence, or treated as authority or transaction commits.

Memory 0.7b transactions use a separate scoped coordinator journal described by `transaction-system.md`. Participant ledgers may contain `weilan_pending_event_v0.7b` envelopes, but reducers must ignore them unless the journal contains a valid COMMIT for the complete bundle. Once committed, the envelope resolves to its original participant payload; it never becomes a Frame event itself.

Memory 0.7c Transition Plans are deterministic read-only documents, not events. Their participant records become ordinary Frame, lineage, Governance, Evidence, or Audit events only through one committed 0.7b transaction. Successor Frames record their transition id, proposal hash, disposition, and selected holder in the `frame_opened` data envelope.

Memory 0.7d Run events are stored in the separate append-only Run Journal described by `runner-system.md`. They record claims, transaction receipts, stop reasons, and recovery state; they are not Frame events and never encode activation authority.

## Lifecycle events

- `frame_opened`
- `candidate_admitted`
- `holder_selected`
- `action_started`
- `evidence_observed`
- `holder_warned`
- `holder_probation_started`
- `discriminating_test_executed`
- `minimal_unit_collapsed`
- `trace_emitted`
- `candidates_regrouped`
- `route_reentered`
- `frame_blocked`
- `frame_closed`

## Required semantic data

For `holder_selected`, record:

- `candidate_id`
- `why_reasonable`
- `next_expected_evidence`
- `death_line`

For `minimal_unit_collapsed`, record:

- `scope`
- `former_holder`
- `invalidating_evidence`

For `trace_emitted`, record:

- `once_reasonable`
- `invalidating_evidence`
- `reusable_results`
- `forbidden_assumption`
- `reentry_condition`

For `frame_closed`, record:

- `outcome`
- `verdict`

## Integrity rules

- `frame_opened` is first and unique.
- `frame_closed` is last and unique when present.
- A collapsed frame must emit `trace_emitted` before it closes.
- A probation event should be followed by a discriminating test, collapse, or explicit block.
- Do not store chain-of-thought, credentials, raw authentication material, or unrelated private content.
- Record observable evidence and concise decision rationale only.

## CLI fields

Pass event data as repeated `--field "key=value"` arguments. Duplicate keys become lists. Values remain strings in version 0.1 to keep recording deterministic and shell-safe.
