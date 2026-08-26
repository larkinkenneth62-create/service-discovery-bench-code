# Qwen3.8 SSE Selection V1.6 public review package

This directory contains only the static Selection V1.5 output contracts, Qwen3.8 V1.6 parser/runner, Q0 preflight, token-budget freezer, synthetic harness, and route invariants. It contains no benchmark row, Gold, candidate instance, request, response, endpoint, credential, status log, or result.

The frozen model binding is official model `Qwen/Qwen3.8-27B-FP8`, served model `qwen3.8-27b-fp8`, experiment revision `QWEN38_SSE_SELECTION_V1_6`, with thinking disabled in every request and verified absent in every accepted response.

The fixed mapping is:

- Machine and Native `single_*`: `TOP5_RANKING_V1`;
- Native `multi_*` and `composable_*`: `SELECTED_SET_V1`;
- unregistered task types: fail closed;
- Unified: outside this revision.

Formal mode rejects `--limit` and `--request-id`, requires exactly 197 Machine or 4,798 Native rows, and cannot resume Qwen3.6 V1.4/V1.5 results. Parser failures are terminal model failures and remain in the denominator; they are not infrastructure retries.

Run `python tests/run_public_code_only_tests_v1_6.py` from this experiment directory or use the root commands in `README.md`.
