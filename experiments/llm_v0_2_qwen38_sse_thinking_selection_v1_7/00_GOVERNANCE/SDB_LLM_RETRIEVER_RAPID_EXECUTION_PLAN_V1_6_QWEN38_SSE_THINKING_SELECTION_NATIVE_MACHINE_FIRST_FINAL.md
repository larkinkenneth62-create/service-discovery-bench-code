# ServiceDiscoveryBench v0.2.0 Retriever 与 LLM 实验快速执行规划

- 文档版本：`V1.6-QWEN38-SSE-THINKING-SELECTION-NATIVE-MACHINE-FIRST-FINAL`
- 日期：2026-08-26
- 对应纲领：`SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_4_QWEN38_SSE_THINKING_SELECTION_NATIVE_MACHINE_FIRST_FROZEN.md`
- 当前状态：`AUTHORIZED_FOR_QWEN38_THINKING_V1_7_GATE_EXECUTION`
- 代码基线分支：`codex/qwen38-sse-selection-v1.6`
- 代码基线 commit：`6c94b1e5124da0dca2f442c930e642a5d1a1e34f`
- 第一正式模型：`Qwen/Qwen3.8-27B-FP8`
- 服务端模型 ID：`qwen3.8-27b-fp8`
- 模型执行 revision：`QWEN38_SSE_THINKING_SELECTION_V1_7`
- 正式传输：`stream=true + SSE heartbeat`
- Retriever：`BGE_DENSE_V2@200`，已完成，不再开发

---

## 0. 本规划与纲领的关系

纲领 V1.4 规定任务定义、模型身份、thinking 分离合同、输出合同、失败边界和不可变规则；本规划只规定执行顺序、时间安排和交付物。

发生冲突时，以纲领 V1.4 为准。

V1.6 在 Q0 的 4 个合成请求上完成传输和模型身份验证，但其非思考参数导致分析文本进入 `content`，严格 JSON 为 0/4；没有发送任何 benchmark row。项目负责人现已授权独立的 thinking V1.7。V1.7 不复用 V1.6 行，并从新的双合同 Q0 开始。

---

## 1. 当前状态

### 1.1 已完成

```text
数据 v0.2.0                          FROZEN
Native/Machine manifests             AVAILABLE
Retriever V2                         BGE_DENSE_V2@200
Retriever Test                       4,798，运行一次
Selection V1.5 代码                  IMPLEMENTED
Selection V1.5 R2 修复               IMPLEMENTED
Python 3.11/3.12/3.13 CI             PASS
Publication audit                    PASS
SSE heartbeat 传输                   PASS
Qwen3.8 V1.6 Q0 transport/model      4/4 PASS
Qwen3.8 V1.6 Q0 strict JSON          0/4 FAIL
Qwen3.8 V1.6 benchmark rows sent     0
preserved-thinking synthetic probe   PASS
```

### 1.2 原 Qwen3.6 状态

```text
Q0 transport                         PASS
actual served model                  qwen3.8-27b-fp8
expected model                       Qwen3.6-35B-A3B-APEX-I-Compact.gguf
model identity                       FAIL
Dev smoke                            NOT STARTED
Machine formal                       NOT STARTED
Native formal                        NOT STARTED
```

固定状态：

```text
QWEN36_BLOCKED_MODEL_UNAVAILABLE_NO_FORMAL_RUN
```

### 1.3 当前待完成

```text
代码与 registry 更新为 thinking V1.7
Qwen3.8 Q0 四 slot × 两类合同 preflight
Qwen3.8 60 条 Dev smoke
Qwen3.8 Machine 197
Qwen3.8 Native 4,798
评分与主结果包
第二模型 Native/Machine
```

Unified LLM 继续后置，不进入当前时间关键路径。

---

## 2. 模型 revision 更新，0–2 小时

### 2.1 新目录与登记

从当前通过 CI 的 Selection V1.5 R2 代码派生新实验目录：

```text
experiments/llm_v0_2_qwen38_sse_thinking_selection_v1_7/
```

