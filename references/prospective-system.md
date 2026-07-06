# SE-0.4 prospective memory and causal event cycle

Prospective memory stores bounded future intentions as append-only scoped goals. It does not poll, wake itself, create empty Frames, or authorize action.

## Lifecycle

Every goal begins `ACTIVE` and can transition once to:

- `SATISFIED`, only with a matching observed causal event;
- `SUPERSEDED`, only when a distinct active replacement already exists;
- `COLLAPSED`, with an explicit invalidating reason.

History is never edited or deleted.

## Register a condition

```powershell
python scripts/weilan_trace.py prospective-register `
  --workspace "<cwd>" --scope "skill-evolution" `
  --goal-ref "goal:review" --description "Review after tests pass" `
  --event-kind tool --event-name tests_green `
  --death-line "collapse if the test contract changes" `
  --source "frame:<id>"
```

Event kinds are `user`, `tool`, `environment`, and `clock`. Only clock conditions accept `--not-before` with a timezone-aware ISO-8601 timestamp.

## Observe one causal event

```powershell
python scripts/weilan_trace.py prospective-observe `
  --workspace "<cwd>" --scope "skill-evolution" `
  --kind tool --name tests_green --source "frame:<id>"
```

Observation appends one source-backed event and derives one finite read-only cycle result:

- `QUIESCENT`: no active condition matched;
- `AMBIGUOUS`: more than one active condition matched, so no plan is emitted;
- `READY`: exactly one goal matched and an explicit transition may be requested.

Observation never changes a goal automatically. Replay a plan with `prospective-cycle --causal-event-id <id>`.

## Clock boundary

A clock event is foreground and explicit. It requires `--goal-ref`, the named goal must be active, its `not_before_utc` must be true at actual observation time, and no other active goal may match the same clock event. No timer, scheduler, daemon, heartbeat, or background wakeup is part of SE-0.4.

## Apply an explicit transition

```powershell
python scripts/weilan_trace.py prospective-transition `
  --workspace "<cwd>" --scope "skill-evolution" `
  --goal-ref "goal:review" --state satisfied `
  --causal-event-id "<id>" --reason "condition verified" `
  --source "frame:<id>"
```

All writes require an explicitly active scope. Prospective state and cycle plans are evidence only; they cannot resume a scope, open a Frame, invoke a tool, or bypass the normal action authority.
