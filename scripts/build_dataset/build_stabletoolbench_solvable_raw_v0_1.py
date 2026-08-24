#!/usr/bin/env python
"""Build raw StableToolBench solvable-query adapter outputs.

This adapter is intentionally conservative. It converts StableToolBench
solvable instruction files into task-level raw records and an external QA
pack, but it does not clean, merge, split, baseline, or train anything.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


GROUP_FILES = {
    "G1": Path("external_sources/StableToolBench/solvable_queries/test_instruction/G1_instruction.json"),
    "G2": Path("external_sources/StableToolBench/solvable_queries/test_instruction/G2_instruction.json"),
    "G3": Path("external_sources/StableToolBench/solvable_queries/test_instruction/G3_instruction.json"),
}

TASK_TYPE_GUESS = {
    "G1": "single_or_api_recommendation_candidate",
    "G2": "multi_service_or_multi_api_candidate",
    "G3": "composable_candidate_requires_dependency_check",
}

QA_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_capability_coverage_check",
    "qa_candidate_validity_check",
    "qa_task_type_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required input file is missing: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Required input path is not a file: {path}")


def load_json_list(path: Path) -> list[dict[str, Any]]:
    require_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON file {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}, got {type(data).__name__}")
    bad = [idx for idx, item in enumerate(data[:20]) if not isinstance(item, dict)]
    if bad:
        raise ValueError(f"Expected list items to be objects in {path}; first bad indices: {bad}")
    return data


def compact_api(api: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_name": str(api.get("tool_name", "")).strip(),
        "api_name": str(api.get("api_name", "")).strip(),
        "api_description": str(api.get("api_description", "")).strip(),
        "category_name": str(api.get("category_name", "")).strip(),
        "method": str(api.get("method", "")).strip(),
    }


def normalize_relevant_api(item: Any) -> dict[str, str]:
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return {"service_name": str(item[0]).strip(), "api_name": str(item[1]).strip()}
    if isinstance(item, dict):
        return {
            "service_name": str(item.get("tool_name") or item.get("service_name") or "").strip(),
            "api_name": str(item.get("api_name") or item.get("name") or "").strip(),
        }
    return {"service_name": "", "api_name": str(item).strip()}


def build_record(group: str, source_path: Path, raw: dict[str, Any], row_index: int) -> dict[str, Any]:
    query_id = raw.get("query_id", row_index)
    task_id = f"StableToolBench_{group}_{query_id}"
    api_list_raw = raw.get("api_list") or []
    if not isinstance(api_list_raw, list):
        api_list_raw = []
    candidate_apis = [compact_api(api) for api in api_list_raw if isinstance(api, dict)]
    candidate_services = sorted({api["service_name"] for api in candidate_apis if api["service_name"]})

    relevant_raw = raw.get("relevant APIs") or raw.get("relevant_APIs") or raw.get("relevant_apis") or []
    if not isinstance(relevant_raw, list):
        relevant_raw = [relevant_raw]
    gold_apis = [normalize_relevant_api(item) for item in relevant_raw]
    gold_apis = [api for api in gold_apis if api["service_name"] or api["api_name"]]
    gold_services = sorted({api["service_name"] for api in gold_apis if api["service_name"]})

    adapter_notes = []
    if group == "G3":
        adapter_notes.append("G3 is not automatically strong composable; requires dependency-chain QA.")
    if not candidate_apis:
        adapter_notes.append("No candidate APIs parsed from api_list.")
    if not gold_apis:
        adapter_notes.append("No gold/relevant APIs parsed from relevant APIs.")

    return {
        "task_id": task_id,
        "source_dataset": "StableToolBench",
        "source_group": group,
        "stable_group": group,
        "source_query_id": str(query_id),
        "source_row_index": row_index,
        "task_type_guess": TASK_TYPE_GUESS[group],
        "query_text": str(raw.get("query", "")).strip(),
        "candidate_services_json": dumps(candidate_services),
        "candidate_apis_json": dumps(candidate_apis),
        "gold_services_json": dumps(gold_services),
        "gold_apis_json": dumps(gold_apis),
        "available_tools_or_apis_json": dumps(candidate_apis),
        "gold_tools_or_apis_json": dumps(gold_apis),
        "candidate_service_count": len(candidate_services),
        "candidate_api_count": len(candidate_apis),
        "gold_service_count": len(gold_services),
        "gold_api_count": len(gold_apis),
        "source_instruction_id": str(query_id),
        "solvable_source_path": str(source_path),
        "adapter_notes": " | ".join(adapter_notes),
        "requires_composable_dependency_check": "yes" if group == "G3" else "no",
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def proportional_quotas(group_counts: dict[str, int], limit: int) -> dict[str, int]:
    total = sum(group_counts.values())
    if total <= limit:
        return dict(group_counts)
    raw = {g: group_counts[g] * limit / total for g in group_counts}
    quotas = {g: int(raw[g]) for g in group_counts}
    remaining = limit - sum(quotas.values())
    order = sorted(group_counts, key=lambda g: (raw[g] - quotas[g], group_counts[g]), reverse=True)
    for g in order[:remaining]:
        quotas[g] += 1
    return quotas


def spread_sample(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    if n >= len(rows):
        return rows
    if n <= 0:
        return []
    if n == 1:
        return [rows[0]]
    indices = [round(i * (len(rows) - 1) / (n - 1)) for i in range(n)]
    return [rows[idx] for idx in indices]


def build_qa_pack(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = {"G1": [], "G2": [], "G3": []}
    for row in rows:
        by_group[row["source_group"]].append(row)
    quotas = proportional_quotas({g: len(v) for g, v in by_group.items()}, limit)
    sampled: list[dict[str, Any]] = []
    for group in ["G1", "G2", "G3"]:
        sampled.extend(spread_sample(by_group[group], quotas[group]))
    for idx, row in enumerate(sampled, start=1):
        row["qa_item_id"] = f"STB-QA-{idx:03d}"
        for field in QA_FIELDS:
            row[field] = ""
    return sampled


def write_report(path: Path, summary: dict[str, Any], input_paths: dict[str, str], output_paths: dict[str, str]) -> None:
    lines = [
        "# StableToolBench Solvable Adapter Report v0.1",
        "",
        f"Generated time: {summary['generated_at']}",
        "",
        "## Inputs",
    ]
    for group, src in input_paths.items():
        lines.append(f"- {group}: `{src}`")
    lines.extend(
        [
            "",
            "## Outputs",
        ]
    )
    for name, dst in output_paths.items():
        lines.append(f"- {name}: `{dst}`")
    lines.extend(
        [
            "",
            "## Counts",
            f"- Total task rows: {summary['total_rows']}",
            f"- Rows by group: `{dumps(summary['rows_by_group'])}`",
            f"- QA pack rows: {summary['qa_pack_rows']}",
            "",
            "## Interpretation Boundary",
            "- This adapter only reconstructs StableToolBench solvable raw task-level records.",
            "- G3 rows are marked as requiring dependency-chain review and are not automatically strong composable.",
            "- These rows are not merged into ToolBench-core, not cleaned, not split, and not used for baseline/training.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_review_plan(path: Path, qa_rows: int, output_csv: Path) -> None:
    text = f"""# StableToolBench Solvable Review Plan v0.1

