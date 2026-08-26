# Changelog

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
