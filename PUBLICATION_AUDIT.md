# Publication audit

- Audit date: 2026-09-01
- Baseline commit: `86a1e73123f80aa88c2d651559b2bf5571dd8c95`
- Audited implementation: DeepSeek V2.2 R2 working tree; final commit is recorded in the handoff package
- Target branch: `fix/deepseek-v4-flash-v2.2-r2-gates-accounting-scoring`
- Scope: sanitized code-only mirror

## Local validation

- `python -m compileall src scripts experiments tests`: PASS
- Full public pytest: 356 passed, 2 skipped because the corresponding private generated fixtures are intentionally absent
- DeepSeek V2.2 R2 focused tests: 75 passed
- Synthetic stage gates, finish accounting, exact longest-request coverage, scorer, paired comparison, and bundle: PASS
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

The DeepSeek V2.2 R2 implementation stores only an endpoint SHA-256 and requires provider configuration through environment variables at runtime. No live R2 Q0, Dev, Machine, or Native request was made during this code update.

## Deliberate omissions

No dataset, Query, Gold, split, candidate instance, instantiated prompt, request, response, raw SSE, parsed prediction, log, metric result, release archive, API key, live endpoint, model weight, PDF, or private path is included.

License and citation metadata remain explicit owner-input blockers; the repository must not yet be described as a fully released final-paper artifact.