不得原地覆盖 Qwen3.6 的私有 run root。

新增或更新：

```text
MODEL_REGISTRY_QWEN38_THINKING_V1_7.json
QWEN38_SSE_THINKING_RUNTIME_FREEZE_V1_7.json
PROMPT_REGISTRY_QWEN38_THINKING_V1_7.json
OUTPUT_CONTRACT_REGISTRY_V1_5.json
RUN_PROVENANCE.json
```

固定：

```text
official_model_id = Qwen/Qwen3.8-27B-FP8
served_model_id   = qwen3.8-27b-fp8
thinking_mode     = enabled_preserved_separate
enable_thinking   = true
preserve_thinking = true
reasoning policy  = saved_not_scored
content policy    = whole_message_strict_json
temperature       = 0
top_p             = 1
n                 = 1
seed              = 0
```

### 2.2 代码只需修改的范围

允许修改：

- expected model ID；
- official model metadata；
- tokenizer/model registry；
- Qwen3.8 preserved-thinking request body；
- 固定的 4,096 token reasoning allowance；
- 实验目录名与结果包名；
- Q0 验证；
-文档与 synthetic tests。

不得修改：

- Selection V1.5 任务合同；
- parser 语义；
- scorer；
- Query/Gold/split；
-候选池和顺序；
- Retriever/K；
- 60 条 smoke identity；
-正式 Test 行数。

### 2.3 公开仓库同步

公开仓库中不得写入 live Base URL 或 key。只提交：

- model ID placeholder/config；
- Qwen3.8 preserved-thinking separation contract；
- synthetic Q0/SSE tests；
-更新后的治理与计划文档；
- changelog 与 model registry 文档。

修改后运行：

```text
Python 3.11 CI
Python 3.12 CI
Python 3.13 CI
Publication audit
```

全部通过后才运行私有 Q0。

---

## 3. Q0：Qwen3.8 四 slot × 双合同 preflight，0.5–1 小时

### 3.1 输入

使用正式环境变量：

```text
SDB_QWEN_BASE_URL=<private>
SDB_QWEN_MODEL=qwen3.8-27b-fp8
SDB_QWEN_API_KEY_01=<secret>
SDB_QWEN_API_KEY_02=<secret>
SDB_QWEN_API_KEY_03=<secret>
SDB_QWEN_API_KEY_04=<secret>
```

### 3.2 精确请求合同

每个 slot 分别发送一个含 5 个候选的 Top-5 请求与一个 selected-set 请求，共 8 个纯合成请求、0 条 benchmark row，使用：

```text
stream=true
stream_options.include_usage=true
temperature=0
top_p=1
n=1
seed=0
enable_thinking=true
preserve_thinking=true
response_format=json_object
max_tokens=1024
```

### 3.3 通过门

8 个请求均须：

```text
HTTP 200
response.model == qwen3.8-27b-fp8
heartbeat received
terminal event received
[DONE] received
final content present
reasoning_content present and non-empty
reasoning_content saved separately and not scored
content contains one complete strict-JSON object only
strict JSON parse PASS
finish_reason = stop
error = null
```

同时记录：

```text
actual context length
actual max output
server/runtime version（可获得时）
usage support
first-event latency
end-to-end latency
```

任一 slot 失败，停止为：

```text
BLOCKED_QWEN38_THINKING_Q0
```

不得启动 smoke。

---

## 4. Q1：60 条正式 Dev smoke，目标 2–8 小时

### 4.1 输入

沿用原冻结 60 条 Dev task identity：

```text
六任务 × 10 条
```

不得选择更容易的新 smoke 集。

### 4.2 输出合同

```text
Single Service/API：
Top-5 ranking

Multi/Composable：
selected set only
```

### 4.3 队列

```text
一个 key 对应一个长期 worker
每 key inflight = 1
global concurrency <= 4
```

候选数大、小样本交错排队，仅用于尽早暴露长请求问题，不改变任务集合或评分。

### 4.4 通过门

