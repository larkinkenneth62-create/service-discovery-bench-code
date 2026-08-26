# Qwen3.8 SSE Thinking Selection V1.7 public review package

This directory contains only the static Selection V1.5 output contracts, Qwen3.8 thinking V1.7 parser/runner, dual-contract Q0 preflight, token-budget freezer, synthetic harness, and route invariants. It contains no benchmark row, Gold, candidate instance, request, response, endpoint, credential, status log, or result.

The frozen model binding is official model `Qwen/Qwen3.8-27B-FP8`, served model `qwen3.8-27b-fp8`, experiment revision `QWEN38_SSE_THINKING_SELECTION_V1_7`. Every request uses `enable_thinking=true` and `preserve_thinking=true`; every accepted response must contain separate non-empty reasoning while the complete `content` alone passes the unchanged strict JSON parser.

The fixed mapping is:

- Machine and Native `single_*`: `TOP5_RANKING_V1`;
- Native `multi_*` and `composable_*`: `SELECTED_SET_V1`;
- unregistered task types: fail closed;
- Unified: outside this revision.

Formal mode rejects `--limit` and `--request-id`, requires exactly 197 Machine or 4,798 Native rows, and cannot resume Qwen3.6 V1.4/V1.5 or Qwen3.8 V1.6 results. Parser failures are terminal model failures and remain in the denominator; they are not infrastructure retries.

Q0 sends eight synthetic requests—four key slots times Top-5 and selected-set—and transmits zero benchmark rows. Run `python tests/run_public_code_only_tests_v1_7.py` from this experiment directory or use the root commands in `README.md`.
