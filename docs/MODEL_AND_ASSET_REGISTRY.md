# Model and asset registry

| Asset | Identifier | Revision | License/status | Included |
|---|---|---|---|---|
| Dense retriever | `BAAI/bge-small-en-v1.5` | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` | MIT | No weights |
| Qwen3.8 inference model | `Qwen/Qwen3.8-27B-FP8` (served as `qwen3.8-27b-fp8`) | `QWEN38_SSE_THINKING_STRUCTURED_SELECTION_V1_8` | Owner license verification required | No weights |
| Qwen3.8 token-budget counter | `UTF8_BYTE_UPPER_BOUND_PLUS_REASONING_4096_V1` | deterministic local counter | N/A | Code only |
| Historical Qwen3.6 route | `Qwen3.6-35B-A3B-APEX-I-Compact.gguf` | terminated at Q0 before formal execution | No result rows reusable in V1.7 | No GGUF |
| Historical Qwen3.8 non-thinking route | `qwen3.8-27b-fp8` | V1.6 terminated at Q0 strict-JSON gate | Zero benchmark rows; no result rows reusable in V1.7 | No weights |

The registered dense configuration uses the fixed English query instruction, CLS last-hidden-state pooling, L2 normalization, float32 embeddings, exact inner product, Service/API-separated corpora, deterministic ID tie-break, and Top-200 depth. Registered RRF uses constant 60.

The Qwen3.8 V1.7 route requires SSE, exact response model identity, `enable_thinking=true`, and `preserve_thinking=true`. Preserved reasoning must be non-empty and separate; it is hashed and saved but not parsed or scored. The complete `content` field alone must pass the unchanged strict Selection V1.5 parser. The public repository contains no endpoint, credential, benchmark row, prompt instance, response, or model weight.
