# LLM成熟库利用

## 核心思想

成熟库可以让我们不用自己从零构造"语言编解码器"，但不能让我们省掉微澜自己的"接入协议"。区别很关键：

- **成熟库负责**：把语言变成一堆高质量观测数据
- **微澜负责**：把这些观测数据变成 WIR object、carrier、kernel trace、semantic deposition proposal

也就是说，我们不用自己造 tokenizer、embedding、parser、retriever，但我们必须自己定义：

1. 这些库输出的数据，在微澜里算什么？
2. 能进哪一层？
3. 能影响 q / support / pressure 的哪一项？
4. 不能直接生成什么？
5. 如何被 WIR 记录？
6. 如何被 kernel 消化？

> 成熟库负责切菜量温度，微澜负责下锅起火。

---

## 成熟库提供的数据类型

### 第一类：token / span / offset 数据（最安全，最适合先接）

比如 Hugging Face Tokenizers 的 Encoding 会给出 token ids、token 字符串、attention mask、offsets 等；offsets 可以把 token 映射回原始输入字符串的字符范围。

**这些数据在微澜里应该这样用：**

| 库输出 | 微澜用途 | 说明 |
|--------|---------|------|
| token id | observation evidence | 不是语义 |
| token string | payload-ref / span-ref | 不直接下沉 |
| offset | lineage ref | 证明这个 carrier 来自原文哪里 |
| attention_mask | input validity / padding mask | - |
| sequence_id | 多句输入时的来源分段 | - |
| special_tokens | 结构标记 | 不是语义身份 |

这类数据非常适合做 LanguageObservationBundle 的第一版。它不会太聪明，不会提前把"话题、谓词、意图、语义"替我们判断完。

---

### 第二类：embedding / vector 数据

比如 OpenAI embeddings 把 embedding 定义为浮点数向量，并说明两个向量之间的距离可用来衡量 relatedness；小距离表示高相关，大距离表示低相关。OpenAI 也提到 embeddings 可用于 clustering，并建议相似度计算中使用 cosine similarity；其 embeddings 归一化到长度 1，因此 cosine similarity 可用 dot product 更快计算。Sentence Transformers 的 encode() 也可以输出 sentence embedding 或 token embedding，结果可为 numpy array 或 tensor。

**这些数据在微澜里不能直接变成语义。它们应该这样用：**

| 库输出 | 微澜用途 | 说明 |
|--------|---------|------|
| embedding vector | vector_evidence_ref | - |
| cosine similarity | field_match / bounded evidence | - |
| nearest neighbors | candidate pressure | 不是 binding |
| cluster id | observation grouping | 不是 semantic attractor |
| sentence embedding | frame-level evidence | - |
| token embedding | span-level evidence | - |

也就是说，embedding 可以说："这几个片段在外部向量空间里接近。"
但**不能**说："它们就是同一个语义。"

在微澜里，**embedding 最多是风向标，不是判官**。

---

### 第三类：transformer 内部输出

Hugging Face Transformers 的模型输出通常是 ModelOutput，可以包含 logits、hidden_states、attentions 等；比如基础模型输出里，last_hidden_state 是最后一层每个 token 的 hidden state，hidden_states 是各层 hidden state，attentions 是注意力权重。

这些数据很有价值，但更危险，因为它们看起来很"像理解"。**我建议微澜这样用：**

| 库输出 | 微澜用途 | 说明 |
|--------|---------|------|
| logits | uncertainty / pressure signal | - |
| hidden_states | latent evidence | 不直接作为语义 |
| attention weights | relation candidate / focus candidate | - |
| layer variance | instability / pressure | - |
| token confidence | q seed modifier 或 pressure modifier | 必须 capped |

比如一句话里某些 token 的分布很不稳定，可以给这些 span 更高 pressure。某些 attention 连接很强，可以生成 relation candidate。但这些仍然只是 candidate。

---

### 第四类：语言学结构数据

spaCy 这类库会给 token text、lemma、POS、tag、dependency、shape、是否字母、是否停用词等；文档里还说明 dependency 是 token 之间的 syntactic dependency，lemma 是词元基本形式，POS/tag 是词性信息。spaCy 也提醒：模型训练时的 tokenization 和运行时 tokenization 不一致时，预测结果可能显著变化；它还支持把预分词文本构造成 Doc。

这类数据对微澜有用，但**不能最先接**。因为它已经带了很多人类语言学预设。可以作为第二阶段或第三阶段的 observation channel：

