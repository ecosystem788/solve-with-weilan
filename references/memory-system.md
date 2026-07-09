# WeiLan memory system 0.6

Cold-start is the activation gate of one federated memory pipeline. It is not a second handoff system.

## Layers and authority

Use physically distributed sources with explicit scope and precedence:

1. The current explicit user instruction.
2. Append-only control events for pause, resume, stop, block, close, and scope changes.
3. Causal lineage rules governing which frame and branch transitions are valid.
4. Append-only governance events for pressure and target transitions inside an active scope.
5. Current files and verified tool observations.
6. Project documents and consolidated Codex memory.
7. Selected source-backed conversation evidence candidates.
8. Append-only evidence lifecycle and persistence-audit decisions.
9. Promoted source-backed workspace semantic memory.
10. Replaceable workspace projections, indexes, branch-head caches, and read-only self projections.
11. Historical Frame/Trace events.

Project documents remain their own source of truth. Codex memories hold stable cross-task preferences and reusable knowledge. WeiLan method-state holds operational control, projections, and observable task episodes. Reference sources; do not duplicate them.

## Distributed storage

Under `$WEILAN_METHOD_HOME`, or `$CODEX_HOME/method-state` when unset:

```text
frames/YYYY-MM-DD/<frame-id>.jsonl
memory/control/workspaces/<workspace-key>/YYYY-MM-DD.jsonl
memory/projections/workspaces/<workspace-key>/<scope-key>.json
memory/episode-indexes/workspaces/<workspace-key>/<scope-key>.json
memory/semantic/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/indexes/workspaces/<workspace-key>/<scope-key>.json
memory/semantic-dispositions/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/prospective/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/lineage/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/lineage/heads/<workspace-key>/<scope-key>.json
memory/evidence/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/evidence/promotions/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/evidence/lifecycle/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/audits/persistence/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/governance/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/transactions/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
memory/runs/workspaces/<workspace-key>/<scope-key>/YYYY-MM-DD.jsonl
```

Control, event, semantic, evidence, audit, and governance files are append-only and date-sharded. Projections and indexes are derived views and may be replaced atomically.

A crash can leave one torn unterminated final line in an append-only shard. Readers skip exactly that torn tail, and the next append seals it into an isolated line after preserving the fragment's byte identity (offset, length, SHA-256) in an append-only `<shard>.torn` quarantine sidecar. Shards are never truncated; lines are decoded as strict UTF-8, and any terminated undecodable line whose exact byte range is not quarantined remains a hard error. Governance appends validate the replayed ledger before writing, so a concurrent-writer race rejects the losing event instead of persisting an invalid replay. Evidence Promotion and semantic consolidation writers hold the same workspace/scope contract fence as the other direct writers, which keeps "one evidence fragment cannot be promoted twice" true under concurrent sessions.

## Control events

Record an explicit user pause, resume, stop, block, close, or scope redirection immediately:

```powershell
python scripts/weilan_trace.py memory-control `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --state paused `
  --directive "Pause implementation pending review"
```

States are `active`, `paused`, `blocked`, and `closed`. `workspace` is the master scope; a paused master scope blocks every child scope. A child scope can remain paused while another child scope is active.

Paused, blocked, and closed controls require confirmation by default. Set `active` only after a specific user instruction authorizes that scope. A bare “continue” is not a specific resume command when the selected scope is paused.

## Scoped projections

A projection contains only current focus, status, next proposed action, compact decisions, open questions, source pointers, source snapshots, and the control heads from which it was derived. It is a cache, never the state authority.

Prefer deterministic rebuild over manual rewriting:

```powershell
python scripts/weilan_trace.py projection-rebuild `
  --workspace "<cwd>" `
  --scope "memory-system"
```

The reducer reads the current control heads, scoped Frames, and active semantic entries. Deleting the projection cache and running the command again must recover the same operational meaning from those sources. `memory-update` remains available for compatibility and exceptional manual projections.

