#!/usr/bin/env python
"""Generate v3.3 freeze and main-four-task transition documents.

This script only writes planning/report artifacts from existing files and the
already-generated dry-run summary. It does not run full cleaning, baseline,
model training, top200 confirmation, or full-G3 search.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


ROOT = Path(".")
DOC_DIR = ROOT / "docs" / "phase1"
DRYRUN_DIR = ROOT / "outputs" / "main_four_tasks_dryrun_v0_2"
DRYRUN_SUMMARY_PATH = DRYRUN_DIR / "main_four_tasks_dryrun_summary.json"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-24_main_four_tasks_transition_v0_2"

REQUIRED_INPUTS = [
    DOC_DIR / "manual_audit_rule_v3_3_draft.md",
    DOC_DIR / "composable_task_phase_decision_after_top100.md",
    DOC_DIR / "full_g3_strong_composable_after_top100_next_step.md",
    DOC_DIR / "full_g3_strong_composable_top100_filled_analysis_report.md",
    DOC_DIR / "full_g3_strong_composable_stage_comparison_report.md",
    ROOT
    / "outputs"
    / "toolbench_full_g3_strong_composable_search_v0_1"
    / "full_g3_strong_composable_top100_filled.csv",
    DRYRUN_SUMMARY_PATH,
]

OUTPUT_DOCS = {
    "rule_v3_3": DOC_DIR / "manual_audit_rule_v3_3.md",
    "seed_decision": DOC_DIR / "composable_seed_set_decision_summary.md",
    "inventory": DOC_DIR / "main_task_data_inventory_report.md",
    "viability": DOC_DIR / "main_four_tasks_source_viability_report.md",
    "schema": DOC_DIR / "service_discovery_bench_v0_2_schema_draft.md",
    "plan": DOC_DIR / "main_four_tasks_construction_plan_v0_2.md",
    "advisor": DOC_DIR / "phase1_main_task_transition_summary_for_advisor.md",
}


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_info(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def list_matching(root: Path, suffixes: Iterable[str], name_contains: Iterable[str], limit: int = 80) -> List[Dict[str, Any]]:
    if not root.exists():
        return []
    suffixes = tuple(suffix.lower() for suffix in suffixes)
    needles = tuple(item.lower() for item in name_contains)
    results: List[Dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        name = path.name.lower()
        if needles and not any(needle in name for needle in needles):
            continue
        results.append(file_info(path))
        if len(results) >= limit:
            break
    return results


def write_rule_v3_3() -> None:
    lines = [
        "# Manual Audit Rule v3.3",
        "",
        f"Frozen at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 1. strong composable 正例必要条件",
        "- `semantic_alignment_ok`.",
        "- `no_blocking_leak`.",
        "- A clear dependency chain is present.",
        "- A later step consumes an earlier output, entity, id, result, filter, or decision signal.",
        "- Keyword evidence such as `based on` is only a recall signal; it is not sufficient by itself.",
        "",
        "## 2. ordinary_multi 负例条件",
        "- Multiple subtasks are merely parallel.",
        "- Subtasks share the same location, time, or topic but do not consume each other's outputs.",
        "- `then`, `after`, `recommend`, `also`, and similar words are not enough to mark composable.",
        "- ordinary_multi should be routed to `multi_service_discovery_candidate` or `multi_api_recommendation_candidate`, depending on the evaluation level.",
        "",
        "## 3. not_eligible 条件",
        "- `semantic_mismatch_uncertain`.",
        "- Query-gold mismatch.",
        "- Gold only covers part of the query.",
        "- Blocking API leak.",
        "- Candidate/gold missing or clearly unreasonable.",
        "",
        "## 4. composable 清洗优先级",
        "1. strong API leak -> remove.",
        "2. semantic mismatch -> uncertain/not_eligible.",
        "3. leakage uncertain -> uncertain.",
        "4. ordinary_multi -> multi_service_discovery_candidate.",
        "5. strong_composable -> composable_service_discovery_candidate.",
        "",
        "## 5. 后续正式脚本定位",
        "- The formal script should first perform candidate screening and evidence export.",
        "- It should not automatically assign final labels.",
        "- Final labels still need manual confirmation, or rules that have been separately validated.",
        "",
        "## 6. Scope Guard",
        "- No top200 continuation by default.",
        "- No raw G3 full composable cleaning.",
        "- No baseline or model training at this stage.",
    ]
    write_text(OUTPUT_DOCS["rule_v3_3"], lines)


def write_seed_decision() -> None:
    lines = [
        "# Composable Seed Set Decision Summary",
        "",
        "## 【为什么不继续 top200】",
        "Top100 的 strong_composable 命中率已经从 top20 的 35% 下降到 top100 的 20%，rows 51–100 只有 14%。继续 top200 的边际收益不确定，人工成本较高，因此不默认继续。",
        "",
        "## 【为什么不直接使用 raw G3】",
        "raw G3 中包含大量 ordinary_multi 和 not_eligible 样本。仅凭 G3 group 不能证明跨服务依赖，也不能保证 query-gold 语义对齐。",
        "",
        "## 【为什么 composable 保留为 screened seed set】",
        "Top100 人审证明 full G3 中确实存在 strong composable 正例，但比例较低。最稳妥的方式是保留为经过 dependency screening、leak gate、semantic gate 和人工确认的 seed set。",
        "",
        "## 【如果导师坚持六任务完整，应该怎么处理】",
        "可以继续扩到 top200 或更大范围，但必须把 composable 标注为 screened-and-human-confirmed task，不允许直接把 raw G3 全量清洗成 composable。",
        "",
        "## 【如果导师接受稳妥路线，下一步应该推进哪些主任务】",
        "优先推进 `single_service_discovery`、`single_api_recommendation`、`multi_service_discovery`、`multi_api_recommendation` 四类更稳定任务。",
        "",
        "- 不默认继续 top200。",
        "- 不跑 raw G3 全量 composable 清洗。",
        "- composable 暂时作为 screened composable seed set。",
        "- 四类主任务优先推进。",
    ]
    write_text(OUTPUT_DOCS["seed_decision"], lines)


def inventory_report() -> Dict[str, Any]:
    scan_dirs = [
        ROOT / "outputs" / "toolbench_full_raw_v0_1",
        ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming_dryrun",
        ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming_dryrun_audit_v3",
        ROOT / "outputs" / "toolbench_full_g3_strong_composable_search_v0_1",
        ROOT / "external_sources" / "ToolBench",
        ROOT / "external_sources" / "MetaTool",
        ROOT / "external_sources" / "StableToolBench",
        ROOT / "external_sources" / "ShortcutsBench",
    ]
    candidate_csvs = list_matching(ROOT / "outputs", [".csv"], ["candidate_level", "service_candidates"], 120)
    task_csvs = list_matching(ROOT / "outputs", [".csv"], ["task_level"], 120)
    toolbench_raw = [
        file_info(ROOT / "external_sources" / "ToolBench" / "data" / "instruction" / name)
        for name in ["G1_query.json", "G2_query.json", "G3_query.json"]
    ]
    toolbench_raw.extend(
        [
            file_info(ROOT / "external_sources" / "ToolBench" / "data" / "test_instruction" / "G3_instruction.json"),
            file_info(ROOT / "external_sources" / "ToolBench" / "data" / "toolllama_G123_dfs_eval.json"),
            file_info(ROOT / "external_sources" / "ToolBench" / "data" / "toolllama_G123_dfs_train.json"),
        ]
    )
    other_sources = {
        "MetaTool": list_matching(ROOT / "external_sources" / "MetaTool", [".json", ".csv", ".jsonl"], [""], 30),
        "StableToolBench": list_matching(ROOT / "external_sources" / "StableToolBench", [".json", ".csv", ".jsonl"], [""], 30),
        "ShortcutsBench": list_matching(ROOT / "external_sources" / "ShortcutsBench", [".json", ".csv", ".jsonl"], [""], 30),
    }
    summary = {
        "scanned_dirs": [file_info(path) for path in scan_dirs],
        "candidate_csvs": candidate_csvs,
        "task_level_csvs": task_csvs,
        "toolbench_raw": toolbench_raw,
        "other_sources": other_sources,
    }
    lines = [
        "# Main Task Data Inventory Report",
        "",
        "## 【扫描了哪些目录】",
    ]
    for info in summary["scanned_dirs"]:
        lines.append(f"- `{info['path']}` exists={info['exists']}")
    lines.extend(["", "## 【找到哪些 candidate-level CSV】"])
    lines.extend([f"- `{item['path']}` ({item['size_bytes']} bytes)" for item in candidate_csvs] or ["- none"])
    lines.extend(["", "## 【找到哪些 task-level CSV】"])
    lines.extend([f"- `{item['path']}` ({item['size_bytes']} bytes)" for item in task_csvs] or ["- none"])
    lines.extend(["", "## 【找到哪些 ToolBench 原始文件】"])
    lines.extend([f"- `{item['path']}` exists={item['exists']} size={item.get('size_bytes', 'NA')}" for item in toolbench_raw])
    lines.extend(["", "## 【找到哪些 MetaTool / StableToolBench / ShortcutsBench 文件】"])
    for name, items in other_sources.items():
        lines.append(f"### {name}")
        lines.extend([f"- `{item['path']}`" for item in items[:15]] or ["- none found"])
    lines.extend(
        [
            "",
            "## 【哪些文件适合构建四类主任务】",
            "- `outputs/toolbench_full_raw_v0_1_streaming_dryrun/G*_task_level.csv`: 当前 dry-run 主输入。",
            "- `outputs/toolbench_full_raw_v0_1_streaming_dryrun/G*_candidate_level.csv`: 后续 candidate-level 对齐和审计输入。",
            "- `external_sources/ToolBench/data/instruction/G1_query.json` / `G2_query.json`: 后续正式清洗脚本的候选来源。",
            "",
            "## 【哪些文件只是 audit / 人审 / composable seed，不适合作为主数据源】",
            "- `outputs/toolbench_full_raw_v0_1_streaming_dryrun_audit_v3/*`: 人审辅助样本。",
            "- `outputs/toolbench_full_g3_strong_composable_search_v0_1/*top*`: composable seed 验证材料。",
            "",
            "## 【是否存在缺失文件】",
            "当前关键 dry-run 输入存在；single_service/single_api 缺口是数据适配问题，不是文件缺失。",
            "",
            "## 【下一步建议】",
            "先用 dry-run 输出做人工抽查；如果 single_service/single_api 仍不足，应评估 MetaTool / ShortcutsBench 是否能补强。",
        ]
    )
    write_text(OUTPUT_DOCS["inventory"], lines)
    return summary


def write_viability(dryrun: Dict[str, Any]) -> None:
    tasks = dryrun["tasks"]
    lines = [
        "# Main Four Tasks Source Viability Report",
        "",
        "## single_service_discovery",
        f"- available source group/dataset: G2 dry-run only, current output rows={tasks['single_service_discovery']['rows_written']}.",
        "- query: ToolBench natural-language query.",
        "- candidate: service.",
        "- gold: service.",
        "- candidate set too small risk: high for G1 because candidate_service_count=1 for all first 100 G1 tasks.",
        "- leak risk: service/API leaks must be removed for service discovery.",
        "- semantic mismatch risk: unverified in dry-run.",
        "- MetaTool / ShortcutsBench: likely needed if G2 single-service cases remain insufficient.",
        "- v0.1 main benchmark: not ready from current dry-run; only dry-run planning.",
        "",
        "## single_api_recommendation",
        f"- available source group/dataset: G1/G2 candidates, current output rows={tasks['single_api_recommendation']['rows_written']}.",
        "- query: ToolBench natural-language query.",
        "- candidate: API.",
        "- gold: API.",
        "- candidate set too small risk: current dry-run has no exactly-one-gold-API tasks after API-leak filtering.",
        "- leak risk: API leak is common and must be removed.",
        "- semantic mismatch risk: unverified in dry-run.",
        "- MetaTool / ShortcutsBench: likely needed, or inspect full G1 beyond first 100 before formal cleaning.",
        "- v0.1 main benchmark: dry-run only.",
        "",
        "## multi_service_discovery",
        f"- available source group/dataset: G2, current output rows={tasks['multi_service_discovery']['rows_written']}.",
        "- query: ToolBench natural-language query.",
        "- candidate: service.",
        "- gold: multiple services.",
        "- candidate set too small risk: moderate; current G2 dry-run yields usable rows.",
        "- leak risk: no obvious leak rows selected.",
        "- semantic mismatch risk: still needs manual spot-check.",
        "- MetaTool / ShortcutsBench: optional supplement, not immediately required.",
        "- v0.1 main benchmark: plausible after manual spot-check and formal cleaning script.",
        "",
        "## multi_api_recommendation",
        f"- available source group/dataset: G2 preferred, G1 supplement, current output rows={tasks['multi_api_recommendation']['rows_written']}.",
        "- query: ToolBench natural-language query.",
        "- candidate: API.",
        "- gold: multiple APIs.",
        "- candidate set too small risk: low in dry-run.",
        "- leak risk: API leak removed; service_leak_only retained as audit bucket.",
        "- semantic mismatch risk: still needs manual spot-check.",
        "- MetaTool / ShortcutsBench: optional supplement.",
        "- v0.1 main benchmark: plausible after manual spot-check and formal cleaning script.",
        "",
        "## Overall",
        "Composable is not included in the four stable main tasks for v0.2; it remains a screened seed set.",
    ]
    write_text(OUTPUT_DOCS["viability"], lines)


def write_schema() -> None:
    lines = [
        "# Service Discovery Bench v0.2 Schema Draft",
        "",
        "## A. task-level schema",
        "- `task_id`: stable unique task id.",
        "- `task_type`: one of the four main task types; composable seed set stored separately.",
        "- `source_dataset`: e.g., ToolBench.",
        "- `source_group`: e.g., G1/G2.",
        "- `query_text`: natural-language user request.",
        "- `candidate_services_json`: service candidate list.",
        "- `candidate_apis_json`: API candidate list.",
        "- `gold_services_json`: gold service list.",
        "- `gold_apis_json`: gold API list.",
        "- `leak_status`: no_obvious_leak / api_leak / service_leak_only / leak_uncertain.",
        "- `semantic_alignment_status`: unverified_dryrun / semantic_alignment_ok / semantic_mismatch_uncertain / uncertain.",
        "- `cleaning_status`: dryrun_candidate / clean_candidate / service_leak_only / remove_api_leak / uncertain.",
        "- `task_eligibility`: service-level or API-level eligibility bucket.",
        "- `task_bucket`: final task bucket used for benchmark routing.",
        "- `split`: empty/dryrun/train/dev/test. No split in current dry-run.",
        "- `metadata_json`: counts, source query id, raw task type, audit notes.",
        "",
        "## B. candidate-level schema",
        "- `task_id`",
        "- `task_type`",
        "- `query_text`",
        "- `candidate_rank`",
        "- `candidate_service_name`",
        "- `candidate_service_description`",
        "- `candidate_api_name`",
        "- `candidate_api_description`",
        "- `is_gold_service`",
        "- `is_gold_api`",
        "- `source_dataset`",
        "- `source_group`",
        "- `leak_status`",
        "- `semantic_alignment_status`",
        "- `cleaning_status`",
        "- `task_bucket`",
        "",
        "## service-level task 和 API-level task 的区别",
        "service-level 评估模型能否选中正确服务；API-level 评估模型能否选中具体接口。",
        "",
        "## single 和 multi 的区别",
        "single 表示 gold service/API 数量为 1；multi 表示 gold service/API 数量大于 1。",
        "",
        "## composable seed set 是否单独存放",
        "是。composable 暂时作为 screened seed set 单独存放，不混入四类稳定主任务。",
        "",
        "## 为什么 task-level 和 candidate-level 都要保留",
        "task-level 便于输入模型和整体评测；candidate-level 便于排序、分类指标、错误定位和候选级审计。",
        "",
        "## 字段用途",
        "- 模型输入：query_text, candidate_services_json, candidate_apis_json。",
        "- 评测：gold_services_json, gold_apis_json, is_gold_service, is_gold_api。",
        "- 审计：leak_status, semantic_alignment_status, cleaning_status, metadata_json。",
    ]
    write_text(OUTPUT_DOCS["schema"], lines)


def write_plan(dryrun: Dict[str, Any]) -> None:
    lines = [
        "# Main Four Tasks Construction Plan v0.2",
        "",
        "## 【总体目标】",
        "构建四类稳定主任务的 dry-run schema、数据源判断和后续正式清洗计划，暂不处理 composable 主任务。",
        "",
        "## 【四类任务定义】",
        "- single_service_discovery: 一个 gold service。",
        "- single_api_recommendation: 一个 gold API。",
        "- multi_service_discovery: 多个 gold services。",
        "- multi_api_recommendation: 多个 gold APIs。",
        "",
        "## 【每类任务来源】",
        "- single_service_discovery: 当前 G2 dry-run 可探索，但数量不足；G1 不强行使用。",
        "- single_api_recommendation: 当前 dry-run 暂无可靠样本，需要 full G1 检查或外部数据补强。",
        "- multi_service_discovery: G2 优先。",
        "- multi_api_recommendation: G2 优先，G1 可补充 API-level cases。",
        "",
        "## 【清洗规则】",
        "先 dry-run 抽样，再人工 spot-check，最后写正式清洗脚本。",
        "",
        "## 【泄漏规则】",
        "API leak 删除；service leak 不进入 service discovery clean set，但可作为 API-level audit bucket。",
        "",
        "## 【semantic alignment gate】",
        "query 和 gold 不匹配时进入 uncertain/not_eligible，不进入 clean-ready 主数据。",
        "",
        "## 【候选集构造规则】",
        "候选集必须大于 gold 集合；service-level 用 candidate_services_json，API-level 用 candidate_apis_json。",
        "",
        "## 【gold 构造规则】",
        "gold_services_json 和 gold_apis_json 必须来自原始 relevant APIs，不用统计数字伪造。",
        "",
        "## 【去重规则】",
        "按 task_signature/query_signature 和 normalized gold set 去重；保留 source metadata。",
        "",
        "## 【train/dev/test split 计划】",
        "本阶段不做 split。正式清洗后再按 task_id/source_group 去重后划分。",
        "",
        "## 【评测指标计划】",
        "Service-level/API-level 均可用 Recall@K、MRR、nDCG、Exact Match；multi 任务补充 set-F1。",
        "",
        "## 【baseline 计划，但本阶段不运行】",
        "后续可用 BM25、embedding retrieval、LLM rerank 作为 baseline；本阶段不运行。",
        "",
        "## 【dry-run 输出计划】",
        f"当前 dry-run summary: `{DRYRUN_SUMMARY_PATH}`。",
        "",
        "## 【风险和待导师确认问题】",
        "- single_service 和 single_api 当前来源不足，是否允许引入 MetaTool/ShortcutsBench？",
        "- service_leak_only 是否可作为 API-level candidate 保留？",
        "- composable 是否仅作为 seed set，还是导师要求六任务完整？",
    ]
    write_text(OUTPUT_DOCS["plan"], lines)


def write_advisor_summary(dryrun: Dict[str, Any]) -> None:
    text = (
        "本阶段已完成 composable 方向的阶段性验证，并将人工审查规则冻结为 v3.3。"
        "在 full G3 strong composable top100 人工确认中，strong_composable 为 20 条，ordinary_multi 为 57 条，"
        "not_eligible 为 23 条，命中率为 20%。这说明 full G3 中确实存在强组合正例，"
        "但 raw G3 不能直接作为 composable benchmark，因为多数样本只是多任务并列，或存在 query 与 gold service/API 不匹配。"
        "因此当前建议把 composable 暂时保留为 screened composable seed set，而不是直接进入主 benchmark 全量构建。"
        "\n\n"
        "接下来主线转向四类更稳定任务：single_service_discovery、single_api_recommendation、multi_service_discovery、multi_api_recommendation。"
        "基于现有 ToolBench streaming dry-run 数据的小规模抽样显示，"
        f"multi_service_discovery 当前可从 G2 输出 {dryrun['tasks']['multi_service_discovery']['rows_written']} 条 dry-run 样本，"
        f"multi_api_recommendation 可输出 {dryrun['tasks']['multi_api_recommendation']['rows_written']} 条；"
        "但 single_service_discovery 和 single_api_recommendation 当前不足。G1 的候选服务数通常只有 1，"
        "不应强行作为 single_service_discovery；G1/G2 中 API leak 较多，single API 场景需要继续检查 full G1，或考虑 MetaTool、ShortcutsBench 补强。"
        "\n\n"
        "建议下一步先人工抽查 dry-run 四类任务样本，确认 candidate/gold 构造、leak 标记和 semantic alignment 风险是否可控；"
        "导师确认数据源策略后，再写正式清洗脚本。当前没有 baseline、训练、full cleaning，也没有继续 top200。"
        "需确认：composable 是否接受作为 seed set；single_service/single_api 是否允许外部数据补强；"
        "service_leak_only 是否可在 API-level recommendation 中保留为审计桶。"
    )
    write_text(OUTPUT_DOCS["advisor"], [text])


def archive(paths: Sequence[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        target = ARCHIVE_DIR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    write_text(
        ARCHIVE_DIR / "ARCHIVE_MANIFEST.md",
        [
            "# Run Archive: 2026-06-24 main four tasks transition v0.2",
            "",
            "Relative project paths are preserved.",
            "",
            "This archive includes v3.3 frozen rules, composable seed decision, data inventory, source viability report, v0.2 schema draft, construction plan, dry-run script, dry-run outputs, dry-run report, and advisor summary.",
            "",
            "No full cleaning, baseline, model training, top200 continuation, or full-G3 re-search was run.",
        ],
    )


def main() -> None:
    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing:
        print(json.dumps({"missing_required_inputs": missing}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    dryrun = read_json(DRYRUN_SUMMARY_PATH)
    write_rule_v3_3()
    write_seed_decision()
    inventory_report()
    write_viability(dryrun)
    write_schema()
    write_plan(dryrun)
    write_advisor_summary(dryrun)

    archive_paths = [
        OUTPUT_DOCS["rule_v3_3"],
        OUTPUT_DOCS["seed_decision"],
        OUTPUT_DOCS["inventory"],
        OUTPUT_DOCS["viability"],
        OUTPUT_DOCS["schema"],
        OUTPUT_DOCS["plan"],
        ROOT / "scripts" / "build_dataset" / "prepare_main_four_tasks_dryrun_v0_2.py",
        ROOT / "scripts" / "build_dataset" / "generate_main_four_tasks_transition_docs_v0_2.py",
        ROOT / "docs" / "phase1" / "main_four_tasks_dryrun_v0_2_report.md",
        OUTPUT_DOCS["advisor"],
        DRYRUN_SUMMARY_PATH,
        DRYRUN_DIR / "single_service_discovery_task_level.csv",
        DRYRUN_DIR / "single_api_recommendation_task_level.csv",
        DRYRUN_DIR / "multi_service_discovery_task_level.csv",
        DRYRUN_DIR / "multi_api_recommendation_task_level.csv",
    ]
    archive(archive_paths)
    print(
        json.dumps(
            {
                "generated_docs": {key: str(value) for key, value in OUTPUT_DOCS.items()},
                "dryrun_summary": str(DRYRUN_SUMMARY_PATH),
                "archive_dir": str(ARCHIVE_DIR),
                "dryrun_rows": {
                    task: info["rows_written"] for task, info in dryrun["tasks"].items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
