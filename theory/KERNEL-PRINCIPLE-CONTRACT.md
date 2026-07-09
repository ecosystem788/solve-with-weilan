# KERNEL-PRINCIPLE-CONTRACT

本文件保存微澜 kernel 的长期原理契约。

它不是阶段计划、不是 receipt、不是旧版本回顾。它只固定当前 kernel 以后必须遵守的底层边界，防止后续 Runtime、WIR、memory、semantic、language、output、feedback 接线时把微澜带回普通模块拼装路线。

理论来源：

```text
WIR.md
元寂计划.md
关于微澜内核的讨论.txt
```

## 1. 核心闭环

kernel 的中心不是语言、记忆、语义或输出模块，而是同一条底层动力学链：

```text
Carrier / 可承载差异个体
→ ExistenceBudget / 总存在量守恒
→ CompetitionOperator / 守恒竞争
→ HolderState / OligarchState / 主导持有
→ PressureState / 压力积累
→ CollapseGate / 崩溃判定
→ CollapseEvent / 离散跳变
→ TraceState / 断裂痕迹
→ RegroupOperator / 分散重组
→ NextCompetition / 下一轮竞争
→ Readout / 高层读出
```

后续任何高层机制都只能作为这条链在不同 carrier 上的投影，不应重新定义一套独立的底层竞争、collapse 或 regroup 逻辑。

## 2. 存在与差异

存在不是声明，而是可承载、可作用、可复现的差异结构。

一个候选要进入 kernel 稳定结构，至少需要满足：

```text
has_carrier
has_difference
has_transfer_effect
has_repeatable_trace_or_projection
```

没有 carrier，不生成 object。
没有 difference，不进入 competition。
没有 transfer effect，不写入 state。
没有 repeatable trace / verified projection，不沉淀为 attractor。

总存在量必须守恒。差异、活性、pressure、支撑关系、结构张力可以变化；q 只能重分配，不能任意漂移。

## 3. 量子化与离散边界

kernel 的基本风格是离散化、事件化、可引用。

计算步是一次状态快照和一次转移边界。carrier、event、trace、projection 都应尽量具备明确 identity，便于比较、引用、验证和回放。

collapse 是离散状态跳变，不是连续衰减。

```text
before_state
→ CollapseEvent
→ after_state
```

内部指标可以连续计算，但 collapse / regroup 的事件边界必须清楚。

## 4. CompetitionOperator

competition 是守恒条件下放大可承载差异。

它不是简单让 q 最大者继续变大，也不是普通加权排序。它是在总存在量守恒前提下，把存在量从低支撑、低复现、低匹配的 carrier，转移给高支撑、高复现、高匹配的 carrier。

advantage 可以由这些因素共同形成：

```text
current_q_advantage
structural_support
memory_reappearance
current_frame_match
pressure_or_fatigue_penalty
void_penalty
```

competition 的基本检查：

```text
total_q_preserved = True
difference_after >= difference_before
competition_transition_recorded = True
```

## 5. OligarchDetector

寡头不是“最强”本身，而是持续占据并压缩系统后续生成能力的主导差异中心。

OligarchDetector 至少观察：

```text
dominance
persistence
suppression
diversity_drop
stagnation
```

短暂胜出不等于寡头。只有强、持续强、压制其他 carrier，并降低后续生成多样性时，才进入寡头风险区。

## 6. CollapseGate

collapse 不是惩罚强者，而是在主导结构从生成结构转为压制结构时，让崩溃吸引子接管。

触发判断的核心不是“强到多少”，而是：继续持有是否还在产生新结构。

```text
collapse_pressure = dominance × persistence × pressure × stagnation × diversity_loss
```

当 competition 继续运行的结构收益低于释放收益时，CollapseGate 可以触发 CollapseEvent。

CollapseEvent 至少应表达：

```text
trigger
broken_holder_identity
released_budget
reduced_difference
trace_residue
after_state_ref
next_competition_opened
```

## 7. RegroupOperator

regroup 是带 trace 的分散重生，不是平均重置，也不是旧寡头换皮复活。

collapse 后应满足：

