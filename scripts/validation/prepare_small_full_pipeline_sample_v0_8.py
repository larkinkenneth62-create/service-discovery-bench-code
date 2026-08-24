"""Prepare v0.8 small full-pipeline input sample.

The output is a trace input sample only. It is not a clean dataset.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence

from small_full_pipeline_v0_8_utils import (
    OUTPUT_DIR,
    SAMPLE_COLUMNS,
    audited_task_ids,
    count_by,
    count_by_level,
    ensure_dirs,
    load_all_candidate_rows,
    now_str,
    parse_int,
    prediction_level,
    write_csv,
)


DEFAULT_MAX_SAMPLE = 300


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a small v0.8 raw/task-level sample for trace-only validation.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--max-sample", type=int, default=DEFAULT_MAX_SAMPLE)
    return parser


def row_bucket(row: Dict[str, object]) -> str:
    source = str(row.get("source_file", ""))
    group = str(row.get("source_group", ""))
    level = prediction_level(str(row.get("task_type", "")))
    if level == "api":
        return "api_level"
    if "G1_task_level" in source or group == "G1":
        return "G1_service"
    if "G3_task_level" in source or group == "G3":
        return "G3_composable_service"
    if group == "G2":
        return "G2_service"
    return f"{group}_{level}"


def has_feature(row: Dict[str, object], feature: str) -> bool:
    csc = parse_int(row.get("candidate_service_count"))
    gsc = parse_int(row.get("gold_service_count"))
    capi = parse_int(row.get("candidate_api_count"))
    gapi = parse_int(row.get("gold_api_count"))
    if feature == "candidate_service_count_eq_1":
        return csc == 1
    if feature == "candidate_service_count_gt_1":
        return csc is not None and csc > 1
    if feature == "candidate_api_count_gt_gold_api_count":
        return capi is not None and gapi is not None and capi > gapi
    if feature == "query_mentions_any_gold_api":
        return str(row.get("query_mentions_any_gold_api")) == "1"
    if feature == "query_mentions_any_gold_service":
        return str(row.get("query_mentions_any_gold_service")) == "1"
    if feature == "no_obvious_leak":
        return str(row.get("query_mentions_any_gold_api")) != "1" and str(row.get("query_mentions_any_gold_service")) != "1"
    return False


def add_rows(
    selected: List[Dict[str, object]],
    selected_keys: set[tuple[str, str]],
    rows: Sequence[Dict[str, object]],
    limit: int,
) -> None:
    for row in rows:
        if len(selected) >= limit:
            return
        key = (str(row.get("task_id", "")), str(row.get("task_type", "")))
        if key in selected_keys:
            continue
        selected.append(dict(row))
        selected_keys.add(key)


def stratified_sample(rows: List[Dict[str, object]], max_sample: int) -> List[Dict[str, object]]:
    non_overlap = [row for row in rows if row.get("overlaps_audited_sample") == "no"]
    overlap = [row for row in rows if row.get("overlaps_audited_sample") == "yes"]
    buckets: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in non_overlap:
        buckets[row_bucket(row)].append(row)

    quotas = [
        ("G1_service", min(50, max_sample)),
        ("G2_service", min(70, max_sample)),
        ("G3_composable_service", min(50, max_sample)),
        ("api_level", min(80, max_sample)),
        ("other", min(50, max_sample)),
    ]
    selected: List[Dict[str, object]] = []
    selected_keys: set[tuple[str, str]] = set()
    for bucket, quota in quotas:
        if bucket == "other":
            pool = [row for key, vals in buckets.items() if key not in {"G1_service", "G2_service", "G3_composable_service", "api_level"} for row in vals]
        else:
            pool = buckets.get(bucket, [])
        add_rows(selected, selected_keys, pool, min(max_sample, len(selected) + quota))

    feature_targets = [
        "candidate_service_count_eq_1",
        "candidate_service_count_gt_1",
        "candidate_api_count_gt_gold_api_count",
        "query_mentions_any_gold_api",
        "query_mentions_any_gold_service",
        "no_obvious_leak",
    ]
    for feature in feature_targets:
        if any(has_feature(row, feature) for row in selected):
            continue
        pool = [row for row in non_overlap if has_feature(row, feature)]
        add_rows(selected, selected_keys, pool, max_sample)

    add_rows(selected, selected_keys, non_overlap, max_sample)
    if len(selected) < max_sample:
        add_rows(selected, selected_keys, overlap, max_sample)

    for idx, row in enumerate(selected, start=1):
        row["v0_8_sample_id"] = f"V08-{idx:03d}"
    return selected[:max_sample]


def distribution_rows(rows: Sequence[Dict[str, object]], key: str) -> List[Dict[str, object]]:
    return [{key: k, "count": v} for k, v in count_by(rows, key).items()]


def count_distribution(rows: Sequence[Dict[str, object]], key: str) -> List[Dict[str, object]]:
    counter = Counter(str(row.get(key, "not_available")) for row in rows)
    return [{key: k, "count": v} for k, v in sorted(counter.items())]


def write_report(path: Path, rows: List[Dict[str, object]], all_rows: List[Dict[str, object]]) -> None:
    overlap_count = sum(1 for row in rows if row.get("overlaps_audited_sample") == "yes")
    feature_counts = {
        "candidate_service_count_eq_1": sum(1 for row in rows if has_feature(row, "candidate_service_count_eq_1")),
        "candidate_service_count_gt_1": sum(1 for row in rows if has_feature(row, "candidate_service_count_gt_1")),
        "candidate_api_count_gt_gold_api_count": sum(1 for row in rows if has_feature(row, "candidate_api_count_gt_gold_api_count")),
        "query_mentions_any_gold_api": sum(1 for row in rows if has_feature(row, "query_mentions_any_gold_api")),
        "query_mentions_any_gold_service": sum(1 for row in rows if has_feature(row, "query_mentions_any_gold_service")),
        "no_obvious_leak": sum(1 for row in rows if has_feature(row, "no_obvious_leak")),
    }
    lines = [
        "# Small Full-Pipeline Sampling Report v0.8",
        "",
        f"Generated time: {now_str()}",
        f"Input candidate rows before sampling: {len(all_rows)}",
        f"Output sample count: {len(rows)}",
        "",
        "Scope: this is a trace-only raw/task-level sample. It is not a clean dataset.",
        "",
        "## Source Distribution",
        "",
        "| source_file | count |",
        "|---|---:|",
    ]
    for item in distribution_rows(rows, "source_file"):
        lines.append(f"| `{item['source_file']}` | {item['count']} |")
    lines.extend(["", "## source_group Distribution", "", "| source_group | count |", "|---|---:|"])
    for item in distribution_rows(rows, "source_group"):
        lines.append(f"| {item['source_group']} | {item['count']} |")
    lines.extend(["", "## Task Type Distribution", "", "| task_type | count |", "|---|---:|"])
    for item in distribution_rows(rows, "task_type"):
        lines.append(f"| {item['task_type']} | {item['count']} |")
    lines.extend(["", "## Prediction-Level Distribution", "", "| prediction_level | count |", "|---|---:|"])
    for key, value in count_by_level(rows).items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Candidate/Gold Count Distribution", ""])
    for key in ["candidate_service_count", "gold_service_count", "candidate_api_count", "gold_api_count"]:
        lines.extend([f"### {key}", "", f"| {key} | count |", "|---|---:|"])
        for item in count_distribution(rows, key):
            lines.append(f"| {item[key]} | {item['count']} |")
        lines.append("")
    lines.extend(["", "## Possible Leak Flag Distribution", "", "| feature | count |", "|---|---:|"])
    for key, value in feature_counts.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Audited-Sample Overlap",
            "",
            f"- Overlap count by task_id/task_type sampling key: {overlap_count}",
            f"- Non-overlap count: {len(rows) - overlap_count}",
        ]
    )
    if overlap_count:
        lines.append("- Some overlap was retained only after non-overlap rows were exhausted for coverage/size.")
    else:
        lines.append("- No audited task_id overlap was needed for the selected sample.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    all_rows = load_all_candidate_rows()
    if not all_rows:
        print("ERROR: no candidate rows available. Run check_v0_8_inputs.py first.")
        return 1
    sample = stratified_sample(all_rows, args.max_sample)
    out_csv = args.output_dir / "small_full_pipeline_input_tasks.csv"
    write_csv(out_csv, sample, SAMPLE_COLUMNS)
    report = args.output_dir / "small_full_pipeline_sampling_report.md"
    write_report(report, sample, all_rows)
    print(f"Candidate rows available: {len(all_rows)}")
    print(f"Small sample rows: {len(sample)}")
    print(f"Overlap with audited task_ids: {sum(1 for row in sample if row.get('overlaps_audited_sample') == 'yes')}")
    print(f"Wrote {out_csv}")
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
