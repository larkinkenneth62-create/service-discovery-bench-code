# ServiceDiscoveryBench

ServiceDiscoveryBench evaluates whether retrieval and language-model systems can identify the Service or API capabilities needed to complete a user request.

## Public status

This repository is a sanitized, code-only research mirror. It publishes implementation, static contracts, synthetic fixtures, tests, and reproducibility documentation. It intentionally excludes benchmark rows, source datasets, Gold labels, splits, instantiated prompts, model responses, logs, and result archives. See [DATA_POLICY.md](DATA_POLICY.md).

ServiceDiscoveryBench v0.2.0 defines six task types and a frozen 4,798-row Native Test evaluation plus a 197-row Machine Challenge. These counts describe the private benchmark scale; no data row is included here.

| Task | Target | V1.5 output |
|---|---|---|
| Single Service | Service | Top-5 ranking |
| Single API | API | Top-5 ranking |
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

No values belong in Git.

## Canonical entry points

- Current Qwen3.8 runner: `experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/code/run_qwen38_sse_structured_selection_v1_9.py`
- Current Qwen parsers: `experiments/llm_v0_2_qwen38_sse_structured_selection_v1_9/code/output_contracts_v1_5.py`
- LLM scoring: `scripts/evaluation/score_native_machine_selection_v1_5.py`
- LLM result bundle: `scripts/release/build_qwen38_structured_native_machine_bundle_v1_9.py`
- Registered dense retriever: `scripts/evaluation/run_bge_retriever.py`
- Publication audit: `scripts/publication/audit_public_repo.py`

See [canonical entry points](docs/CANONICAL_ENTRYPOINTS.md), [reproducibility](docs/REPRODUCIBILITY.md), [data availability](docs/DATA_AVAILABILITY.md), [tasks and metrics](docs/TASKS_AND_METRICS.md), and [artifact map](docs/ARTIFACT_MAP.md).

## License, citation, limitations, and security

The code license awaits owner approval; see [LICENSE_DECISION_REQUIRED.md](LICENSE_DECISION_REQUIRED.md). Citation metadata also awaits owner input; see [CITATION_METADATA_REQUIRED.md](CITATION_METADATA_REQUIRED.md). Until both are resolved, this repository must not be described as a fully released final-paper artifact.

Known limitations are documented in [LIMITATIONS.md](docs/LIMITATIONS.md). Report credential, endpoint, or private-artifact exposure according to [SECURITY.md](SECURITY.md). Changes are tracked in [CHANGELOG.md](CHANGELOG.md).
