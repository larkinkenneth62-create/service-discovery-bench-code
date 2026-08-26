# Qwen3.8 SSE Thinking Selection V1.7

This revision does not change the benchmark route, Prompt semantics, parser, scorer, or Selection V1.5 output-task semantics. It binds a new independent runtime route after V1.6 failed strict JSON at Q0 with zero benchmark rows:

- official model `Qwen/Qwen3.8-27B-FP8`, served ID `qwen3.8-27b-fp8`;
- explicit preserved-thinking request flags, separate reasoning-channel evidence, and fail-closed model/content checks;
- one deterministic sequential worker per API key, preventing same-key overlap;
- selected-set token budgets covering the complete legal in-pool output space without reading Gold, plus a frozen 4,096-token reasoning allowance;
- exact manifest/status identity checks and blocking infrastructure/API errors before scoring;
- Macro-6 Task Success plus contract-specific ranking and set-selection macros;
- exact 60/197/4,798 bundle gates, P50/P95 latency, ZIP CRC and SHA-256 sidecar;
- one-time Service/API corpus encoding in the public BGE reproduction entry point;
- supported-version GitHub CI for Python 3.11, 3.12 and 3.13.

Q0 must pass eight synthetic requests: four key slots times Top-5 and selected-set. Each must return the exact Qwen3.8 served ID, heartbeat, terminal event, `[DONE]`, `finish_reason=stop`, non-empty separate reasoning, and strict parseable `content`. No Qwen3.6 or V1.6 row may be resumed or reused.
