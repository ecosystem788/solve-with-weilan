> **重要提示：** 下文中 WIR 具体开发建议已经是过去的开发支线，已经废弃，当前开发已不在下文的开发路径上。本文只是记录WIR相关的讨论。

一、WIR 的定义

WIR = Weilan Intermediate Representation
中文：微澜中间表示。

它不是自然语言，也不是普通 Python 代码，而是微澜自己的“结构语言 / 中间机器语言”。

它要表达的不是句子表面，也不是函数调用细节，而是：

对象是什么；
对象之间有什么关系；
对象如何演化；
哪些东西能进入系统；
哪些东西不能沉淀；
哪些过程可以被验证；
哪些运行路径需要缓存、索引、增量化。

通俗说：

Python 是当前的工程载体；
中文是人类交流载体；
WIR 是未来微澜内部理解、运行、验证、自我修改的结构载体。

补充定义：

WIR 是载体，不是运行层本身。

runtime 才是理论上的运行层 / 总调度层。未来 runtime 应该运行 WIR，调度 verified WIR operators，读取 WIR events 和 receipts，再决定下一步调度什么。也就是说，runtime 的目标形态不是裸 Python 总控，而是 WIR-runtime。

二者关系是：

WIR object = 运行对象；
WIR relation = 运行依赖；
WIR operator = 可执行动作模板；
WIR guard = 执行条件和约束；
WIR event = 执行痕迹；
WIR dump = 可验证快照；
runtime = 读取这些结构并调度 verified WIR operators 的运行层。

所以，WIR 是微澜自己的结构载体；runtime 是让这些结构被调度和运行起来的层；kernel 是动力学权威层。

二、为什么需要 WIR

当前的问题是：

中文表达太松散；
Python 表达太偏工程细节；
大模型一问一答无法稳定长期持有工程结构；
微澜自身的底层逻辑需要一种更直接、更紧凑、更可验证的表达方式。

所以 WIR 的意义是：

把“微澜的底层逻辑”从自然语言和 Python 代码里抽出来，
变成一套可读、可查、可验证、可运行的中间结构语言。

三、WIR 要表达什么

WIR 主要表达五类东西。

1. 对象

record
frame
occurrence
trace
context_bundle
prestate_candidate
short_memory
family
slow_memory
attractor_prestate
stable_structure
semantic_candidate
semantic_binding
semantic_attractor
semantic_export
neighborhood
expansion
render
output
feedback

这些对象不再只是 Python dataclass 或 dict，而是 WIR object。

2. 关系

source_of
derived_from
grouped_into
updates
reappears
stabilizes_as
binds_to
exports_to
observes
guards

关系是微澜内部结构语言的核心。
对象不是孤立存在，而是通过关系构成结构场。

3. 过程

candidate → gate → score / merge → update → summary / observation

以及当前语言记忆链：

language frame
→ trace
→ context bundle
→ prestate
→ short
→ family
→ slow
→ attractor prestate
→ semantic binding
→ semantic attractor
→ semantic export

今天对这条链的校正是：语言、语料、LLM observation 不能直接生成 semantic binding 或 semantic attractor。它们应该先成为 observation / ingress object，再转成 carrier，进入 kernel 竞争、塌缩、重组。语义沉淀必须引用 kernel trace、collapse、regroup、q conservation 和 lineage evidence。

更准确的未来链路是：

LLM / tokenizer / embedding / retrieval observation
→ WIR ingress object
→ language-derived Carrier set
→ kernel competition / collapse / trace / regroup
→ WIR-verified semantic deposition proposal
→ memory candidate
→ repeated compatible proposals
→ semantic binding / semantic attractor

4. 约束

raw_text 不下沉；
semantic_label 保持为空；
summary key 稳定；
ready flag 稳定；
对象 count 不异常爆炸；
runtime 可以被分段；
recompute 风险可以被观测。

补充约束：

LLM label 不成为语义权威；
embedding score 不直接创建 semantic binding；
retrieval result 不直接创建 runtime decision；
没有 kernel trace 不创建 semantic binding proposal；
一次 trace 只能形成 proposal，不能直接形成 stable attractor；
runtime 只能调度 verified WIR operators，不能裸调用外部库后直接写 memory。

