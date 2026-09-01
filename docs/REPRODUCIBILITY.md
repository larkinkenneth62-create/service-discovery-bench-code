# Reproducibility

## Publicly reproducible

- strict V1.5 Top-5 and selected-set parsing;
- formal row-count and CLI guards;
- synthetic SSE heartbeat and terminal handling;
- ranking and selected-set metric calculations;
- the DeepSeek three-contract request → parser → scorer linkage, including a six-API selected set;
- registered BGE/RRF contracts;
- code-only result packaging and publication auditing.

Run the commands in the root README. They require no network or credentials after dependencies are installed.

## Privately reproducible

The real Qwen, DeepSeek, and Retriever experiments additionally require the frozen manifests, candidate documents, Gold, tokenizer/model assets where applicable, and owner-authorized endpoint credentials. Those assets are hash-bound at runtime and remain outside Git.

V1.5 never reuses V1.4 output rows. Formal mode hard-requires exactly 197 Machine or 4,798 Native inputs and rejects subset flags. Parse failures stay in the denominator; unresolved infrastructure or API errors block completion.

DeepSeek V2.2 has an independent result namespace and scorer. Its admission order is synthetic linkage, six-request Q0, 60-row Dev smoke, 197-row Machine, and 4,798-row Native. Passing Qwen output is neither an input nor a prerequisite. Without `SDB_DEEPSEEK_API_KEY`, `SDB_DEEPSEEK_BASE_URL`, private manifests, and expense authorization, live-provider stages remain explicitly not run.
