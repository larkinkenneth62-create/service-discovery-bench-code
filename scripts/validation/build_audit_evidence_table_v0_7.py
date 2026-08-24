"""Build a unified manual-audit evidence table for v0.7 policy replay.

Inputs are manual40, Round2, and Round3 audited CSVs. This script does not use
manual final decisions as policy inputs; it only preserves them for later replay
comparison.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

from cleaning_policy_v0_7_utils import (
    AUDIT_EVIDENCE_PATH,
    DOCS_DIR,
    EVIDENCE_COLUMNS,
    MANUAL40_PATH,
    OUTPUT_DIR,
    ROUND2_PATH,
    ROUND3_PATH,
    count_by,
    ensure_dirs,
    load_audit_round,
    markdown_table,
    now_str,
    write_csv,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge manual40, Round2, and Round3 reviewed rows into one v0.7 evidence table."
    )
    parser.add_argument("--manual40", type=Path, default=MANUAL40_PATH)
    parser.add_argument("--round2", type=Path, default=ROUND2_PATH)
    parser.add_argument("--round3", type=Path, default=ROUND3_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def distribution(rows: List[Dict[str, object]], key: str) -> Dict[str, int]:
    return dict(sorted(Counter(str(row.get(key, "not_available")) for row in rows).items()))


def write_report(
    path: Path,
    input_paths: Dict[str, Path],
    counts: Dict[str, int],
    total_rows: int,
    summary: Dict[str, object],
) -> None:
    expected = counts.get("manual40", 0) + counts.get("round2", 0) + counts.get("round3", 0)
    lines: List[str] = [
        "# Audit Evidence Table Report v0.7",
        "",
        f"Generated time: {now_str()}",
        f"Project root: `{Path.cwd()}`",
        "",
        "Scope: this report only merges audited manual-review rows. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Input Files",
        "",
        "| audit_round | input path | rows |",
        "|---|---|---:|",
    ]
    for key, path_item in input_paths.items():
        lines.append(f"| {key} | `{path_item}` | {counts.get(key, 0)} |")
    lines.extend(
        [
            "",
            "## Output",
            "",
            f"- Evidence table: `{AUDIT_EVIDENCE_PATH}`",
            f"- Output rows: {total_rows}",
            f"- Expected rows from inputs: {expected}",
        ]
    )
    if total_rows != expected:
        lines.append("- Note: output row count differs from input sum; inspect the script before using replay results.")
    else:
        lines.append("- Row-count check passed: no audited sample was dropped.")
    lines.extend(
        [
            "",
            "## Required Unified Fields",
            "",
            "| field | status |",
            "|---|---|",
        ]
    )
    for field in EVIDENCE_COLUMNS:
        lines.append(f"| {field} | included |")
    lines.extend(
        [
            "",
            "## Distribution Summary",
            "",
            "### By audit_round",
            "",
        ]
    )
    by_round = [{"audit_round": key, "count": value} for key, value in summary["by_audit_round"].items()]
    lines.extend(markdown_table(by_round, ["audit_round", "count"], max_rows=10))
    lines.extend(["", "### By manual_final_decision", ""])
    by_decision = [
        {"manual_final_decision": key, "count": value}
        for key, value in summary["manual_final_decision_distribution"].items()
    ]
    lines.extend(markdown_table(by_decision, ["manual_final_decision", "count"], max_rows=10))
    lines.extend(
        [
            "",
            "## Missing Field Compatibility",
            "",
            "- manual40 and Round2 do not have a native `capability_coverage_check`; it is normalized to `not_available`.",
            "- Round3 has explicit `capability_coverage_check` and it is preserved.",
            "- Missing fields are represented as `not_available`, not inferred.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.docs_dir.mkdir(parents=True, exist_ok=True)

    input_paths = {"manual40": args.manual40, "round2": args.round2, "round3": args.round3}
    missing = [str(path) for path in input_paths.values() if not path.exists()]
    if missing:
        print("ERROR: missing required input file(s):")
        for path in missing:
            print(f"  - {path}")
        return 1

    manual40 = load_audit_round(args.manual40, "manual40")
    round2 = load_audit_round(args.round2, "round2")
    round3 = load_audit_round(args.round3, "round3")
    rows = manual40 + round2 + round3
    counts = {"manual40": len(manual40), "round2": len(round2), "round3": len(round3)}
    summary = {
        "generated_time": now_str(),
        "input_files": {key: str(path) for key, path in input_paths.items()},
        "row_counts": counts,
        "total_rows": len(rows),
        "expected_total_rows": sum(counts.values()),
        "expected_40_80_100_check": {
            "manual40_is_40": len(manual40) == 40,
            "round2_is_80": len(round2) == 80,
            "round3_is_100": len(round3) == 100,
            "total_is_220": len(rows) == 220,
        },
        "by_audit_round": distribution(rows, "audit_round"),
        "manual_final_decision_distribution": distribution(rows, "manual_final_decision"),
        "task_type_distribution": distribution(rows, "task_type"),
        "semantic_alignment_distribution": distribution(rows, "semantic_alignment_check"),
        "capability_coverage_distribution": distribution(rows, "capability_coverage_check"),
        "leakage_distribution": distribution(rows, "leakage_check"),
        "candidate_validity_distribution": distribution(rows, "candidate_validity_check"),
        "task_type_check_distribution": distribution(rows, "task_type_check"),
        "scope_guardrails": {
            "full_cleaning": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }
    evidence_path = args.output_dir / AUDIT_EVIDENCE_PATH.name
    write_csv(evidence_path, rows, EVIDENCE_COLUMNS)
    write_json(args.output_dir / "audit_evidence_summary.json", summary)
    write_report(
        args.docs_dir / "audit_evidence_table_report_v0_7.md",
        input_paths,
        counts,
        len(rows),
        summary,
    )
    print(f"Wrote {evidence_path} ({len(rows)} rows)")
    print(f"Wrote {args.output_dir / 'audit_evidence_summary.json'}")
    print(f"Wrote {args.docs_dir / 'audit_evidence_table_report_v0_7.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
