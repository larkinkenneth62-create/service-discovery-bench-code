# ServiceDiscoveryBench v0.2.0 Retriever 与 LLM 试验执行协议

- 文档版本：`V1.2-FROZEN-ROUTE-QWEN-SSE-NATIVE-MACHINE-FIRST`
- 日期：2026-08-25
- 状态：`APPROVED_EXECUTION_ROUTE_SSE_RESTORED_RETRIEVER_V2_FROZEN`
- 数据基线：`ServiceDiscoveryBench-v0.2.0-composable-expansion-docfix1.zip`
- 数据 release SHA-256：`a199562a898fc0e3ec00563205bc0d739f2e3f592ef25878e085bca55082751c`
- 第一正式模型：`Qwen3.6-35B-A3B-APEX-I-Compact.gguf`
- 当前公网 Base URL：由私有运行环境变量提供；公开镜像不保留历史临时地址。
- 正式长请求协议：`OpenAI-compatible Chat Completions + SSE stream=true`
- 正式 Retriever：`BGE_DENSE_V2@200`
- 适用范围：Qwen Native、Machine Challenge、后置 Unified LLM，以及论文中的 Retriever V2 结果
- 不适用范围：继续开发 Retriever V3、重新构造数据、修改 Query/Gold/split、重新选择 Retriever/K、公开发布授权

---

## 0. 本次修订的核心结论

本文件取代 `SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_1_QWEN_LOCAL_FIRST_FROZEN.md`，但保留其数据治理、Dev/Test 隔离、Gold 隔离和三轨定义。

本次只固化四项已经达成的共识：

1. **Retriever 到 V2 即停止。** `BGE_DENSE_V2@200` 已经在 4,827 条 Dev 上完成选型，并对 4,798 条 Test 只运行一次；当前不开发 Cross-Encoder、Query decomposition、监督训练或 Retriever V3。
2. **Native 与 Machine 是 LLM 主实验。** 两者不依赖 Unified Retriever，必须优先完整运行；它们用于评价模型在给定候选条件下的排序、集合选择和抗干扰能力。
3. **Unified 是后置系统实验。** 它使用冻结的 `BGE_DENSE_V2@200`，用于评价“大目录检索 + LLM”的端到端系统；它不能取代 Native，也不阻塞 Native/Machine 主结果。
4. **Qwen 长请求通道已经修复。** 正式请求必须实际设置 `stream=true`，利用 15 秒 SSE 心跳跨过 Cloudflare 无数据读取超时；此前的短候选子集结果、Compact Alias 结果和非标准 serializer 结果只保留为历史诊断，不进入正式主表。

---

## 1. 当前冻结事实

### 1.1 数据规模

| 项目 | 数量 |
|---|---:|
| 总任务 | 60,240 |
| Train | 50,615 |
| Dev | 4,827 |
| Test | 4,798 |
| Single Service | 19,560 |
| Single API | 38,573 |
| Multi Service | 879 |
| Multi API | 879 |
| Composable Service | 223 |
| Composable API | 126 |

正式 Test 中包括完整六任务；Composable Service 与 Composable API 各 25 条。小样本分层必须同时报告百分比与原始计数。

### 1.2 Retriever V2

```text
selected_retriever = BGE_DENSE_V2
selected_k = 200
model = BAAI/bge-small-en-v1.5
revision = 5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
dev_rows = 4,827
test_rows = 4,798
test_run_count = 1
```

正式 Test reference-Gold 覆盖状态：

```text
ALL_GOLD_RETRIEVED     = 3,356
PARTIAL_GOLD_RETRIEVED =   771
ZERO_GOLD_RETRIEVED    =   671
```

Retriever V2 的用途是：

- 论文中的 lexical / dense / hybrid 检索对比；
- Unified 轨道的固定候选来源；
- Retriever-only coverage 与 failure 分析。

它不是新的方法贡献，也不代表 Top-200 是生产系统中理想的最终候选预算。

### 1.3 旧 Qwen 结果的地位

以下结果只作工程诊断，不得与新正式结果拼接：

- `candidate_count <= 10` 的 3,173 条 Native 子集；
- Compact-Alias Machine 197 条；
- V3/V4/V5 serializer、hierarchical ranking 或混合 revision；
- 因 HTTP 524 未完成的请求。

正式 Qwen 结果必须在本协议下从完整 manifest 重新运行。

---

## 2. 三条轨道的正式定位

### 2.1 Native：主要 LLM 能力轨

```text
Query + 数据集原生候选池
→ Qwen 排序 / 集合选择
→ 与冻结 Gold 比较
```

Native 测量：

- 候选已提供时的语义理解；
- Single 任务的首选判断；
- Multi/Composable 的完整集合选择；
- 漏选、过选和候选规模敏感性。

