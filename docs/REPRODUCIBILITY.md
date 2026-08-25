# Reproducibility

## Publicly reproducible

- strict V1.5 Top-5 and selected-set parsing;
- formal row-count and CLI guards;
- synthetic SSE heartbeat and terminal handling;
- ranking and selected-set metric calculations;
- registered BGE/RRF contracts;
- code-only result packaging and publication auditing.

Run the commands in the root README. They require no network or credentials after dependencies are installed.

## Privately reproducible

The real Qwen and Retriever experiments additionally require the frozen manifests, candidate documents, Gold, tokenizer/model assets, and owner-authorized endpoint credentials. Those assets are hash-bound at runtime and remain outside Git.

V1.5 never reuses V1.4 output rows. Formal mode hard-requires exactly 197 Machine or 4,798 Native inputs and rejects subset flags. Parse failures stay in the denominator; unresolved infrastructure or API errors block completion.
