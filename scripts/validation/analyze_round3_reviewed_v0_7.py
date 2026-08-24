"""Deep analysis for Round3 reviewed samples in v0.7.

This script analyzes the already-reviewed Round3 targeted validation set only.
It does not run full cleaning, split, baseline, or model training.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

from cleaning_policy_v0_7_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    ROUND3_PATH,
    crosstab_decision,
    ensure_dirs,
    load_audit_round,
    markdown_table,
    norm_decision,
    now_str,
    read_csv,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze Round3 reviewed CSV and produce v0.7 deep-analysis artifacts."
    )
    parser.add_argument("--input", type=Path, default=ROUND3_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def decision_distribution(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    counter = Counter(norm_decision(row.get("manual_final_decision", "")) for row in rows)
    total = len(rows)
    return [
        {
            "manual_final_decision": key,
            "count": counter.get(key, 0),
            "rate": f"{counter.get(key, 0) / total * 100:.1f}%" if total else "0.0%",
        }
        for key in ["keep_for_cleaning_candidate", "remove", "uncertain", "not_available"]
        if counter.get(key, 0) or key != "not_available"
    ]


def cross_tab_raw(rows: List[Dict[str, str]], group_col: str) -> List[Dict[str, object]]:
    grouped: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        group = (row.get(group_col) or "not_available").strip() or "not_available"
        decision = norm_decision(row.get("manual_final_decision", ""))
        grouped[group][decision] += 1
    out: List[Dict[str, object]] = []
    for group, counts in sorted(grouped.items()):
        total = sum(counts.values())
        out.append(
            {
                group_col: group,
                "keep_for_cleaning_candidate": counts.get("keep_for_cleaning_candidate", 0),
                "remove": counts.get("remove", 0),
                "uncertain": counts.get("uncertain", 0),
                "total": total,
                "keep_rate": f"{counts.get('keep_for_cleaning_candidate', 0) / total * 100:.1f}%" if total else "0.0%",
            }
        )
    return out


def compact_row(row: Dict[str, str], example_type: str) -> Dict[str, object]:
    cols = [
        "round3_review_id",
        "task_id",
        "task_type",
        "source_group",
        "risk_category",
        "risk_subtype",
        "query_text",
        "leak_status",
        "manual_final_decision",
        "semantic_alignment_check",
        "capability_coverage_check",
        "leakage_check",
        "candidate_validity_check",
        "task_type_check",
        "human_notes",
    ]
    out: Dict[str, object] = {"example_type": example_type}
    for col in cols:
        out[col] = row.get(col, "")
    return out


def collect_failure_examples(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    examples: List[Dict[str, object]] = []
    for row in rows:
        decision = norm_decision(row.get("manual_final_decision", ""))
        risk_category = row.get("risk_category", "")
        leakage = (row.get("leakage_check") or "").strip()
        coverage = (row.get("capability_coverage_check") or "").strip()
        leak_status = (row.get("leak_status") or "").strip()
        if coverage == "coverage_mismatch" and leak_status in {"no_obvious_leak", "no_blocking_leak", "none", ""}:
            examples.append(compact_row(row, "coverage_mismatch_but_original_no_obvious_leak"))
        if leakage == "api_leak_blocking" and decision == "keep_for_cleaning_candidate":
            examples.append(compact_row(row, "api_leak_but_human_keep"))
        if leakage == "service_leak_only" and decision == "keep_for_cleaning_candidate":
            examples.append(compact_row(row, "service_leak_only_but_human_keep"))
        if risk_category == "rule_keep_candidate" and decision in {"remove", "uncertain"}:
            examples.append(compact_row(row, "rule_keep_candidate_but_human_remove_or_uncertain"))
        if risk_category == "api_level_single_service_boundary" and decision == "remove":
            examples.append(compact_row(row, "api_level_single_service_boundary_but_remove"))
    return examples


def section_table(title: str, rows: List[Dict[str, object]], cols: List[str]) -> List[str]:
    lines = ["", f"## {title}", ""]
    lines.extend(markdown_table(rows, cols, max_rows=40))
    return lines


def write_report(
    path: Path,
    input_path: Path,
    rows: List[Dict[str, str]],
    decision_rows: List[Dict[str, object]],
    by_tables: Dict[str, List[Dict[str, object]]],
    examples: List[Dict[str, object]],
) -> None:
    lines: List[str] = [
        "# Round3 Reviewed Deep Analysis v0.7",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: only the reviewed Round3 targeted validation set was analyzed. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Core Conclusion",
        "",
        "Round3 的核心发现不是“规则失败”，而是旧规则缺少 `capability_coverage_check` 这道门：`no_obvious_leak` 不能推出 gold service/API 能覆盖 query。",
        "",
        "同时，API-level 的 `candidate_service_count = 1` 基本成立，不应被当作 fatal；API-level 关键是 `candidate_api_count > gold_api_count`、gold API 在候选中、能力覆盖和语义对齐成立。",
        "",
        "## Overall Final Decision Distribution",
        "",
    ]
    lines.extend(markdown_table(decision_rows, ["manual_final_decision", "count", "rate"], max_rows=10))
    table_specs = [
        ("By risk_category", "risk_category"),
        ("By risk_subtype", "risk_subtype"),
        ("By task_type", "task_type"),
        ("By source_group", "source_group"),
        ("By leakage_check", "leakage_check"),
        ("By capability_coverage_check", "capability_coverage_check"),
        ("By candidate_validity_check", "candidate_validity_check"),
        ("By task_type_check", "task_type_check"),
    ]
    for title, key in table_specs:
        lines.extend(
            section_table(
                title,
                by_tables[key],
                [key, "keep_for_cleaning_candidate", "remove", "uncertain", "total", "keep_rate"],
            )
        )
    lines.extend(
        [
            "",
            "## Failure-Mode Examples",
            "",
            "The CSV `round3_failure_examples.csv` contains the full compact list used here.",
        ]
    )
    if examples:
        lines.extend(
            markdown_table(
                examples,
                [
                    "example_type",
                    "round3_review_id",
                    "task_id",
                    "risk_category",
                    "manual_final_decision",
                    "capability_coverage_check",
                    "leakage_check",
                    "query_text",
                    "human_notes",
                ],
                max_rows=30,
            )
        )
    else:
        lines.append("No failure-mode examples matched the scripted filters.")
    lines.extend(
        [
            "",
            "## Rule Implications",
            "",
            "1. `capability_coverage_check` must be a clean-ready gate. Round3 capability-risk cases include many removes even when leak is not obvious.",
            "2. Old `rule_keep_candidate` cannot be treated as automatically clean-ready. It still contains remove/uncertain cases, mostly because query capability coverage is not guaranteed.",
            "3. API-level single-service samples should not be removed solely because `candidate_service_count = 1`.",
            "4. Strong endpoint/API leak still blocks clean-ready, but weak/generic mentions should be routed to uncertain/review rather than mechanically removed.",
            "5. Service leak only is not clean service discovery, but it can remain analyzable for API-level recommendation when API choice space and capability coverage are valid.",
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
    if not args.input.exists():
        print(f"ERROR: missing Round3 reviewed input: {args.input}")
        return 1
    _, raw_rows = read_csv(args.input)
    normalized_rows = load_audit_round(args.input, "round3")

    decision_rows = decision_distribution(raw_rows)
    by_tables = {
        "risk_category": crosstab_decision(normalized_rows, "risk_category"),
        "risk_subtype": crosstab_decision(normalized_rows, "risk_subtype"),
        "task_type": crosstab_decision(normalized_rows, "task_type"),
        "source_group": crosstab_decision(normalized_rows, "source_group"),
        "leakage_check": crosstab_decision(normalized_rows, "leakage_check"),
        "capability_coverage_check": crosstab_decision(normalized_rows, "capability_coverage_check"),
        "candidate_validity_check": crosstab_decision(normalized_rows, "candidate_validity_check"),
        "task_type_check": crosstab_decision(normalized_rows, "task_type_check"),
    }
    examples = collect_failure_examples(raw_rows)

    write_csv(args.output_dir / "round3_decision_distribution.csv", decision_rows)
    write_csv(args.output_dir / "round3_by_risk_category.csv", by_tables["risk_category"])
    write_csv(args.output_dir / "round3_failure_examples.csv", examples)
    write_report(
        args.docs_dir / "round3_reviewed_deep_analysis_v0_7.md",
        args.input,
        raw_rows,
        decision_rows,
        by_tables,
        examples,
    )
    print(f"Round3 rows analyzed: {len(raw_rows)}")
    print(f"Wrote {args.output_dir / 'round3_decision_distribution.csv'}")
    print(f"Wrote {args.output_dir / 'round3_by_risk_category.csv'}")
    print(f"Wrote {args.output_dir / 'round3_failure_examples.csv'}")
    print(f"Wrote {args.docs_dir / 'round3_reviewed_deep_analysis_v0_7.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
