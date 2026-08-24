"""Build LLM-assisted semcap detector prompt pack without calling any API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from semcap_detector_v0_9_utils import OUTPUT_DIR, V08_SAMPLE, ensure_dirs, parse_list, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build v0.9 semcap LLM prompt JSONL pack without API calls.")
    parser.add_argument("--sample", type=Path, default=V08_SAMPLE)
    parser.add_argument("--heuristic", type=Path, default=OUTPUT_DIR / "semcap_predictions_v0_8_sample_heuristic.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser


def template_text() -> str:
    return """# SemCap LLM Judge Prompt v0.9

You judge semantic alignment and capability coverage for a service/API discovery benchmark item.

Return strict JSON only:

```json
{
  "semantic_alignment_check": "ok|uncertain|mismatch",
  "semantic_alignment_confidence": "high|medium|low",
  "capability_coverage_check": "coverage_ok|coverage_uncertain|coverage_mismatch",
  "capability_coverage_confidence": "high|medium|low",
  "core_requirements": ["..."],
  "covered_requirements": ["..."],
  "missing_requirements": ["..."],
  "mismatch_type": "...",
  "reason": "..."
}
```

Rules:

- Do not reward gold just because it is in the candidate list.
- No leak does not imply coverage.
- If gold only partially covers the query, output `coverage_uncertain` or `coverage_mismatch`.
- If descriptions are insufficient, prefer `uncertain`.
- Detector output is not human final.
"""


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.sample.exists() or not args.heuristic.exists():
        print("ERROR: missing v0.8 sample or heuristic prediction input.")
        return 1
    _, sample_rows = read_csv(args.sample)
    _, pred_rows = read_csv(args.heuristic)
    pred_by_task = {row.get("task_id"): row for row in pred_rows}
    template_path = args.output_dir / "semcap_llm_prompt_template_v0_9.md"
    requests_path = args.output_dir / "semcap_llm_requests_v0_9.jsonl"
    sample20_path = args.output_dir / "semcap_llm_request_sample_20.jsonl"
    template_path.write_text(template_text(), encoding="utf-8")
    records = []
    for idx, row in enumerate(sample_rows, start=1):
        pred = pred_by_task.get(row.get("task_id"), {})
        records.append(
            {
                "custom_id": row.get("v0_8_sample_id") or f"V09-REQ-{idx:03d}",
                "task_id": row.get("task_id", ""),
                "task_type": row.get("task_type", ""),
                "query_text": row.get("query_text", ""),
                "candidate_services": parse_list(row.get("candidate_services_json", "")),
                "candidate_apis": parse_list(row.get("candidate_apis_json", "")),
                "gold_services": parse_list(row.get("gold_services_json", "")),
                "gold_apis": parse_list(row.get("gold_apis_json", "")),
                "heuristic_prediction": pred,
                "instruction": "Judge semantic alignment and capability coverage. Return strict JSON only.",
            }
        )
    with requests_path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    with sample20_path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records[:20]:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Wrote {template_path}")
    print(f"Wrote {requests_path} ({len(records)} requests)")
    print(f"Wrote {sample20_path} (20 requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
