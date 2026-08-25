# ServiceDiscoveryBench v0.2.0 Retriever 与 LLM 实验快速执行规划

- 文档版本：`V1.4-QWEN-SSE-NATIVE-MACHINE-FIRST-FINAL`
- 日期：2026-08-25
- 对应纲领：`SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_2_QWEN_SSE_NATIVE_MACHINE_FIRST_FROZEN.md`
- 当前状态：`READY_TO_RESUME_FORMAL_QWEN_EXPERIMENT`
- 第一正式模型：`Qwen3.6-35B-A3B-APEX-I-Compact.gguf`
- 正式端点：`https://deutschland-spread-granny-holders.trycloudflare.com/v1`
- 正式传输：`stream=true + 15s SSE heartbeat`
- Retriever：`BGE_DENSE_V2@200`，已完成，不再开发

---

## 0. 本规划与纲领的关系

纲领 MD 规定实验定义、冻结边界和不可变规则；本规划只规定执行顺序、并行方式、时间安排和交付物。

发生冲突时，以纲领 V1.2 为准。

本次规划不再安排 Retriever 构造、模型选择或 V3。Retriever V2 已有完整论文结果：Dev 4,827、Test 4,798、Test All/Partial/Zero 为 `3356 / 771 / 671`。当前关键路径已经转回 LLM benchmark。

---

## 1. 当前状态

### 1.1 已完成

```text
数据 v0.2.0              已冻结
Native manifests          已有
Machine manifests         已有
Unified corpus            已完成
Retriever V2              BGE_DENSE_V2@200
Retriever Test            4,798，一次完成
Retriever 论文证据         已完成
Qwen SSE heartbeat 通道    已修复并端到端验证
```

### 1.2 尚未完成

```text
正式 SSE 60 条 Dev smoke
正式 Native 4,798
正式 Machine 197
Qwen 主结果包
第二模型 Native/Machine
后置 Unified LLM 4,798
```

### 1.3 不再作为主结果的历史运行

```text
Native candidate_count<=10 的 3,173 条
Compact-Alias Machine 197
旧非流式、V3/V4/V5 serializer、hierarchical 结果
```

这些只作为工程诊断，不能 resume 或拼接到正式主实验。

---

## 2. 最快完成目标

### 2.1 第一里程碑：Qwen 主实验

目标：

```text
完整 Native 4,798
+
完整 Machine 197
+
统一指标与效率表
```

这是第一套可用于论文比较模型能力的正式结果，不等待 Unified。

### 2.2 第二里程碑：至少两个模型

在 Qwen Native/Machine 完成后，使用同一 manifest 和评分程序追加一个开放权重或强闭源模型的 Native/Machine。

### 2.3 第三里程碑：后置 Unified

使用 `BGE_DENSE_V2@200` 对 Qwen 或其他长上下文模型运行完整 Unified，形成 Retriever + LLM 系统结果。该里程碑不阻塞前两个里程碑。

---

## 3. 固定运行配置

### 3.1 环境变量

```text
SDB_QWEN_BASE_URL=https://deutschland-spread-granny-holders.trycloudflare.com/v1
SDB_QWEN_MODEL=Qwen3.6-35B-A3B-APEX-I-Compact.gguf
SDB_QWEN_API_KEY_01=<secret>
SDB_QWEN_API_KEY_02=<secret>
SDB_QWEN_API_KEY_03=<secret>
SDB_QWEN_API_KEY_04=<secret>
```

实际拥有少于 4 个 key 时，只使用存在的 key。密钥不落盘。

### 3.2 正式请求

```text
stream = true
temperature = 0
top_p = 1
seed = 0
n = 1
connect timeout = 30s
SSE read timeout = 45s
max wall time = 7,500s
heartbeat expected every 15s
global concurrency = min(4, available key count)
per-key inflight = 1
```

### 3.3 SSE 结果保存

每个请求生成：

```text
request.json
raw_sse_events.jsonl
final_response.json
parsed_prediction.json
status.json
```

`status.json` 至少记录：

```text
benchmark_task_id
track
model
request_hash
candidate_order_hash
start/end time
heartbeat_count
first_event_latency
end_to_end_latency
retry_count
HTTP status
finish reason
parse status
error code
```

---

## 4. 执行时间线

以下时间是执行目标，不是虚假保证；实际墙钟时间由长请求比例决定。所有阶段均可断点续跑。

## 阶段 A：运行时更新与 SSE preflight，0–1 小时

### 动作

1. 更新 `MODEL_REGISTRY.json` 和 `QWEN_SSE_RUNTIME_FREEZE.json`；
2. 记录 Base URL、模型名、heartbeat=15s、server maximum inference=7200s；
3. `GET /v1/models`；
4. 发送 1 条短 `stream=true` 请求；
5. 检查 heartbeat、内容拼接、`[DONE]` 和 raw log。

