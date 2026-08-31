# Single API contract correction addendum V1.10

`single_service_discovery` remains ranking-only because its Gold service is unique. `single_api_recommendation` has one parent service but may require multiple Gold APIs; its formal contract is therefore Top-5 ranking plus a model-selected minimal sufficient API set. Multi and composable tasks retain their frozen selected-set contracts.

V1.10 reruns only the 3,043 Native Test rows whose task type is `single_api_recommendation`. It does not modify the dataset, split, candidate order, model, decoding, retriever, Machine, Unified, or the prior V1.9 artifacts. Test Gold cardinality is excluded from prompts, schemas, budgets, and selected-cardinality decisions.

Strict parse failures are retained in the denominator, scored zero, and never retried as model-format failures. Multiple acceptable Gold sets retain outer-OR/inner-AND scoring semantics and are never unioned.

## R02 Q0 budget alignment

R01 sent its synthetic Q0 with 1,024 output tokens while the existing formal budget freeze was 5,924 tokens. Runtime patch `V1_10_R02_Q0_BUDGET_ALIGNMENT` changes only Q0 budget plumbing and audit metadata so that Q0 is tested under the already frozen 5,924-token budget. The original eight R01 responses remain an archived failed run. The task contract, visible prompt, schema, parser, model, sampling, thinking mode, retriever, dataset, and formal budget are unchanged.
