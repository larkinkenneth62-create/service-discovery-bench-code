# Private runtime namespace

The private executor generates `SINGLE_API_CORRECTION_TOKEN_BUDGET_FREEZE_V1_10.json` here after hash-binding the 3,043 formal and ten frozen Dev-smoke manifests. The deterministic budget covers the Top-5 JSON plus a selected set containing every candidate, 64 safety tokens, and the frozen 4,096-token reasoning allowance. It reads no Gold or Gold cardinality. Runtime JSON, ledgers, SSE, requests, responses, reasoning, logs, predictions, and results are ignored and must never be committed.

R02 uses the public `QWEN38_SINGLE_API_CORRECTION_RUNTIME_FREEZE_V1_10_R02_TEMPLATE.json` only as a code-review template. The actual private runtime freeze is not committed. Its `q0_max_output_tokens` must equal the existing budget freeze's 5,924 tokens and its patch identity must be `V1_10_R02_Q0_BUDGET_ALIGNMENT`.
