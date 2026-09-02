# Publication audit

- Audit date: 2026-09-01
- Baseline commit: `3657a53b3ac3c98adc66ee3475111ba2115b83a3`
- Audited implementation: DeepSeek V2.2 R3 provenance-bound offline scoring working tree; final commit is recorded in the handoff package
- Target branch: `fix/deepseek-v4-flash-v2.2-r3-nonstream-scoring-provenance`
- Scope: sanitized code-only mirror

## Local validation

- `python -m compileall src scripts experiments tests`: PASS
- Full public pytest: 419 passed, 2 skipped because the corresponding private generated fixtures are intentionally absent
- DeepSeek V2.2 R3 provenance/scoring/bundle focused tests: 61 passed
- Synthetic exact-hash binding, R3-only scoring, paired comparison, and bundle validation: PASS
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

The DeepSeek V2.2 R3 implementation stores only an endpoint SHA-256 and requires provider configuration through environment variables at runtime. This update performed no API request or model inference. It adds only offline binding/scoring/packaging code and synthetic tests. Original private R3 result files remain unedited, and no real R3 score or result artifact is included in the public repository.

## Deliberate omissions

No dataset, Query, Gold, split, candidate instance, instantiated prompt, request, response, raw SSE, parsed prediction, log, metric result, release archive, API key, live endpoint, model weight, PDF, or private path is included.

License and citation metadata remain explicit owner-input blockers; the repository must not yet be described as a fully released final-paper artifact.
