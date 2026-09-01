# DeepSeek V2.2 R3 non-stream gateway adapter

This additive implementation revision supports a temporary OpenAI-compatible gateway that accepts JSON chat completions but rejects SSE streaming. It does not replace or mutate the frozen R2 SSE implementation.

## Frozen scientific contract

- Experiment revision: `DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2`
- Implementation revision: `DEEPSEEK_V4_FLASH_V2_2_R3_NONSTREAM_GATEWAY`
- Served/request model ID: `DeepSeek-V4-Flash`
- Prompt, candidate ordering, output contracts, output-budget method, retry accounting, stage gates, and scorers remain unchanged.
- Stage order remains synthetic Q0, Dev smoke 60, Machine 197, then Native 4,798.

## Transport delta

- `stream` is fixed to `false`.
- The response is captured as one complete JSON object.
- Raw response evidence is saved as `raw_response_attempt_<n>.json` with a SHA-256 binding in `ATTEMPT_LEDGER.jsonl`.
- A completed response must have exactly one choice with textual `message.content` and a registered finish reason.
- The runner records `response_complete_received=true`; SSE terminal and `[DONE]` fields are explicitly `null`, not synthesized.
- Read timeout is 7,200 seconds because this transport has no SSE heartbeat.

## Security and publication boundary

The endpoint and credential are supplied only through environment variables. Results record only the endpoint SHA-256. The repository contains no endpoint, credential, benchmark rows, or model outputs.

## Entrypoints

- `code/freeze_output_budgets_v2_2_r3_nonstream.py`
- `code/run_q0_v2_2_r3_nonstream.py`
- `code/run_deepseek_v4_flash_v2_2_r3_nonstream.py`
- `schemas/DEEPSEEK_V4_FLASH_RUNTIME_FREEZE_V2_2_R3_NONSTREAM.json`