```text
new_total_q = old_total_q
old_holder_identity_not_continued = True
new_distribution_more_distributed = True
trace_preserved_as_weak_constraint = True
trace_not_direct_holder = True
next_step_returns_to_competition = True
```

如果 collapse 发生，regroup 后的结构倾向应是：

```text
new_max_q < old_max_q
new_effective_count >= old_effective_count
new_entropy >= old_entropy
```

这些不是所有场景的固定数值阈值，而是 regroup 的方向性契约。

## 8. TraceState

trace 是断裂后留下的结构痕迹。

trace 可以作为：

```text
lineage
weak_seed
constraint
projection_source
verification_reference
```

trace 不能作为：

```text
direct_holder
forced_identity
hidden_memory_write
collapse_bypass
```

没有 trace，collapse 会变成失忆。trace 太强，collapse 会变成旧寡头复活。kernel 必须维持 trace 的弱保留边界。

## 9. VoidGuard

空无不作为对象建模。

VoidGuard 的职责不是压制表达，而是阻止无承载、无差异、无转移影响的内容稳定化。

```text
no_carrier → no_object
no_difference → no_competition
no_transfer_effect → no_state_write
no_repeatable_trace → no_attractor
```

微澜的“自我”不设为额外实体，只能从当前闭环状态中投影读出。状态之外的“另一部分”若无 carrier，只能作为短暂语言扰动，不能沉淀为稳定结构。

## 10. WIR 契约

WIR 是 kernel 闭环的结构表达语言，不只是旧语言管线的中间格式。

WIR 必须优先表达 kernel 的对象、关系、operator、event、guard：

```text
WirObject: carrier / existence_budget / holder / pressure / collapse_event / trace / regroup_event / readout / void_boundary
WirRelation: competes_with / holds / suppresses / accumulates_pressure / collapses_into / leaves_trace / regroups_from / projects_to / guards
WirOperator: competition_operator / oligarch_detector / collapse_gate / regroup_operator / void_guard / readout_operator
WirEvent: competition_step / holder_formed / pressure_accumulated / collapse_triggered / trace_emitted / regroup_created / next_cycle_opened
WirGuard: total_existence_conserved / difference_amplified_in_competition / difference_reduced_after_collapse / old_identity_not_continued / trace_not_direct_holder / void_not_objectified
```

WIR dump 必须可读、可比较、可验证。WIR verifier 的职责是防止结构断链、守恒漂移、void 对象化、trace 越权、runtime 黑箱化。

## 11. 高层投影契约

memory、semantic、language、output、feedback、runtime、corpus 都是 kernel 闭环在不同 carrier 上的投影。

它们可以提供：

```text
carrier_source
projection
readout
feedback_signal
memory_trace
runtime_decision_context
```

它们不能绕过 kernel 直接定义底层主导、collapse、regroup 或 stable identity。

renderer 只能作为受控语言外壳，不能反向接管结构选择。

feedback 只能通过明确的 carrier / trace / readiness / pressure / projection 通道回到系统，不能作为外部 reward 贴片直接改写底层动力学。

## 12. Runtime 接入边界

当前 kernel 主链已经形成 verified WIR handoff envelope。后续 Runtime / WIR runtime 可以从该 envelope 读取状态、判断路径、执行受控接线。

Runtime 接入时必须保持：

```text
kernel_authority_preserved
q_conservation_preserved
operator_authority_preserved
payload_ref_only_preserved
void_guard_preserved
trace_weak_boundary_preserved
wir_verifier_not_bypassed
```

Runtime 可以调度、读取、缓存、索引、恢复，但不能把 kernel 降格成普通数据源，也不能绕过 WIR verifier 直接沉淀稳定结构。

## 13. 最小长期判断

微澜的底层原理不是：

```text
输入 → 记忆 → 语义 → 输出
```

而是：

```text
差异承载
→ 守恒竞争
→ 主导形成
→ 压力积累
→ 离散崩溃
→ 痕迹保留
→ 分散重组
→ 再竞争
→ 高层读出
→ 反馈再进入
```

这条链是 kernel、WIR、memory、semantic、language、output、feedback 后续全部工作的共同底座。
