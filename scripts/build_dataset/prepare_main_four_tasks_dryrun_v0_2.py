#!/usr/bin/env python
"""Prepare dry-run samples for the four stable main benchmark tasks.

Scope guard:
- dry-run only, max 50 tasks per task type
- no full cleaning
- no train/dev/test split
- no baseline
- no model training
- no full-G3 re-search
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple


ROOT = Path(".")
INPUT_DIR = ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming_dryrun"
OUTPUT_DIR = ROOT / "outputs" / "main_four_tasks_dryrun_v0_2"
REPORT_PATH = ROOT / "docs" / "phase1" / "main_four_tasks_dryrun_v0_2_report.md"
SUMMARY_PATH = OUTPUT_DIR / "main_four_tasks_dryrun_summary.json"

GROUP_TASK_FILES = {
    "G1": INPUT_DIR / "G1_task_level.csv",
    "G2": INPUT_DIR / "G2_task_level.csv",
    "G3": INPUT_DIR / "G3_task_level.csv",
}

TASK_OUTPUTS = {
    "single_service_discovery": OUTPUT_DIR / "single_service_discovery_task_level.csv",
    "single_api_recommendation": OUTPUT_DIR / "single_api_recommendation_task_level.csv",
    "multi_service_discovery": OUTPUT_DIR / "multi_service_discovery_task_level.csv",
    "multi_api_recommendation": OUTPUT_DIR / "multi_api_recommendation_task_level.csv",
}

TASK_LEVEL_FIELDS = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "leak_status",
    "semantic_alignment_status",
    "cleaning_status",
    "task_eligibility",
    "task_bucket",
    "split",
    "metadata_json",
]


def read_csv_rows(path: Path) -> Iterator[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        yield from csv.DictReader(file)


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for count, row in enumerate(rows, start=1):
            writer.writerow(row)
    return count


def parse_json_list(value: str) -> List[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def int_flag(value: str) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def count_unique_services(items: List[Any]) -> int:
    services = set()
    for item in items:
        if isinstance(item, dict):
            service = item.get("service_name", "")
        else:
            service = str(item)
        if service:
            services.add(str(service).strip().lower())
    return len(services)


def annotate(row: Dict[str, str]) -> Dict[str, Any]:
    candidate_services = parse_json_list(row.get("candidate_services_json", "[]"))
    candidate_apis = parse_json_list(row.get("candidate_apis_json", "[]"))
    gold_services = parse_json_list(row.get("gold_services_json", "[]"))
    gold_apis = parse_json_list(row.get("gold_apis_json", "[]"))
    query_mentions_api = int_flag(row.get("query_mentions_any_gold_api", "0"))
    query_mentions_service = int_flag(row.get("query_mentions_any_gold_service", "0"))

    if query_mentions_api:
        leak_status = "api_leak"
        cleaning_status = "remove_api_leak_candidate"
    elif query_mentions_service:
        leak_status = "service_leak_only"
        cleaning_status = "service_leak_only_review"
    else:
        leak_status = "no_obvious_leak"
        cleaning_status = "dryrun_candidate"

    metadata = {
        "source_query_id": row.get("source_query_id", ""),
        "original_raw_task_type": row.get("task_type", ""),
        "candidate_service_count": count_unique_services(candidate_services),
        "candidate_api_count": len(candidate_apis),
        "gold_service_count": count_unique_services(gold_services),
        "gold_api_count": len(gold_apis),
        "query_mentions_any_gold_api": query_mentions_api,
        "query_mentions_any_gold_service": query_mentions_service,
        "dry_run_note": "not full cleaning; semantic alignment not manually verified",
    }
    annotated = dict(row)
    annotated["_candidate_service_count"] = metadata["candidate_service_count"]
    annotated["_candidate_api_count"] = metadata["candidate_api_count"]
    annotated["_gold_service_count"] = metadata["gold_service_count"]
    annotated["_gold_api_count"] = metadata["gold_api_count"]
    annotated["_leak_status"] = leak_status
    annotated["_cleaning_status"] = cleaning_status
    annotated["_metadata"] = metadata
    return annotated


def output_row(row: Dict[str, Any], task_type: str) -> Dict[str, str]:
    return {
        "task_id": row.get("task_id", ""),
        "task_type": task_type,
        "source_dataset": row.get("source_dataset", ""),
        "source_group": row.get("source_group", ""),
        "query_text": row.get("query_text", ""),
        "candidate_services_json": row.get("candidate_services_json", "[]"),
        "candidate_apis_json": row.get("candidate_apis_json", "[]"),
        "gold_services_json": row.get("gold_services_json", "[]"),
        "gold_apis_json": row.get("gold_apis_json", "[]"),
        "leak_status": row["_leak_status"],
        "semantic_alignment_status": "unverified_dryrun",
        "cleaning_status": row["_cleaning_status"],
        "task_eligibility": {
            "single_service_discovery": "service_level_dryrun_candidate",
            "single_api_recommendation": "api_level_dryrun_candidate",
            "multi_service_discovery": "service_level_dryrun_candidate",
            "multi_api_recommendation": "api_level_dryrun_candidate",
        }[task_type],
        "task_bucket": task_type,
        "split": "dryrun",
        "metadata_json": json.dumps(row["_metadata"], ensure_ascii=False, sort_keys=True),
    }


def load_all_tasks() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    source_summary: Dict[str, Any] = {}
    for group, path in GROUP_TASK_FILES.items():
        if not path.exists():
            source_summary[group] = {"exists": False, "rows": 0}
            continue
        rows = [annotate(row) for row in read_csv_rows(path)]
        source_summary[group] = {
            "exists": True,
            "path": str(path),
            "rows": len(rows),
            "candidate_service_count_distribution": dict(
                Counter(row["_candidate_service_count"] for row in rows)
            ),
            "gold_service_count_distribution": dict(
                Counter(row["_gold_service_count"] for row in rows)
            ),
            "gold_api_count_distribution": dict(Counter(row["_gold_api_count"] for row in rows)),
            "leak_status_distribution": dict(Counter(row["_leak_status"] for row in rows)),
        }
        all_rows.extend(rows)
    return all_rows, source_summary


def clean_for_service(row: Dict[str, Any]) -> bool:
    return row["_leak_status"] == "no_obvious_leak"


def api_eligible(row: Dict[str, Any]) -> bool:
    # API-level tasks may retain service_leak_only as a review bucket, but never API leak.
    return row["_leak_status"] != "api_leak"


def select_samples(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, str]]], Dict[str, Any]]:
    selected: Dict[str, List[Dict[str, str]]] = {key: [] for key in TASK_OUTPUTS}
    reasons: Dict[str, str] = {}

    # G1 is intentionally not used as the primary source for service discovery
    # because dry-run evidence shows many G1 tasks have only one candidate service.
    single_service_candidates = [
        row
        for row in rows
        if row.get("source_group") in {"G2"}
        and row["_gold_service_count"] == 1
        and row["_candidate_service_count"] > 1
        and clean_for_service(row)
    ]
    selected["single_service_discovery"] = [
        output_row(row, "single_service_discovery") for row in single_service_candidates[:50]
    ]
    reasons["single_service_discovery"] = (
        "Uses G2 only when a task has one gold service, multiple candidate services, and no obvious API/service leak. "
        "G1 is not forced into this task because candidate_service_count is often 1."
    )

    single_api_candidates = [
        row
        for row in rows
        if row.get("source_group") in {"G1", "G2"}
        and row["_gold_api_count"] == 1
        and row["_candidate_api_count"] > 1
        and api_eligible(row)
    ]
    single_api_candidates.sort(key=lambda row: (row.get("source_group") != "G1", row.get("task_id", "")))
    selected["single_api_recommendation"] = [
        output_row(row, "single_api_recommendation") for row in single_api_candidates[:50]
    ]
    reasons["single_api_recommendation"] = (
        "Uses G1/G2 tasks with exactly one gold API and multiple candidate APIs; API leak is excluded. "
        "service_leak_only is retained as an audit status because API-level recommendation may still be inspectable."
    )

    multi_service_candidates = [
        row
        for row in rows
        if row.get("source_group") == "G2"
        and row["_gold_service_count"] > 1
        and row["_candidate_service_count"] > row["_gold_service_count"]
        and clean_for_service(row)
    ]
    selected["multi_service_discovery"] = [
        output_row(row, "multi_service_discovery") for row in multi_service_candidates[:50]
    ]
    reasons["multi_service_discovery"] = (
        "Uses G2 tasks with multiple gold services, a larger candidate-service set, and no obvious leak. "
        "G3 is excluded from the four stable main tasks at this stage because it is reserved as screened composable seed evidence."
    )

    multi_api_candidates = [
        row
        for row in rows
        if row.get("source_group") in {"G1", "G2"}
        and row["_gold_api_count"] > 1
        and row["_candidate_api_count"] > row["_gold_api_count"]
        and api_eligible(row)
    ]
    multi_api_candidates.sort(key=lambda row: (row.get("source_group") != "G2", row.get("task_id", "")))
    selected["multi_api_recommendation"] = [
        output_row(row, "multi_api_recommendation") for row in multi_api_candidates[:50]
    ]
    reasons["multi_api_recommendation"] = (
        "Uses G1/G2 tasks with multiple gold APIs and a larger candidate-API set; API leak is excluded. "
        "G2 is preferred, but G1 can contribute API-level cases inside one service."
    )

    return selected, reasons


def write_report(summary: Dict[str, Any]) -> None:
    lines = [
        "# Main Four Tasks Dry-run v0.2 Report",
        "",
        "## 【本次 dry-run 做了什么】",
        "从现有 ToolBench streaming dry-run 的 G1/G2/G3 task-level CSV 中读取小规模样本，按四类稳定主任务定义抽取每类最多 50 条。没有跑全量清洗、没有 train/dev/test split、没有 baseline、没有训练模型。",
        "",
        "## 【每类任务输出多少条】",
    ]
    for task_name, info in summary["tasks"].items():
        lines.append(f"- `{task_name}`: {info['rows_written']} rows -> `{info['output_file']}`")

    lines.extend(["", "## 【每类任务为什么可用或不可用】"])
    for task_name, reason in summary["selection_reasons"].items():
        rows = summary["tasks"][task_name]["rows_written"]
        if rows:
            lines.append(f"- `{task_name}`: 当前 dry-run 可输出 {rows} 条。{reason}")
        else:
            lines.append(f"- `{task_name}`: 当前 dry-run 未输出样本。{reason}")

    lines.extend(
        [
            "",
            "## 【single_service_discovery 是否仍需 MetaTool / ShortcutsBench 补强】",
            "需要继续评估。ToolBench G1 不适合作为 single_service_discovery 主来源，因为 dry-run 显示 G1 经常只有一个候选服务。当前 dry-run 优先从 G2 抽取 single-service-with-multiple-candidate-services 样本；如果数量或质量不足，应考虑 MetaTool / ShortcutsBench 补强。",
            "",
            "## 【G1 是否更适合 single_api_recommendation】",
            "是。G1 更像单服务内部 API 推荐，但需要严格排除 API leak；service leak 可以作为 API-level audit 状态保留，不能混入 clean service discovery。",
            "",
            "## 【G2 是否适合 multi_service / multi_api】",
            "相对适合。G2 更容易提供多服务、多 API 的候选集，但仍需要 leak gate 和 semantic alignment gate。",
            "",
            "## 【发现了哪些 leak / semantic mismatch 风险】",
            "本 dry-run 使用原始转换中的 query_mentions_any_gold_api / query_mentions_any_gold_service 作为初步 leak flag。semantic alignment 未在本脚本中人工验证，统一标记为 `unverified_dryrun`。",
            "",
            "## 【是否建议进入 full cleaning】",
            "不建议立刻进入 full cleaning。建议先人工抽查本 dry-run 四类任务样本，确认 G1/G2 的 leak 和 semantic mismatch 风险是否可控，再写正式清洗脚本。",
            "",
            "## 【下一步建议】",
            "先人工检查四类 dry-run CSV 每类 10–20 条，确认 schema、candidate/gold、leak_status 是否符合预期；再决定是否进入正式清洗脚本设计。",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows, source_summary = load_all_tasks()
    selected, reasons = select_samples(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    task_summary: Dict[str, Any] = {}
    for task_name, path in TASK_OUTPUTS.items():
        rows_written = write_csv(path, selected[task_name], TASK_LEVEL_FIELDS)
        task_summary[task_name] = {
            "output_file": str(path),
            "rows_written": rows_written,
            "leak_status_distribution": dict(
                Counter(row.get("leak_status", "") for row in selected[task_name])
            ),
            "source_group_distribution": dict(
                Counter(row.get("source_group", "") for row in selected[task_name])
            ),
        }

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": True,
        "max_tasks_per_task_type": 50,
        "input_dir": str(INPUT_DIR),
        "source_summary": source_summary,
        "tasks": task_summary,
        "selection_reasons": reasons,
        "scope_guard": {
            "full_cleaning": False,
            "train_dev_test_split": False,
            "baseline": False,
            "model_training": False,
            "full_g3_research": False,
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
