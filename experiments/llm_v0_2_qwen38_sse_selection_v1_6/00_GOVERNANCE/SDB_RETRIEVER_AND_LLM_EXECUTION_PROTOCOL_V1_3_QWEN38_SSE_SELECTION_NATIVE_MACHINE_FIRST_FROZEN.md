# ServiceDiscoveryBench v0.2.0 Retriever 与 LLM 试验执行协议

- 文档版本：`V1.3-FROZEN-ROUTE-QWEN38-SSE-SELECTION-NATIVE-MACHINE-FIRST`
- 日期：2026-08-26
- 状态：`APPROVED_EXECUTION_ROUTE_QWEN38_MODEL_REVISION`
- 数据基线：`ServiceDiscoveryBench-v0.2.0-composable-expansion-docfix1.zip`
- 数据 release SHA-256：`a199562a898fc0e3ec00563205bc0d739f2e3f592ef25878e085bca55082751c`
- 公开代码基线分支：`fix/qwen-selection-contract-v1.5-r2`
- 公开代码基线 commit：`dbdf20b0ba8acba03d85ff5e4af6051ae27efb78`
- 第一正式模型：`Qwen/Qwen3.8-27B-FP8`
- 服务端模型 ID：`qwen3.8-27b-fp8`
- 模型执行 revision：`QWEN38_SSE_SELECTION_V1_6`
- 正式传输：`OpenAI-compatible Chat Completions + SSE stream=true`
- 正式 Retriever：`BGE_DENSE_V2@200`
- 适用范围：Qwen3.8 Native、Machine Challenge，以及后续单独授权的 Unified LLM
- 不适用范围：重构数据、修改 Query/Gold/split、重新选择 Retriever/K、开发 Retriever V3、把旧 Qwen3.6 结果拼入新模型结果、自动公开发布

---

## 0. 本次修订的核心决定

本文件取代：

```text
SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_2_QWEN_SSE_NATIVE_MACHINE_FIRST_FROZEN.md
```

本次模型切换被登记为新的正式模型 revision，而不是在原实验中静默替换模型。

### 0.1 为什么允许从 Qwen3.6 切换到 Qwen3.8

原冻结模型：

```text
Qwen3.6-35B-A3B-APEX-I-Compact.gguf
```

在新的 V1.5 Selection 合同下只完成了 Q0 传输检查。Q0 返回的实际模型为：

```text
qwen3.8-27b-fp8
```

因此原 Qwen3.6 路线在正式 Dev smoke 前即被阻断。没有产生：

- V1.5 的正式 60 条 Dev smoke；
- V1.5 Machine 正式结果；
- V1.5 Native 正式结果；
- 可用于模型选择的 Test 结果。

原模型状态固定为：

```text
QWEN36_BLOCKED_MODEL_UNAVAILABLE_NO_FORMAL_RUN
```

因此，切换到 Qwen3.8 不属于根据 Test 成绩换模型，也不存在新旧正式结果拼接。

### 0.2 当前路线的五个冻结结论

1. `BGE_DENSE_V2@200` 继续作为论文 Retriever baseline；不开发 Retriever V3。
2. Native 4,798 与 Machine 197 是当前 LLM 主实验。
3. Unified LLM 继续后置，不阻塞 Native/Machine；如需执行，必须另行形成 Unified addendum。
4. Qwen3.8 是新的第一正式模型，使用独立 model/runtime registry、独立 run root 和独立结果包。
5. 输出合同继续使用已经修正的 Selection V1.5：Single/Machine 返回 Top-5；Multi/Composable 返回 selected set，不再要求完整候选排列。

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
| Machine Challenge | 197 |

正式 Test 覆盖完整六任务。Composable Service 与 Composable API 在 Test 中各为 25 条；相关结果必须同时报告百分比和原始计数。

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

Retriever V2 用于：

- BM25、BGE Dense 与 RRF 的论文对比；
- Unified 的固定第一阶段候选来源；
- Retriever-only coverage 和 failure 分析。

它不表示 Top-200 是最终生产短名单，也不授权继续开发 reranker、query decomposition 或 Retriever V3。

### 1.3 旧 Qwen 工件的地位

以下工件仅为历史诊断：

- Qwen3.6 的 full-permutation V1.4 运行；
- `candidate_count <= 10` 的旧 Native 子集；
- Compact-Alias Machine；
- V3/V4/V5 serializer 与 hierarchical ranking；
- Qwen3.6 Selection V1.5 的 Q0 model mismatch。

旧工件不得：