```text
terminal rows = 60
infra_error = 0
api_error = 0
overall parse success >= 54/60
每个 task type parse success >= 8/10
最大候选 Single 至少 1 条合法 Top-5
最大候选 selected-set 至少 1 条合法集合
```

Dev 准确率不用于修改 Prompt。

失败状态：

```text
BLOCKED_QWEN38_SELECTION_SMOKE
```

---

## 5. Q2：Machine 197，目标 2–8 小时

Smoke 通过后先完整运行 Machine：

```text
rows = 197
old rows reused = 0
formal --limit forbidden
formal --request-id forbidden
```

四个 slot 全部用于 Machine。Machine 完成后再启动 Native，避免两个正式轨竞争同一端点。

### 5.1 输出

每道题返回 Top-5。

### 5.2 完成定义

```text
197/197 terminal
unresolved infra/api error = 0
parse failure 保留
```

完成状态：

```text
QWEN38_MACHINE_COMPLETE_ALL_PARSED
或
QWEN38_MACHINE_COMPLETE_WITH_MODEL_FAILURES
```

---

## 6. Q3：Native 4,798，目标 12–48 小时

### 6.1 运行

```text
rows = 4,798
old Qwen3.6 rows reused = 0
old V1.4/V1.5 run root resume = forbidden
```

任务合同：

```text
Single Service/API：
Top-5

Multi/Composable：
selected set
```

### 6.2 输出预算

Top-5 预算按最长 5 个 ID 合法 JSON 的 UTF-8 byte 上界、64 固定安全余量和 4,096 固定 reasoning allowance 冻结。

Selected-set 预算按最大候选池的全部 ID 合法 JSON UTF-8 byte 上界、64 固定安全余量和 4,096 固定 reasoning allowance 冻结，不按 Gold 数量估算。

```text
max_tokens = legal-answer UTF-8 byte upper bound + 64 + 4,096
```

预算表在 smoke 前一次性冻结；不得按 Dev/Test 表现修改。

### 6.3 失败处理

基础设施错误最多重试 3 次：

```text
15s → 30s → 60s
```

模型格式失败不重试。

### 6.4 完成定义

```text
4,798/4,798 terminal
unresolved infra/api error = 0
status IDs 与 manifest IDs exact match
```

完成状态：

```text
QWEN38_NATIVE_COMPLETE_ALL_PARSED
或
QWEN38_NATIVE_COMPLETE_WITH_MODEL_FAILURES
```

---

## 7. Q4：评分与交付，2–6 小时

### 7.1 Single 和 Machine

```text
Hit@1
MRR@5
Recall@5
nDCG@5
Parse Failure Rate
```

### 7.2 Multi/Composable

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

### 7.3 聚合

```text
Macro-6 Task Success
Micro Task Success
Single Ranking Macro
Set-Selection Macro
Service/API
Single/Multi/Composable
candidate-count buckets
Gold-count buckets
parse-status buckets
```

其中：

```text
Single task_success = Hit@1
Multi/Composable task_success = Exact Set Match
```

不计算把不适用指标补零的伪 Macro-6。

### 7.4 效率

```text
input/output tokens
latency mean/P50/P95
first-event latency
heartbeat distribution
retry rate
throughput
parse-failure taxonomy
```

### 7.5 主结果包

```text
SDB_QWEN38_NATIVE_MACHINE_THINKING_SELECTION_RESULT_V1_7.zip
SDB_QWEN38_NATIVE_MACHINE_THINKING_SELECTION_RESULT_V1_7.zip.sha256
LATEST_RESULT.md
RESULT_SET_INDEX.json
```

状态：

```text
QWEN38_NATIVE_MACHINE_THINKING_SELECTION_V1_7_COMPLETE
```

---

## 8. 预计时间线

