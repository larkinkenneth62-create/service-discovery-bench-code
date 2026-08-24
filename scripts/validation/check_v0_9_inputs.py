"""Check inputs for v0.9 semantic/capability detector pilot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from semcap_detector_v0_9_utils import (
    AUDIT_EVIDENCE,
    DOCS_DIR,
    MANUAL_COLUMNS,
    OUTPUT_DIR,
    ROUND3_REVIEWED,
    V08_DETECTOR_TRACE,
    V08_POLICY_TRACE,
    V08_REPORTS,
    V08_SAMPLE,
    V42_POLICY_DOC,
    count_by,
    ensure_dirs,
    now_str,
    read_csv,
    write_json,
)


REQUIRED_V08_DETECTOR_COLUMNS = [
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "api_leak_detector_status",
    "service_leak_detector_status",
    "candidate_space_status",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "semantic_alignment_check",
    "capability_coverage_check",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check v0.9 semcap detector pilot inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser


def missing_inputs_report(paths: List[Path]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Missing Inputs for v0.9",
        "",
        f"Generated time: {now_str()}",
        "",
        "The following required inputs are missing. v0.9 stops here; no data is fabricated.",
        "",
        "| missing path |",
        "|---|",
    ]
    for path in paths:
        lines.append(f"| `{path}` |")
    lines.extend(["", "No full cleaning, split, baseline, or model training was run."])
    (OUTPUT_DIR / "MISSING_INPUTS.md").write_text("\n".join(lines), encoding="utf-8")


def summarize_csv(path: Path) -> Dict[str, object]:
    cols, rows = read_csv(path)
    return {
        "path": str(path),
        "rows": len(rows),
        "columns": cols,
        "empty_counts": {col: sum(1 for row in rows if not (row.get(col) or "").strip()) for col in cols},
    }


def write_report(path: Path, summaries: List[Dict[str, object]], checks: Dict[str, object]) -> None:
    lines = [
        "# v0.9 Input Check Report",
        "",
        f"Generated time: {now_str()}",
        f"Project root: `{Path.cwd()}`",
        "",
        "Scope: input validation only. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "## CSV Inputs",
        "",
        "| path | rows | columns |",
        "|---|---:|---:|",
    ]
    for summary in summaries:
        lines.append(f"| `{summary['path']}` | {summary['rows']} | {len(summary['columns'])} |")
    lines.extend(["", "## Checks", "", "| check | value |", "|---|---|"])
    for key, value in checks.items():
        lines.append(f"| {key} | {value} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    required = [V42_POLICY_DOC, AUDIT_EVIDENCE, ROUND3_REVIEWED, V08_SAMPLE, V08_DETECTOR_TRACE, V08_POLICY_TRACE, *V08_REPORTS]
    missing = [path for path in required if not path.exists()]
    if missing:
        missing_inputs_report(missing)
        print("ERROR: missing v0.9 required inputs:")
        for path in missing:
            print(f"  - {path}")
        return 1
    csv_paths = [AUDIT_EVIDENCE, ROUND3_REVIEWED, V08_SAMPLE, V08_DETECTOR_TRACE, V08_POLICY_TRACE]
    summaries = [summarize_csv(path) for path in csv_paths]
    round3_cols, round3_rows = read_csv(ROUND3_REVIEWED)
    v08_detector_cols, v08_detector_rows = read_csv(V08_DETECTOR_TRACE)
    _, v08_policy_rows = read_csv(V08_POLICY_TRACE)
    checks = {
        "round3_has_manual_columns": all(col in round3_cols for col in MANUAL_COLUMNS),
        "round3_manual_capability_nonempty": sum(1 for row in round3_rows if (row.get("capability_coverage_check") or "").strip()),
        "v0_8_detector_has_required_columns": all(col in v08_detector_cols for col in REQUIRED_V08_DETECTOR_COLUMNS),
        "v0_8_policy_keep_count": sum(1 for row in v08_policy_rows if row.get("policy_decision") == "keep_for_cleaning_candidate"),
        "v0_8_policy_decision_distribution": count_by(v08_policy_rows, "policy_decision"),
    }
    payload = {
        "generated_time": now_str(),
        "csv_summaries": summaries,
        "checks": checks,
        "scope_guardrails": {
            "full_cleaning": False,
            "final_clean_dataset": False,
            "split": False,
            "baseline": False,
            "training": False,
            "prediction_is_human_final": False,
        },
    }
    write_json(args.output_dir / "input_schema_summary.json", payload)
    write_report(args.output_dir / "input_check_report.md", summaries, checks)
    print(f"Wrote {args.output_dir / 'input_schema_summary.json'}")
    print(f"Wrote {args.output_dir / 'input_check_report.md'}")
    print(f"v0.8 policy keep count: {checks['v0_8_policy_keep_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