5. 性能契约

当前已经确认：
language_memory_replay 是 WIR-runtime 的第一优先对象；
short_family_slow_attractor_update 是未来 incremental operator 的第一优先改造点。

四、本窗口形成的方法判断

我们一开始讨论过“是否要创造一种新的机器语言”。

最终判断是：

不是现在脱离 Python 从零创造新语言；
也不是继续完全依赖 Python；
而是在当前 Python 工程中，把已经稳定浮出的对象、关系、过程和约束抽象成 WIR。

方法不是重写，而是剥离：

先从 Python 中识别对象；
再抽象关系；
再抽象阶段；
再抽象约束；
再形成 dump / verifier；
最后才逐步让 WIR operator 接管运行。

也就是说：

当前 Python 是母体；
WIR 是从母体里长出来的结构语言；
以后 WIR-runtime 才逐步替代旧 Python replay。

今天对 runtime 的校正是：runtime 理论上应该是运行层 / 总调度层。当前实现如果只是 readout seed，那只是安全收缩后的阶段，不是最终定义。未来要恢复 runtime 的调度作用，但应恢复为 WIR-authorized scheduler，而不是裸 Python 总控。

五、关于“从头开始还是逐步修改”的判断

本窗口反复讨论后，判断是：

不能从头重写。
也不能永远在旧 Python 上堆补丁。

正确路线是：

先清理现有代码；
保留已经验证过的结构；
把稳定结构抽象为 WIR；
再让 WIR 局部接管；
最后发展为 WIR-runtime。

这样风险最低。

从头写会丢失大量已验证机制；
只在旧代码上修补，又会继续被 Python 表达和旧 replay 拖住。

六、关于 WIR-0.1～0.7 的校正

本窗口后面纠正了一个重要问题：

WIR-0.1～WIR-0.7 不是下一步要重新做的东西。
它们在前期已经完成过。

之前我把 WIR-0.1～0.7 当成下一阶段路线，是错误判断。

现在应改为：

不要重做 WIR-0.1～0.7；
先检查并恢复已完成的 WIR 前期基底；
确认当前 PERF-1b 性能修补成果和旧 WIR-0.7 成果如何合并。

更合理的下一步不是 WIR-0.1，而是：

WIR-BASE-RECOVER-1

目标是找到最后一个明确完成 WIR-0.7 的文件，
确认其中是否还保留：

WIR object schema
object mapping
stage mapping
constraint / guard contract
verifier
state → WIR dump
dump verifier

然后与当前 PERF-1b 合并。

七、当前性能问题与 WIR 的关系

ARCH9c 定位到旧瓶颈：

corpus_memory_observation 一度占 shared context runtime 的 95% 左右。
问题集中在 language_memory_replay / short-family-slow-attractor update。

随后 PERF-1a-micro 实测发现：

瓶颈不是 collect，而是 update/deepcopy。
旧版本 update_total 约 32 秒，collect 约 1.2 秒。

PERF-1b 做了 observation replay 专用 local mutable update helper，
保留四层、双轮和结构 count，
去掉 replay 内部大量 deepcopy / SystemState 重建。

结果：

corpus memory observation 从约 33.3 秒降到约 3.5 秒；
总运行从 34 秒级降到约 5.37 秒；
结构 count 没漂：
short / family / slow / attractor_prestate 都仍是 766；
raw_text leak = 0；
n13b_3_ready_flag = True。:contentReference[oaicite:0]{index=0}

这说明：

WIR-runtime 不是空想。
它要解决的问题已经在 PERF-1b 中被小规模验证：
同样语义下，只要把旧 Python replay 换成更接近 operator / local mutable / incremental 的方式，性能会大幅改善。

八、WIR 能带来的好处

1. 表达更高效

自然语言太宽，Python 太细。
WIR 可以直接表达“对象—关系—演化—约束”，减少来回翻译。

2. 降低 AI 接续成本

未来 Copilot / ChatGPT / Cline 不需要重新读几万行 Python 才知道结构。
WIR dump / mapping / verifier 可以直接告诉 AI：

