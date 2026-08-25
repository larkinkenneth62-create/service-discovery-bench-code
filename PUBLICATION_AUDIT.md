# Publication audit

- Audit date: 2026-08-25
- Baseline commit: `3ca6c7b02bac91d3e90502bc6a1ee68a8ca42d8d`
- Audited implementation commit: `bab1ef6`
- Target branch: `fix/qwen-selection-contract-v1.5`
- Scope: sanitized code-only mirror

## Local validation

- `python -m compileall src scripts experiments`: PASS
- Full public pytest: 165 passed, 2 skipped because the corresponding private generated fixtures are intentionally absent
- V1.5 contract/formal-guard tests: 34 passed
- Synthetic manifest → payload → SSE → parser → score → bundle: PASS
- `git diff --check`: PASS

## Executable publication audit

`python scripts/publication/audit_public_repo.py --root .` returned PASS:

```text
forbidden files = 0
secret findings = 0
absolute private paths = 0
live tunnel URLs = 0
instantiated benchmark rows = 0
symlinks = 0
large files = 0
publication audit status = PASS
```

The public V1.4 diagnostic copy retains historical logic but its temporary endpoint literals were sanitized after the private run was stopped. The private frozen V1.4 artifacts were not modified. V1.5 has no default endpoint and requires `SDB_QWEN_BASE_URL` at runtime.

## Deliberate omissions

No dataset, Query, Gold, split, candidate instance, instantiated prompt, request, response, raw SSE, parsed prediction, log, metric result, release archive, API key, live endpoint, model weight, PDF, or private path is included.

License and citation metadata remain explicit owner-input blockers; the repository must not yet be described as a fully released final-paper artifact.
