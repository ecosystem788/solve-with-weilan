# Solve with WeiLan / 微澜方法 Skill

`solve-with-weilan` is a Codex/agent Skill that applies the WeiLan problem-solving method to ordinary engineering tasks, research work, and long-running agent workflows.

`solve-with-weilan` 是一个 Codex/agent Skill，用来把微澜方法应用到日常工程任务、研究任务和长期 agent 工作流中。

## What It Does / 功能概览

- Proportional framing: the agent chooses L0 to L3 depth according to task risk, uncertainty, and cost.
- 倍率化任务框定：agent 会根据任务风险、不确定性和成本，在 L0 到 L3 之间选择合适深度。
- Temporary holder and collapse discipline: the agent keeps the current route only while it keeps producing useful evidence.
- 临时 holder 与 collapse 纪律：agent 只在当前路线持续产生有效证据时保持它的主导地位。
- Frame and Trace ledger: for L2/L3 work, the agent records observable decisions, evidence, route changes, and receipts.
- Frame/Trace 账本：对于 L2/L3 工作，agent 会记录可观察的决策、证据、路线变化和收据。
- Workspace memory recall: the agent restores active scope, paused state, lineage, and reusable semantic memory from append-only records.
- 工作区记忆召回：agent 会从 append-only 记录中恢复 active scope、paused state、lineage 和可复用语义记忆。
- Long-horizon control modules: governance, prospective planning, metabolic transaction, transition planning, and bounded runner support controlled multi-step work.
- 长程控制模块：governance、prospective planning、metabolic transaction、transition planning 和 bounded runner 支持受控的多步骤工作。
- Skill Evolution support: the agent can coordinate with external proposal, evaluation, adoption, deployment, and rollback workflows.
- Skill Evolution 支持：agent 可以对接外部 proposal、evaluation、adoption、deployment 和 rollback 工作流。
- Hard L0/L1 fast path: low-risk work stays lightweight unless a concrete escalation trigger appears.
- 硬性 L0/L1 快速路径：低风险工作保持轻量，只有出现明确升级触发条件才进入重流程。

## Repository Layout / 仓库结构

```text
SKILL.md                 Main Codex Skill entrypoint / Codex Skill 主入口
agents/openai.yaml       Agent configuration / agent 配置
references/              Method contracts and subsystem specs / 方法契约与子系统说明
scripts/weilan_trace.py  Main CLI used by the agent / agent 使用的主 CLI
scripts/*.py             Runtime modules and tests / 运行模块与测试
theory/                  WeiLan theory notes from WeilanSkillEvolution / 微澜理论资料
```

## Install / 安装

This is a one-time setup step for the Codex environment owner or maintainer. Ordinary end users normally do not need to run the commands during task work.

这是给 Codex 环境所有者或维护者的一次性安装步骤。普通使用者在日常任务中通常不需要手动运行这些命令。

```powershell
$skills = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME "skills" } else { Join-Path $HOME ".codex\skills" }
git clone https://github.com/ecosystem788/solve-with-weilan.git (Join-Path $skills "solve-with-weilan")
```

To update an existing installation:

更新已有安装：

```powershell
cd (Join-Path $skills "solve-with-weilan")
git pull
```

After installation, Codex can invoke the skill as `$solve-with-weilan`.

安装后，Codex 可以通过 `$solve-with-weilan` 调用这个 Skill。

## Agent Operation / Agent 自动运行方式

The following commands document what the agent should do internally. They are not instructions that a normal reader must run by hand.

下面这些命令说明 agent 内部应该如何运行。它们不是普通读者必须手动执行的使用步骤。

After mandatory activation/control checks, the agent first tries the L0/L1 fast path. For L0/L1 work it should not open Frames, create competing candidates, inspect lineage/governance/memory indexes, or mention WeiLan machinery unless that helps the task outcome.

在完成必要的 activation/control 检查后，agent 会先尝试 L0/L1 快速路径。对于 L0/L1 工作，它不应该打开 Frame、创建竞争候选、检查 lineage/governance/memory index，也不应该向用户提起微澜机制，除非这些动作直接帮助任务结果。

The agent escalates to L2/L3 only when there is a concrete trigger: external publish/deploy/push, irreversible edits, stale or paused long-running scope, real architectural alternatives, repeated failure, governance-level judgment, or direct work on the method internals.