```powershell
python scripts/weilan_trace.py memory-update `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --focus "..." `
  --status "..." `
  --next "..." `
  --source "frame:<id>"
```

The writer rejects projections above 16 KiB. Split independent work into scopes instead of expanding one projection.

## Episodic recall

Frames remain append-only evidence. Their derived scoped index is rebuildable and contains the problem, success criteria, candidates, temporary holders and death lines, tests, observations, collapses, traces, outcomes, lineage, and a `frame:<id>` provenance pointer.

```powershell
python scripts/weilan_trace.py episode-index --workspace "<cwd>" --scope "memory-system"
python scripts/weilan_trace.py episode-search --workspace "<cwd>" --scope "memory-system" --query "collapse test"
```

Search uses only exact workspace and scope. A stale or absent index falls back to a live Frame scan; the derived index cannot activate or resume work.

## Activation gate

At the first task of a new run, recall by current working directory. Add `--scope` only when the user has identified one explicitly.

```powershell
python scripts/weilan_trace.py memory-recall --workspace "<cwd>"
```

Interpret the result:

- `ACTIVE`: one scope is authorized and its projection matches current control heads and source snapshots. Verify relevant sources, then continue.
- `PAUSED`: report only; do not execute until the user specifically resumes the scope.
- `CONFIRM_REQUIRED`: control is missing, blocked, closed, confirmation-gated, or multiple scopes are ambiguous.
- `STALE`: control heads or source snapshots changed after projection generation. Rebuild before continuing.
- `NO_CONTEXT`: no exact or ancestor workspace memory matched. Use only the current request.

When multiple scopes exist, select automatically only if exactly one is `ACTIVE`. Otherwise ask the user to identify the scope.

For a fresh-window continuation of an already active scope, recall is a bounded entry step, not permission to explore every remembered topic. After activation succeeds, inspect only the current active scope unless the user asks for a different scope or a source-freshness check proves the projection stale. Verify the current task source before the first write, and keep unrelated paused scopes unchanged.

Under an explicit task budget, do not run `memory-search`, `episode-search`, or `projection-rebuild` after a fresh `ACTIVE` recall unless the source is `STALE`, the current requirement contradicts the projection, or a lineage command fails. A current requirement file is stronger evidence than older recall text.

## Causal frame lineage and concurrent branches

Memory 0.4 defines continuity by explicit causal adjacency, not wall-clock proximity or background heartbeat. A frame is a discrete identity-bearing unit. Every lineaged frame declares its scope, branch, relation, and closed causal parents.

The four relations are:

- `root`: begins a causally independent empty scope and has no parent;
- `continue`: advances one active branch from exactly its current head;
- `fork`: creates a new active branch from one current active branch head while leaving the source branch active;
- `join`: creates one target-branch frame from two or more distinct active branch heads, then marks every non-target parent branch as joined.

All causal parents must be valid, closed frames in the same workspace and scope. The first lineaged frame in a scope may use `continue` from one closed legacy frame to bootstrap an existing history. New lineaged work uses:

```powershell
python scripts/weilan_trace.py open `
  --level L2 `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --branch "main" `
  --relation continue `
  --parent "<current-branch-head>" `
  --problem "..." `
  --success "..."
```

Inspect and validate the causal directed acyclic graph (DAG, 有向无环图) plus branch states with:

```powershell
python scripts/weilan_trace.py lineage-show --workspace "<cwd>" --scope "memory-system"
```

Lineage transitions are serialized by a per-workspace, per-scope file lock. Validation and branch-head comparison occur while holding that lock. If two writers try to continue the same branch from the same parent, at most one succeeds; the other receives a branch-head conflict. This preserves real concurrent branches instead of silently imposing last-writer-wins order.

The append-only lineage ledger is the transition evidence. The branch-head JSON is a replaceable cache derived from that ledger. A joined branch cannot continue or fork. Re-entry requires a new explicit fork from an active head; renaming a joined branch does not restore it.

