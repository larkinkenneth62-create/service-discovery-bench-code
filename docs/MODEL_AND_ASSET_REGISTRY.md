# Model and asset registry

| Asset | Identifier | Revision | License/status | Included |
|---|---|---|---|---|
| Dense retriever | `BAAI/bge-small-en-v1.5` | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` | MIT | No weights |
| Qwen inference model | `Qwen3.6-35B-A3B-APEX-I-Compact.gguf` | deployment identity fixed by protocol | Owner license verification required | No GGUF |
| Qwen tokenizer binding | `Qwen/Qwen3.6-35B-A3B` | `995ad96eacd98c81ed38be0c5b274b04031597b0` | Upstream terms apply | No tokenizer files |

The registered dense configuration uses the fixed English query instruction, CLS last-hidden-state pooling, L2 normalization, float32 embeddings, exact inner product, Service/API-separated corpora, deterministic ID tie-break, and Top-200 depth. Registered RRF uses constant 60.
