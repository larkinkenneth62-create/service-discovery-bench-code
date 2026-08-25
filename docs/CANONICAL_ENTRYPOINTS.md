# Canonical entry points

| Capability | Canonical path | Status |
|---|---|---|
| Dataset build | `scripts/release/build_v0_2_composable_expansion.py` | Current v0.2.0 builder |
| Release validation | `scripts/release/validate_release_zip.py` | Current archive validator |
| Dense retrieval | `scripts/evaluation/run_bge_retriever.py` | `BGE_DENSE_V2@200` |
| Qwen Native/Machine | `experiments/llm_v0_2_qwen_sse_selection_v1_5/code/run_qwen_sse_selection_v1_5.py` | Current V1.5 |
| LLM parsing | `experiments/llm_v0_2_qwen_sse_selection_v1_5/code/output_contracts_v1_5.py` | Current V1.5 |
| LLM scoring | `scripts/evaluation/score_native_machine_selection_v1_5.py` | Current V1.5 |
| LLM release bundle | `scripts/release/build_llm_native_machine_bundle_v1_5.py` | Current V1.5 |
| Publication boundary | `scripts/publication/audit_public_repo.py` | Required CI gate |

Numbered root scripts and V1.4 experiment code remain for historical reproduction and are not current formal LLM entry points.