Lineage governs causal and branch validity only. It never activates a paused, blocked, closed, ambiguous, or stale scope. Activation remains exclusively controlled by user instructions and the control ledger.

For multi-window milestone work, every milestone must leave a closed scoped Frame whose parent is the previous real milestone head. A later window should restore the active scope, inspect `lineage-show`, and continue from that head before making material changes. Synthetic or backfilled Frames are not valid continuity evidence. When the task or its history already names a scope for the work, reuse that exact scope for every recall, lineage, Frame, audit, and receipt operation; do not substitute a broader package or project scope.

For milestone work with hard tool budgets, the expected continuity path is one recall, one lineage inspection, one lineaged open, one batched source read, one focused implementation, one focused verification, and one close/validate. Divide an aggregate budget roughly evenly across stages and treat clearly overrunning one stage as the death line for optional exploration. Combine sequential trace commands in one shell call when their order is known, and do not split file reads one file per tool call. Skip archival, retention planning, semantic searches, self projection, governance inspection, previous-milestone rereads, and duplicate verification commands unless the task explicitly requires them, activation blocks progress, or a verification failure creates new evidence.

For single-window L2 prototype work under a tight tool budget, memory operations must be front-loaded and batched. A single shell call may perform recall, open one Frame, and record holder metadata. After that, prefer task artifacts such as a decision record, an evidence record, and a discriminating test over additional memory searches or separate trace events. Do not run `episode-search`, `memory-search`, `projection-rebuild`, retention planning, or governance inspection unless recall returns `STALE`/blocked or the current source contradicts the projection. Passing verification plus complete task artifacts is enough; do not spend reserve calls on standalone JSON pretty-print checks or duplicate test runs.

## Conversation Evidence（对话证据层）

Memory 0.5 introduces a bounded candidate layer between important conversation and semantic memory. Capture is always explicit. There is no background transcript collector, and no command defaults to saving every turn.

One evidence fragment contains a concise claim, a durable signal, at least one precise conversation source locator, optional supporting sources, source snapshots, and a privacy scan result:

```powershell
python scripts/weilan_trace.py evidence-capture `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --signal architectural_decision `
  --claim "Memory 0.5 uses selected conversation evidence and a promotion gate" `
  --source "conversation:<thread-id>#<exact-turn-id>"
```

Persistable signals are `explicit_user_directive`, `architectural_decision`, `durable_constraint`, `verified_result`, `repeated_preference`, and `unresolved_risk`. `casual_chat`, `transient_status`, and `social_acknowledgement` are rejected without creating a shard.

The claim is a paraphrased evidence fragment, not a transcript. It is limited to six lines and 4 KiB including metadata. Credential-like content and private-key material are rejected without persistence. The exact thread and turn must resolve to one Codex session. Its public user/assistant messages are hashed into the source snapshot; hidden reasoning and tool output are excluded. Frame and file sources may supplement the conversation turn but cannot replace it. `WEILAN_CODEX_SESSIONS_HOME` may point tests or installations at a non-default session root. `WEILAN_ALLOW_UNRESOLVED_CONVERSATION=1` is a legacy-test escape hatch and must not be used in production.

Captured evidence is only a candidate. It cannot activate work and is not returned by semantic search until promoted. Inspect candidates and promotion status with:

```powershell
python scripts/weilan_trace.py evidence-show --workspace "<cwd>" --scope "memory-system"
```

`evidence-show`, `governance-show`, `prospective-show`, and `metabolic-transaction-show` listings return the newest 20 entries by default (`--limit N`, `0` = unlimited) and always report the total count plus a truncated flag, so the caller's context stays bounded regardless of ledger age.

## Promotion Gate（晋升门）

Chat-derived content must pass `evidence-promote`; do not bypass the gate with direct `memory-consolidate`.

```powershell
python scripts/weilan_trace.py evidence-promote `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --evidence-id "<id>" `
  --kind decision `
  --summary "..." `
  --stable `
  --reusable `
  --privacy-reviewed
