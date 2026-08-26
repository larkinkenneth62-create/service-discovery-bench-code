# Tasks, output contracts, and metrics

| Task family | Contract | Metrics |
|---|---|---|
| Single Service/API | `TOP5_RANKING_V1` | Hit@1, MRR@5, Recall@5, nDCG@5, parse-failure rate |
| Multi Service/API | `SELECTED_SET_V1` | exact set match, precision, recall, F1, completeness, Jaccard, under/over-selection, cardinality error, parse-failure rate |
| Composable Service/API | `SELECTED_SET_V1` | same selected-set metrics, with `n` and raw success counts |
| Machine | `TOP5_RANKING_V1` | Hit@1, MRR@5, Recall@5, nDCG@5, parse-failure rate |

Multiple acceptable Gold sets retain frozen outer-OR/inner-AND semantics. Scoring compares against the best legal alternative and never unions alternatives. Parse failure contributes zero to core metrics and one to parse-failure rate; it is never dropped or converted into an empty model-selected set.

Reports include six task rows, Macro-6, micro overall, Service/API, Single/Multi/Composable, candidate-count buckets, Gold-count buckets, and parse status.

## V1.5 R2 aggregation contract

Across all six Native tasks, the common primary metric is `task_success`: Hit@1 for Single tasks and Exact Set Match for Multi/Composable tasks. Ranking metrics are macro-averaged only over Single tasks; set-selection metrics are macro-averaged only over Multi/Composable tasks. Parse failure is reported across all tasks. Infrastructure and API errors block scoring and are never converted into model parse failures.
