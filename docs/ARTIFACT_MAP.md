# Paper artifact map

| Claim/table class | Source | Configuration | Private input class | Expected output class | Availability |
|---|---|---|---|---|---|
| Six-task benchmark construction | `scripts/release/build_v0_2_composable_expansion.py` | `configs/`, `schemas/` | source datasets and adjudication | release archive and manifests | Code public; data private |
| Retriever baseline | `scripts/evaluation/run_bge_retriever.py` | `BGEConfig` | corpus, Query, split | Top-200 rankings | Code public; rows/results private |
| Qwen3.8 Native/Machine protocol | V1.6 runner and Selection V1.5 prompt registry | model/runtime/output registries and token freeze | frozen request manifests | request statuses, raw SSE events and parsed predictions | Code public; runs private |
| Ranking tables | `score_native_machine_selection_v1_5.py` | task/metric registry | Gold and parsed predictions | aggregate score tables | Code public; tables pending run |
| Result validation | `build_llm_native_machine_bundle_v1_5.py` | runtime provenance | run summaries and score tables | manifest, checksums, latest result | Code public; bundle private |
| Single API contract correction | `experiments/llm_v0_2_qwen38_native_single_api_correction_v1_10/` | combined contract and V1.10 runtime freeze | filtered 3,043-row request manifest | statuses and combined predictions | Code public; rows/results private |
| Corrected Native scoring | `scripts/evaluation/score_single_api_correction_v1_10.py` | exact-task aggregation | V1.9 retained rows plus V1.10 Single API outputs | corrected six-task and comparison tables | Code public; tables private |
| DeepSeek V4 Flash Native/Machine | `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/` | independent V2.2 runtime, budget, and three output contracts | frozen DeepSeek-visible manifests | DeepSeek-only statuses and parsed predictions | Code public; rows/results private |
| DeepSeek full-track scoring | `scripts/evaluation/score_deepseek_full_v2_2.py` | exact-task six-task aggregation | one complete DeepSeek Machine or Native run plus its Gold manifest | independent DeepSeek score tables | Code public; tables pending run |
| DeepSeek paired comparison | `scripts/evaluation/build_deepseek_native_machine_comparison_v2_2.py` | explicit frozen pairing IDs | Native/Machine per-request scores and private pairing artifact | matched ranking deltas or `PAIRING_NOT_AVAILABLE` | Code public; pairing/results private |
| DeepSeek paper-ready bundle | `scripts/release/build_deepseek_v4_flash_v2_2_bundle.py` | R2 prerequisite hash chain and provenance | complete Q0/Smoke/Machine/Native summaries, statuses, and scores | validated ZIP, sidecar, manifest, checksums, latest result | Code public; bundle private |
| DeepSeek R3 result provenance | `scripts/evaluation/build_deepseek_v2_2_r3_provenance_binding.py` | exact public-commit blob and private result hashes | unedited R3 summaries, statuses, ledgers, manifests, runtime, and budget | result-specific PASS sidecar and report | Code public; sidecar/results private |
| DeepSeek R3 offline scoring | `scripts/evaluation/score_deepseek_full_v2_2_r3_nonstream.py` | frozen R2 formulas, R3 identity checks, and exact private request/formal/truth hash crosswalk | one complete R3 Machine or Native run and its result-specific binding | independent R3 score tables and scoring provenance | Code public; Gold/tables private |
| DeepSeek R3 paired comparison | `scripts/evaluation/build_deepseek_native_machine_comparison_v2_2_r3_nonstream.py` | explicit frozen pairing IDs and R3 binding | R3 Native/Machine per-request scores | matched deltas or `PAIRING_NOT_AVAILABLE` | Code public; pairing/results private |
| DeepSeek R3 paper-ready bundle | `scripts/release/build_deepseek_v4_flash_v2_2_r3_nonstream_bundle.py` | R3 prerequisite and provenance hash chain | complete R3 gates, unedited statuses, and R3 scores | validated ZIP, sidecar, manifest, checksums, latest result | Code public; bundle private |

No numeric model result is claimed by this map before the corresponding private run passes its gate.
