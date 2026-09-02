# Canonical entry points

| Capability | Canonical path | Status |
|---|---|---|
| Dataset build | `scripts/release/build_v0_2_composable_expansion.py` | Current v0.2.0 builder |
| Release validation | `scripts/release/validate_release_zip.py` | Current archive validator |
| Dense retrieval | `scripts/evaluation/run_bge_retriever.py` | `BGE_DENSE_V2@200` |
| Qwen Native/Machine | `experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/code/run_qwen38_sse_structured_selection_v1_9.py` | Current Qwen3.8 structured V1.9 |
| DeepSeek Native/Machine | `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/run_deepseek_v4_flash_v2_2.py` | Independent DeepSeek V4 Flash V2.2 full six-task route |
| DeepSeek parser | `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/output_contracts_v2_2.py` | Three strict DeepSeek V2.2 output contracts |
| DeepSeek output budget | `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/freeze_output_budgets_v2_2.py` | Shared legal-answer upper-bound freeze |
| DeepSeek Q0 | `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/run_q0_v2_2.py` | Six synthetic requests; two per output contract |
| DeepSeek full scoring | `scripts/evaluation/score_deepseek_full_v2_2.py` | Independent 197-row Machine or 4,798-row Native scoring; no Qwen merge |
| DeepSeek paired comparison | `scripts/evaluation/build_deepseek_native_machine_comparison_v2_2.py` | Explicit frozen pairing IDs only |
| DeepSeek result bundle | `scripts/release/build_deepseek_v4_flash_v2_2_bundle.py` | Private paper-ready validation and checksummed ZIP |
| DeepSeek R3 provenance binding | `scripts/evaluation/build_deepseek_v2_2_r3_provenance_binding.py` | Exact-hash sidecar binding; original result files remain read-only |
| DeepSeek R3-only scoring | `scripts/evaluation/score_deepseek_full_v2_2_r3_nonstream.py` | Accepts only V2.2 R3 JSON non-stream rows and a result-specific PASS binding |
| DeepSeek R3 paired comparison | `scripts/evaluation/build_deepseek_native_machine_comparison_v2_2_r3_nonstream.py` | Explicit frozen pairing IDs only; otherwise `PAIRING_NOT_AVAILABLE` |
| DeepSeek R3 result bundle | `scripts/release/build_deepseek_v4_flash_v2_2_r3_nonstream_bundle.py` | Private R3 paper-ready validation and checksummed ZIP |
| LLM parsing | `experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/code/output_contracts_v1_5.py` | Unchanged Selection V1.5 contract |
| LLM scoring | `scripts/evaluation/score_native_machine_selection_v1_5.py` | Current V1.5 |
| LLM release bundle | `scripts/release/build_qwen38_structured_native_machine_bundle_v1_9.py` | Current Qwen3.8 V1.9 |
| Publication boundary | `scripts/publication/audit_public_repo.py` | Required CI gate |

Numbered root scripts and V1.4 experiment code remain for historical reproduction and are not current formal LLM entry points.

Provider result namespaces are disjoint. The Qwen and DeepSeek entries above share task definitions and frozen metric formulas only; they do not share credentials, runtime freezes, output paths, status rows, or model predictions. Generic LLM parsing is provider-specific: use the DeepSeek parser for DeepSeek V2.2 and the Qwen parser for Qwen routes.
