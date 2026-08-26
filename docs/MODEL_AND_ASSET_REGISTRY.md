# Model and asset registry

| Asset | Identifier | Revision | License/status | Included |
|---|---|---|---|---|
| Dense retriever | `BAAI/bge-small-en-v1.5` | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` | MIT | No weights |
| Qwen3.8 inference model | `Qwen/Qwen3.8-27B-FP8` (served as `qwen3.8-27b-fp8`) | `QWEN38_SSE_SELECTION_V1_6` | Owner license verification required | No weights |
| Qwen3.8 token-budget counter | `UTF8_BYTE_UPPER_BOUND_V1` | deterministic local counter | N/A | Code only |
| Historical Qwen3.6 route | `Qwen3.6-35B-A3B-APEX-I-Compact.gguf` | terminated at Q0 before formal execution | No result rows reusable in V1.6 | No GGUF |

The registered dense configuration uses the fixed English query instruction, CLS last-hidden-state pooling, L2 normalization, float32 embeddings, exact inner product, Service/API-separated corpora, deterministic ID tie-break, and Top-200 depth. Registered RRF uses constant 60.

The Qwen3.8 V1.6 route requires SSE, exact response model identity, and non-thinking generation (`enable_thinking=false`, `preserve_thinking=false`). The public repository contains no endpoint, credential, benchmark row, prompt instance, response, or model weight.
