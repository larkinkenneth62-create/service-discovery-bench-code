# Frozen prompt contract

This document exposes the exact static experiment prompt contract without any
benchmark query, candidate document, candidate ID, label, split, or generated
model response.

## System message

```text
You are a deterministic candidate-ranking engine. Return only JSON matching the supplied schema.
```

## User-message envelope

Native:

```text
SETTING=native
OUTPUT_SCHEMA={ranking_only_v9|ranking_and_selected_set_v9}
INPUT_JSON=<CANONICAL_VISIBLE_INPUT_JSON>
```

Machine Challenge:

```text
SETTING=machine_challenge
OUTPUT_SCHEMA=ranking_only_v9
INPUT_JSON=<CANONICAL_VISIBLE_INPUT_JSON>
```

`CANONICAL_VISIBLE_INPUT_JSON` is a compact UTF-8 JSON object whose keys are:

```json
{
  "candidate_documents": [
    {
      "candidate_id": "<OPAQUE_CANDIDATE_ID>",
      "document": "<MODEL_VISIBLE_SERIALIZED_DOCUMENT>"
    }
  ],
  "instructions": "<INSTRUCTION_STRING>",
  "prediction_target": "<service|api>",
  "query": "<BENCHMARK_QUERY>",
  "task_type": "<FROZEN_TASK_TYPE>"
}
```

For `ranking_only_v9`, the instruction string is:

```text
Return strict JSON with ranked_candidate_ids.
```

For `ranking_and_selected_set_v9`, the instruction string is:

```text
Return strict JSON with ranked_candidate_ids and selected_candidate_ids.
```

## Response contracts

`ranking_only_v9`:

```json
{
  "ranked_candidate_ids": ["<CANDIDATE_ID>"]
}
```

The ranking must be a duplicate-free full permutation of the supplied pool.

`ranking_and_selected_set_v9`:

```json
{
  "ranked_candidate_ids": ["<CANDIDATE_ID>"],
  "selected_candidate_ids": ["<CANDIDATE_ID>"]
}
```

The ranking must be a duplicate-free full permutation. The selected list must
be duplicate-free and contain only supplied candidate IDs.

## Chat Completions wrapper

```json
{
  "model": "Qwen3.6-35B-A3B-APEX-I-Compact.gguf",
  "messages": [
    {"role": "system", "content": "<SYSTEM_MESSAGE_ABOVE>"},
    {"role": "user", "content": "<USER_MESSAGE_ENVELOPE_ABOVE>"}
  ],
  "response_format": {"type": "json_object"},
  "temperature": 0,
  "top_p": 1,
  "n": 1,
  "seed": 0,
  "max_tokens": "<FROZEN_TRACK_BUDGET>",
  "stream": true
}
```

The runner requires the exact served model identity, a terminal SSE event, and
`[DONE]`. It does not repair, deduplicate, complete, or infer truncated output.

## Source of truth

- System prompt and strict parser: `code/frozen_adapter_v1.py`
- Runtime overrides and SSE transport: `code/run_qwen_sse_formal_v1.py`
- Governance: `00_GOVERNANCE/`

This document is explanatory. In any discrepancy, the copied frozen code and
governance documents control.
