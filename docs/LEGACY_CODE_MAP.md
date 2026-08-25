# Legacy code map

- `experiments/llm_v0_2_qwen_sse_formal_v1_4/` preserves the stopped full-permutation implementation. It is diagnostic-only and cannot resume into V1.5.
- `src/servicediscoverybench_v011_closure_v2/` preserves v0.1.1 closure behavior.
- Root numbered scripts `00_` through `17_` preserve the historical dataset-construction sequence; the current release and validation entry points are listed in `CANONICAL_ENTRYPOINTS.md`.
- Qwen semantic-capability scripts under `scripts/validation/` belong to earlier data-quality workflows, not the V1.5 Native/Machine evaluation.

Legacy paths are not deleted because byte-level historical traceability matters. New formal work must use the canonical paths.