只有出现明确触发条件时，agent 才升级到 L2/L3：对外 publish/deploy/push、不可逆编辑、长期 scope 的 stale 或 paused 状态、真实架构分歧、重复失败、治理级判断，或直接修改方法内部机制。

At the first task of a new run, the agent recalls the current workspace state:

在每次新 run 的第一个任务里，agent 会召回当前工作区状态：

```powershell
python scripts/weilan_trace.py memory-recall --workspace "<cwd>"
```

The agent then obeys the returned `activation.state`:

然后 agent 按返回的 `activation.state` 执行：

- `ACTIVE`: verify task-relevant sources, then continue.
- `ACTIVE`：验证当前任务相关来源后继续。
- `PAUSED`: report only; a bare "continue" does not resume the scope.
- `PAUSED`：只报告状态；简单的 "continue" 不会自动恢复 scope。
- `CONFIRM_REQUIRED`: ask the user which scope or direction is intended.
- `CONFIRM_REQUIRED`：向用户确认目标 scope 或方向。
- `STALE`: rebuild the projection, then recall again.
- `STALE`：先 rebuild projection，再重新 recall。
- `NO_CONTEXT`: start clean from the current request.
- `NO_CONTEXT`：不继承旧上下文，从当前请求开始。

For material L2/L3 work, the agent opens a Frame, records the holder and evidence, completes the persistence audit, closes the Frame, and validates the receipt:

对于重要的 L2/L3 工作，agent 会打开 Frame，记录 holder 和 evidence，完成 persistence audit，关闭 Frame，并验证 receipt：

```powershell
python scripts/weilan_trace.py open --level L2 --workspace "<cwd>" --scope "<scope>" --branch main --relation continue --parent "<frame-id>" --problem "..." --success "..."
python scripts/weilan_trace.py event --frame-id "<frame-id>" --type holder_selected --field "candidate_id=..." --field "why_reasonable=..." --field "next_expected_evidence=..." --field "death_line=..."
python scripts/weilan_trace.py persistence-audit --frame-id "<frame-id>" --trigger round_end --decision not_persisted --reason "No durable cross-task conclusion."
python scripts/weilan_trace.py close --frame-id "<frame-id>" --outcome success --verdict "..."
python scripts/weilan_trace.py validate --frame-id "<frame-id>" --require-closed
```

## Main Modules / 主要模块

- `weilan_trace.py`: command-line entrypoint the agent uses for Frame/Trace, memory, evidence, governance, prospective planning, transactions, transition planning, and bounded runs.
- `weilan_trace.py`：agent 用于 Frame/Trace、memory、evidence、governance、prospective planning、transaction、transition planning 和 bounded run 的命令行入口。
- `runtime_core.py`: shared filesystem, locking, JSONL, and receipt helpers.
- `runtime_core.py`：共享文件系统、锁、JSONL 和 receipt 工具。
- `governance.py`: append-only pressure and target lifecycle state.
- `governance.py`：append-only pressure 与 target lifecycle 状态。
- `prospective.py`: bounded future-condition registration and observation.
- `prospective.py`：有界未来条件注册与 observation。
- `metabolism.py`: read-only metabolic contracts and transition proposal checks.
- `metabolism.py`：只读 metabolic contract 与 transition proposal 检查。
- `transaction.py`: explicit prepare, commit, abort, and recover flows.
- `transaction.py`：显式 prepare、commit、abort 和 recover 流程。
- `transition_planner.py`: deterministic one-step collapse, regroup, and materialization planning.
- `transition_planner.py`：确定性单步 collapse、regroup 和 materialization 计划。
- `runner.py`: finite foreground runner for bounded event manifests.
- `runner.py`：用于有界 event manifest 的有限前台 runner。

## Theory Notes / 理论资料

The `theory/` directory contains source notes from `WeilanSkillEvolution`. These files preserve conceptual background and should be treated as theory provenance, not as automatic action authority.

`theory/` 目录来自 `WeilanSkillEvolution`，保存微澜理论背景资料。它们是 theory provenance（理论来源依据），不是自动行动授权。

## License / 协议

MIT License. See [LICENSE](LICENSE).

MIT 协议。见 [LICENSE](LICENSE)。
