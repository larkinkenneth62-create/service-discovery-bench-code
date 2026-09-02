# DeepSeek V2.2 R3 non-stream gateway adapter

This additive implementation revision supports a temporary OpenAI-compatible gateway that accepts JSON chat completions but rejects SSE streaming. It does not replace or mutate the frozen R2 SSE implementation.

## Frozen scientific contract

- Experiment revision: `DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2`
- Implementation revision: `DEEPSEEK_V4_FLASH_V2_2_R3_NONSTREAM_GATEWAY`
- Served/request model ID: `DeepSeek-V4-Flash`
- Prompt, candidate ordering, output contracts, output-budget method, retry accounting, stage gates, and scorers remain unchanged.
- Stage order remains synthetic Q0, Dev smoke 60, Machine 197, then Native 4,798.
- R3 is transport-only: prompts, candidate IDs/order, three output contracts, Gold handling, output budgets, retry taxonomy, and frozen scoring formulas are unchanged.

## Transport delta

- `stream` is fixed to `false`.
- The response is captured as one complete JSON object.
- Raw response evidence is saved as `raw_response_attempt_<n>.json` with a SHA-256 binding in `ATTEMPT_LEDGER.jsonl`.
- A completed response must have exactly one choice with textual `message.content` and a registered finish reason.
- The runner records `response_complete_received=true`; SSE terminal and `[DONE]` fields are explicitly `null`, not synthesized.
- Read timeout is 7,200 seconds because this transport has no SSE heartbeat.

## Security and publication boundary

The endpoint and credential are supplied only through environment variables. Results record only the endpoint SHA-256. The repository contains no endpoint, credential, benchmark rows, or model outputs.

## Offline provenance and scoring

An original R3 result may retain `git_commit_sha=UNKNOWN`. It is never rewritten. The separate provenance builder can issue a PASS sidecar only when the recorded runner/parser/runtime/budget/manifest hashes and the immutable result-file hashes agree with the exact public inference commit blobs. The R3-only scorer requires that result-specific sidecar and rejects R2, SSE, Qwen, mixed, or foreign rows. Model-format failures remain in the denominator and score zero without repair or retry. The public repository contains only code and synthetic tests; real status rows, predictions, scores, and bundles remain private.

## Entrypoints

- `code/freeze_output_budgets_v2_2_r3_nonstream.py`
- `code/run_q0_v2_2_r3_nonstream.py`
- `code/run_deepseek_v4_flash_v2_2_r3_nonstream.py`
- `schemas/DEEPSEEK_V4_FLASH_RUNTIME_FREEZE_V2_2_R3_NONSTREAM.json`
- `../../scripts/evaluation/build_deepseek_v2_2_r3_provenance_binding.py`
- `../../scripts/evaluation/score_deepseek_full_v2_2_r3_nonstream.py`
- `../../scripts/evaluation/build_deepseek_native_machine_comparison_v2_2_r3_nonstream.py`
- `../../scripts/release/build_deepseek_v4_flash_v2_2_r3_nonstream_bundle.py`