```

The gate requires all of the following:

- evidence sources still match their captured snapshots;
- stability, cross-task reuse value, and privacy review are explicitly confirmed;
- the requested semantic kind is allowed for the evidence signal;
- the semantic summary has lexical grounding in the evidence claim;
- the promoted text passes the sensitive-material scan.

A failed gate writes only an append-only audit record containing the evidence id, check results, reason codes, and a summary hash. It writes no semantic entry and stores no rejected semantic text. A successful gate writes exactly one semantic entry whose sources include `evidence:<id>` plus the evidence's original sources, then records the resulting semantic-memory id. One evidence fragment cannot be promoted twice.

Promotion and evidence status are not activation authority. User instructions and the control ledger remain the only source of resume permission.

## Evidence Lifecycle（证据生命周期）

Memory 0.5.1 gives every captured evidence fragment a derived lifecycle state:

- `CANDIDATE`: captured but not promoted;
- `PROMOTED`: passed Promotion Gate and owns one semantic entry;
- `WITHDRAWN`: explicitly retracted;
- `EXPIRED`: its planning or validity horizon ended;
- `SUPERSEDED`: replaced by another non-terminal evidence fragment.

Terminal transitions are append-only and never delete the evidence, promotion audit, or semantic history:

```powershell
python scripts/weilan_trace.py evidence-disposition `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --evidence-id "<id>" `
  --state withdrawn `
  --reason "obsolete after explicit user correction"
```

`superseded` additionally requires `--replacement-evidence-id` in the same workspace and scope. The replacement must be non-terminal. A terminal evidence fragment cannot be promoted or transitioned again; correction requires a new fragment.

When promoted evidence becomes withdrawn, expired, or superseded, its semantic entry remains in append-only storage but immediately leaves the active semantic index. The replaceable index includes evidence dependency heads, so a lifecycle change makes an old index stale even if no semantic JSONL file changed. Search rebuild or fallback therefore cannot allow revoked evidence to continue influencing active recall.

## Persistence Audit（持久化审计）

Every new lineaged L2/L3 frame declares `persistence_audit_required`. Before close, it must record one of:

- `PROMOTED`: an evidence fragment has passed Promotion Gate and resolves to exactly one semantic entry;
- `NOT_PERSISTED`: a concise explicit reason explains why the frame has no durable cross-task conclusion.

Record the audit before closing:

```powershell
python scripts/weilan_trace.py persistence-audit `
  --frame-id "<id>" `
  --trigger round_end `
  --decision promoted `
  --evidence-id "<promoted-evidence-id>"
```

Required triggers are:

- `round_end` for every audited lineaged frame;
- `route_change` when the frame collapses, regroups, or re-enters a route;
- `version_switch` when a version/scope-redirection control event occurs after the frame opens and before it closes. Later controls never retroactively change a closed frame's audit requirements.

`close` refuses to close while a required trigger is missing. `NOT_PERSISTED` requires a non-empty, bounded, non-sensitive reason. Failed audits do not satisfy the trigger. Inspect coverage with `persistence-audit-show --frame-id <id>`.

The audit ledger is append-only and stores frame, trigger, decision, evidence id, resulting semantic-memory id, or the explicit non-persistence reason. It is governance evidence, never activation authority.

## Governance Ledger and Self Projection

Memory 0.6 adds a separate append-only Governance Ledger（治理账本） for source-backed Feedback Pressure（反馈压力） and GovernanceTarget（治理目标） lifecycle. It is not stored inside the replaceable workspace projection.

Governance writes require an explicitly active scope. Pressure changes only the eligibility of registered assumptions, holders, goals, routes, or frames; it cannot modify evidence, facts, scope control, or semantic memory. Cross-scale pressure movement is always an explicit propagation event.

