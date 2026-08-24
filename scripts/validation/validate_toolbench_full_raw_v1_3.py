from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from toolbench_v1_3_common import (
    FULL_DIR,
    VALIDATION_DIR,
    DOC_DIR,
    ensure_dir,
    now_text,
    parse_json_array,
    table_lines,
    write_json,
    write_md,
)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate ToolBench full raw v1.3 outputs.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--full-dir", type=Path, default=FULL_DIR)
    parser.add_argument("--validation-dir", type=Path, default=VALIDATION_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    full_dir = root / args.full_dir
    validation_dir = root / args.validation_dir
    ensure_dir(validation_dir)
    task_path = full_dir / "toolbench_full_task_level_raw.csv"
    candidate_path = full_dir / "toolbench_full_candidate_level_raw.csv"
    if not task_path.exists() or not candidate_path.exists():
        raise FileNotFoundError("Full task-level or candidate-level raw CSV is missing.")

    errors: list[dict] = []
    task_ids: set[str] = set()
    duplicate_task_ids = 0
    group_stats: dict[str, dict] = defaultdict(lambda: {
        "task_rows": 0,
        "candidate_rows": 0,
        "candidate_service_total": 0,
        "candidate_api_total": 0,
        "gold_service_total": 0,
        "gold_api_total": 0,
        "query_mentions_any_gold_api_count": 0,
        "query_mentions_any_gold_service_count": 0,
        "candidate_service_count_distribution": Counter(),
        "candidate_api_count_distribution": Counter(),
        "gold_service_count_distribution": Counter(),
        "gold_api_count_distribution": Counter(),
    })

    with task_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row_num, row in enumerate(csv.DictReader(f), start=2):
            task_id = row.get("task_id", "")
            group = row.get("source_group", "")
            if not task_id:
                errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": "empty_task_id", "message": ""})
            if task_id in task_ids:
                duplicate_task_ids += 1
                errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": "duplicate_task_id", "message": ""})
            task_ids.add(task_id)
            if not row.get("query_text", "").strip():
                errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": "empty_query_text", "message": ""})
            parsed = {}
            for field in ["candidate_services_json", "candidate_apis_json", "gold_services_json", "gold_apis_json"]:
                data = parse_json_array(row.get(field, ""))
                parsed[field] = data
                if not data:
                    errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": f"empty_or_unparseable_{field}", "message": ""})
            expected_counts = {
                "candidate_service_count": len(parsed["candidate_services_json"]),
                "candidate_api_count": len(parsed["candidate_apis_json"]),
                "gold_service_count": len(parsed["gold_services_json"]),
                "gold_api_count": len(parsed["gold_apis_json"]),
            }
            for field, expected in expected_counts.items():
                try:
                    actual = int(row.get(field, ""))
                except Exception:
                    actual = -1
                if actual != expected:
                    errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": f"{field}_mismatch", "message": f"actual={actual}; expected={expected}"})
            if row.get("gold_in_candidate_services") != "yes":
                errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": "gold_services_not_subset_candidates", "message": ""})
            if row.get("gold_in_candidate_apis") != "yes":
                errors.append({"level": "task", "row_num": row_num, "task_id": task_id, "error_type": "gold_apis_not_subset_candidates", "message": ""})

            stats = group_stats[group]
            stats["task_rows"] += 1
            stats["candidate_service_total"] += expected_counts["candidate_service_count"]
            stats["candidate_api_total"] += expected_counts["candidate_api_count"]
            stats["gold_service_total"] += expected_counts["gold_service_count"]
            stats["gold_api_total"] += expected_counts["gold_api_count"]
            stats["query_mentions_any_gold_api_count"] += int(str(row.get("query_mentions_any_gold_api", "0")) == "1")
            stats["query_mentions_any_gold_service_count"] += int(str(row.get("query_mentions_any_gold_service", "0")) == "1")
            stats["candidate_service_count_distribution"][str(expected_counts["candidate_service_count"])] += 1
            stats["candidate_api_count_distribution"][str(expected_counts["candidate_api_count"])] += 1
            stats["gold_service_count_distribution"][str(expected_counts["gold_service_count"])] += 1
            stats["gold_api_count_distribution"][str(expected_counts["gold_api_count"])] += 1

    candidate_task_ids: set[str] = set()
    candidate_tasks_with_gold: set[str] = set()
    empty_candidate_name = 0
    with candidate_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row_num, row in enumerate(csv.DictReader(f), start=2):
            task_id = row.get("task_id", "")
            group = row.get("source_group", "")
            candidate_task_ids.add(task_id)
            group_stats[group]["candidate_rows"] += 1
            if not row.get("candidate_row_id", ""):
                errors.append({"level": "candidate", "row_num": row_num, "task_id": task_id, "error_type": "empty_candidate_row_id", "message": ""})
            if not task_id:
                errors.append({"level": "candidate", "row_num": row_num, "task_id": task_id, "error_type": "empty_task_id", "message": ""})
            if not row.get("candidate_api_name", "") and not row.get("candidate_service_name", ""):
                empty_candidate_name += 1
                errors.append({"level": "candidate", "row_num": row_num, "task_id": task_id, "error_type": "empty_candidate_name", "message": ""})
            if str(row.get("is_gold_api", "")) in {"1", "true", "True"}:
                candidate_tasks_with_gold.add(task_id)

    missing_candidate_tasks = task_ids - candidate_task_ids
    extra_candidate_tasks = candidate_task_ids - task_ids
    missing_gold_candidate_tasks = task_ids - candidate_tasks_with_gold
    for task_id in list(missing_candidate_tasks)[:1000]:
        errors.append({"level": "cross", "row_num": "", "task_id": task_id, "error_type": "task_without_candidate_rows", "message": ""})
    for task_id in list(extra_candidate_tasks)[:1000]:
        errors.append({"level": "cross", "row_num": "", "task_id": task_id, "error_type": "candidate_task_not_in_task_level", "message": ""})
    for task_id in list(missing_gold_candidate_tasks)[:1000]:
        errors.append({"level": "cross", "row_num": "", "task_id": task_id, "error_type": "task_without_gold_candidate_row", "message": ""})

    fatal_error_types = [
        "empty_task_id",
        "empty_query_text",
        "empty_or_unparseable_candidate_services_json",
        "empty_or_unparseable_candidate_apis_json",
        "empty_or_unparseable_gold_services_json",
        "empty_or_unparseable_gold_apis_json",
        "gold_services_not_subset_candidates",
        "gold_apis_not_subset_candidates",
        "task_without_candidate_rows",
        "candidate_task_not_in_task_level",
        "task_without_gold_candidate_row",
    ]
    error_counts = Counter(error["error_type"] for error in errors)
    fatal_error_count = sum(error_counts.get(t, 0) for t in fatal_error_types)
    summary_group_stats = {}
    for group, stats in group_stats.items():
        task_rows = stats["task_rows"] or 1
        summary_group_stats[group] = {
            "task_rows": stats["task_rows"],
            "candidate_rows": stats["candidate_rows"],
            "avg_candidate_services_per_task": round(stats["candidate_service_total"] / task_rows, 4),
            "avg_candidate_apis_per_task": round(stats["candidate_api_total"] / task_rows, 4),
            "avg_gold_services_per_task": round(stats["gold_service_total"] / task_rows, 4),
            "avg_gold_apis_per_task": round(stats["gold_api_total"] / task_rows, 4),
            "query_mentions_any_gold_api_count": stats["query_mentions_any_gold_api_count"],
            "query_mentions_any_gold_service_count": stats["query_mentions_any_gold_service_count"],
            "candidate_service_count_distribution": dict(stats["candidate_service_count_distribution"]),
            "candidate_api_count_distribution": dict(stats["candidate_api_count_distribution"]),
            "gold_service_count_distribution": dict(stats["gold_service_count_distribution"]),
            "gold_api_count_distribution": dict(stats["gold_api_count_distribution"]),
        }

    summary = {
        "generated_time": now_text(),
        "task_level_csv": str(task_path),
        "candidate_level_csv": str(candidate_path),
        "task_rows": len(task_ids),
        "candidate_task_id_count": len(candidate_task_ids),
        "candidate_rows": sum(stats["candidate_rows"] for stats in group_stats.values()),
        "duplicate_task_ids": duplicate_task_ids,
        "missing_candidate_task_count": len(missing_candidate_tasks),
        "extra_candidate_task_count": len(extra_candidate_tasks),
        "missing_gold_candidate_task_count": len(missing_gold_candidate_tasks),
        "error_count": len(errors),
        "fatal_error_count": fatal_error_count,
        "error_type_counts": dict(error_counts),
        "group_stats": summary_group_stats,
        "gold_in_candidate_pass_rate": "100.0%" if fatal_error_count == 0 else "see_errors",
        "validation_has_fatal_errors": fatal_error_count > 0,
    }
    write_json(validation_dir / "full_raw_validation_summary.json", summary)
    write_csv(validation_dir / "full_raw_validation_errors.csv", errors, ["level", "row_num", "task_id", "error_type", "message"])

    lines = [
        "# ToolBench Full Raw Validation Report v1.3",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Task-level input: `{task_path}`",
        f"Candidate-level input: `{candidate_path}`",
        "",
        "Scope: raw conversion validation only. No cleaning, split, baseline, model training, final clean dataset, or new human review was run.",
        "",
        "## Summary",
        "",
        f"- task rows: {summary['task_rows']}",
        f"- candidate rows: {summary['candidate_rows']}",
        f"- fatal_error_count: {fatal_error_count}",
        f"- missing_candidate_task_count: {len(missing_candidate_tasks)}",
        f"- missing_gold_candidate_task_count: {len(missing_gold_candidate_tasks)}",
        "",
        "## Group statistics",
        "",
        "| group | task rows | candidate rows | avg candidate APIs | avg gold APIs | gold API leaks | service leaks |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in ["G1", "G2", "G3"]:
        stats = summary_group_stats.get(group, {})
        lines.append(
            f"| {group} | {stats.get('task_rows', 0)} | {stats.get('candidate_rows', 0)} | "
            f"{stats.get('avg_candidate_apis_per_task', 0)} | {stats.get('avg_gold_apis_per_task', 0)} | "
            f"{stats.get('query_mentions_any_gold_api_count', 0)} | {stats.get('query_mentions_any_gold_service_count', 0)} |"
        )
    lines.extend(["", "## Error type counts", "", *table_lines(dict(error_counts) if error_counts else {"none": 0})])
    write_md(Path("docs/phase1/toolbench_full_raw_validation_report_v1_3.md"), lines)

    print("Validation complete.")
    print("fatal_error_count:", fatal_error_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
