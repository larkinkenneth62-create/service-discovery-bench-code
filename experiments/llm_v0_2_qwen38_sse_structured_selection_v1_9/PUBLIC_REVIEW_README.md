# Qwen3.8 SSE Structured Selection V1.9 public review package

This code-only directory contains the static Selection V1.5 contracts, V1.9 parser/runner, 24-request Q0 preflight, token-budget freezer, synthetic harness, governance, and route invariants. It contains no benchmark row, Gold, candidate instance, instantiated request, response, endpoint, credential, private log, or result.

The frozen binding is official model `Qwen/Qwen3.8-27B-FP8`, served model `qwen3.8-27b-fp8`, and revision `QWEN38_SSE_STRUCTURED_SELECTION_MODEL_FAILURE_ACCOUNTING_V1_9`. Requests retain `enable_thinking=true`, `preserve_thinking=true`, SSE, and a per-request strict JSON Schema with the visible candidate-ID enum. Reasoning is optional audit metadata. The complete `content` field alone is parsed without extraction or repair; invalid Selection V1.5 content is a non-retryable `parse_failure`, retained in the denominator, and scored zero.

The fixed mapping is:

- Machine and Native `single_*`: `TOP5_RANKING_V1`;
- Native `multi_*` and `composable_*`: `SELECTED_SET_V1`;
- unregistered task types: fail closed;
- Unified: outside this revision.

Formal mode rejects row filters, requires exactly 197 Machine or 4,798 Native rows, and cannot resume or reuse V1.8 or earlier runs. Model mismatch, authentication, transport, incomplete SSE, and ambiguous attempt-ledger state remain hard blocks.

Q0 sends 24 distinct synthetic requests (four key slots × two contracts × three rounds) and transmits zero benchmark rows. It passes with at least 22/24 parsed, at least 10/12 per contract, at least 5/6 per key slot, and zero infrastructure/API errors. Run `python tests/run_public_code_only_tests_v1_9.py` from this experiment directory or use the root commands in `README.md`.
