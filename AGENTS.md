# Repository guidance

## Scope

This is a sanitized code-only mirror. Treat the repository as suitable for architecture analysis, implementation planning, code review, and tests that do not require private or unpublished datasets.

## External datasets

- ToolBench and all other source datasets live outside this repository.
- If a legitimate local copy is available, expose it at `external_sources/ToolBench` or through an explicit configuration value.
- Treat external datasets as read-only.
- Never add raw source data, derived benchmark rows, human-review records, release archives, or dataset junctions/symlinks to Git.

## Security and privacy

- Read credentials only from environment variables or an approved secret manager.
- Do not commit `.env` files, tokens, private workstation paths, contact information, chat exports, or generated artifacts.
- Do not interpret the public availability of this code as authorization to publish any dataset or unpublished research material.

## Planning expectations

- Identify the commit SHA and files inspected.
- Separate confirmed facts, inferences, and unknowns.
- Tie each implementation step to specific files, symbols, tests, and acceptance criteria.
- Flag any step that would require unavailable datasets or external services.
