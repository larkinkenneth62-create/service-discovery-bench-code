# Qwen3.8 SSE Structured Selection V1.9

V1.8 is permanently closed after its zero-benchmark synthetic Q0 completed 24/24 terminal requests, 23/24 strict parses, zero infrastructure errors, and zero retries. The single complete response that ignored the requested structured output is treated in V1.9 as an observed model-format failure, not an infrastructure failure. No V1.8 row is reused.

V1.9 keeps the model, SSE transport, visible Selection V1.5 prompt, dynamic strict JSON Schema request, candidate order, parser, scorer, token budget, task mapping, smoke identity, and formal row guards unchanged. It changes only failure accounting:

- reasoning is optional audit metadata, saved when present and never scored;
- the entire non-empty `content` string is parsed strictly, with no extraction, repair, or fallback;
- invalid model content and non-stop completion are non-retryable `parse_failure` outcomes scored zero;
- model mismatch, authentication, transport, incomplete SSE, and malformed response envelopes remain hard blocks;
- Q0 requires 24 terminal synthetic requests, zero infrastructure/API errors, at least 22/24 successes, at least 10/12 per contract, and at least 5/6 per key slot.

Formal Machine and Native execution remains gated by fresh V1.9 Q0 and the frozen 60-row Dev smoke. This public revision does not execute Q0 or contain private results.