- 作为 Qwen3.8 缓存；
- 拼入 Qwen3.8 正式结果；
- 用于补齐缺失行；
- 作为 Qwen3.8 Dev smoke 的通过证据。

---

## 2. 三条轨道的能力边界

### 2.1 Native：主要 LLM 能力轨

```text
Query + 冻结的原生候选池
→ Qwen3.8 Top-5 排序或集合选择
→ 与冻结 Gold 比较
```

Native 测量：

- Single 的首选判断；
- Multi/Composable 的完整集合选择；
- 漏选、过选和 cardinality 判断；
- candidate-count 与 Gold-count 敏感性。

Native 不使用 Unified Retriever。它是 bounded candidate selection，不是开放目录检索。

### 2.2 Machine Challenge：主要鲁棒性轨

```text
Query + 10 个高相似干扰候选
→ Qwen3.8 Top-5
→ 与冻结 reference 比较
```

Machine 测量：

- 名称相似；
- 功能相似；
- provider、version、operation 和 endpoint 混淆；
- 相对于 Native 的 matched performance drop。

### 2.3 Unified：后置系统轨

Unified Retriever-only 已完成。Unified LLM 不进入本次 Qwen3.8 Native/Machine revision。

若后续授权：

```text
Query
→ BGE_DENSE_V2 Top-200
→ 单独冻结的 Unified shortlist/output protocol
→ LLM
```

不得直接复用已废止的“完整 200-ID 排列”合同，也不得让 Unified 阻塞当前主实验。

---

## 3. Qwen3.8 模型与运行时合同

### 3.1 模型身份

正式 registry 同时记录：

```text
official_model_id = Qwen/Qwen3.8-27B-FP8
served_model_id   = qwen3.8-27b-fp8
model_family      = Qwen3.8
parameter_class   = 27B dense
weight_format     = FP8
```

Q0 和每条正式响应都必须满足：

```text
response.model == qwen3.8-27b-fp8
```

任何其他模型 ID 均记录为：

```text
BLOCKED_MODEL_IDENTITY_MISMATCH
```

不得通过修改 expected model 字符串来兼容临时路由。

### 3.2 官方能力与实际运行时的区别

官方模型资产提供：

```text
native_context_length = 262,144 tokens
thinking_mode = enabled by default
thinking can be disabled per request
```

但正式实验只能依赖服务端实际能力。Q0 必须记录：

- 实际 served model；
- 实际 context window；
- 实际 max output tokens；
- SSE heartbeat；
- thinking control 是否被接受；
- `reasoning_content` 是否为空或缺失；
- tokenizer/chat-template identity（可获得时）；
- endpoint/runtime 版本（可获得时）。

官方上下文上限不能替代本地部署的实际配置。

### 3.3 正式非思考模式

当前 benchmark 是单次候选选择，不要求展示推理过程。正式请求固定为非思考模式：

```json
{
  "model": "qwen3.8-27b-fp8",
  "messages": ["..."],
  "stream": true,
  "stream_options": {
    "include_usage": true
  },
  "temperature": 0,
  "top_p": 1,
  "n": 1,
  "seed": 0,
  "chat_template_kwargs": {
    "enable_thinking": false,
    "preserve_thinking": false
  }
}
```

若当前代理要求把 `chat_template_kwargs` 放在 `extra_body` 中，runner 可以按 OpenAI-compatible transport 封装，但机器可见语义必须完全相同。

Q0 必须用正式 payload 验证：

```text
request accepted
response model exact match
final content present
reasoning_content absent or empty
terminal event received
[DONE] received
error = null
```

若服务端不支持该非思考 payload，状态为：

```text
BLOCKED_QWEN38_NON_THINKING_RUNTIME
```

不得无声明切换为默认 `xhigh` thinking。

### 3.4 为什么继续使用确定性解码

Qwen 官方为通用非思考生成提供采样建议；本 benchmark 为单次、严格 JSON 的选择任务，因此继续使用：

```text
temperature = 0
top_p = 1
n = 1
seed = 0
```

目的在于：

- 降低随机性；
- 保持模型间可比性；
- 保持单次正式 Test 可复现；
- 减少 JSON 输出波动。

该设置必须在 Dev smoke 前冻结，不能根据 Test 准确率修改。

### 3.5 环境变量

```text
SDB_QWEN_BASE_URL
SDB_QWEN_MODEL=qwen3.8-27b-fp8
SDB_QWEN_API_KEY_01
SDB_QWEN_API_KEY_02
SDB_QWEN_API_KEY_03
SDB_QWEN_API_KEY_04
```