| 库输出 | 微澜用途 | 说明 |
|--------|---------|------|
| lemma | normalization candidate | - |
| POS | syntactic evidence | - |
| dependency | relation candidate | - |
| entity span | object candidate | - |
| sentence split | frame boundary candidate | - |
| noun chunk | span grouping candidate | - |

但要注意：dependency = nsubj **不能直接等于**"主语语义"；entity = ORG **不能直接成为** stable object。它们只是外部 parser 的观察，不是微澜内部语义。

---

### 第五类：retrieval / reranker 数据

Sentence Transformers 文档里有 semantic search、retrieve & re-rank、bi-encoder retrieval、cross-encoder re-ranker、clustering、paraphrase mining 等工具链。

这些很适合未来接语料库，但也最容易让系统退化成"检索系统"。**它们应该这样用：**

| 库输出 | 微澜用途 | 说明 |
|--------|---------|------|
| retrieved_doc_id | corpus_candidate_ref | - |
| retrieval_score | bounded pressure | - |
| reranker_score | evidence_strength | - |
| top_k list | candidate set | - |
| paraphrase pair | perturbation pair | - |
| cluster assignment | candidate family | not semantic family |

**检索结果不能直接进入 memory。**检索结果只能说："这些语料值得被微澜看一眼。"
看完以后，还是要进 carrier、kernel、trace、regroup。

---

## 怎么利用？四层接入策略

我建议把成熟库分成四层用。

### 1. 编码层：先用 tokenizer

**第一阶段只接 tokenizer / offset。不要急着接 embedding。**

原因是 tokenizer 给的是最机械的数据，最不容易偷渡语义。

**输入：**
```
原句：语言应该先编码成载体，再投入微澜内核。
```

**库输出：**
- token ids
- tokens
- offsets
- attention mask
- special token mask

**微澜生成：**
- LanguagePayloadRef
- TokenSpanEvidence
- OffsetLineage
- SpanCarrierCandidate
- AdjacencyRelationCandidate

这一步的目标是让"原句"变成可追踪的结构碎片。

---

### 2. 观测层：再接 embedding

**第二阶段接 embedding。**

embedding 进入微澜时，不进 semantic binding，而是进：
- VectorEvidence
- field_match
- similarity_pressure
- candidate_group_pressure

比如两个 span embedding 很近，只能产生：
```
these two carriers may compete / co-pressure / be tested together
```

**不能**产生：
```
these two are same semantic identity
```

---

### 3. 扰动层：接 paraphrase / retrieval

**第三阶段用 LLM 或 Sentence Transformers 生成 paraphrase、相似句、近邻语料。**

这非常适合验证微澜理论。比如：

> 自然语言不能直接进入内核。
> 语言要先编码成载体。
> 文本应该先转为结构再参与动力学。

成熟库可以告诉我们它们相似。
但微澜要验证的是：**这些句子转成 carrier 后，经过 kernel competition/collapse/regroup，是否反复形成兼容 trace。**

这一步才是理论验钞机。

---

### 4. 语义层：最后才做 deposition

**真正的 semantic binding 不能由库直接给。**它只能来自：

- kernel trace
- collapse event
- regroup event
- q conservation proof
- source lineage
- multiple compatible runs

所以最终路径是：

```
库输出 observation
-> WIR object
-> carrier
-> kernel run
-> trace evidence
-> semantic deposition proposal
-> memory candidate
```

---

## 数据结构设计

核心不是接某个库，而是定义统一的 ObservationBundle。比如：

```python
@dataclass(frozen=True)
class LanguageObservationBundle:
    bundle_id: str
    provider_name: str
    provider_version: str
    source_payload_ref: str
    raw_text_hash: str

    token_ids_ref: str
    tokens_ref: str
    offsets_ref: str
    attention_mask_ref: str | None = None

    embedding_refs: tuple[str, ...] = ()
    similarity_edges_ref: str | None = None
    logits_ref: str | None = None
    hidden_state_refs: tuple[str, ...] = ()
    attention_refs: tuple[str, ...] = ()

    parser_evidence_refs: tuple[str, ...] = ()
    retrieval_candidate_refs: tuple[str, ...] = ()

    semantic_binding_created: bool = False
    semantic_attractor_created: bool = False
    memory_write_performed: bool = False
    runtime_decision_created: bool = False
```

注意这里所有大对象都用 `*_ref`。
**不要把 raw text、embedding 大数组、parser label 直接塞进核心状态。**

然后 WIR 里变成：

