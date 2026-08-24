#!/usr/bin/env python
"""Summarize a future valid v0.3 composable paired-task human review."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_composable_paired_task_review_v0_3 import read_csv, validate_rows  # noqa: E402


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def parse_json(value: Any, default: Any) -> Any:
    raw = text(value)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def distribution(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(text(row.get(field)) or "<blank>" for row in rows).items()))


def build_summary(rows: list[dict[str, str]], validation: dict[str, Any]) -> dict[str, Any]:
    labels = distribution(rows, "composition_final_label")
    actions = distribution(rows, "composable_release_action")
    service_eligible = sum(1 for row in rows if text(row.get("service_level_eligible")) == "true")
    api_eligible = sum(1 for row in rows if text(row.get("api_level_eligible")) == "true")
    both_eligible = sum(1 for row in rows if text(row.get("service_level_eligible")) == "true" and text(row.get("api_level_eligible")) == "true")
    dependency_edge_pass = sum(1 for row in rows if text(row.get("dependency_edge_valid")) == "true")
    dependency_types: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    for row in rows:
        domains[text(row.get("catalog_domain_signature")) or "unknown"] += 1
        dependency_types.update(parse_json(row.get("dependency_type_distribution_json"), {}))
    count = len(rows)
    return {
        "generated_at": now_iso(),
        "status": "HUMAN_REVIEW_SUMMARIZED",
        "rows": count,
        "validation": validation,
        "true_composable_count": labels.get("true_composable", 0),
        "parallel_multi_count": labels.get("parallel_multi", 0),
        "hybrid_count": labels.get("hybrid_composable_multi", 0),
        "insufficient_evidence_count": labels.get("insufficient_evidence", 0),
        "invalid_task_count": labels.get("invalid_task", 0),
        "service_level_eligible_count": service_eligible,
        "api_level_eligible_count": api_eligible,
        "both_levels_eligible_count": both_eligible,
        "reconstruct_api_count": actions.get("reconstruct_api_then_reaudit", 0),
        "reconstruct_service_count": actions.get("reconstruct_service_then_reaudit", 0),
        "rewrite_count": actions.get("rewrite_query_then_reaudit", 0),
        "reclassify_multi_count": actions.get("reclassify_as_multi", 0),
        "hold_count": actions.get("hold", 0),
        "remove_count": actions.get("remove", 0),
        "dependency_edge_pass_rate": dependency_edge_pass / count if count else 0.0,
        "service_level_pass_rate": service_eligible / count if count else 0.0,
        "api_level_pass_rate": api_eligible / count if count else 0.0,
        "paired_pass_rate": both_eligible / count if count else 0.0,
        "composition_label_distribution": labels,
        "release_action_distribution": actions,
        "domain_distribution": dict(sorted(domains.items())),
        "dependency_type_distribution": dict(sorted(dependency_types.items())),
    }


def write_csv(path: Path, summary: dict[str, Any]) -> None:
    scalar = [(key, value) for key, value in summary.items() if not isinstance(value, (dict, list))]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(scalar)


def write_report(path: Path, summary: dict[str, Any], reviewed_path: Path) -> None:
    lines = [
        "# Composable Paired Task Human Review Summary v0.3",
        "",
        f"Generated at: `{summary['generated_at']}`",
        f"Reviewed input: `{reviewed_path}`",
        f"Rows: `{summary['rows']}`",
        "",
        "## Core Counts",
        "",
    ]
    keys = [
        "true_composable_count", "parallel_multi_count", "hybrid_count",
        "insufficient_evidence_count", "invalid_task_count", "service_level_eligible_count",
        "api_level_eligible_count", "both_levels_eligible_count", "reconstruct_api_count",
        "reconstruct_service_count", "rewrite_count", "reclassify_multi_count", "hold_count",
        "remove_count", "dependency_edge_pass_rate", "service_level_pass_rate",
        "api_level_pass_rate", "paired_pass_rate",
    ]
    lines.extend(f"- {key}: `{summary[key]}`" for key in keys)
    lines.extend([
        "",
        "This report summarizes human decisions only. It does not generate final benchmark rows, split data, run baselines, or train a model.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Summarize a future reviewed composable v0.3 CSV.")
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument("--base-csv", type=Path, default=Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_items_v0_3.csv"))
    parser.add_argument("--reviewed-csv", type=Path, default=Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_items_v0_3_reviewed.csv"))
    parser.add_argument("--output-json", type=Path, default=Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_summary_v0_3.json"))
    parser.add_argument("--output-csv", type=Path, default=Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_summary_v0_3.csv"))
    parser.add_argument("--output-report", type=Path, default=Path("docs/phase1/composable_paired_task_review_summary_v0_3.md"))
    return parser.parse_args()


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    base_path = resolve(root, args.base_csv)
    reviewed_path = resolve(root, args.reviewed_csv)
    if not base_path.exists():
        raise FileNotFoundError(f"Base review CSV does not exist: {base_path}")
    if not reviewed_path.exists():
        print(json.dumps({
            "status": "WAITING_FOR_HUMAN_REVIEW",
            "reviewed_csv": str(reviewed_path),
            "final_rows_generated": False,
        }, ensure_ascii=False, indent=2))
        return 0
    base_rows = read_csv(base_path)
    reviewed_rows = read_csv(reviewed_path)
    validation = validate_rows(base_rows, reviewed_rows)
    if validation["status"] != "VALID":
        print(json.dumps({"status": "REVIEW_VALIDATION_FAILED", "validation": validation}, ensure_ascii=False, indent=2))
        return 2
    summary = build_summary(reviewed_rows, validation)
    json_path = resolve(root, args.output_json)
    csv_path = resolve(root, args.output_csv)
    report_path = resolve(root, args.output_report)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(csv_path, summary)
    write_report(report_path, summary, reviewed_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
