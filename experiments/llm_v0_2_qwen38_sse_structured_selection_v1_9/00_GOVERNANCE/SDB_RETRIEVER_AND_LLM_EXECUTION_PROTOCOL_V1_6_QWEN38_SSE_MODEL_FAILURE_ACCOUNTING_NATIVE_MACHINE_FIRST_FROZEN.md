# ServiceDiscoveryBench v0.2.0 Retriever 与 LLM 试验执行协议

- 文档版本：`V1.6-FROZEN-ROUTE-QWEN38-SSE-MODEL-FAILURE-ACCOUNTING-NATIVE-MACHINE-FIRST`
- 日期：2026-08-26
- 状态：`APPROVED_EXECUTION_ROUTE_QWEN38_V1_9`
- 代码基线 commit：`ed44ef0ab38c68ebb1508cd0807810d91db38183`
- 新代码分支：`fix/qwen38-sse-model-failure-accounting-v1.9`
- 模型：`Qwen/Qwen3.8-27B-FP8`
- served model：`qwen3.8-27b-fp8`
- experiment revision：`QWEN38_SSE_STRUCTURED_SELECTION_MODEL_FAILURE_ACCOUNTING_V1_9`
- route revision（继承，不变）：`QWEN38_SSE_THINKING_STRUCTURED_SELECTION_V1_8`
- protocol revision：`SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_6_QWEN38_SSE_MODEL_FAILURE_ACCOUNTING_NATIVE_MACHINE_FIRST_FROZEN`
- Retriever：`BGE_DENSE_V2@200`，保持冻结

## 0. 权威修订

V1.8 的 24 条纯合成 Q0 完成 24/24 terminal、23/24 strict parse、0 infrastructure error、0 retry、0 benchmark rows。唯一失败在完整 HTTP/SSE 响应中返回非 JSON 分析文本。

V1.9 将“完整端点响应但最终答案不符合输出合同”统一分类为模型 `parse_failure`，不重试并在正式评分中计零。它不再把 reasoning channel 缺失本身视为 API 硬阻断。

以下保持不变：

- Selection V1.5：Single/Machine Top-5，Multi/Composable selected set；
- 可见 Prompt；
- strict parser；
- Query、Gold、split、candidate pool/order；
- SSE、heartbeat、终态、`[DONE]`；
- exact model identity；
- Retriever/K；
- 正式 Test 行数；
- no JSON extraction/repair；
- model/schema/parser failure 不重试。

## 1. Reasoning 与 Structured Output

请求继续声明 preserved thinking 和 strict JSON Schema。服务端可能遵守，也可能间歇性忽略。

- reasoning 若存在：保存、哈希、不评分；
- reasoning 若缺失：记录 finding，不单独失败；
- content 通过原 parser：成功；
- content 不通过原 parser：parse failure；
- exact model 不匹配：API hard block；
- SSE/HTTP 不完整：infrastructure hard block。

## 2. Q0

24 条纯合成请求，0 benchmark rows。

硬门：

- 24 terminal；
- exact model / HTTP 200 / heartbeat / terminal / `[DONE]` / stop；
- infra=0；
- api=0；
- ledger balanced。

格式门：

- overall valid >=22/24；
- each contract >=10/12；
- each slot >=5/6。

状态：

- 24/24：`PASS_ALL_PARSED`
- 门通过但有 parse failure：`PASS_WITH_MODEL_FORMAT_FINDING`
- 否则：`FAIL`

## 3. Q1

原 60 条 Dev identity：

- valid >=54/60；
- 每 task type >=8/10；
- infra/api=0；
- 最大候选两类合同至少各一条成功。

## 4. Formal

Machine 197 后 Native 4,798。

parse failure：

- 不重试；
- 不删除；
- 计零；
- 纳入 parse failure rate。

infra/API error 必须在评分前清零。

## 5. 结果边界

V1.8 永久关闭，旧行不复用。V1.9 必须使用独立 run root、registry、Q0、smoke 和结果包。

Unified LLM 继续后置。Retriever V3 不进入当前路线。
