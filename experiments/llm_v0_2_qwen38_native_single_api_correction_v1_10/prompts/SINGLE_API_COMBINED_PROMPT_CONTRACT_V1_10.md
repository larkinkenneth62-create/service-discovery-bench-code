# Single API combined output contract V1.10

For `single_api_recommendation`, the model receives only the frozen query, task metadata, ordered candidate documents, and candidate IDs.

It must first rank the five most relevant candidates (or all candidates when fewer than five exist), then select the minimal sufficient API set needed to complete the request. It must select every necessary API and exclude APIs that are only similar or unnecessary. No target set size is given or implied.

The response is one strict JSON object with exactly these fields:

```json
{
  "ranked_candidate_ids": ["<up to five candidate IDs>"],
  "selected_candidate_ids": ["<minimal sufficient API set>" ]
}
```

The dynamic JSON Schema binds both arrays to the current visible candidate-ID enum. Gold, Gold count, acceptable alternatives, split evidence, and evaluation metadata are never included in the request, schema, token budget, or selected-cardinality decision.

