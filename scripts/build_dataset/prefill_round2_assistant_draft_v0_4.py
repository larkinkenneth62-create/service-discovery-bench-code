#!/usr/bin/env python
"""Create assistant-draft review decisions for Round2 manual-check samples.

This script does not create final labels. It applies the manual40 fail-closed
review notes to produce an auxiliary draft that the user can approve or edit.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUT_CSV = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "round2_selected_80.csv"
OUTPUT_DIR = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4"
HTML_PATH = OUTPUT_DIR / "main_four_tasks_round2_review_app_80.html"
HTML_BACKUP_PATH = OUTPUT_DIR / "main_four_tasks_round2_review_app_80.before_assistant_draft.html"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-26_round2_assistant_draft_review_v0_4"
DOC_PATH = ROOT / "docs" / "phase1" / "main_four_tasks_round2_assistant_draft_decision_reasons_v0_4.md"
DRAFT_CSV = OUTPUT_DIR / "round2_assistant_draft_decisions_80.csv"
SUMMARY_JSON = OUTPUT_DIR / "round2_assistant_draft_summary_v0_4.json"


MANUAL_FIELDS = [
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "manual_decision_reason",
]

DETAIL_FIELDS = [
    "assistant_review_source",
    "assistant_review_completed",
    "semantic_alignment_reason",
    "leak_check_reason",
    "candidate_gold_validity_reason",
    "task_type_check_reason",
    "final_decision_reason",
    "assistant_warning_tags",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_json_list(text: str) -> list:
    try:
        data = json.loads(text or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def as_int(row: dict[str, str], key: str) -> int:
    try:
        return int(float(row.get(key, "") or 0))
    except ValueError:
        return 0


def yes(row: dict[str, str], key: str) -> bool:
    return as_int(row, key) == 1


def json_names(text: str, key: str = "service_name") -> list[str]:
    values = []
    for item in parse_json_list(text):
        if isinstance(item, dict):
            value = item.get(key)
            if value:
                values.append(str(value))
        elif isinstance(item, str):
            values.append(item)
    return values


def gold_api_names(row: dict[str, str]) -> list[str]:
    values = []
    for item in parse_json_list(row.get("gold_apis_json", "")):
        if isinstance(item, dict):
            api = item.get("api_name")
            service = item.get("service_name")
            if api and service:
                values.append(f"{service}::{api}")
            elif api:
                values.append(str(api))
        elif isinstance(item, str):
            values.append(item)
    return values


def service_names(row: dict[str, str]) -> tuple[list[str], list[str]]:
    candidate = json_names(row.get("candidate_services_json", ""), "service_name")
    gold = json_names(row.get("gold_services_json", ""), "service_name")
    return candidate, gold


def risk_tags(row: dict[str, str]) -> list[str]:
    tags: list[str] = []
    if yes(row, "high_risk_generic_tracking"):
        tags.append("generic_tracking_specific_service_risk")
    if yes(row, "high_risk_generic_address_or_postal"):
        tags.append("generic_address_or_postal_specific_region_risk")
    if yes(row, "high_risk_service_leak"):
        tags.append("service_leak_risk")
    if yes(row, "high_risk_candidate_count_close"):
        tags.append("candidate_count_too_close_to_gold_risk")
    if yes(row, "high_risk_gold_not_unique_possible"):
        tags.append("gold_not_uniquely_supported_by_query_risk")
    query = (row.get("query_text") or "").lower()
    gold_blob = " ".join(service_names(row)[1] + gold_api_names(row)).lower()
    if "container" in gold_blob and any(word in query for word in ["package", "parcel", "mail", "gift", "delivery"]):
        if "container" not in query:
            tags.append("package_mail_vs_container_tracking_risk")
    if any(name.lower() in gold_blob for name in ["cep brazil", "turkey postal", "pridnestrovie", "fastway australia", "amex australia"]):
        if not any(marker in query for marker in ["brazil", "cep", "correios", "turkey", "turkish", "australia", "fastway", "amex", "pridnestrovie"]):
            tags.append("country_or_carrier_not_evidenced_in_query_risk")
    return tags


def explain_warning_tags(tags: list[str]) -> str:
    if not tags:
        return "未触发已知高风险模式。"
    mapping = {
        "generic_tracking_specific_service_risk": "query 只是泛化追踪包裹/物流，不能自动绑定到某个国家、地区或承运商服务。",
        "generic_address_or_postal_specific_region_risk": "query 只是泛化邮编/地址查询，不能自动等于 CEP Brazil 或其他强地域邮编服务。",
        "service_leak_risk": "query 可能直接出现 gold service 名称，service-level 主任务需要保守处理。",
        "candidate_count_too_close_to_gold_risk": "候选数量与 gold 数量过近，可能缺少真实选择空间。",
        "gold_not_uniquely_supported_by_query_risk": "query 对 gold 的指向不唯一，人工需要确认 gold 是否真的被原文支持。",
        "package_mail_vs_container_tracking_risk": "query 是普通包裹/邮件/礼物配送语境，但 gold 涉及 container tracking，需要警惕语义错配。",
        "country_or_carrier_not_evidenced_in_query_risk": "gold 服务带有国家/地区/承运商绑定，但 query 没有给出足够地域或承运商证据。",
    }
    return "；".join(mapping.get(tag, tag) for tag in tags)


def classify_row(row: dict[str, str]) -> dict[str, str]:
    tags = risk_tags(row)
    task_type = row.get("task_type", "")
    bucket = row.get("mechanical_screening_bucket", "")
    leak_status = row.get("leak_status", "")
    c_service = as_int(row, "candidate_service_count")
    g_service = as_int(row, "gold_service_count")
    c_api = as_int(row, "candidate_api_count")
    g_api = as_int(row, "gold_api_count")
    is_service_task = task_type == "multi_service_discovery"
    is_api_task = task_type == "multi_api_recommendation"

    has_api_leak = leak_status == "api_leak" or yes(row, "query_mentions_any_gold_api")
    has_service_leak = leak_status == "service_leak_only" or yes(row, "query_mentions_any_gold_service") or yes(row, "high_risk_service_leak")
    has_semantic_risk = any(
        tag in tags
        for tag in [
            "generic_tracking_specific_service_risk",
            "generic_address_or_postal_specific_region_risk",
            "gold_not_uniquely_supported_by_query_risk",
            "package_mail_vs_container_tracking_risk",
            "country_or_carrier_not_evidenced_in_query_risk",
        ]
    )

    if has_api_leak:
        leak_check = "api_leak_blocking"
        leak_reason = "query 直接命中 gold API 或原始 leak_status=api_leak。按已确认规则，strong API leak 优先级最高，应从 clean 主数据中移除。"
    elif has_service_leak:
        leak_check = "service_leak_only"
        leak_reason = "query 可能直接暴露 gold service。它不是 API leak，但不适合直接进入 clean service discovery 主任务。"
    elif leak_status not in {"", "no_obvious_leak"}:
        leak_check = "leak_uncertain"
        leak_reason = f"leak_status={leak_status}，不是明确 no_obvious_leak，需要人工复核。"
    else:
        leak_check = "no_blocking_leak"
        leak_reason = "未发现 query 直接泄露 gold API 或 gold service 的机械证据。"

    if has_semantic_risk:
        semantic = "semantic_alignment_uncertain"
        semantic_reason = explain_warning_tags(tags)
    elif bucket == "high_confidence_candidate":
        semantic = "semantic_alignment_ok"
        semantic_reason = "机械筛选为 high_confidence_candidate，且未触发泛化追踪、泛化地址/邮编、国家/承运商绑定、container tracking 等已知语义风险。"
    elif has_api_leak or has_service_leak:
        semantic = "semantic_alignment_ok"
        semantic_reason = "主要问题是 leak，而不是明显 query-gold 语义错配；仍需人工看原文确认。"
    else:
        semantic = "semantic_alignment_uncertain"
        semantic_reason = "该样本不是 high_confidence_candidate，且缺少足够机械证据证明 query 与 gold 完全对齐。按 fail-closed 原则先标 uncertain。"

    if is_service_task:
        if g_service <= 0:
            candidate_gold_validity = "gold_incomplete"
            candidate_reason = "service-level 任务没有 gold service，gold 不完整。"
        elif c_service <= 1 or c_service <= g_service:
            candidate_gold_validity = "candidate_set_too_small"
            candidate_reason = "service-level 需要真实服务选择空间；candidate_service_count 必须明显大于 gold_service_count，且不能只有一个候选服务。"
        elif has_semantic_risk:
            candidate_gold_validity = "uncertain"
            candidate_reason = "数量上有候选空间，但 query 是否足以支持这些 gold service 存在语义风险，需要人工确认。"
        else:
            candidate_gold_validity = "valid"
            candidate_reason = "candidate services 多于 gold services，且当前未触发已知 gold 不唯一或强语义风险。"
    elif is_api_task:
        if g_api <= 0:
            candidate_gold_validity = "gold_incomplete"
            candidate_reason = "API-level 任务没有 gold API，gold 不完整。"
        elif c_api <= 1 or c_api <= g_api:
            candidate_gold_validity = "candidate_set_too_small"
            candidate_reason = "API-level 需要具体 API 的选择空间；candidate_api_count 必须明显大于 gold_api_count。"
        elif has_semantic_risk:
            candidate_gold_validity = "uncertain"
            candidate_reason = "API 候选数量足够，但 query 对 gold API/service 的支持不唯一或存在泛化绑定风险。"
        else:
            candidate_gold_validity = "valid"
            candidate_reason = "candidate APIs 多于 gold APIs，且当前未触发已知 gold 不唯一或强语义风险。"
    else:
        candidate_gold_validity = "uncertain"
        candidate_reason = "未知 task_type，不能确认 candidate/gold 合法性。"

    if is_service_task:
        if c_service > g_service and g_service >= 2:
            task_type_check = "valid_multi_service_discovery"
            task_type_reason = "该行目标是选多个 service；candidate services 多于 gold services，具备服务层选择空间。"
        elif c_api > g_api and g_api >= 1:
            task_type_check = "should_be_multi_api"
            task_type_reason = "服务层选择空间不足，但 API 层可能还有选择空间；更像 API-level 样本。"
        elif c_service <= 1:
            task_type_check = "should_be_single_service"
            task_type_reason = "候选服务只有一个，不适合 multi-service discovery。"
        else:
            task_type_check = "ordinary_or_unclear"
            task_type_reason = "服务层候选与 gold 的关系不足以支持清晰的 multi-service discovery。"
    elif is_api_task:
        if c_api > g_api and g_api >= 2:
            task_type_check = "valid_multi_api_recommendation"
            task_type_reason = "该行目标是选多个具体 API；candidate APIs 多于 gold APIs，具备 API 层选择空间。"
        elif c_service > g_service and g_service >= 2:
            task_type_check = "should_be_multi_service"
            task_type_reason = "API 层选择空间不足或 gold API 不充分，但服务层可能存在选择空间。"
        elif g_api <= 1:
            task_type_check = "should_be_single_api"
            task_type_reason = "gold API 数量不足以构成 multi-api recommendation。"
        else:
            task_type_check = "ordinary_or_unclear"
            task_type_reason = "API 层候选与 gold 的关系不足以支持清晰的 multi-api recommendation。"
    else:
        task_type_check = "not_eligible"
        task_type_reason = "未知 task_type，不能进入当前四类主任务。"

    if has_api_leak:
        final_decision = "remove"
        final_reason = "strong API leak 是最高优先级阻断项；即使 candidate/gold 数量合理，也不能进入 clean 主数据。"
    elif semantic == "semantic_mismatch_uncertain" or candidate_gold_validity == "gold_wrong":
        final_decision = "remove"
        final_reason = "存在 query-gold 语义错配或 gold 错误风险，不能保留为 clean candidate。"
    elif (
        semantic == "semantic_alignment_ok"
        and leak_check == "no_blocking_leak"
        and candidate_gold_validity == "valid"
        and task_type_check in {"valid_multi_service_discovery", "valid_multi_api_recommendation"}
        and bucket == "high_confidence_candidate"
    ):
        final_decision = "keep_for_cleaning_candidate"
        final_reason = "无明显 leak、语义初步对齐、candidate/gold 有选择空间、任务类型匹配，适合作为正式清洗前的 clean candidate。"
    else:
        final_decision = "uncertain"
        final_reason = "触发 leak、语义、候选空间或任务边界中的至少一个保守项；按 fail-closed 原则先进入 uncertain，等待人工确认。"

    decision_reason = (
        f"语义：{semantic_reason} "
        f"Leak：{leak_reason} "
        f"Candidate/Gold：{candidate_reason} "
        f"Task type：{task_type_reason} "
        f"最终：{final_reason}"
    )

    return {
        "manual_semantic_alignment": semantic,
        "manual_leak_check": leak_check,
        "manual_candidate_gold_validity": candidate_gold_validity,
        "manual_task_type_check": task_type_check,
        "manual_final_decision": final_decision,
        "manual_decision_reason": decision_reason,
        "assistant_review_source": "assistant_draft_round2_v0_4_needs_user_confirmation",
        "assistant_review_completed": "yes",
        "semantic_alignment_reason": semantic_reason,
        "leak_check_reason": leak_reason,
        "candidate_gold_validity_reason": candidate_reason,
        "task_type_check_reason": task_type_reason,
        "final_decision_reason": final_reason,
        "assistant_warning_tags": ";".join(tags),
    }


def summarize(rows: list[dict[str, str]]) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_file": str(INPUT_CSV),
        "row_count": len(rows),
        "assistant_draft_only": True,
        "not_final_human_labels": True,
        "manual_final_decision_distribution": dict(Counter(r["manual_final_decision"] for r in rows)),
        "manual_semantic_alignment_distribution": dict(Counter(r["manual_semantic_alignment"] for r in rows)),
        "manual_leak_check_distribution": dict(Counter(r["manual_leak_check"] for r in rows)),
        "manual_candidate_gold_validity_distribution": dict(Counter(r["manual_candidate_gold_validity"] for r in rows)),
        "manual_task_type_check_distribution": dict(Counter(r["manual_task_type_check"] for r in rows)),
        "mechanical_screening_bucket_distribution": dict(Counter(r.get("mechanical_screening_bucket", "") for r in rows)),
        "task_type_distribution": dict(Counter(r.get("task_type", "") for r in rows)),
        "source_group_distribution": dict(Counter(r.get("source_group", "") for r in rows)),
        "output_files": {
            "draft_csv": str(DRAFT_CSV),
            "summary_json": str(SUMMARY_JSON),
            "reason_doc": str(DOC_PATH),
            "review_html": str(HTML_PATH),
            "html_backup": str(HTML_BACKUP_PATH),
            "archive_dir": str(ARCHIVE_DIR),
        },
        "rule_basis": [
            "API leak is blocking and maps to remove.",
            "Generic tracking/postal/address and country/carrier-specific gold without query evidence map to uncertain.",
            "Package/mail tracking is not automatically container tracking.",
            "Service-level tasks need candidate_service_count > gold_service_count and more than one candidate service.",
            "API-level tasks need candidate_api_count > gold_api_count and multiple gold APIs for multi-api recommendation.",
            "High confidence rows without known risks are assistant-drafted as keep candidates, pending user approval.",
        ],
    }


def make_markdown(rows: list[dict[str, str]], summary: dict) -> str:
    lines: list[str] = []
    lines.append("# Round2 Assistant Draft Decision Reasons v0.4")
    lines.append("")
    lines.append(f"生成时间：{summary['generated_at']}")
    lines.append("")
    lines.append("边界：本文件是 assistant draft 辅助评判，不是最终人工标签，不是正式清洗脚本，不是 baseline，不是训练规则。")
    lines.append("")
    lines.append("## 总体分布")
    lines.append("")
    for key in [
        "manual_final_decision_distribution",
        "manual_semantic_alignment_distribution",
        "manual_leak_check_distribution",
        "manual_candidate_gold_validity_distribution",
        "manual_task_type_check_distribution",
    ]:
        lines.append(f"- {key}: `{json.dumps(summary[key], ensure_ascii=False)}`")
    lines.append("")
    lines.append("## 使用的保守原则")
    lines.append("")
    for item in summary["rule_basis"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 逐条辅助判断")
    lines.append("")
    for r in rows:
        gold_services = ", ".join(service_names(r)[1]) or "(empty)"
        gold_apis = "; ".join(gold_api_names(r)) or "(empty)"
        lines.append(f"### {r['round2_review_id']} | {r['task_id']} | {r['task_type']}")
        lines.append("")
        lines.append(f"- Query: {r.get('query_text', '')}")
        lines.append(f"- Gold services: {gold_services}")
        lines.append(f"- Gold APIs: {gold_apis}")
        lines.append(f"- Mechanical bucket: `{r.get('mechanical_screening_bucket', '')}`")
        lines.append(f"- Assistant final draft: `{r['manual_final_decision']}`")
        lines.append(f"- 1. semantic_alignment = `{r['manual_semantic_alignment']}`：{r['semantic_alignment_reason']}")
        lines.append(f"- 2. leak_check = `{r['manual_leak_check']}`：{r['leak_check_reason']}")
        lines.append(f"- 3. candidate_gold_validity = `{r['manual_candidate_gold_validity']}`：{r['candidate_gold_validity_reason']}")
        lines.append(f"- 4. task_type_check = `{r['manual_task_type_check']}`：{r['task_type_check_reason']}")
        lines.append(f"- 5. final_decision = `{r['manual_final_decision']}`：{r['final_decision_reason']}")
        if r["assistant_warning_tags"]:
            lines.append(f"- Warning tags: `{r['assistant_warning_tags']}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def update_html_with_defaults(rows: list[dict[str, str]]) -> None:
    if not HTML_PATH.exists():
        return
    if not HTML_BACKUP_PATH.exists():
        shutil.copy2(HTML_PATH, HTML_BACKUP_PATH)
    html = HTML_PATH.read_text(encoding="utf-8")
    defaults = {
        r["round2_review_id"]: {field: r[field] for field in MANUAL_FIELDS}
        for r in rows
    }
    defaults_js = "const DEFAULT_ASSISTANT_DRAFT_DECISIONS = " + json.dumps(defaults, ensure_ascii=False) + ";\n"
    if "const DEFAULT_ASSISTANT_DRAFT_DECISIONS =" in html:
        start = html.index("const DEFAULT_ASSISTANT_DRAFT_DECISIONS =")
        end = html.index(";\n", start) + 2
        html = html[:start] + defaults_js + html[end:]
    else:
        marker = 'const STORAGE_KEY = "main_four_tasks_round2_review_app_80_v0_4_decisions";\n'
        html = html.replace(marker, marker + defaults_js, 1)
    old_load = """function loadDecisions() {
  try { return Object.assign({}, JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}")); }
  catch { return {}; }
}
function saveDecisions() { localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }
function decisionFor(id) { if (!decisions[id]) decisions[id] = emptyDecision(); return decisions[id]; }"""
    new_load = """function loadDecisions() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return Object.assign({}, DEFAULT_ASSISTANT_DRAFT_DECISIONS, stored);
  }
  catch { return Object.assign({}, DEFAULT_ASSISTANT_DRAFT_DECISIONS); }
}
function saveDecisions() { localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }
function decisionFor(id) { if (!decisions[id]) decisions[id] = Object.assign({}, DEFAULT_ASSISTANT_DRAFT_DECISIONS[id] || emptyDecision()); return decisions[id]; }"""
    if old_load in html:
        html = html.replace(old_load, new_load, 1)
    HTML_PATH.write_text(html, encoding="utf-8")


def archive_outputs(files: list[Path]) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for file in files:
        if file.exists():
            shutil.copy2(file, ARCHIVE_DIR / file.name)


def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Missing input CSV: {INPUT_CSV}")
    rows = read_csv(INPUT_CSV)
    drafted_rows = []
    for row in rows:
        draft = classify_row(row)
        merged = {**row, **draft}
        drafted_rows.append(merged)

    output_fields = list(rows[0].keys()) + MANUAL_FIELDS + DETAIL_FIELDS
    write_csv(DRAFT_CSV, drafted_rows, output_fields)
    summary = summarize(drafted_rows)
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(make_markdown(drafted_rows, summary), encoding="utf-8")
    update_html_with_defaults(drafted_rows)
    archive_outputs([DRAFT_CSV, SUMMARY_JSON, DOC_PATH, HTML_PATH, HTML_BACKUP_PATH])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
