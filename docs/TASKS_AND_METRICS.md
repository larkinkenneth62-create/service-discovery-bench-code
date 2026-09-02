# Tasks, output contracts, and metrics

| Task family | Contract | Metrics |
|---|---|---|
| Single Service | `TOP5_RANKING_V1` | Hit@1, MRR@5, Recall@5, nDCG@5, parse-failure rate |
| Single API | `RANKING_AND_SELECTED_SET_V1_10` | ranking metrics plus exact set match, precision, recall, F1, completeness, Jaccard, under/over-selection, cardinality error, parse-failure rate |
| Multi Service/API | `SELECTED_SET_V1` | exact set match, precision, recall, F1, completeness, Jaccard, under/over-selection, cardinality error, parse-failure rate |
| Composable Service/API | `SELECTED_SET_V1` | same selected-set metrics, with `n` and raw success counts |
| Machine | `TOP5_RANKING_V1` | Hit@1, MRR@5, Recall@5, nDCG@5, parse-failure rate |

Multiple acceptable Gold sets retain frozen outer-OR/inner-AND semantics. Scoring compares against the best legal alternative and never unions alternatives. Parse failure contributes zero to core metrics and one to parse-failure rate; it is never dropped or converted into an empty model-selected set.

Reports include six task rows, Macro-6 Exact Task Success, Micro Exact Task Success, Single-Service ranking, Single-API ranking, set-selection macro, Service/API, Single/Multi/Composable, candidate-count buckets, Gold-count buckets, and parse status.

For DeepSeek V2.2 R2 and the transport-only R3 implementation, exact task success is Hit@1 only for Single Service. Single API and all Multi/Composable tasks use selected-set Exact Set Match. R3 does not modify any scoring formula: its dedicated scorer adds result-identity and provenance checks around the frozen R2 algorithms. `MACRO_6_EXACT_TASK_SUCCESS` is computed after task-level aggregation with six tasks weighted equally. `SET_SELECTION_MACRO_TASK_EQUAL` weights the five set-selection tasks equally; the distinct `SET_SELECTION_MICRO_ROW_WEIGHTED` reports the direct row-weighted result. Metrics that do not apply are emitted as `N/A`/`NOT_AVAILABLE`, not numeric zero. Optional source/evidence strata require an explicit private metadata field map.

## V1.5 R2 aggregation contract

Across all six Native tasks, the common primary metric is `task_success`: Hit@1 for Single tasks and Exact Set Match for Multi/Composable tasks. Ranking metrics are macro-averaged only over Single tasks; set-selection metrics are macro-averaged only over Multi/Composable tasks. Parse failure is reported across all tasks. Infrastructure and API errors block scoring and are never converted into model parse failures.

## V1.10 corrected exact-task contract

The V1.5 R2 aggregate above is retained as `V1.9_HISTORICAL_MIXED_CONTRACT_DIAGNOSTIC`. It is not a unified exact-completion result because `single_api_recommendation` can require multiple APIs even though it has one parent service. V1.10 defines exact task success as Hit@1 for Single Service and Exact Set Match for Single API, Multi, and Composable tasks. Test Gold cardinality is never used to select an output set.
