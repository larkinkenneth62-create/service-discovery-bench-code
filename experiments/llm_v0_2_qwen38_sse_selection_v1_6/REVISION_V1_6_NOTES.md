# Qwen3.8 SSE Selection V1.6

This revision does not change the benchmark route or Selection V1.5 output-task semantics. It binds a new independent model route:

- official model `Qwen/Qwen3.8-27B-FP8`, served ID `qwen3.8-27b-fp8`;
- explicit non-thinking request flags and fail-closed reasoning/model response checks;
- one deterministic sequential worker per API key, preventing same-key overlap;
- selected-set token budgets covering the complete legal in-pool output space without reading Gold;
- exact manifest/status identity checks and blocking infrastructure/API errors before scoring;
- Macro-6 Task Success plus contract-specific ranking and set-selection macros;
- exact 60/197/4,798 bundle gates, P50/P95 latency, ZIP CRC and SHA-256 sidecar;
- one-time Service/API corpus encoding in the public BGE reproduction entry point;
- supported-version GitHub CI for Python 3.11, 3.12 and 3.13.

Q0 must return the exact Qwen3.8 served ID, strict parseable JSON, and no reasoning content before Dev smoke is authorized. No Qwen3.6 row may be resumed or reused.
