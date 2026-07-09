# Solve with WeiLan / 微澜方法 Skill

`solve-with-weilan` is a Codex Skill that applies the WeiLan problem-solving method to ordinary engineering tasks, research work, and long-running agent workflows.

`solve-with-weilan` 是一个 Codex Skill，用来把微澜方法应用到日常工程任务、研究任务和长期 agent 工作流中。

## What It Does / 功能概览

- Proportional task framing: choose L0 to L3 depth according to risk, uncertainty, and cost.
- 倍率化任务框定：根据风险、不确定性和成本，在 L0 到 L3 之间选择合适深度。
- Temporary holder and collapse discipline: keep the current route only while it keeps producing useful evidence.
- 临时 holder 与 collapse 纪律：当前路线只有在持续产生有效证据时才保持主导。
- Frame and Trace ledger: persist observable L2/L3 decisions, evidence, route changes, and receipts.
- Frame/Trace 账本：持久化 L2/L3 的可观察决策、证据、路线变化和收据。
- Workspace memory recall: restore active scope, paused state, lineage, and reusable semantic memory from append-only records.
- 工作区记忆召回：从 append-only 记录恢复 active scope、paused state、lineage 和可复用语义记忆。
- Governance, prospective planning, metabolic transaction, transition planning, and bounded runner modules for controlled long-horizon work.
- 包含治理、前瞻计划、代谢事务、转移计划和有界 runner 模块，用于受控的长期工作。
- Skill Evolution support: integrate with external proposal, evaluation, adoption, deployment, and rollback workflows.
- Skill Evolution 支持：对接外部 proposal、evaluation、adoption、deployment 和 rollback 工作流。

## Repository Layout / 仓库结构

```text
SKILL.md                 Main Codex Skill entrypoint / Codex Skill 主入口
agents/openai.yaml       Agent configuration / agent 配置
references/              Method contracts and subsystem specs / 方法契约与子系统说明
scripts/weilan_trace.py  Main CLI for Frame, memory, governance, and runner operations / 主 CLI
scripts/*.py             Runtime modules and tests / 运行模块与测试
theory/                  WeiLan theory notes from WeilanSkillEvolution / 微澜理论资料
```

## Install / 安装

Clone this repository into your Codex skills directory:

将本仓库克隆到 Codex skills 目录：

```powershell
$skills = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME "skills" } else { Join-Path $HOME ".codex\skills" }
git clone https://github.com/ecosystem788/solve-with-weilan.git (Join-Path $skills "solve-with-weilan")
```

If the directory already exists, update it with:

如果目录已经存在，用下面命令更新：

```powershell
cd (Join-Path $skills "solve-with-weilan")
git pull
```

After installation, Codex can invoke the skill as `$solve-with-weilan`.

安装后，Codex 可以通过 `$solve-with-weilan` 调用这个 Skill。

## Basic Use / 基本使用

At the first task of a new run, recall the current workspace state:

每次新 run 的第一个任务，先召回当前工作区状态：

```powershell
python scripts/weilan_trace.py memory-recall --workspace "<cwd>"
```

Then follow the returned `activation.state`:

然后按返回的 `activation.state` 执行：

- `ACTIVE`: continue after verifying task-relevant sources.
- `ACTIVE`：验证当前任务相关来源后继续。
- `PAUSED`: report only; do not resume from a bare "continue".
- `PAUSED`：只报告状态；不能用简单的 "continue" 自动恢复。
- `CONFIRM_REQUIRED`: ask the user which scope or direction to use.
- `CONFIRM_REQUIRED`：向用户确认 scope 或方向。
- `STALE`: rebuild projection, then recall again.
- `STALE`：先 rebuild projection，再重新 recall。
- `NO_CONTEXT`: start clean from the current request.
- `NO_CONTEXT`：不继承旧上下文，从当前请求开始。

For material L2/L3 work, open and close a Frame:

对于重要的 L2/L3 工作，打开并关闭 Frame：

```powershell
python scripts/weilan_trace.py open --level L2 --workspace "<cwd>" --scope "<scope>" --branch main --relation continue --parent "<frame-id>" --problem "..." --success "..."
python scripts/weilan_trace.py event --frame-id "<frame-id>" --type holder_selected --field "candidate_id=..." --field "why_reasonable=..." --field "next_expected_evidence=..." --field "death_line=..."
python scripts/weilan_trace.py persistence-audit --frame-id "<frame-id>" --trigger round_end --decision not_persisted --reason "No durable cross-task conclusion."
python scripts/weilan_trace.py close --frame-id "<frame-id>" --outcome success --verdict "..."
python scripts/weilan_trace.py validate --frame-id "<frame-id>" --require-closed
```

## Main Modules / 主要模块

- `weilan_trace.py`: command-line entrypoint for Frame/Trace, memory, evidence, governance, prospective planning, transactions, transition planning, and bounded runs.
- `weilan_trace.py`：Frame/Trace、memory、evidence、governance、prospective planning、transaction、transition planning 和 bounded run 的命令行入口。
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
- `transition_planner.py`: deterministic one-step collapse/regroup/materialization planning.
- `transition_planner.py`：确定性单步 collapse、regroup、materialization 计划。
- `runner.py`: finite foreground runner for bounded event manifests.
- `runner.py`：用于有界 event manifest 的有限前台 runner。

## Theory Notes / 理论资料

The `theory/` directory contains source notes from `WeilanSkillEvolution`. These files preserve conceptual background and should be treated as theory provenance, not as automatic action authority.

`theory/` 目录来自 `WeilanSkillEvolution`，保存微澜理论背景资料。它们是理论 provenance（来源依据），不是自动行动授权。

## License / 协议

MIT License. See [LICENSE](LICENSE).

MIT 协议。见 [LICENSE](LICENSE)。