Base URL 和 key 不得写入 Git、Markdown、registry、结果 ZIP 或错误报告。

---

## 4. SSE 与并发合同

### 4.1 SSE

正式请求必须：

- `stream=true`；
- 识别并忽略 heartbeat；
- 拼接 `content`；
- 单独保存但不评分 `reasoning_content`；
- 收到终态和 `[DONE]` 后才标记完成；
- 保存 raw SSE events、final response、parsed prediction 和 status；
- 在 45 秒内未收到任何 SSE 事件时记录 read-timeout；
- 最大单次墙钟时间为 7,500 秒。

### 4.2 并发

```text
formal_global_concurrency = min(4, available_api_key_count)
per_key_inflight = 1
```

实现必须是：

```text
一个 key
→ 一个长期 worker
→ worker 内严格串行
```

不得让同一个 key 或同一个 `httpx.Client` 同时处理两个请求。

---

## 5. V1.5 Selection 输出合同

### 5.1 Single Service、Single API 与 Machine

输出：

```json
{
  "ranked_candidate_ids": [
    "<candidate_id_1>",
    "<candidate_id_2>",
    "<candidate_id_3>",
    "<candidate_id_4>",
    "<candidate_id_5>"
  ]
}
```

规则：

```text
expected_k = min(5, candidate_count)
返回恰好 expected_k 个 ID
ID 唯一
ID 必须来自候选池
只允许 ranked_candidate_ids
```

### 5.2 Multi 与 Composable

输出：

```json
{
  "selected_candidate_ids": [
    "<candidate_id>"
  ]
}
```

规则：

```text
选择完成任务所需的最小充分集合
不向模型提供 Gold 数量
允许空集合
ID 唯一
ID 必须来自候选池
只允许 selected_candidate_ids
```

### 5.3 严格解析

parser 不得：

- 自动去重；
- 自动补 ID；
- 自动删除池外 ID；
- 从 prose 中抽取局部 JSON；
- 把旧 partial ranking 转换为 selected set；
- 把 parse failure 当作模型主动返回空集合。

Parse failure 属于模型失败，进入正式分母。

---

## 6. 输出预算

### 6.1 Top-5

预算覆盖：

```text
当前轨道最长 5 个 candidate IDs
+ JSON overhead
+ 固定安全余量
```

### 6.2 Selected set

预算不得按 Gold 数量估算。必须覆盖：

```text
当前 Native 最大候选池的全部 candidate IDs
+ JSON overhead
+ 固定安全余量
```

这样模型过选会被评分为：

```text
low precision
over-selection
cardinality error
```

而不会因 max_tokens 太小被误记为截断 parse failure。

---

## 7. 正式执行门

### Q0：模型与运行时 preflight

四个 key slot 均须通过：

- exact served model；
-正式非思考 payload；
- SSE heartbeat；
-终态；
- `[DONE]`；
- final content；
-无认证和模型路由错误。

### Q1：60 条 Dev smoke

继续使用原冻结的 60 条 Dev task identity，六任务各 10 条。

通过门：

```text
terminal rows = 60
infra_error = 0
api_error = 0
overall parse success >= 54/60
每个 task type parse success >= 8/10
最大候选 Single 至少 1 条合法 Top-5
最大候选 selected-set 至少 1 条合法集合
```

Smoke 只验证协议与运行链路，不使用 Dev 准确率修改 Prompt。

### Q2：Machine 197

Machine 优先完整运行：

```text
terminal rows = 197
old rows reused = 0
```

完成后全部 key slot 转给 Native。

### Q3：Native 4,798

```text
terminal rows = 4,798
old rows reused = 0
```

Formal 模式禁止：

```text
--limit
--request-id
```

### Q4：评分与结果包

只有 unresolved infra/API error 为 0 时才允许评分。Parse failure 计零分并保留在分母。

---

## 8. 正式指标

### 8.1 Single 与 Machine

```text
Hit@1
MRR@5
Recall@5
nDCG@5
Parse Failure Rate
```

### 8.2 Multi 与 Composable

```text
Exact Set Match
Precision
Recall
F1
Completeness
Jaccard
Under-selection
Over-selection
Cardinality Error
Parse Failure Rate
```

多个 acceptable Gold sets 保持：

```text
outer OR / inner AND
```

不得将多个替代集合 union。

### 8.3 聚合

跨六任务的共同主指标：

```text
task_success
```

定义：

```text
Single task_success = Hit@1
Multi/Composable task_success = Exact Set Match
```