| 阶段 | 目标时间 | 累计 |
|---|---:|---:|
| 模型 revision 与公开代码更新 | 0–2 小时 | 2 小时 |
| Q0 四 slot × 双合同 | 0.5–1 小时 | 3 小时 |
| 60 条 smoke | 2–8 小时 | 5–11 小时 |
| Machine 197 | 2–8 小时 | 7–19 小时 |
| Native 4,798 | 12–48 小时 | 19–67 小时 |
| 评分与打包 | 2–6 小时 | 21–73 小时 |

现实目标：

```text
1–3 天形成 Qwen3.8 Native + Machine 第一模型主结果
```

实际墙钟以 completed/total、P50/P95 与长候选比例为准。

---

## 9. 第二模型

Qwen3.8 主结果包通过后，立即启动第二模型 Native/Machine。

第二模型必须复用：

-相同 Query；
-相同候选顺序；
-相同 Selection V1.5 语义；
-相同评分器；
-相同失败计分规则。

Provider-specific transport 可以不同，但不得给模型额外信息。

---

## 10. Unified

当前论文最小完整实验为：

```text
Retriever V2
+
Qwen3.8 Native/Machine
+
第二模型 Native/Machine 或明确 blocked
```

Unified LLM 是增强项：

- 不进入当前 1–3 天目标；
- 不阻塞主结果；
- 不恢复 200-ID 完整排列；
- 后续另行制定 addendum。

---

## 11. 运行状态看板

| 阶段 | 完成定义 | 当前状态 |
|---|---|---|
| Retriever V2 | BGE@200 Test once | COMPLETE |
| Selection V1.5 R2 code | CI + audit | COMPLETE |
| Qwen3.6 | Q0 exact model | TERMINATED BEFORE FORMAL |
| Qwen3.8 non-thinking V1.6 | Q0 strict JSON | TERMINATED, 0 BENCHMARK ROWS |
| Qwen3.8 thinking V1.7 revision | code/docs/registry | IN PROGRESS |
| Qwen3.8 thinking Q0 | 8/8 requests, 4 slots × 2 contracts | PENDING |
| Qwen3.8 smoke | 60 terminal + gate | PENDING |
| Qwen3.8 Machine | 197 terminal | PENDING |
| Qwen3.8 Native | 4,798 terminal | PENDING |
| Qwen3.8 result bundle | metrics + ZIP | PENDING |
| Second model | Native/Machine | PENDING |
| Unified LLM | separate addendum | DEFERRED |

---

## 12. 本轮明确不做

```text
Retriever V3
Cross-Encoder
Query decomposition
Retriever 微调
新 embedding 模型
重新选择 K
重跑 Retriever Test
修改 Query/Gold/split/candidate pool
恢复 Qwen3.6 partial rows
完整 199-ID 或 200-ID permutation
多 Prompt 消融
Pass@3/5/10
LLM-as-a-Judge
Unified LLM
```

---

## 13. 当前立即执行清单

```text
1. 将两份核心 MD 升级到 V1.4 / V1.6
2. 在公开代码中登记 Qwen3.8 thinking V1.7 model/runtime revision
3. 更新 synthetic tests 和 CI
4. 保持 CI 3.11/3.12/3.13 与 publication audit 全绿
5. 私有环境运行 Q0 四 slot × Top-5/selected-set
6. Q0 通过后运行 60 条 smoke
7. Smoke 通过后依次运行 Machine 197、Native 4,798
8. 评分并生成 Qwen3.8 主结果包
9. 启动第二模型 Native/Machine
```

---

## 14. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.2 | 2026-08-21 | 快速执行基础规划 |
| V1.3 | 2026-08-21 | 固定原 Qwen3.6 本地第一模型 |
| V1.4 | 2026-08-25 | SSE 恢复、Retriever V2 冻结、Native/Machine-first |
| V1.5 | 2026-08-26 | 原 Qwen3.6 在 formal 前终止；冻结 Qwen3.8-27B-FP8 非思考 Selection V1.6，并保持 Native/Machine-first |
| V1.6 | 2026-08-26 | V1.6 Q0 strict-JSON 失败且未发送 benchmark；冻结 preserved-thinking 分离合同、4,096 allowance 与独立 V1.7 revision |