Native 不使用 Unified Retriever。它是 bounded candidate selection，而不是开放目录检索。

### 2.2 Machine Challenge：主要鲁棒性轨

```text
Query + 10 个高相似干扰候选
→ Qwen 排序
→ 与 Native 同题或对应 reference 结果比较
```

Machine 测量名称、功能、provider、version、operation 等细粒度混淆。正式轨只使用冻结的标准 manifest 和标准 Prompt；旧 Compact-Alias 结果不作为主结果。

### 2.3 Unified：后置端到端系统轨

```text
Query
→ 冻结 BGE_DENSE_V2 Top-200
→ Qwen ranking-only
→ Retriever-only / Conditional / Full End-to-End
```

Unified 测量完整搜索系统，不单独代表 LLM 能力。它在 Native/Machine 完成后运行；即使 Unified 因资源耗时尚未完成，Native/Machine 和 Retriever-only 仍可形成论文主结果。

---

## 3. 冻结的 Qwen SSE 运行合同

### 3.1 服务身份

```text
Base URL:
<owner-authorized-private-endpoint>

Model:
Qwen3.6-35B-A3B-APEX-I-Compact.gguf

Authentication:
Bearer Token；密钥只从环境变量读取
```

推荐环境变量：

```text
SDB_QWEN_BASE_URL
SDB_QWEN_MODEL
SDB_QWEN_API_KEY_01
SDB_QWEN_API_KEY_02
SDB_QWEN_API_KEY_03
SDB_QWEN_API_KEY_04
```

任何 key 不得写入 Markdown、JSON registry、日志、raw request、结果 ZIP 或错误报告。

### 3.2 正式请求必须流式

所有正式 Native、Machine 和 Unified 请求统一设置：

```json
{
  "model": "Qwen3.6-35B-A3B-APEX-I-Compact.gguf",
  "messages": ["..."],
  "stream": true,
  "temperature": 0,
  "top_p": 1,
  "seed": 0
}
```

服务端当前事实：

```text
heartbeat_interval = 15 seconds
maximum_single_inference = 7200 seconds
non_streaming = still supported for short preflight only
```

正式 runner 必须：

1. 识别并忽略 heartbeat 事件，不把 heartbeat 写入模型答案；
2. 逐块拼接 `content`，将 `reasoning_content` 单独保存但不作为答案解析；
3. 收到标准终止事件和 `[DONE]` 后才将请求标为完成；
4. 保存 raw SSE event log、规范化 final response、parsed prediction 和请求状态；
5. 发现 SSE error event、连接中断或超过 7,500 秒总墙钟时间时，记录基础设施失败。

### 3.3 客户端超时与并发

固定客户端设置：

```text
connect_timeout = 30 seconds
SSE_read_timeout = 45 seconds
maximum_wall_time = 7,500 seconds
formal_global_concurrency = min(4, available_api_key_count)
per_key_inflight = 1
```

并发只影响吞吐，不改变实验方法。不得因模型准确率调并发。若某 key 缺失，则总并发按实际可用 key 数确定，但不超过 4。

### 3.4 解码与输出预算

```text
temperature = 0
top_p = 1
n = 1
seed = 0（接口支持时）
不要求输出 Chain-of-Thought
```

正式运行前，使用实际 tokenizer 对现有 manifest 做一次确定性输入/输出预算计算，并冻结三个 track 的 `max_tokens`。规则仅为：覆盖该轨全部合法 JSON 排名的最坏长度并留 10% 余量；不得根据准确率改变。若服务器输出上限不足以容纳标准 Schema，则该轨阻断，不能用省略候选、自动补全或修改答案代替。

---

## 4. Prompt、Schema 与 parser

### 4.1 共用输入边界

模型只能看到：

- Query；
- task type；
- prediction target；
- 当前轨道的有序候选 ID；
- 对应 model-visible candidate documents；
- 输出格式要求。

模型不得看到：

- Gold ID 或 Gold 数量；
- acceptable Gold sets；
- retrieval status；
- source path；
-人工 QA、split 决策或 identity evidence。

### 4.2 Native 输出

- `Single Service`：完整 `ranked_candidate_ids`；
- 其他 Native 任务：完整 `ranked_candidate_ids` + `selected_candidate_ids`。

`ranked_candidate_ids` 必须是当前候选的完整 permutation：每个候选恰好一次，不缺失、不重复、不出现池外 ID。

### 4.3 Machine 和 Unified 输出

统一使用 ranking-only：

```json
{
  "ranked_candidate_ids": ["全部当前候选 ID 的完整排列"]
}
```

不得要求 `selected_candidate_ids`。Unified 的 non-Gold 未穷尽裁决，因此不能用 selected-set Precision/F1 作为主指标。

### 4.4 严格解析

