#!/usr/bin/env python
"""Analyze GPT-5.5 Pro reviewed external QA CSVs.

This is reporting-only. It does not merge external sources, create final
datasets, split data, run baselines, train models, or call any external API.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ARCHIVE_DIR = Path("outputs/run_archives/2026-07-05_external_qa_manual_reviewed_by_gpt55pro_analysis_v0_1")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def split_error_types(rows: list[dict[str, str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        raw = row.get("qa_error_type", "")
        if not raw:
            counter[""] += 1
            continue
        for part in raw.split(";"):
            counter[part.strip()] += 1
    return counter


def dist(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(Counter(row.get(field, "") for row in rows))


def sample_rows(rows: list[dict[str, str]], predicate, limit: int = 8) -> list[dict[str, str]]:
    out = []
    for row in rows:
        if predicate(row):
            out.append(
                {
                    "review_item_id": row.get("review_item_id", row.get("qa_item_id", "")),
                    "task_id": row.get("task_id", ""),
                    "stable_group": row.get("stable_group", ""),
                    "qa_final_decision": row.get("qa_final_decision", ""),
                    "qa_severity": row.get("qa_severity", ""),
                    "qa_error_type": row.get("qa_error_type", ""),
                    "qa_notes": row.get("qa_notes", "")[:240],
                }
            )
        if len(out) >= limit:
            break
    return out


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md_analysis(path: Path, summary: dict[str, Any]) -> None:
    meta = summary["metatool"]
    stb = summary["stabletoolbench"]
    lines = [
        "# External QA Manual Reviewed By GPT-5.5 Pro Analysis v0.1",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Inputs",
        "",
        f"- MetaTool reviewed CSV: `{meta['input_csv']}`",
        f"- StableToolBench reviewed CSV: `{stb['input_csv']}`",
        "",
        "## Validation",
        "",
        f"- MetaTool validation fatal: `{meta['validation_is_fatal']}`",
        f"- StableToolBench validation fatal: `{stb['validation_is_fatal']}`",
        "",
        "## MetaTool",
        "",
        f"- rows: {meta['rows']}",
        f"- reviewed_count: {meta['reviewed_count']}",
        f"- qa_final_decision_distribution: `{meta['qa_final_decision_distribution']}`",
        f"- qa_severity_distribution: `{meta['qa_severity_distribution']}`",
        f"- leakage_check_distribution: `{meta['leakage_check_distribution']}`",
        f"- candidate_validity_distribution: `{meta['candidate_validity_distribution']}`",
        f"- service_catalog_check_distribution: `{meta['service_catalog_check_distribution']}`",
        f"- status: `{meta['status']}`",
        "",
        "MetaTool signal: the catalog is structurally valid and most rows are keep candidates, but leakage is not resolved because 94/100 are `leak_uncertain` and 5/100 are `service_leak_blocking`. This means MetaTool cannot be merged as clean data yet; it needs a source-specific leak/rewrite policy.",
        "",
        "## StableToolBench",
        "",
        f"- rows: {stb['rows']}",
        f"- reviewed_count: {stb['reviewed_count']}",
        f"- qa_final_decision_distribution: `{stb['qa_final_decision_distribution']}`",
        f"- qa_severity_distribution: `{stb['qa_severity_distribution']}`",
        f"- leakage_check_distribution: `{stb['leakage_check_distribution']}`",
        f"- candidate_validity_distribution: `{stb['candidate_validity_distribution']}`",
        f"- task_type_check_distribution: `{stb['task_type_check_distribution']}`",
        f"- stable_group_distribution: `{stb['stable_group_distribution']}`",
        f"- g3_not_strong_composable_count: {stb['g3_not_strong_composable_count']}",
        f"- status: `{stb['status']}`",
        "",
        "StableToolBench signal: this source fails the current external QA gate because it has 2 critical rows, 56 remove rows, 25 invalid candidate-space rows, and 17 G3 rows marked `composable_not_strong_dependency`.",
        "",
        "## Combined",
        "",
        f"- combined_rows: {summary['combined']['rows']}",
        f"- combined_final_decision_distribution: `{summary['combined']['qa_final_decision_distribution']}`",
        f"- combined_critical_count: {summary['combined']['critical_count']}",
        "",
        "## Representative Remove/Critical Samples",
        "",
        "### MetaTool",
        "",
    ]
    for row in meta["remove_or_critical_examples"]:
        lines.append(f"- {row['review_item_id']} / {row['task_id']} / {row['qa_error_type']}: {row['qa_notes']}")
    lines.extend(["", "### StableToolBench", ""])
    for row in stb["remove_or_critical_examples"]:
        lines.append(f"- {row['review_item_id']} / {row['task_id']} / {row.get('stable_group','')} / {row['qa_error_type']}: {row['qa_notes']}")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- No external source merge was performed.",
            "- No final clean dataset was generated.",
            "- No split, baseline, training, Qwen call, or external API call was performed.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_go_no_go(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# External QA Manual Reviewed By GPT-5.5 Pro Go/No-Go v0.1",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Decisions",
        "",
        "- can_accept_metatool_external_qa_as_pass: `false`",
        "- can_accept_stabletoolbench_external_qa_as_pass: `false`",
        "- can_merge_external_sources_now: `false`",
        "- can_generate_full_six_task_benchmark_now: `false`",
        "- can_generate_final_clean_dataset_now: `false`",
        "- can_create_split_now: `false`",
        "- can_run_baseline_now: `false`",
        "- can_train_model_now: `false`",
        "",
        "## Reason",
        "",
        "- MetaTool has no critical rows and high keep count, but leakage is unresolved (`leak_uncertain=94`, `service_leak_blocking=5`). It needs a source-specific leakage/rewrite policy before use.",
        "- StableToolBench fails the gate because `critical_count=2`, `remove=56`, and G3 composable validity is weak (`composable_not_strong_dependency=17`). It needs source-specific filtering and likely a candidate-space reconstruction policy.",
        "",
        "## Recommended Next Step",
        "",
        "Build source-specific policies separately: MetaTool leakage/rewrite policy first, StableToolBench candidate-space/leakage/composable filtering second. Do not merge either source into the final benchmark yet.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive(project_root: Path, files: list[Path]) -> Path:
    archive_root = project_root / ARCHIVE_DIR
    manifest = []
    for src in files:
        if not src.exists():
            manifest.append({"source": str(src), "copied": False, "reason": "missing"})
            continue
        rel = src.relative_to(project_root)
        dst = archive_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest.append({"source": str(src), "archive_path": str(dst), "copied": True, "size_bytes": src.stat().st_size})
    write_json(archive_root / "archive_manifest.json", {"generated_time": now(), "files": manifest})
    return archive_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze GPT-5.5 Pro reviewed external QA CSVs.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current working directory.")
    parser.add_argument(
        "--metatool-csv",
        default="outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100_manual_reviewed_by_gpt55pro.csv",
    )
    parser.add_argument(
        "--stabletoolbench-csv",
        default="outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_manual_reviewed_by_gpt55pro.csv",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    meta_csv = project_root / args.metatool_csv
    stb_csv = project_root / args.stabletoolbench_csv
    meta_rows = read_csv(meta_csv)
    stb_rows = read_csv(stb_csv)
    meta_validation = read_json(project_root / "outputs/external_qa_v0_1/metatool/metatool_csv_validation_report_manual_reviewed_by_gpt55pro.json")
    stb_validation = read_json(project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_csv_validation_report_manual_reviewed_by_gpt55pro.json")
    meta_summary = read_json(project_root / "outputs/external_qa_v0_1/metatool/metatool_external_qa_summary_manual_reviewed_by_gpt55pro.json")
    stb_summary = read_json(project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_external_qa_summary_manual_reviewed_by_gpt55pro.json")

    combined_rows = meta_rows + stb_rows
    summary: dict[str, Any] = {
        "generated_time": now(),
        "metatool": {
            **meta_summary,
            "input_csv": str(meta_csv),
            "validation_is_fatal": meta_validation.get("is_fatal"),
            "remove_or_critical_examples": sample_rows(
                meta_rows, lambda r: r.get("qa_final_decision") == "remove" or r.get("qa_severity") == "critical", 10
            ),
        },
        "stabletoolbench": {
            **stb_summary,
            "input_csv": str(stb_csv),
            "validation_is_fatal": stb_validation.get("is_fatal"),
            "stable_group_distribution": dist(stb_rows, "stable_group"),
            "remove_or_critical_examples": sample_rows(
                stb_rows, lambda r: r.get("qa_final_decision") == "remove" or r.get("qa_severity") == "critical", 15
            ),
        },
        "combined": {
            "rows": len(combined_rows),
            "qa_final_decision_distribution": dict(Counter(r.get("qa_final_decision", "") for r in combined_rows)),
            "qa_severity_distribution": dict(Counter(r.get("qa_severity", "") for r in combined_rows)),
            "critical_count": sum(1 for r in combined_rows if r.get("qa_severity") == "critical"),
        },
        "go_no_go": {
            "can_accept_metatool_external_qa_as_pass": False,
            "can_accept_stabletoolbench_external_qa_as_pass": False,
            "can_merge_external_sources_now": False,
            "can_generate_full_six_task_benchmark_now": False,
            "can_generate_final_clean_dataset_now": False,
            "can_create_split_now": False,
            "can_run_baseline_now": False,
            "can_train_model_now": False,
            "recommended_next_step": "source-specific MetaTool leakage/rewrite policy and StableToolBench filtering/candidate-space/composable policy before any merge",
        },
    }

    combined_json = project_root / "outputs/external_qa_v0_1/external_qa_manual_reviewed_by_gpt55pro_combined_summary_v0_1.json"
    analysis_md = project_root / "docs/phase1/external_qa_manual_reviewed_by_gpt55pro_analysis_v0_1.md"
    go_no_go_md = project_root / "docs/phase1/external_qa_manual_reviewed_by_gpt55pro_go_no_go_v0_1.md"
    write_json(combined_json, summary)
    write_md_analysis(analysis_md, summary)
    write_go_no_go(go_no_go_md, summary)

    archive_root = archive(
        project_root,
        [
            meta_csv,
            stb_csv,
            project_root / "outputs/external_qa_v0_1/metatool/metatool_csv_validation_report_manual_reviewed_by_gpt55pro.json",
            project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_csv_validation_report_manual_reviewed_by_gpt55pro.json",
            project_root / "outputs/external_qa_v0_1/metatool/metatool_external_qa_summary_manual_reviewed_by_gpt55pro.json",
            project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_external_qa_summary_manual_reviewed_by_gpt55pro.json",
            combined_json,
            analysis_md,
            go_no_go_md,
            project_root / "docs/phase1/external_qa_csv_validation_report_manual_reviewed_by_gpt55pro_v0_1.md",
            project_root / "docs/phase1/external_qa_csv_summary_report_manual_reviewed_by_gpt55pro_v0_1.md",
            project_root / "scripts/validation/analyze_external_qa_manual_reviewed_by_gpt55pro_v0_1.py",
        ],
    )
    summary["archive_dir"] = str(archive_root)
    write_json(combined_json, summary)
    print(json.dumps({
        "metatool_decision_distribution": summary["metatool"]["qa_final_decision_distribution"],
        "stabletoolbench_decision_distribution": summary["stabletoolbench"]["qa_final_decision_distribution"],
        "combined": summary["combined"],
        "go_no_go": summary["go_no_go"],
        "archive_dir": str(archive_root),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