```
WirObject: LanguageObservationBundleRef
WirObject: TokenSpanEvidenceRef
WirObject: VectorEvidenceRef
WirObject: ParserEvidenceRef
WirObject: RetrievalCandidateRef

WirRelation: token_span_from_payload
WirRelation: vector_evidence_for_span
WirRelation: retrieval_candidate_for_payload
WirRelation: parser_relation_candidate_for_span
```

再由 carrier builder 变成：

```python
Carrier(
  carrier_type="language_span_carrier",
  q=normalized_initial_q,
  source_ref="token_span_ref:...",
  signature=(span_hash, offset_range, token_shape, provider_trace),
  relations=(adjacent_to, contains, co_occurs_with...)
)
```

---

## 接入优先级

我会按这个顺序接：

1. **第一优先：token + offset**
   - 这是最干净的
   - 它提供来源、切片、位置、长度、邻接、包含
   - 它让语言可以变成 carrier，但还没偷渡语义

2. **第二优先：embedding similarity**
   - 它提供候选 pressure 和 field_match
   - 用它能快速测试多句相似表达是否会在 kernel 后形成兼容 trace

3. **第三优先：retrieval top-k**
   - 它让语料库可以进来，但只作为候选扰动
   - 先别接大规模，先接很小的 controlled corpus

4. **第四优先：logits / uncertainty**
   - 它适合做 pressure、fatigue、ambiguity
   - 不适合做语义身份

5. **第五优先：parser / POS / dependency**
   - 有用，但要小心
   - 它会提前注入语言学解释，所以最好作为辅助证据，不作为第一入口

---

## 一句话答案

是的，成熟库可以帮我们省掉大量编解码工作。
但它们提供的不是"语义成品"，而是**"观测材料"**。

微澜要做的是：

- ✅ 不自造 tokenizer，但自造 ingress contract
- ✅ 不自造 embedding 模型，但自造 vector evidence guard
- ✅ 不自造 parser，但自造 relation candidate filter
- ✅ 不自造 retrieval，但自造 corpus candidate admission
- ❌ **不让库生成语义，只让 kernel 沉淀语义**

我会建议第一个可实现版本只接：

```
Hugging Face tokenizer
+ SentenceTransformer embedding
+ 一个 mock / small corpus retrieval
```

然后把所有输出统一包装成 LanguageObservationBundle。
先不接复杂 parser，不接自由 LLM summary，不接大规模 RAG。**先让"库数据 -> WIR -> carrier -> kernel"这条主链跑通。**

---

## 更新历史

- 2026-06-05: 创建文档，记录成熟库利用策略与四层接入方案
- 2026-06-10: 新增 Current Practical Use Boundary，明确 20.5-20.7 语义验证分支计划

---

## 20.4 pre-20.5 前置条件

Embedding adapter must not be connected directly after 20.close. Before 20.5, WeiLan needs:

- real occurrence/type carriers
- provisional type identity chain
- symbolic split boundary
- multi-step trajectory bundle
- WIR DynamicEdgeEvidence
- semantic role candidate competition

原因：
Embedding/similarity must enter field_match or bounded pressure on real occurrence/type carriers. If carriers are still structural slots such as language_sensation or token_span_evidence, embedding signal is semantically meaningless and may reinforce coactivation blur.

---

## Current Practical Use Boundary

成熟 LLM 库作为外部语义工具，在微澜中的实际使用边界：

**第一验证库：** sentence-transformers
**第一验证模型：** sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
**第一验证任务：** 30 到 100 条短文本，验证 embedding / similarity / top-k signal

**职责分工：**
- 外部库负责：提供 embedding vectors、cosine similarity scores、top-k candidate lists
- 微澜负责：semantic candidate、pressure、deposition、source trace、competition、decision boundary 和 feedback

**关键约束：**
- 外部库不作为微澜内核
- 外部库只提供信号，不创造语义身份
- 所有 semantic deposition 必须来自微澜 kernel trace
- 外部 embedding 只能作为 bounded evidence，不能作为 semantic authority
- 必须先通过 20.5 adapter boundary、20.6 small corpus validation、20.7 semantic deposition audit，才能进入 21.x Decision Merge Candidate

**验证路线：**
```
20.close (verified)
-> 20.5 Mature LLM Library Adapter Boundary (planned)
-> 20.6 Small Semantic Corpus Validation (planned)
-> 20.7 Semantic Deposition Audit (planned)
-> 21.x Decision Merge Candidate (planned, after validation)
```
