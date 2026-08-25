# ServiceDiscoveryBench code mirror

This repository is a sanitized, code-only mirror of the ServiceDiscoveryBench research workspace. It exists so planning and review tools can inspect the implementation without receiving local datasets, unpublished paper materials, chat exports, credentials, or generated experiment artifacts.

## Included

- `src/`: reusable project modules
- `scripts/`: dataset construction, validation, evaluation, provider, and release utilities
- `tests/`: automated tests
- `configs/`: non-secret configuration
- `schemas/`: data and release schemas
- `experiments/llm_v0_2_qwen_sse_formal_v1_4/`: sanitized Qwen SSE
  experiment code, exact static prompt contract, tests, and frozen governance
  documents for independent review

## Not included

- ToolBench or any other external source dataset
- ServiceDiscoveryBench release rows, human-review records, or derived corpora
- experiment outputs, checkpoints, archives, PDFs, or execution packs
- local paths, credentials, environment files, or ChatGPT exports
- workstation-specific legacy handoff scripts that cannot run without private local inputs

The absence of datasets is intentional. This repository publishes implementation code for inspection and planning; it does not publish or license the underlying datasets. See `DATA_POLICY.md`.

The Qwen experiment review package contains no instantiated prompt, manifest,
request, response, prediction, metric, status log, or benchmark row. Start with
`experiments/llm_v0_2_qwen_sse_formal_v1_4/PUBLIC_REVIEW_README.md`.

## Local development

Use Python 3.11 or newer. External datasets, when legitimately available, must be mounted outside the repository and referenced through local configuration. Never commit source data or generated benchmark releases.

Before changing code, read `AGENTS.md`. For planning tasks, cite concrete files and symbols, state assumptions explicitly, and keep dataset publication outside the task scope.

## Planning prompt

When using a repository connector, ask the model to inspect `AGENTS.md`, map the relevant modules and tests, and produce an implementation plan tied to the current commit SHA. The model should not infer that omitted data or research artifacts are available.