### 输出

```text
QWEN_SSE_PREFLIGHT_REPORT.md
QWEN_SSE_RUNTIME_FREEZE.json
```

### 完成条件

```text
model identity exact match
streaming response pass
heartbeat pass
final data pass
[DONE] pass
error = null
```

---

## 阶段 B：60 条正式 Dev smoke，1–6 小时

### 动作

使用原冻结 60 条 Dev smoke，从头按正式 SSE 协议运行。不得复用旧 Compact 或 partial response。

队列分配：

```text
最多 4 个并发 slot
同一 key 同时只跑 1 条
按候选数从小到大与从大到小交错排队
```

交错排队的目的仅是尽早暴露长请求问题，不改变样本或结果。

### 完成条件

```text
60/60 terminal status
所有最长 Native 请求能持续收到 heartbeat
无系统性 context overflow
无系统性 output truncation
raw SSE / final content / parser 可追溯
```

模型格式失败可通过 smoke，但必须如实记录；基础设施导致的大面积失败不可通过。

### 冻结输出预算

在 smoke 前对 actual manifests 计算最坏合法 JSON 长度，用实际 tokenizer 冻结 track-level `max_tokens`，留 10% 余量。该值只由输出长度决定，不由准确率决定。

---

## 阶段 C：完整 Machine + Native，Smoke 通过后立即启动

### C1. Machine 197

开始时固定占用 1 个 slot；其余 slot 运行 Native。Machine 完成后 slot 全部转给 Native。

必须从头运行标准 manifest，不使用 Compact Alias。

### C2. Native 4,798

从头运行完整 Test。旧 3,173 子集不得作为缓存命中。

建议队列：

```text
slot 1-3：Native
slot 4：Machine；Machine 完成后转 Native
```

若仅有 1–3 个 key，则按相同优先级比例执行。

### 预期墙钟

```text
Machine：通常数小时内完成
Native：目标 12–36 小时
```

该估算基于大多数 Native 请求较短、少部分长请求由 SSE 心跳支撑。以实际 completed/total 和 P50/P95 为准。

### 失败处理

基础设施失败最多重试 3 次：

```text
15s → 30s → 60s
```

模型格式错误、候选遗漏、重复或选择错误不重试。

---

## 阶段 D：Qwen 主结果评分与交付，Native/Machine 完成后 2–6 小时

### D1. Native 指标

Single：

```text
Hit@1
MRR
nDCG@5
Recall@5
```

Multi / Composable：

```text
Exact Set Match
Macro F1
Completeness
Jaccard
Under-selection
Over-selection
Cardinality Error
```

同时生成：

```text
六任务分别
Macro-6
Micro overall
candidate-count buckets
gold-count buckets
Service/API
Single/Multi/Composable
```

### D2. Machine 指标

```text
Hit@1
MRR
Recall@5
nDCG@5
Native→Machine matched delta
```

### D3. 工程指标

```text
parse failure
candidate-ID error
incomplete ranking
input/output tokens
latency P50/P95
heartbeat count distribution
retry rate
throughput
```

### D4. 交付包

```text
SDB_QWEN36_NATIVE_MACHINE_FORMAL_RESULT_V1.zip
LATEST_RESULT.md
RESULT_SET_INDEX.json
```

状态：

```text
QWEN36_NATIVE_MACHINE_FORMAL_COMPLETE
```

---

## 阶段 E：第二模型 Native/Machine，并行于 Qwen Unified

Qwen 主结果包通过后立即启动第二模型 Native/Machine。第二模型不得等待 Unified。

第二模型使用：

```text
同一 Query
同一 candidate order
同一 Prompt 语义
同一输出 Schema
同一 parser
同一评分代码
```

允许 provider-specific JSON transport，但不得改变任务说明或给额外信息。

目标：

```text
48–72 小时内形成至少两个模型的 Native + Machine 主表
```

前提是第二模型 access 已就绪；否则明确标记 blocked，不伪造时间。

---

## 阶段 F：后置 Qwen Unified

### 输入

```text
BGE_DENSE_V2@200
4,798 Unified ranking-only requests
```

### 顺序

只在 Qwen Native/Machine 主结果包通过后启动。它可以与第二模型并行。

### 运行特性

Unified 每题 200 候选，预计明显慢于 Native。完整运行可能需要数天；状态按：

```text
completed / 4,798
success / parse failure / infrastructure failure
平均 heartbeat 数
P50/P95 latency
```

实时记录，不用抽样分数冒充正式主结果。

### 结果

