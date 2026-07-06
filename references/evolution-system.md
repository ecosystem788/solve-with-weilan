# External Skill Evolution plane

Use this workflow only when the current workspace contains the canonical external authority files `ROADMAP.md`, `ARCHITECTURE.md`, and `EVALUATION_POLICY.md` plus `tools/evolution_cli.py`.

The candidate Skill may propose a change. It may not modify or approve the roadmap, evaluation cases, scoring policy, frozen baseline, adoption decision, deployment target, or rollback authority.

## Bounded proposal

A proposal declares rationale, base and candidate artifact hashes, changed candidate paths, changed-file budget, target metrics, and rollback triggers. Validate it externally:

```powershell
python tools/evolution_cli.py proposal-validate --proposal "<proposal.json>"
```

Authority paths such as `ROADMAP.md`, `EVALUATION_POLICY.md`, `baseline/`, `evals/`, `deployments/`, and `tools/` are forbidden candidate changes.

## Content-addressed candidate

Freeze an already validated source candidate outside the deployed Skill:

```powershell
python tools/evolution_cli.py candidate-freeze `
  --source "<candidate-skill>" `
  --artifact-root "<external-artifact-root>"
```

The tree hash is its address. Reusing a modified artifact at that address is rejected. Freezing never deploys it.

## Fixed evaluation authority

An evaluation manifest must contain non-empty cases, budgets, weighted metrics, repeated-trial counts, `status: approved`, `frozen: true`, and an external approval source. Draft validation is structural only:

```powershell
python tools/evolution_cli.py eval-validate --manifest evals/manifest.json --allow-draft
```

Do not compare or adopt from a draft. Baseline and candidate trial receipts must use identical configuration hashes and case budgets.

## Method Impact Trace

Each material method effect records only observable process evidence:

- the gate that fired;
- whether it changed the intended action;
- the observable failure avoided, exposed, or left unchanged;
- source evidence;
- added tool calls, elapsed milliseconds, and context tokens.

This trace distinguishes useful Meta-reasoning（元推理） from ritual compliance. It contains no hidden chain-of-thought.

## Authority boundary

Evaluation output is evidence only. `adoption_eligible` means the external gate may consider an adoption; it never changes the installed Skill. A candidate cannot approve its own cases, compare unequal trials, deploy itself, or remove its rollback predecessor.

## Shadow competition and release

SE-0.6 remains in the external Release Plane. An approved shadow plan binds the baseline and candidate artifact hashes, frozen evaluation-manifest and case-set hashes, evaluator artifact hashes, configuration, receipt budget, and predeclared gate. The candidate may produce trial output but cannot validate the pair, choose adoption, or deploy.

An adoption decision must cite an independent authority source and the exact shadow-result hash. Deployment requires a separate explicit command, preserves a content-addressed predecessor, verifies the installed tree, and writes a deterministic receipt. Canary observations may fire only predeclared rollback triggers. Evidence that rollback is required never authorizes rollback by itself; an external authority source is still required.

```powershell
python tools/evolution_cli.py shadow-validate --plan "<plan.json>" --manifest evals/manifest.json
python tools/evolution_cli.py decision-validate --decision "<decision.json>" --shadow-result "<result.json>"
```

Never pass `--confirm-deployment`, `--confirm-rollback`, or `--allow-deployment` without an explicit current authorization covering the exact artifact and target.

## One finite evolution manifest

SE-0.7 may execute only a caller-supplied, finite foreground manifest through the external tool. The manifest fixes step, proposal, candidate-generation, evaluation-receipt, shadow-comparison, and deployment budgets. Decision and rollback files must come from declared external authority roots.

The runner stops on invalid proposals, exhausted budgets, unequal or regressing evaluation, missing authority, rejected adoption, deployment gating, invalid canary evidence, conflicts, or a required rollback. It writes one replayable receipt and exits. It never schedules itself, polls, waits, generates evaluation authority, treats candidate output as approval, or starts a background Agent.

```powershell
python tools/evolution_cli.py evolution-validate --manifest "<run.json>"
python tools/evolution_cli.py evolution-run --manifest "<run.json>" --root "<workspace>" --receipt "<receipt.json>"
```

An admissible manifest is not deployment authority. Without the explicit deployment flag and a valid external adoption decision, the runner must stop before changing the installed Skill.
