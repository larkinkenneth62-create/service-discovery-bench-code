# Qwen3.8 Native Single API correction V1.10

This code-only revision corrects `single_api_recommendation` from ranking-only to a combined Top-5 ranking and model-selected minimal sufficient API set. One parent service may require multiple APIs; no output cardinality is supplied to the model.

The formal scope is exactly 3,043 Native Test Single API rows. The other 1,755 Native rows, Machine, Unified, Retriever V2, and all V1.9 artifacts remain unchanged. The public directory contains no benchmark row, Gold, candidate instance, request, response, SSE event, endpoint, credential, or private result.

The strict JSON response has exactly `ranked_candidate_ids` and `selected_candidate_ids`. Both fields are constrained only by the current candidate-ID enum. The selected set may contain more than five candidates. Empty, wrapped, repaired, or otherwise invalid content is a non-retryable parse failure retained in the denominator.

Execution gates are eight synthetic requests (four slots, serial then concurrent), the original frozen ten Single API Dev-smoke identities, and one fresh 3,043-row formal run. Corrected scoring uses Hit@1 for Single Service and Exact Set Match for Single API, Multi, and Composable tasks. V1.9 mixed-contract aggregates remain historical diagnostics.

R02 aligns the synthetic Q0 output budget with the existing 5,924-token formal freeze. This is a runtime-budget plumbing correction only: the previous 1,024-token Q0 remains archived as failed, while prompt, parser, schema, model, thinking, sampling, failure accounting, and the formal budget remain unchanged.

Run focused tests with:

```text
python -m pytest tests/unit/test_single_api_correction_v1_10.py -q
```