```text
Retriever-only
Conditional LLM（ALL_GOLD=3,356 的 reference coverage 子集）
Full End-to-End（完整 4,798）
```

注意：`3,356` 是 reference-Gold 全部进入 Top-200 的数量，不是功能成功的绝对上限；Unified 中可能存在未裁决的替代 API，因此不能把所有 non-Gold 自动视为错误。

---

## 5. 运行状态看板

| 阶段 | 输入 | 完成定义 | 当前状态 |
|---|---|---|---|
| Retriever V2 | Dev/Test | BGE@200 Test once | COMPLETE |
| SSE preflight | endpoint | heartbeat + DONE | READY |
| Dev smoke | 60 Dev | 60 terminal | PENDING |
| Machine | 197 Test | 197 terminal | PENDING FORMAL RERUN |
| Native | 4,798 Test | 4,798 terminal | PENDING FORMAL RERUN |
| Qwen 主结果 | Native+Machine | metrics + bundle | PENDING |
| 第二模型 | Native+Machine | full result | PENDING ACCESS |
| Qwen Unified | 4,798 | full result | POST-MAIN |

---

## 6. 任务责任与并行

### 执行器

只负责：

- 按固定 manifest 发送请求；
- SSE 拼接、heartbeat 记录；
- 基础设施重试；
- 原始输出落盘；
- 调用固定 parser 和评分程序；
- 断点续跑与打包。

执行器不得：

- 修改 Prompt；
- 改候选顺序；
- 改 selected-set 定义；
- 跳过困难任务；
- 复用旧 partial 结果；
- 根据结果换 Retriever/K；
- 开发 Retriever V3。

### 人工/GPTPro

负责：

- 审核 smoke 和正式结果包；
- 解释指标与失败；
- 决定第二、第三模型；
- 论文表述和限制；
- 最终发布批准。

---

## 7. 明确删除的工作

为了最快完成，本轮删除：

```text
Retriever V3
Cross-Encoder
Query decomposition
Retriever 微调
新 embedding 模型
RRF 调参
Unified Top-K 重新选择
多套 Prompt 消融
Pass@3/5/10
LLM-as-a-Judge
大规模人工复审
```

仅保留：

```text
完整 Native
完整 Machine
已有 Retriever V2
后置 Unified
多模型公平复现
```

---

## 8. 论文表与图

### 主表 1：模型 × Native 六任务

Single 用 Hit@1/MRR；Multi/Composable 用 Exact Set Match/F1/Completeness；同时给 Macro-6 和 Micro。

### 主表 2：Machine Robustness

Native 与 Machine 的 matched delta。

### 主表 3：Retriever V2

BM25、BGE Dense、RRF；Dev K 曲线和 Test coverage。

### 补充表 4：Unified 三层

仅在完整 Unified LLM 完成后加入。

### 效率表

tokens、P50/P95 latency、heartbeat、retry、parse failure、throughput。

### 图

1. Native 六任务模型对比；
2. candidate-count 对性能的影响；
3. Native→Machine 性能下降；
4. Retriever All/Partial/Zero；
5. 成本/延迟—效果关系。

---

## 9. 完成标准

### 9.1 第一正式模型主结果

```text
Qwen Native = 4,798 terminal
Qwen Machine = 197 terminal
旧 partial reused = 0
raw/parsed/status complete
metrics reproducible
```

### 9.2 可向导师汇报的最小完整实验

```text
Retriever V2 paper-ready
+
Qwen Native/Machine full
+
第二模型 Native/Machine full 或明确 blocked
+
主要表格与效率结果
```

### 9.3 论文增强实验

```text
Qwen 或其他模型 Unified full
+
Retriever-only / Conditional / Full E2E
+
错误分析
```

Unified 是增强项，不再阻塞最小完整实验。

---

## 10. 当前立即执行清单

```text
1. 更新两份核心 MD 和 runtime registry
2. 设置 Base URL、model、4 个 key 环境变量
3. 运行 SSE preflight
4. 完成 60 条正式 Dev smoke
5. 冻结 track-level max_tokens
6. 从头运行 Machine 197 + Native 4,798
7. 评分并生成 Qwen 主结果包
8. 启动第二模型 Native/Machine
9. 后置运行 Qwen Unified 4,798
```

当前第一动作不是继续讨论 Retriever，而是用已经恢复的 SSE 通道完成正式 60 条 smoke。

---

## 11. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.2 | 2026-08-21 | 快速执行基础规划 |
| V1.3 | 2026-08-21 | 固定 Qwen 本地第一模型 |
| V1.4 | 2026-08-25 | SSE heartbeat 通道恢复；Retriever V2 冻结并停止 V3；Native/Machine 改为主关键路径，Unified 后置 |
