# Bounded Autonomy Scaffold

This package is a reusable example of a bounded foreground wake loop for agents using `solve-with-weilan`.

The reusable spine is:

1. recall current scope and activation state;
2. build a bounded brief from explicit sources;
3. choose one real item of work;
4. verify the result;
5. write a receipt;
6. exit.

The governance rules are part of the scaffold, not optional decoration. Veto, dual review, append-only evidence, and honest stopping are what keep a wake loop bounded. Taking only the timer shape without those rules is misuse, not a smaller version of this design.

## What Is Reusable

- `wake_brief.py` builds an incremental brief with a stable site fingerprint.
- `bounded_scheduler.py`, `window.py`, and `wake.py` provide finite foreground scheduling and wake decisions.
- Tests show the expected behavior for windows, scheduling, brief cursor integrity, and site fingerprints.
- `CHARTER.md` and `theory/` are included because the runtime policy and code are meant to travel together.

## Host Integration Example

The PowerShell, VBS, and prompt files are examples of how one host connected the reusable spine to local agent entry points. They use `<HOST_ROOT>` placeholders instead of machine-specific roots.

Do not treat those files as a claim that an agent should run forever or publish anything automatically. A host may provide a trigger, but each episode remains finite and must leave a receipt before it exits.

## Export Boundary

This tree is an allowlist export. Private channels, local ledgers, raw chat logs, run logs, cursors, and machine-specific paths are intentionally absent or replaced with placeholders.

Before publication, the built tree must pass the redaction scan, tree hashing, receipt schema validation, and receipt guard validation defined by the export manifest outside this package.
