# ServiceDiscoveryBench Qwen3.8 V1.9 快速执行规划

- 文档版本：`V1.8-QWEN38-SSE-MODEL-FAILURE-ACCOUNTING-FINAL`
- 对应纲领：Protocol V1.6
- 基线：`ed44ef0ab38c68ebb1508cd0807810d91db38183`
- 目标：在不修改 benchmark 语义的前提下，使纯公网端点的偶发格式失败成为可计分模型失败，而非整批 API 阻断。

## 阶段 A：GitHub 修订，1–3 小时

1. 从基线创建 V1.9 分支。
2. 新建 V1.9 experiment 目录。
3. 修改 runner、Q0、runtime freeze、registry、文档和测试。
4. Python 3.11/3.12/3.13 CI 全绿。
5. Publication audit PASS。
6. 推送分支并生成脱敏 handoff。

## 阶段 B：V1.9 Q0，约 0.5–2 小时

重新运行 24 条纯合成请求。

允许：

```text
22–24 条 strict parse
```

但不允许：

```text
infra/API/model identity error
```

若通过，立即进入 60 条 smoke。

## 阶段 C：60 条 Dev smoke，约 2–8 小时

门保持 54/60 与每类 8/10。

若通过：

```text
Machine 197
→ Native 4,798
```

若失败：停止，不发送 Test。

## 阶段 D：Machine，约 2–8 小时

197/197 terminal；parse failure 保留计零；infra/API 清零。

## 阶段 E：Native，约 12–48 小时

4,798/4,798 terminal；旧 revision 行复用 0。

## 阶段 F：评分与打包，2–6 小时

报告：

- Single/Machine ranking metrics；
- Multi/Composable set metrics；
- Macro-6 Task Success；
- parse failure rate 与 taxonomy；
- latency/heartbeat/retry；
- Q0/Smoke findings。

## 当前明确不做

- 不修服务器；
- 不访问 GPU 后台；
- 不重试模型格式失败；
- 不抽取或修复 JSON；
- 不修改数据或 Retriever；
- 不启动 Unified LLM。
