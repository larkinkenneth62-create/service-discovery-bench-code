# Paper artifact map

| Claim/table class | Source | Configuration | Private input class | Expected output class | Availability |
|---|---|---|---|---|---|
| Six-task benchmark construction | `scripts/release/build_v0_2_composable_expansion.py` | `configs/`, `schemas/` | source datasets and adjudication | release archive and manifests | Code public; data private |
| Retriever baseline | `scripts/evaluation/run_bge_retriever.py` | `BGEConfig` | corpus, Query, split | Top-200 rankings | Code public; rows/results private |
| Qwen Native/Machine protocol | V1.5 runner and prompt registry | contract registry and token freeze | frozen request manifests | request statuses and parsed predictions | Code public; runs private |
| Ranking tables | `score_native_machine_selection_v1_5.py` | task/metric registry | Gold and parsed predictions | aggregate score tables | Code public; tables pending run |
| Result validation | `build_llm_native_machine_bundle_v1_5.py` | runtime provenance | run summaries and score tables | manifest, checksums, latest result | Code public; bundle private |

No numeric model result is claimed by this map before the corresponding private run passes its gate.