The deterministic reducer derives target state, typed pressure vectors, source invalidation, scale summaries, collapse traces, and a governance head. `self-project` then combines those results with current control and lineage heads into a read-only projection. It always declares that it cannot authorize action and cannot restore a paused scope.

Read the complete event model, transition gates, scale rules, replay contract, and 0.7 boundary in [governance-system.md](governance-system.md) before writing governance events.

Memory 0.7a derives a read-only Metabolic Contract（代谢契约） and validates one Transition Proposal（转移提案） against current control, lineage, audit, evidence, governance, and Self Projection heads. It does not write state or open frames. Read [metabolism-system.md](metabolism-system.md) before using `metabolic-contract` or `metabolic-propose`.

Memory 0.7b adds explicit cross-ledger transaction commits, recovery, idempotency, and pending-event isolation as documented in [transaction-system.md](transaction-system.md). It still does not add a bounded runner or background autonomous loop.

Its completion boundary includes an exact derived receipt body and one shared workspace/scope Contract Commit Fence（契约提交栅栏） across internal contract-affecting writers; a hash checked outside that fence is not an atomic contract check.

Memory 0.7c adds deterministic one-step transition plans and explicit materialization for collapse, regroup, completion, blocking, testing, and successor Frames as documented in [transition-planner-system.md](transition-planner-system.md). It still performs no scheduling or repeated event consumption.

Memory 0.7d adds an explicitly invoked foreground Bounded Runner（有界运行器）, finite event manifests, scoped concurrency claims, crash recovery, and deterministic run receipts as documented in [runner-system.md](runner-system.md). It never becomes a background autonomous Agent.

Runner recovery reconciles a materialization exception with the durable transaction state before stopping. An unknown or already committed outcome cannot be converted into a terminal zero-step `INVALID_EVENT`.

## Semantic consolidation and recall

Semantic memory holds durable decisions, constraints, facts, lessons, procedures, and open questions that remain useful beyond one projection. It is evidence for recall, never authority to continue work. Only current user instructions and control events can activate a scope.

Declare known disagreement explicitly with `--conflicts-with <memory-id>`. Inspect unresolved pairs with `memory-conflicts`. The system does not invent contradictions from lexical similarity. Resolve a conflict by superseding or explicitly retiring the obsolete entry while preserving both histories.

Bound active recall causally, not by wall-clock decay:

```powershell
python scripts/weilan_trace.py memory-retention-plan --workspace "<cwd>" --scope "memory-system" --max-active 100
python scripts/weilan_trace.py memory-disposition --workspace "<cwd>" --scope "memory-system" --memory-id "<id>" --state dormant --reason "bounded active recall"
```

The plan is read-only. `dormant` and `retired` dispositions are append-only, remove an entry from active search, and never delete semantic history. `active` can explicitly reactivate an entry. Constraints, open questions, and `critical` tagged entries are protected by the planner; an explicit disposition is still required.

Append one compact entry after a material L2/L3 result becomes stable:

```powershell
python scripts/weilan_trace.py memory-consolidate `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --kind decision `
  --summary "Cold start is a control-gated projection recall" `
  --tag "cold-start" `
  --source "frame:<id>"
```

Every entry must have at least one source. Entries are append-only and limited to 16 KiB. Correct an older entry with `--supersedes <memory-id>`; never edit historical lines in place. The current active set is all entries not named by a later entry's `supersedes` list.

Search only after the activation gate has allowed work, and only when task-relevant history is needed beyond the scoped projection:

```powershell
python scripts/weilan_trace.py memory-search `
  --workspace "<cwd>" `
  --scope "memory-system" `
  --query "cold start"
