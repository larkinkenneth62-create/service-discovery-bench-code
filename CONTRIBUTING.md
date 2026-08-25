# Contributing

Changes should be narrowly scoped, tested on synthetic fixtures, and accompanied by documentation when they alter a public contract. Pull requests must not include datasets, benchmark rows, Gold, splits, instantiated prompts, requests, responses, logs, results, ZIP files, model weights, live endpoints, credentials, or absolute private paths.

Run `python -m pytest -q`, the V1.5 synthetic harness, and the publication audit before submitting. Output-contract or metric changes require project-owner approval and a new revision; do not silently change a frozen route.
