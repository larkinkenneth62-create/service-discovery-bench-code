# ServiceDiscoveryBench

> Known-result correction: V1.9 treated `single_api_recommendation` as ranking-only. Because one parent service may require multiple APIs, its 91.46% Hit@1 and the historical mixed-contract Micro/Macro indices are ranking diagnostics, not unified exact-completion rates. The V1.10 code-only correction is under `experiments/llm_v0_2_qwen38_native_single_api_correction_v1_10/`.

ServiceDiscoveryBench evaluates whether retrieval and language-model systems can identify the Service or API capabilities needed to complete a user request.

## Public status

This repository is a sanitized, code-only research mirror. It publishes implementation, static contracts, synthetic fixtures, tests, and reproducibility documentation. It intentionally excludes benchmark rows, source datasets, Gold labels, splits, instantiated prompts, model responses, logs, and result archives. See [DATA_POLICY.md](DATA_POLICY.md).

ServiceDiscoveryBench v0.2.0 defines six task types and a frozen 4,798-row Native Test evaluation plus a 197-row Machine Challenge. These counts describe the private benchmark scale; no data row is included here.

| Task | Target | Current frozen output contract |
|---|---|---|
| Single Service | Service | Top-5 ranking |
| Single API | API | Top-5 ranking plus independent minimal sufficient selected set |
| Multi Service | Service | Minimal sufficient selected set |
| Multi API | API | Minimal sufficient selected set |
| Composable Service | Service | Minimal sufficient selected set |
| Composable API | API | Minimal sufficient selected set |

## Evaluation tracks

| Track | Capability boundary | Public mirror status |
|---|---|---|
| Native | Select directly from the frozen Native candidate pool | Code and contracts only |
| Machine | Top-5 selection on the 197-row Machine Challenge | Code and contracts only |
| Unified | Retriever-assisted LLM setting | Deferred from Qwen3.8 Structured Selection V1.9 |

The registered paper retriever is `BGE_DENSE_V2@200`. The current independent LLM experiment revision is `QWEN38_SSE_STRUCTURED_SELECTION_MODEL_FAILURE_ACCOUNTING_V1_9`, using the unchanged Selection V1.5 visible prompt, parser, scorer, and output semantics. The reasoning channel is optional audit metadata and is never scored. The complete `content` field must pass the strict Selection V1.5 parser; invalid model content is a non-retryable `parse_failure`, retained in the denominator and scored zero. A per-request strict JSON Schema is requested but not assumed to be enforced. Qwen3.6 V1.4/V1.5 and Qwen3.8 V1.6–V1.8 rows cannot be resumed or reused.

DeepSeek V4 Flash V2.2 is a separate full six-task experiment under `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/`. It has its own provider configuration, credentials, runtime freeze, Q0/Dev/formal result namespace, and full-track scorer. It never resumes, merges, or scores Qwen rows. Its Chat Completions adapter uses `thinking.type=enabled`, `reasoning_effort=high`, and `response_format.type=json_object`, followed by the same strict local task validation. Thinking-mode sampling parameters are not sent because they are inapplicable.

The R2 implementation requires code-enforced stage prerequisites, finish-reason accounting, exact longest-request smoke coverage, backend fingerprint capture, and full paper scoring. R3 is an additive transport-only implementation revision for a JSON non-stream gateway; it does not change prompts, candidates, output contracts, budgets, metrics, or R2 code. Its offline closeout uses a separate sidecar to bind an original `git_commit_sha=UNKNOWN` result set to the public inference commit only when runner, parser, runtime, budget, manifest, status, summary, and ledger hashes match exactly. Original status files are never edited. The public mirror contains the binding/scoring tools and synthetic tests, but no real R3 results.

## Installation

Python 3.11–3.13 is supported.

```bash
python -m pip install -e ".[dev,llm,retriever]"
```

## Public synthetic verification

```bash
python -m compileall src scripts experiments
python -m pytest -q
python experiments/llm_v0_2_qwen_sse_selection_v1_5/tests/run_public_code_only_tests_v1_5.py
python experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/tests/run_public_code_only_tests_v1_9.py
python scripts/publication/audit_public_repo.py --root .
```

The synthetic path covers manifest → payload → SSE heartbeat → final JSON → parser → score → bundle without network access, private data, or credentials.

## Private experiment configuration

Real experiments cannot run from this mirror alone because the frozen manifests, candidate documents, Gold labels, tokenizer assets, and result directories are private. When those assets are legitimately available, credentials and the temporary endpoint are supplied only through environment variables:

```text
SDB_QWEN_BASE_URL
SDB_QWEN_MODEL
SDB_QWEN_API_KEY_01
SDB_QWEN_API_KEY_02
SDB_QWEN_API_KEY_03
SDB_QWEN_API_KEY_04
```

DeepSeek uses only its independent variables:

```text
SDB_DEEPSEEK_BASE_URL
SDB_DEEPSEEK_MODEL
SDB_DEEPSEEK_API_KEY
```

No values belong in Git.

## Canonical entry points

- Current Qwen3.8 runner: `experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/code/run_qwen38_sse_structured_selection_v1_9.py`
- DeepSeek V4 Flash V2.2 runner: `experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/run_deepseek_v4_flash_v2_2.py`
- DeepSeek V4 Flash V2.2 full-track scorer: `scripts/evaluation/score_deepseek_full_v2_2.py`
- DeepSeek Native/Machine paired comparison: `scripts/evaluation/build_deepseek_native_machine_comparison_v2_2.py`
- DeepSeek V4 Flash V2.2 result bundle: `scripts/release/build_deepseek_v4_flash_v2_2_bundle.py`
- DeepSeek V2.2 R3 provenance binding: `scripts/evaluation/build_deepseek_v2_2_r3_provenance_binding.py`
- DeepSeek V2.2 R3-only scorer: `scripts/evaluation/score_deepseek_full_v2_2_r3_nonstream.py`
- DeepSeek V2.2 R3 paired comparison: `scripts/evaluation/build_deepseek_native_machine_comparison_v2_2_r3_nonstream.py`
- DeepSeek V2.2 R3 result bundle: `scripts/release/build_deepseek_v4_flash_v2_2_r3_nonstream_bundle.py`
- Current Qwen parsers: `experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/code/output_contracts_v1_5.py`
- LLM scoring: `scripts/evaluation/score_native_machine_selection_v1_5.py`
- LLM result bundle: `scripts/release/build_qwen38_structured_native_machine_bundle_v1_9.py`
- Registered dense retriever: `scripts/evaluation/run_bge_retriever.py`
- Publication audit: `scripts/publication/audit_public_repo.py`

See [canonical entry points](docs/CANONICAL_ENTRYPOINTS.md), [reproducibility](docs/REPRODUCIBILITY.md), [data availability](docs/DATA_AVAILABILITY.md), [tasks and metrics](docs/TASKS_AND_METRICS.md), and [artifact map](docs/ARTIFACT_MAP.md).

## License, citation, limitations, and security

The code license awaits owner approval; see [LICENSE_DECISION_REQUIRED.md](LICENSE_DECISION_REQUIRED.md). Citation metadata also awaits owner input; see [CITATION_METADATA_REQUIRED.md](CITATION_METADATA_REQUIRED.md). Until both are resolved, this repository must not be described as a fully released final-paper artifact.

Known limitations are documented in [LIMITATIONS.md](docs/LIMITATIONS.md). Report credential, endpoint, or private-artifact exposure according to [SECURITY.md](SECURITY.md). Changes are tracked in [CHANGELOG.md](CHANGELOG.md).