parser 不得：

- 自动去重；
- 自动删除池外 ID；
- 自动补齐遗漏 ID；
- 从 prose 中猜测答案；
- 将截断 JSON 修成合法结果。

格式错误属于模型结果。只有网络、SSE、5xx、429 等基础设施错误可以重试。

---

## 5. 正式执行顺序

### 5.1 Gate Q0：SSE preflight

执行：

```text
GET /v1/models
→ 确认 served model
→ 1 条短 stream=true 请求
→ 验证 heartbeat、内容拼接、[DONE]、鉴权和 raw log
```

该步骤只确认运行通道，不评价准确率。

### 5.2 Gate Q1：固定 60 条 Dev smoke

继续使用已经冻结的 60 条分层 Dev 集合，六任务各 10 条，并覆盖最长 Native 请求、最大候选数、ranking-only、ranking+selected-set 和 Composable。

全部 60 条必须取得终止状态。Smoke 只用于确认：

- SSE 长连接可持续；
- 请求不超上下文；
- 输出预算足够；
- parser 能记录合法与非法响应；
- 四 key 并发可稳定运行；
- 断点续跑和缓存键正确。

模型答错不构成 smoke 阻断；普遍截断、上下文溢出、SSE 协议错误或无法保存 raw output 才构成阻断。

### 5.3 Gate Q2：完整 Native + Machine

Q1 通过后立即启动：

```text
Native  = 4,798
Machine =   197
```

两轨从头运行，不读取旧 partial predictions 作为正式缓存。Machine 优先占用 1 个 slot，完成后所有可用 slot 转给 Native。

### 5.4 Gate Q3：主结果评分

Native 与 Machine 完成后，立即生成 Qwen 第一模型主结果包，不等待 Unified：

- raw / parsed / failures；
- 六任务 Native；
- Machine 结果和 matched delta；
- tokens、latency、吞吐、retry、parse failure；
- candidate-count 与 Gold-count 分桶；
-错误分析清单。

### 5.5 Gate Q4：后置 Unified

主结果包通过后，使用已冻结的：

```text
BGE_DENSE_V2@200
4,798 Unified requests
ranking-only Schema
stream=true
```

进行完整 Unified LLM。不得抽样结果冒充完整主结果。由于每题 200 候选，Unified 可作为单独长运行，不阻塞 Native/Machine 的论文主表与第二模型启动。

---

## 6. 正式指标

### 6.1 Native

所有任务：

```text
Hit@1
MRR
Recall@3 / @5
nDCG@3 / @5
Parse Failure Rate
```

需要 selected set 的任务：

```text
Exact Set Match
Macro Precision / Recall / F1
Completeness
Jaccard
Under-selection
Over-selection
Cardinality Error
```

论文主结论必须同时报告：

- 六任务分别结果；
- 六任务等权 Macro-6；
- 样本加权 Micro overall；
- candidate-count 分桶；
- Gold-count 分桶。

Native 中，MRR 只能作为排序辅助指标；Multi/Composable 的主要指标是 Exact Set Match、F1 和 Completeness。

### 6.2 Machine Challenge

```text
Hit@1
MRR
Recall@5
nDCG@5
Parse Failure Rate
```

并报告相对于对应 Native 任务的：

```text
ΔHit@1
ΔMRR
ΔnDCG@5
```

### 6.3 Unified

必须拆成三层：

1. `Retriever-only`：Recall、Completeness、MRR、nDCG、All/Partial/Zero；
2. `Conditional LLM`：仅 `ALL_GOLD_RETRIEVED` 子集；
3. `Full End-to-End`：完整 4,798，Retriever miss 计作系统失败。

Conditional 不能替代 Full End-to-End，也不能用于给 LLM 单独排名。

### 6.4 工程效率

每个 track 保存：

```text
input/output tokens
end-to-end latency P50/P95
first-data latency
heartbeat count
requests per second
retry rate
HTTP/SSE error rate
parse failure rate
candidate-ID error rate
context/output overflow rate
```

---

## 7. 重试与失败规则

允许重试：

- connect failure；
- SSE 连接中断；
- HTTP 429；
- HTTP 5xx / 524；
- 服务端明确 error event；
- 15 秒心跳机制异常导致 45 秒无任何事件。

每个请求最多 3 次基础设施重试，固定退避 `15s → 30s → 60s`。同一请求重试时 Prompt、候选顺序、Schema 和解码参数不得改变。

不允许重试：

- 模型选错；
- JSON 不合法；
- ID 重复、遗漏或池外；
- selected set 错误；
- 模型主动提前结束但未完成完整排名。

这些计入模型失败。

---

## 8. 第二、第三模型

Qwen Native + Machine 正式结果包通过后，第二模型可立即使用相同 manifest、Prompt 语义、Schema 和评分代码运行 Native + Machine，不等待 Qwen Unified。

