# Qwen SSE Selection V1.5 public review package

This directory contains only the static V1.5 output contracts, parser, runner, token-budget freezer, synthetic harness, and route invariants. It contains no benchmark row, Gold, candidate instance, request, response, endpoint, credential, status log, or result.

The fixed mapping is:

- Machine and Native `single_*`: `TOP5_RANKING_V1`;
- Native `multi_*` and `composable_*`: `SELECTED_SET_V1`;
- unregistered task types: fail closed;
- Unified: outside this revision.

Formal mode rejects `--limit` and `--request-id`, requires exactly 197 Machine or 4,798 Native rows, and cannot resume V1.4 results. Parser failures are terminal model failures and remain in the denominator; they are not infrastructure retries.

Run `python tests/run_public_code_only_tests_v1_5.py` from this experiment directory or use the root commands in `README.md`.
