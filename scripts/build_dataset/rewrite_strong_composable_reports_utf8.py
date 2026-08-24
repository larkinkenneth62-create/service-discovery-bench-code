#!/usr/bin/env python
"""Rewrite strong-composable Markdown reports with reliable UTF-8 text.

The Windows shell used in this environment can corrupt direct CJK literals in
generated source files. This helper keeps the source ASCII-only and creates the
required Chinese section headings through Unicode escape decoding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(".")
DRYRUN_DIR = ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming_dryrun_audit_v3_2_strong_composable_search"
FULL_G3_DIR = ROOT / "outputs" / "toolbench_full_g3_strong_composable_search_v0_1"
DOC_DIR = ROOT / "docs" / "phase1"


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def h(text: str) -> str:
    return f"## {u(text)}"


def bullet(items: Iterable[Any]) -> str:
    values = list(items)
    if not values:
        return "- none"
    return "\n".join(f"- `{item}`" for item in values)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rewrite_filled_analysis() -> None:
    summary = load_json(DRYRUN_DIR / "strong_composable_candidate_search_filled_summary.json")
    invalid_counts = {key: len(value) for key, value in summary["invalid_values"].items()}
    conclusion = u(
        r"\u5f53\u524d dry-run audit \u6837\u672c\u4e0d\u8db3\u4ee5\u6784\u5efa composable "
        r"\u4e3b\u4efb\u52a1\uff0c\u9700\u8981\u4ece\u539f\u59cb ToolBench full G3 \u6269\u5927\u641c\u7d22\u3002"
    )
    lines = [
        "# Strong Composable Candidate Search Filled Analysis",
        "",
        h(r"\u3010\u672c\u6b21\u505a\u4e86\u4ec0\u4e48\u3011"),
        "Read the 16-row filled dry-run strong-composable confirmation table and summarized manual labels.",
        "",
        h(r"\u301016 \u6761\u662f\u5426\u5168\u90e8\u586b\u5199\u5b8c\u6574\u3011"),
        f"- row_count: {summary['row_count']}",
        f"- missing_by_col: `{json.dumps(summary['completion']['missing_by_col'], ensure_ascii=False)}`",
        f"- all_required_filled: `{summary['completion']['all_required_filled']}`",
        "",
        h(r"\u3010\u662f\u5426\u5b58\u5728\u975e\u6cd5\u53d6\u503c\u3011"),
        f"- invalid_values: `{json.dumps(invalid_counts, ensure_ascii=False)}`",
        "",
        h(r"\u3010strong_composable_final_label \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['strong_composable_final_label_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010semantic_alignment_manual_check \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['semantic_alignment_manual_check_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010leakage_manual_check \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['leakage_manual_check_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010\u6700\u7ec8\u662f\u5426\u786e\u8ba4 strong_composable\u3011"),
        f"- confirmed_strong_composable_count: {summary['confirmed_strong_composable_count']}",
        f"- {conclusion}",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48 dry-run audit \u4e0d\u8db3\u4ee5\u652f\u6491 composable \u4e3b\u4efb\u52a1\u3011"),
        "All 16 filled candidates were labeled either ordinary_multi or not_eligible. This means the dry-run audit can validate rules, but cannot support a composable main task.",
        "",
        h(r"\u3010\u54ea\u4e9b\u6837\u672c\u662f ordinary_multi\u3011"),
        bullet(summary["ordinary_multi_task_ids"]),
        "",
        h(r"\u3010\u54ea\u4e9b\u6837\u672c\u662f not_eligible\u3011"),
        bullet(summary["not_eligible_task_ids"]),
        "",
        h(r"\u3010\u54ea\u4e9b\u6837\u672c\u5b58\u5728 leakage \u6216 semantic mismatch\u3011"),
        bullet(summary["leakage_or_semantic_issue_task_ids"]),
        "",
        h(r"\u3010\u662f\u5426\u9700\u8981\u6269\u5927 full G3 \u641c\u7d22\u3011"),
        conclusion,
    ]
    write(DOC_DIR / "strong_composable_candidate_search_filled_analysis_report.md", lines)


def rewrite_full_g3_report() -> None:
    summary = load_json(FULL_G3_DIR / "full_g3_strong_composable_search_summary.json")
    core_files = [
        "external_sources/ToolBench/data/instruction/G3_query.json",
        "external_sources/ToolBench/data/test_instruction/G3_instruction.json",
        "external_sources/ToolBench/data/test_query_ids/G3_instruction.json",
        "external_sources/ToolBench/data/retrieval_test_query_ids/G3_test_query_ids.json",
        "external_sources/ToolBench/data/answer/G3_answer/*.json",
        "external_sources/ToolBench/data/toolllama_G123_dfs_eval.json",
        "external_sources/ToolBench/data/toolllama_G123_dfs_train.json",
    ]
    conclusion = u(
        r"\u4e0d\u5efa\u8bae\u8dd1\u5168\u91cf\u6e05\u6d17\u3002\u5f53\u524d\u53ea\u662f\u5019\u9009\u641c\u7d22\uff0c\u8fd8\u9700\u8981\u5148\u4eba\u5de5\u786e\u8ba4 top20/top50\u3002"
    )
    lines = [
        "# Full G3 Strong Composable Candidate Search Report",
        "",
        h(r"\u3010\u672c\u6b21\u505a\u4e86\u4ec0\u4e48\u3011"),
        "Searched ToolBench full G3 for possible strong-composable candidates. No full cleaning, no baseline, and no training were run.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48\u4ece dry-run audit \u6269\u5927\u5230 full G3\u3011"),
        "The 16-row dry-run confirmation found 0 strong_composable positives, so the dry-run sample is insufficient for a composable main task.",
        "",
        h(r"\u3010\u627e\u5230\u4e86\u54ea\u4e9b\u539f\u59cb G3 \u6587\u4ef6\u3011"),
        f"- Core input: `{summary['input_g3_file']}`",
        "- Important G3-related files/directories:",
        bullet(core_files),
        f"- Broad related-file scan count: {len(summary['discovered_g3_related_files'])}",
        "",
        h(r"\u3010\u8bfb\u53d6\u4e86\u591a\u5c11\u6761\u539f\u59cb\u8bb0\u5f55\u3011"),
        f"- records_read: {summary['records_read']}",
        f"- non_none_signal_count: {summary['non_none_signal_count']}",
        "",
        h(r"\u3010dependency_signal_strength \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['dependency_signal_strength_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010ordinary_multi_risk \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['ordinary_multi_risk_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010leakage_risk \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['leakage_risk_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010semantic_alignment_risk \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['semantic_alignment_risk_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010\u8f93\u51fa\u4e86\u591a\u5c11 top100 \u5019\u9009\u3011"),
        f"- top100_written: {summary['top100_written']}",
        f"- file: `{summary['output_files']['top100']}`",
        "",
        h(r"\u3010high confidence \u5019\u9009\u6709\u591a\u5c11\u3011"),
        f"- high_confidence_count_total: {summary['high_confidence_count_total']}",
        f"- file: `{summary['output_files']['high_confidence']}`",
        "",
        h(r"\u3010medium \u5019\u9009\u6709\u591a\u5c11\u3011"),
        f"- medium_candidate_count_total: {summary['medium_candidate_count_total']}",
        f"- file: `{summary['output_files']['medium_candidates']}`",
        "",
        h(r"\u3010ordinary_multi \u98ce\u9669\u6837\u672c\u6709\u591a\u5c11\u3011"),
        f"- ordinary_multi_risk_high_count_total: {summary['ordinary_multi_risk_high_count_total']}",
        f"- file: `{summary['output_files']['ordinary_multi_risk_examples']}`",
        "",
        h(r"\u3010\u662f\u5426\u8db3\u4ee5\u8fdb\u5165\u4eba\u5de5\u786e\u8ba4\u3011"),
        "Yes. The output is sufficient for manual confirmation, especially top20/top50 review.",
        "",
        h(r"\u3010\u662f\u5426\u4ecd\u7136\u4e0d\u5efa\u8bae\u8dd1\u5168\u91cf\u3011"),
        conclusion,
    ]
    write(DOC_DIR / "full_g3_strong_composable_search_report.md", lines)


def rewrite_guideline() -> None:
    lines = [
        "# Full G3 Strong Composable Human Confirm Guideline",
        "",
        h(r"\u3010\u76ee\u6807\u3011"),
        "Confirm whether each candidate has a real cross-service dependency. The key test is whether a later step consumes an earlier service/API result.",
        "",
        "## strong_composable",
        "Use this label only when the output of one service/API changes the input, choice, filter, judgment, or recommendation of a later service/API.",
        "`strong_composable_final_label=strong_composable; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; strong_composable_decision_reason=later step depends on earlier returned result.`",
        "",
        "## ordinary_multi",
        "Use this label when several services are requested in parallel, with no result dependency between them.",
        "`strong_composable_final_label=ordinary_multi; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; strong_composable_decision_reason=parallel sub-tasks, no cross-service dependency.`",
        "",
        "## ambiguous",
        "Use this label when the query suggests order but does not prove that a later step uses an earlier result.",
        "`strong_composable_final_label=ambiguous; semantic_alignment_manual_check=semantic_alignment_uncertain; leakage_manual_check=leak_uncertain; strong_composable_decision_reason=dependency is possible but not explicit.`",
        "",
        "## not_eligible",
        "Use this label when there is semantic mismatch, missing candidate/gold information, or blocking API leak.",
        "`strong_composable_final_label=not_eligible; semantic_alignment_manual_check=semantic_mismatch_uncertain; leakage_manual_check=api_leak_blocking; strong_composable_decision_reason=not usable for composable main task.`",
        "",
        "## semantic_alignment_manual_check",
        "- `semantic_alignment_ok`: query and gold service/API are semantically aligned.",
        "- `semantic_alignment_uncertain`: alignment is partial or unclear.",
        "- `semantic_mismatch_uncertain`: query and gold are likely mismatched.",
        "",
        "## leakage_manual_check",
        "- `no_blocking_leak`: no blocking leak.",
        "- `api_leak_blocking`: query directly reveals gold API name.",
        "- `service_leak_only`: query reveals gold service name.",
        "- `leak_uncertain`: unclear leak status.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48\u4e0d\u80fd\u53ea\u770b dependency keywords\u3011"),
        "`then`, `after`, `recommend`, and `also` are recall signals only. They often describe ordinary multi-task requests.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48\u201c\u540e\u4e00\u6b65\u5403\u524d\u4e00\u6b65\u7ed3\u679c\u201d\u91cd\u8981\u3011"),
        "Composable discovery should evaluate dependency chains, not just selecting several unrelated services.",
    ]
    write(DOC_DIR / "full_g3_strong_composable_human_confirm_guideline.md", lines)


def rewrite_next_step() -> None:
    summary = load_json(FULL_G3_DIR / "full_g3_strong_composable_search_summary.json")
    lines = [
        "# Full G3 Strong Composable Next Step",
        "",
        "## 1. " + u(r"\u73b0\u5728\u662f\u5426\u5efa\u8bae\u8dd1\u5168\u91cf\u6e05\u6d17\uff1f"),
        "No. This is still a candidate-search stage, not final cleaning.",
        "",
        "## 2. " + u(r"full G3 \u662f\u5426\u627e\u5230\u8db3\u591f strong signal\uff1f"),
        f"- strong signal: {summary['dependency_signal_strength_distribution'].get('strong', 0)}",
        f"- high confidence candidates: {summary['high_confidence_count_total']}",
        f"- medium candidates: {summary['medium_candidate_count_total']}",
        "These are enough for manual confirmation, but not enough to declare final positives.",
        "",
        "## 3. " + u(r"\u662f\u5426\u5e94\u5148\u4eba\u5de5\u786e\u8ba4 top20/top50\uff1f"),
        "Yes. Start with top20; expand to top50 if the hit rate is promising.",
        "",
        "## 4. " + u(r"\u5982\u679c high confidence \u4ecd\u5c11\uff0c\u662f\u5426\u5e94\u6682\u7f13 composable \u4e3b\u4efb\u52a1\uff1f"),
        "Yes. If human-confirmed strong_composable positives remain rare, keep composable as a later extension.",
        "",
        "## 5. " + u(r"\u662f\u5426\u53ef\u5148\u6784\u5efa single/multi/API recommendation \u56db\u7c7b\u4e3b\u4efb\u52a1\uff1f"),
        "Yes. The current evidence supports stabilizing single_service, single_api, multi_service, and multi_api tasks first.",
        "",
        "## 6. " + u(r"\u4e0b\u4e00\u6b65\u5efa\u8bae"),
        "Manually confirm the first 20 rows of `full_g3_strong_composable_candidates_top100.csv`, then summarize strong_composable / ordinary_multi / ambiguous / not_eligible counts.",
    ]
    write(DOC_DIR / "full_g3_strong_composable_next_step.md", lines)


def main() -> None:
    rewrite_filled_analysis()
    rewrite_full_g3_report()
    rewrite_guideline()
    rewrite_next_step()
    print("rewritten UTF-8 markdown reports")


if __name__ == "__main__":
    main()