最终论文最低配置：

```text
M_LOCAL：Qwen3.6 35B，Native + Machine 必做；Unified 后置
M_OPEN：至少 Native + Machine
M_FRONTIER：预算允许时至少 Native + Machine
Retriever：BM25 / BGE Dense / RRF 的既有结果
```

若某第二模型具备低延迟长上下文，可先于 Qwen 完成 Unified，但其候选、Schema 与评分必须与冻结 V2 一致。

---

## 9. 明确停止的工作

当前路线明确不再执行：

- Retriever V3；
- Cross-Encoder reranker；
- Query decomposition；
- Retriever 监督微调；
- 新 embedding 模型海选；
- RRF 参数搜索；
- 重新选择 K；
- 重跑 Retriever Test；
- 修改 Query、Gold、split 或 candidate identity；
- 只跑短候选 Native 子集并冒充完整结果；
- 把旧 Compact-Alias 与新正式结果拼接；
- 用 Test 结果修改 Prompt、Schema 或解码。

---

## 10. Go / No-Go 门

### G-R：Retriever 门

已完成：

```text
selected = BGE_DENSE_V2@200
Dev freeze = PASS
Test once = PASS
Retriever V3 = NOT IN CURRENT ROUTE
```

### G-SSE：端点门

- `/v1/models` 精确匹配；
- `stream=true` 有 heartbeat、data 和 `[DONE]`；
- raw SSE 与 final content 可追溯；
- API key 未落盘。

### G-SMOKE：60 条门

- 60/60 有终止状态；
- 长 Native 请求成功跨过旧 125/300 秒限制；
- 无普遍 context/output truncation；
- parser、缓存和断点续跑可用。

### G-MAIN：Native/Machine 门

```text
Native terminal rows = 4,798
Machine terminal rows = 197
旧 partial rows reused = 0
```

### G-UNIFIED：后置门

- 主结果包已经生成；
- V2 request/truth 隔离通过；
- Unified 全部 ranking-only；
- 正式运行使用 stream=true。

---

## 11. 交付物

### 11.1 Qwen 主结果包

```text
SDB_QWEN36_NATIVE_MACHINE_FORMAL_RESULT_V1.zip
```

至少包含：

- 本协议与执行规划；
- model/runtime registry；
- 60 条 smoke；
- Native 4,798 raw/parsed/status；
- Machine 197 raw/parsed/status；
- 统一评分结果；
- 六任务、Macro/Micro、候选规模分层；
- Machine delta；
- tokens/latency/SSE/heartbeat 统计；
- error analysis；
- manifest 和 SHA-256。

### 11.2 Unified 后置结果包

```text
SDB_QWEN36_UNIFIED_V2_FORMAL_RESULT_V1.zip
```

包含 Retriever-only、Conditional 和 Full End-to-End 三层结果，不与主结果包互相覆盖。

---

## 12. 决策记录

| 决策 ID | 决策 | 状态 |
|---|---|---|
| D-1 | Qwen3.6 35B 为第一正式模型 | FROZEN |
| D-2 | 正式长请求统一 `stream=true` | FROZEN |
| D-3 | 15 秒 heartbeat；最长单次推理 7,200 秒 | FROZEN_RUNTIME_FACT |
| D-4 | Retriever 固定 `BGE_DENSE_V2@200` | FROZEN |
| D-5 | Retriever V3 不进入当前路线 | FROZEN |
| D-6 | Native + Machine 为 LLM 主实验 | FROZEN |
| D-7 | Unified LLM 后置，不阻塞主结果 | FROZEN |
| D-8 | 旧 partial/Compact 结果不进入正式主表 | FROZEN |
| D-9 | 正式 Test 不因结果修改 Prompt/K/Schema | FROZEN |
| D-10 | 第二模型可在 Qwen Unified 前启动 Native/Machine | FROZEN |

---

## 13. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-21 | 冻结 Retriever 快速路线与三轨协议 |
| V1.1 | 2026-08-21 | 冻结本地 Qwen 第一模型和原 endpoint 门 |
| V1.2 | 2026-08-25 | 冻结 BGE Retriever V2；停止 V3；SSE heartbeat 通道恢复；主线改为完整 Native + Machine，Unified 后置 |

---

## 14. 当前下一动作

```text
Q0：更新 Qwen SSE runtime registry
→ Q1：用正式 stream=true 协议完成 60 条 Dev smoke
→ Q2：从头完成 Native 4,798 + Machine 197
→ Q3：生成 Qwen 第一模型主结果包
→ Q4：并行启动第二模型 Native/Machine
→ Q5：后置运行 Qwen Unified 4,798
```

当前不再开展任何 Retriever 方法开发。
