# Qwen SSE formal experiment — public review package

This directory is the code-and-protocol review surface for the frozen
ServiceDiscoveryBench Qwen experiment. Its path mirrors the private research
workspace so imports and reviewer references remain stable.

## Included

- `code/`: the frozen prompt/parser adapter, SSE runner, preflight, budget
  validation, smoke audit, and conservative Q1 adjudicator.
- `tests/`: the exact local contract tests plus a code-only public test runner.
- `00_GOVERNANCE/`: the frozen execution plan and protocol supplied by the
  project owner.
- `prompts/FROZEN_PROMPT_CONTRACT.md`: the exact static prompt envelope and
  response contracts, expressed only with placeholders.

## Deliberately excluded

No benchmark row or generated experiment artifact is published here. In
particular, this directory excludes:

- Native, Machine, smoke, or Unified manifests;
- queries, candidate documents, candidate IDs, Gold labels, and splits;
- instantiated per-row prompts and request payloads;
- raw SSE events, final responses, predictions, status JSONL, metrics, and
  experiment logs;
- tokenizer/model caches, checkpoints, archives, and API credentials.

The runner therefore cannot perform the formal experiment from this mirror
alone. A reviewer can inspect the complete execution logic and prompt contract,
but must obtain authorized manifests separately.

## Integrity boundary

`code/frozen_adapter_v1.py` is bound by the formal runner to SHA-256:

```text
24e8f423cb58e1f284560689e430cdbdece82d7786ed2ab076c5990fd1923afc
```

The public export does not modify the copied code, governance, or original test
files. `PUBLICATION_MANIFEST.sha256` records every published file in this
directory.

## Public code-only tests

Install `httpx`, then run from the repository root:

```text
python experiments/llm_v0_2_qwen_sse_formal_v1_4/tests/run_public_code_only_tests_v1.py
```

This runs all data-independent contracts. Two original integration tests are
intentionally omitted by the public harness because they verify SHA bindings
against the unpublished 60-row smoke manifest and its private budget registry.
The tests remain in the source file for review and run unchanged in the private
workspace.

## Runtime secrets

API keys are read only from `SDB_QWEN_API_KEY_01` through
`SDB_QWEN_API_KEY_04`. Never commit their values. The runner redacts secrets
before artifact writes and performs a final credential scan.