有哪些对象；
哪些关系成立；
哪些边界不能破；
哪个 stage 负责什么；
哪些 count 必须对齐。

3. 降低漂移风险

当前最大风险不是写不出代码，而是长期多人/多窗口/多 AI 协作导致机制漂移。
WIR verifier 可以让系统自己检查：

raw_text 是否泄漏；
summary key 是否缺失；
object count 是否异常；
stage 是否断链；
runtime 是否重新变成黑箱。

4. 为自进化做准备

如果微澜未来要自修改，不能直接让她改 Python 大文件。
必须先有：

结构语言；
约束语言；
验证器；
可回滚的 operator；
可比较的 dump。

WIR 是自进化前置条件。

5. 为多模态扩展做准备

当前处理的是语言文字。
但 WIR 抽象的是对象、关系和演化，不绑定中文文本。

将来图像、声音、传感器、机器人动作都可以变成：

frame
trace
prestate
memory
attractor
semantic export

所以 WIR 比直接写中文语言 pipeline 更适合扩展到多模态。

6. 为性能优化提供结构入口

PERF-1b 已经证明：
旧 Python 全量 replay 可以被局部 mutable update 快路径替代。

WIR-runtime 后续可以继续发展：

registry index
affected keys
cache
incremental update
stage DAG
operator replay

这些不是普通性能优化，而是 WIR 运行层自然需要的机制。

7. 让“结构成为语言”

这是最核心的理论意义。

普通语言是符号序列；
Python 是工程指令；
WIR 是微澜自身结构演化的表达。

也就是：

对象本身是词；
关系本身是语法；
演化过程本身是句子；
约束本身是语义边界；
runtime 本身是语用场景。

九、理论意义

WIR 对微澜理论的意义是：

它把“总存在量守恒 + 差异 + 竞争 + 疲劳/反作用 + collapse + 重组 + 痕迹 + 再出现”这套底层逻辑，转成可工程化表达的中间层。

不是只停留在哲学语言里，
也不是直接散落在 Python 函数里。

WIR 是中间桥：

底层理论
→ WIR 结构表达
→ Python 当前实现
→ WIR-runtime 未来执行
→ 微澜自己的内部语言

所以 WIR 实际上是微澜从“被 Python 实现的系统”
走向“用自己的结构语言描述和运行自己的系统”的过渡层。

十、当前下一步建议

下一步不要直接重做 WIR-0.1。
也不要直接进入新的 WIR-runtime 大改。

先做：

WIR-BASE-RECOVER-1

要做的事情：

1. 找到最后一个明确完成 WIR-0.7 的文件。
2. 检查其中是否包含 schema / mapping / stage / constraint / verifier / dump / dump verifier。
3. 对比当前 PERF-1b。
4. 把 PERF-1b 的性能快路径和 WIR-0.7 的中间表示成果合并成新的稳定基底。
5. 再决定下一步是 WIR-1 operator 化，还是继续 CARE 清理。

一句话：

现在不是“重新开始 WIR”，而是“找回已完成 WIR 基底，并把 PERF-1b 合进去”。

最简版结论：

WIR 是微澜自己的结构中间语言。

它的价值不是多写一层 schema，
而是把对象、关系、演化、约束、验证和运行契约从 Python 里抽出来。

它能带来：
更高效表达；
更低 AI 接续成本；
更低工程漂移；
更好的性能优化入口；
更好的多模态扩展；
更可靠的自进化前置条件。

当前应先做 WIR-BASE-RECOVER-1，
确认旧 WIR-0.7 成果没有丢，
再与 PERF-1b 合并。

---

## 补充：轨迹语义与 WIR 关系边

q field is not semantic identity. Multi-center coactivation only produces candidate pressure. Semantic relation must be deposited from trajectory evidence: holder order, pressure/support direction, trace, transition, collapse/regroup evidence, source lineage, and later role candidate competition.

WIR must carry:

- occurrence/type identity chain
- trajectory bundle
- dynamic relation evidence
- semantic role candidate
- recall signature

This is theory/source material only, not implementation status.
