# Publication audit

Audit date: 2026-08-24

## Export scope

The mirror contains 327 text source/configuration files from `src/`, `scripts/`, `tests/`, `configs/`, and `schemas/`, plus repository guidance. It contains no external dataset directory, generated release, experiment output, archive, paper, chat export, or binary research artifact.

## Privacy and secret checks

- No email address remains in the mirror.
- No private workstation username, WeChat path, attachment path, or home-directory path remains.
- No AWS, Google, GitHub, OpenAI-style, or PEM private-key signature was detected.
- API runners reference environment-variable names only; secret values are not embedded.
- Two `sk-` lexical matches are known false positives in ordinary identifiers (`risk-*` and `--task-*`), not credentials.

## Deliberate omissions

Eight legacy manual-review/handoff scripts with hard-coded workstation input locations were omitted. Their maintained functionality should be reintroduced only after converting all inputs to explicit command-line arguments or repository-relative configuration.

## Dataset boundary

The audit covers this code mirror, not any separately stored dataset. Publication of this repository does not authorize publication of ServiceDiscoveryBench rows, ToolBench copies, annotations, model outputs, or release archives.

## 2026-08-25 Qwen SSE review-package addition

- Added `experiments/llm_v0_2_qwen_sse_formal_v1_4/` at its original workspace-relative path.
- Exported 12 source-of-truth files without byte changes: eight Python
  implementation files, two contract-test files, and two frozen governance
  documents.
- Added five public-only review aids: a directory `.gitignore`, a static Prompt
  contract with placeholders, a review README, a data-independent test runner,
  and a SHA-256 publication manifest.
- Experiment directory size at audit: 17 text files, 177,695 bytes.
- Public data-independent tests: 17/17 PASS. Two preserved integration tests
  require the deliberately omitted private 60-row smoke manifest and were not
  executed by the public harness.
- Forbidden-file scan found no JSONL, CSV/TSV, spreadsheet, archive, GGUF,
  Parquet, PDF, or other experiment-data/binary artifact.
- Secret/privacy scan found no workstation path, email address, private-key
  block, GitHub/OpenAI-style token, concrete Bearer value, or standalone
  48-hex credential.
- Excluded in full: all runtime caches, manifests, instantiated prompts,
  requests, raw SSE events, responses, predictions, status rows, metrics,
  experiment logs, labels, queries, candidate documents, candidate IDs, and
  dataset/release artifacts.
