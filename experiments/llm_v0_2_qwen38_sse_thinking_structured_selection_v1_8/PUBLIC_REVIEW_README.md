# Qwen3.8 SSE Thinking Structured Selection V1.8 public review package

This directory contains only the static Selection V1.5 output contracts, Qwen3.8 thinking structured V1.8 parser/runner, 24-request Q0 preflight, token-budget freezer, synthetic harness, and route invariants. It contains no benchmark row, Gold, candidate instance, request, response, endpoint, credential, status log, or result.

The frozen model binding is official model `Qwen/Qwen3.8-27B-FP8`, served model `qwen3.8-27b-fp8`, experiment revision `QWEN38_SSE_THINKING_STRUCTURED_SELECTION_V1_8`. Every request uses `enable_thinking=true`, `preserve_thinking=true`, and a per-request strict JSON Schema whose candidate-ID enum is added outside the visible prompt. Every accepted response must contain separate non-empty reasoning while the complete `content` alone passes the unchanged strict JSON parser.

The fixed mapping is:

- Machine and Native `single_*`: `TOP5_RANKING_V1`;
- Native `multi_*` and `composable_*`: `SELECTED_SET_V1`;
- unregistered task types: fail closed;
- Unified: outside this revision.

Formal mode rejects `--limit` and `--request-id`, requires exactly 197 Machine or 4,798 Native rows, and cannot resume Qwen3.6 V1.4/V1.5 or Qwen3.8 V1.6/V1.7 results. Parser/model/schema failures are terminal and remain in the denominator; they are not infrastructure retries. The append-only attempt ledger is written before and after each network attempt, raw SSE is persisted incrementally, and ambiguous interrupted state blocks resume.

Q0 sends 24 distinct synthetic requests—four key slots times two contracts times three rounds—and transmits zero benchmark rows. Round 1 is globally serial and rounds 2–3 are four-way concurrent. Run `python tests/run_public_code_only_tests_v1_8.py` from this experiment directory or use the root commands in `README.md`.
