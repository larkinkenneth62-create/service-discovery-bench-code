# Changelog

## V1.10 — Single API ranking-and-set contract correction

- Correct `single_api_recommendation` from ranking-only to a combined Top-5 ranking plus minimal-sufficient selected API set.
- Add a strict parser and candidate-enum schema supporting selected sets larger than five without using Test Gold cardinality.
- Add an 8-request synthetic gate, a 10-row frozen Single API Dev-smoke gate, and a 3,043-row targeted formal runner.
- Add corrected six-task exact-completion scoring while retaining V1.9 mixed-contract results as historical diagnostics.
- Add focused regression coverage and code-only publication controls; no benchmark rows or private run artifacts are published.

## Qwen3.8 Structured Selection V1.9 — 2026-08-26

- Closed V1.8 fail-closed after its synthetic Q0 produced 23/24 strict parses with zero infrastructure errors and zero benchmark rows; no V1.8 result row is reused.
- Made the reasoning channel optional audit metadata while preserving the unchanged Selection V1.5 full-content parser and scorer.
- Classified complete-envelope invalid model content as a non-retryable `parse_failure` that remains in the denominator and scores zero; model identity, transport, authentication, and incomplete SSE remain hard blocks.
- Froze a 24-request Q0 feasibility gate: at least 22/24 overall, 10/12 per contract, 5/6 per key slot, and zero infrastructure/API errors.

## Qwen3.8 Thinking Structured Selection V1.8 — 2026-08-26

- Terminated V1.7 fail-closed after a Dev-smoke reasoning-channel contract violation; no V1.7 Machine or Native run is reused.
- Added per-request strict JSON Schema enforcement with candidate-ID enums while retaining the visible Selection V1.5 prompt, parser, scorer, and task mapping.
- Added append-only attempt start/finish evidence, incrementally persisted raw SSE, explicit initial-plus-three network retry semantics, and fail-closed resume checks.
- Expanded zero-benchmark Q0 to 24 distinct requests covering four key slots, both output contracts, one serial round, and two four-way concurrent rounds.

## Unreleased — Qwen3.8 SSE Thinking Selection V1.7

- Recorded the V1.6 Q0 strict-JSON failure and its zero transmitted benchmark rows; no V1.6 output is reused.
- Added the independent preserved-thinking runtime contract: reasoning is saved separately and never scored, while the full `content` alone must pass the unchanged Selection V1.5 parser.
- Expanded Q0 to four key slots times Top-5 and selected-set, added runtime-freeze/hash gates, and froze a 4,096-token formal reasoning allowance.
- Preserved Prompt semantics, parser, scorer, data, candidate order, Retriever/K, smoke identity, and formal row counts.

## Unreleased — Qwen3.8 SSE Selection V1.6

- Registered the independent `Qwen/Qwen3.8-27B-FP8` route with exact served ID `qwen3.8-27b-fp8`.
- Added explicit non-thinking request flags, fail-closed model/reasoning response checks, raw SSE event audit artifacts, Q0 capability reporting, and independent resume namespace guards.
- Preserved the frozen Selection V1.5 task mapping, retriever, candidate pools, split, scoring semantics, Machine/Native counts, and conditional execution gates; no Qwen3.6 row is reused.
- Added public registries, governance documents, synthetic tests, and a review-indexed V1.6 result-bundle builder without publishing endpoints, credentials, benchmark data, requests, or responses.

## Unreleased — Qwen SSE Selection V1.5

- Archived V1.4 full-permutation execution as diagnostic-only.
- Replaced complete permutations with Top-5 ranking for Single/Machine and minimal sufficient selected sets for Multi/Composable.
- Added formal subset guards, strict no-repair parsers, frozen token-budget tooling, scoring, result bundling, public synthetic tests, CI, and an executable publication audit.
- Added registered BGE Dense V2 and RRF public implementations plus reproducibility documentation.

## Qwen SSE Selection V1.5 R2

Fixed per-key concurrency, legal selected-set output budgeting, scoring aggregation, exact result-bundle validation, and the public BGE reproduction entry point. No dataset, Retriever selection, Prompt semantics, Gold, split, candidate pool, or formal result was changed.