Generated time: {now_iso()}

## Review CSV

- `{output_csv}`

## Sample Size

- Review items: {qa_rows}

## Human Review Goal

This QA pack checks whether StableToolBench solvable rows can be safely used as external support material for service/API discovery benchmark construction.

Reviewers should verify:

- Whether query and gold APIs are semantically aligned.
- Whether candidate APIs provide real choice space.
- Whether G1/G2/G3 task-type guess is appropriate.
- Whether G3 has a real dependency chain. Do not mark G3 as strong composable only because it is from G3.
- Whether any row has leakage, missing core requirement, or invalid candidate/gold structure.

## Boundary

This is an external-source QA pack only. It is not a final clean dataset, split, baseline, or training set.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build StableToolBench solvable raw adapter outputs.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument(
        "--output-dir",
        default="outputs/external_sources_adapters_v0_1/stabletoolbench",
        help="Adapter output directory.",
    )
    parser.add_argument(
        "--qa-output-dir",
        default="outputs/external_qa_v0_1/stabletoolbench",
        help="QA pack output directory.",
    )
    parser.add_argument("--qa-limit", type=int, default=100, help="Maximum QA rows.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = project_root / args.output_dir
    qa_output_dir = project_root / args.qa_output_dir

    input_paths = {group: project_root / rel for group, rel in GROUP_FILES.items()}
    rows: list[dict[str, Any]] = []
    rows_by_group: dict[str, int] = {}
    for group, path in input_paths.items():
        records = load_json_list(path)
        rows_by_group[group] = len(records)
        for idx, raw in enumerate(records, start=1):
            rows.append(build_record(group, path.relative_to(project_root), raw, idx))

    adapter_csv = output_dir / "stabletoolbench_solvable_task_level_raw.csv"
    fieldnames = list(rows[0].keys()) if rows else []
    write_csv(adapter_csv, rows, fieldnames)

    qa_rows = build_qa_pack(rows, args.qa_limit)
    qa_csv = qa_output_dir / "stabletoolbench_solvable_review_items_100_or_all.csv"
    qa_fieldnames = ["qa_item_id"] + fieldnames + QA_FIELDS
    write_csv(qa_csv, qa_rows, qa_fieldnames)

    summary = {
        "generated_at": now_iso(),
        "project_root": str(project_root),
        "input_files": {g: str(p) for g, p in input_paths.items()},
        "output_file": str(adapter_csv),
        "qa_output_file": str(qa_csv),
        "total_rows": len(rows),
        "rows_by_group": rows_by_group,
        "qa_pack_rows": len(qa_rows),
        "g3_rows_marked_requires_dependency_check": rows_by_group.get("G3", 0),
        "adapter_boundary": "raw_solvable_task_level_only_no_cleaning_no_merge_no_split_no_baseline_no_training",
    }
    summary_json = output_dir / "stabletoolbench_adapter_summary.json"
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = project_root / "docs/phase1/stabletoolbench_solvable_adapter_report_v0_1.md"
    write_report(
        report_path,
        summary,
        {g: str(p) for g, p in input_paths.items()},
        {"task_level_raw": str(adapter_csv), "summary_json": str(summary_json), "qa_pack": str(qa_csv)},
    )
    review_plan_path = project_root / "docs/phase1/stabletoolbench_solvable_review_plan_v0_1.md"
    write_review_plan(review_plan_path, len(qa_rows), qa_csv)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
