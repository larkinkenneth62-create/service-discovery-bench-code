# Qwen3.8 SSE Thinking Structured Selection V1.8

This revision does not change the benchmark route, visible Prompt semantics, parser, scorer, or Selection V1.5 output-task semantics. It binds a new independent runtime route after V1.7 passed synthetic Q0 but failed closed during Dev smoke when one terminal response lost the separate reasoning channel and returned no final JSON:

- official model `Qwen/Qwen3.8-27B-FP8`, served ID `qwen3.8-27b-fp8`;
- explicit preserved-thinking request flags, separate reasoning-channel evidence, and fail-closed model/content checks;
- per-request strict JSON Schema with an enum containing exactly the visible candidate IDs; the visible output contract remains unchanged and no Gold is read;
- append-only attempt start/finish evidence, incremental raw-SSE JSONL, and fail-closed resume on ambiguous interrupted state;
- initial plus at most three frozen network/HTTP retries; model, reasoning-channel, choice, schema, and parser failures are never retried;
- one deterministic sequential worker per API key, preventing same-key overlap;
- selected-set token budgets covering the complete legal in-pool output space without reading Gold, plus a frozen 4,096-token reasoning allowance;
- exact manifest/status identity checks and blocking infrastructure/API errors before scoring;
- Macro-6 Task Success plus contract-specific ranking and set-selection macros;
- exact 60/197/4,798 bundle gates, P50/P95 latency, ZIP CRC and SHA-256 sidecar;
- one-time Service/API corpus encoding in the public BGE reproduction entry point;
- supported-version GitHub CI for Python 3.11, 3.12 and 3.13.

Q0 must pass 24 distinct synthetic requests: four key slots times Top-5 and selected-set times three rounds. Round 1 is globally serial; rounds 2 and 3 are globally four-way concurrent. Each request must return the exact Qwen3.8 served ID, heartbeat, terminal event, `[DONE]`, `finish_reason=stop`, non-empty separate reasoning, and strict parseable `content`. No Qwen3.6, V1.6, or V1.7 row may be resumed or reused.
