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