```

The tokenizer supports lowercase Latin terms and Chinese character/bigram matching. Search reports source freshness for each result. A missing or stale index triggers an in-memory linear fallback so the index cannot hide a live entry. Rebuild the replaceable index explicitly with `memory-index`.

Use direct `memory-consolidate` for non-chat evidence or trusted system-produced results. Chat-derived claims must enter through Conversation Evidence and Promotion Gate. Do not consolidate routine tool calls, transient status, hidden reasoning, raw conversations, credentials, or facts already owned by a project source without adding a useful cross-task conclusion.

## Conservation and reorganization（守恒与重组, Memory 0.8）

The active semantic set is a bounded existence budget, not an unbounded log. Every scope has a binding `max_active` head (default 100, set explicitly with `memory-budget-set --max-active N --reason ...`; append-only history). Once the budget is full, any write that grows the active set — `memory-consolidate`, `evidence-promote`, `memory-split`, or a `memory-disposition --state active` reactivation — must name `--displace <memory-id>`. Displacement appends a dormant disposition (`displaced_by_budget:<new-id>`), reversible and never a deletion. Entries the same write supersedes count as leaving, so a correction or merge at full budget needs no displacement. A rejected admission lists the oldest unprotected displacement candidates; `constraint`, `open_question`, and `critical`-tagged entries are never suggested, though an explicit `--displace` may still name them.

Reorganization is how the set competes instead of merely accumulating:

```powershell
python scripts/weilan_trace.py memory-merge --workspace "<cwd>" --scope "..." --from <id> --from <id> --kind decision --summary "..."
python scripts/weilan_trace.py memory-split --workspace "<cwd>" --scope "..." --memory-id <id> --part '{"summary": "..."}' --part '{"summary": "..."}'
```

`memory-merge` folds redundant active entries into one entry that supersedes all inputs, inherits the union of their sources (including `evidence:` refs — restructuring preserves grounding without re-passing the Promotion Gate), and records `reorganization: {kind: merge, from: [...]}`. `memory-split` breaks one over-mixed entry into two or more grounded parts; only the final part supersedes the parent, so an interrupted split never deactivates the parent before every part is durable. Merged or split entries whose inherited `evidence:` sources later become withdrawn, expired, or superseded leave active recall exactly like promotion-backed entries. Merge and split summaries must have lexical grounding in their inputs; the caller (the LLM) remains the cohesion judge, the ledger only enforces lineage, budget, and grounding.

`memory-retention-plan` now defaults to the scope budget and remains read-only. The dynamics this section adds are deliberately minimal: bounded budget plus explicit merge/split/displace is the method-plane projection of conservation-competition-collapse; there is still no timer, no background loop, and no automatic transition.

## Archive boundary

Archival must preserve control heads, source resolution, and append-only evidence. Until an integrity-manifest archive reader exists:

- do not move or delete control shards;
- do not move open frames;
- do not move frames referenced by a current projection or active semantic entry;
- treat indexes and projections as rebuildable, not archive authorities;
- use `memory-archive-plan --before YYYY-MM-DD` only to list unreferenced closed-frame candidates.

`memory-archive-plan` is deliberately non-mutating. Physical rollover requires a later version that writes an integrity manifest, keeps `find_frame` transparent across hot and cold storage, verifies hashes before removal from hot storage, and passes activation plus source-freshness regression tests.

## Persistence boundaries

Always persist explicit control directives. Persist Frame/Trace only for L2/L3 and use causal lineage when a parent is known. Capture conversation evidence only for explicit durable signals, and promote it only after every gate check passes. Before L2/L3 close, route change, or version switch, complete the required Persistence Audit with promoted evidence or an explicit non-persistence reason. Withdraw, expire, or supersede evidence by append-only disposition; never delete history. Record pressure and GovernanceTarget transitions only in an active scope, require explicit scale propagation, and treat Self Projection as read-only. Consolidate semantic memory only for stable reusable results with sources. Do not persist routine tool calls, full conversations, ordinary chat, hidden chain-of-thought, credentials, raw private data, or speculative identity claims.

Keep old event shards as evidence or archive them later. Do not load all shards during cold-start; use projections and source pointers as the bounded entry surface.
