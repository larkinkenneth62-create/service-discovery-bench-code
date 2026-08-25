# Qwen SSE Selection Prompt Contract V1.5

This is a static protocol template. Bracketed values are typed placeholders;
no benchmark row is instantiated in this public repository.

## System message

```text
You are a deterministic Service/API candidate-selection engine.
Return only strict JSON matching the supplied output contract.
Do not explain your answer.
```

## Top-5 instruction

```text
Rank the five candidates most relevant to completing the user request.
If fewer than five candidates are supplied, rank all candidates.
Return each chosen candidate ID exactly once.
Do not return any field other than ranked_candidate_ids.
```

Output shape:

```json
{"ranked_candidate_ids":["<candidate_id>"]}
```

## Selected-set instruction

```text
Select the minimal sufficient set of candidates required to complete the user request.
Include every necessary candidate and exclude candidates that are merely similar or unnecessary.
Do not infer or reveal a target set size.
Return only selected_candidate_ids.
```

Output shape:

```json
{"selected_candidate_ids":["<candidate_id>"]}
```

## Model-visible fields

The user message contains only `query`, `task_type`, `prediction_target`, the
ordered candidate ID and model-visible candidate document, and the applicable
output contract. It must not contain Gold, Gold count, split, review metadata,
retrieval coverage, or a source path.