报告：

```text
Macro-6 Task Success
Micro Task Success
```

另外分别报告：

```text
Single Ranking Macro：
Hit@1 / MRR@5 / Recall@5 / nDCG@5

Set-Selection Macro：
ESM / Precision / Recall / F1 / Completeness /
Jaccard / Under-selection / Over-selection / Cardinality Error
```

不得将“不适用指标”补零后计算无意义 Macro-6。

### 8.4 工程指标

```text
input/output tokens
latency mean/P50/P95
first-event latency
heartbeat count
retry rate
throughput
parse failure taxonomy
candidate-ID error
```

若 SSE usage 缺失，使用冻结 tokenizer 离线核算，并明确标记为 offline token count。

---

## 9. 重试与失败

允许重试：

- connect failure；
- SSE 中断；
- 429；
- 5xx / 524；
- server error event；
- 45 秒内没有任何 SSE event。

每条最多 3 次：

```text
15s → 30s → 60s
```

不重试：

- 模型选错；
- JSON 错误；
- ID 重复或池外；
- Top-5 数量错误；
- selected set 错误；
- 模型主动终止但未满足合同。

---

## 10. 结果隔离与交付

### 10.1 Run root

新 run root 必须独立，例如：

```text
experiments/llm_v0_2_qwen38_sse_selection_v1_6/
```

不得 resume：

```text
Qwen3.6 V1.4
Qwen3.6 Selection V1.5
```

### 10.2 主结果包

```text
SDB_QWEN38_NATIVE_MACHINE_SELECTION_RESULT_V1.zip
```

至少包含：

- 本协议与执行规划；
- model/runtime registry；
- Q0；
- 60 条 smoke；
- Machine 197 status/predictions；
- Native 4,798 status/predictions；
- 评分结果；
- Macro/Micro 与分桶；
- latency/heartbeat/retry；
- failure taxonomy；
- manifest、CRC 与 SHA-256。

公开 GitHub 继续只同步 code、contracts、synthetic tests 和文档；真实请求、响应、Gold、日志和结果包保持私有。

---

## 11. 第二模型与 Unified

Qwen3.8 Native/Machine 完成后，可以立即启动第二模型 Native/Machine。

Unified LLM：

- 不进入本次 Qwen3.8 V1.6 主结果门；
- 不阻塞第二模型；
- 需要单独 addendum；
- 不得重新选择 Retriever/K；
- 不得恢复完整 Top-200 permutation 合同。

---

## 12. 决策记录

| 决策 ID | 决策 | 状态 |
|---|---|---|
| D-1 | 原 Qwen3.6 路线因模型不可用在 formal 前终止 | FROZEN |
| D-2 | Qwen3.8-27B-FP8 为第一正式模型 | FROZEN |
| D-3 | served model 必须为 `qwen3.8-27b-fp8` | FROZEN |
| D-4 | 使用非思考、单次确定性输出 | FROZEN |
| D-5 | SSE stream=true 与 heartbeat 保留 | FROZEN |
| D-6 | Selection V1.5 输出合同保留 | FROZEN |
| D-7 | Native + Machine 为主实验 | FROZEN |
| D-8 | Retriever 固定 `BGE_DENSE_V2@200` | FROZEN |
| D-9 | Retriever V3 不进入当前路线 | FROZEN |
| D-10 | Unified LLM 需要单独授权 | FROZEN |
| D-11 | 旧 Qwen3.6 行不得复用 | FROZEN |
| D-12 | Test 结果不得反向修改模型参数或合同 | FROZEN |

---

## 13. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-21 | 冻结 Retriever 与三轨基础路线 |
| V1.1 | 2026-08-21 | 冻结原本地 Qwen3.6 第一模型 |
| V1.2 | 2026-08-25 | 冻结 BGE Retriever V2 与 SSE Native/Machine-first |
| V1.3 | 2026-08-26 | 原 Qwen3.6 在 formal 前终止；冻结 Qwen3.8-27B-FP8、非思考模式与独立 V1.6 模型 revision |

---

## 14. 当前下一动作

```text
更新公开代码中的 model/runtime registry 与 Qwen3.8 experiment revision
→ Q0：四 slot 验证 exact Qwen3.8 + 非思考 SSE
→ Q1：60 条 Dev smoke
→ Q2：Machine 197
→ Q3：Native 4,798
→ Q4：评分与 Qwen3.8 主结果包
→ 启动第二模型 Native/Machine
```

当前不再开展 Retriever 方法开发，也不启动 Unified LLM。
