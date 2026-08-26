# Legacy code map

- `experiments/llm_v0_2_qwen_sse_formal_v1_4/` preserves the stopped Qwen3.6 full-permutation implementation. It is diagnostic-only and cannot resume into V1.5 or V1.6.
- `experiments/llm_v0_2_qwen_sse_selection_v1_5/` preserves the Qwen3.6 Selection route stopped at Q0. It contributes no formal result row and cannot be resumed or reused by Qwen3.8 V1.7.
- `experiments/llm_v0_2_qwen38_sse_selection_v1_6/` preserves the non-thinking Qwen3.8 route stopped at the strict-JSON Q0 gate after zero benchmark rows. It cannot be resumed or reused by thinking V1.7.
- `src/servicediscoverybench_v011_closure_v2/` preserves v0.1.1 closure behavior.
- Root numbered scripts `00_` through `17_` preserve the historical dataset-construction sequence; the current release and validation entry points are listed in `CANONICAL_ENTRYPOINTS.md`.
- Qwen semantic-capability scripts under `scripts/validation/` belong to earlier data-quality workflows, not the V1.6 Native/Machine evaluation.

Legacy paths are not deleted because byte-level historical traceability matters. New formal work must use the canonical paths.
