#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prefill main-four-tasks review app with assistant draft decisions.

This is not full cleaning, not a baseline, not model training, not a split,
not top200, and not a new full-G3 search. It only prepares an assistant-draft
human-review aid for the existing 40-sample review app.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_HTML = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_review_app_40.html"
BACKUP_HTML = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_review_app_40.before_assistant_prefill.html"
CSV_OUT = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_manual_decisions_40_assistant_prefilled.csv"
SUMMARY_JSON = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_manual_decisions_40_assistant_prefilled_summary.json"
REPORT_MD = PROJECT_ROOT / "docs" / "phase1" / "main_four_tasks_assistant_prefill_report.md"
ARCHIVE_ROOT = PROJECT_ROOT / "outputs" / "run_archives" / "2026-06-26_main_four_tasks_assistant_prefill_v0_2"

GENERATOR = PROJECT_ROOT / "scripts" / "build_dataset" / "generate_main_four_tasks_review_app_v0_2.py"


DECISIONS: Dict[str, Dict[str, str]] = {
    "R001": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: query needs Brazil address lookup and air-cargo tracking/CO2; gold services cover both needs.",
    },
    "R002": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: postal-code part aligns, but flight/passenger carbon-emission wording may not match Air Cargo CO2 service.",
    },
    "R003": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: address lookup and package tracking require two services and gold covers both.",
    },
    "R004": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: customs-agency contact and package tracking map to Transitaires plus Pridnestrovie Post.",
    },
    "R005": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package carrier/status and customs contact are separate service-level needs; multiple gold APIs under TrackingMore are acceptable for service-level audit.",
    },
    "R006": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: Istanbul postal-code/address lookup plus package tracking align with two gold services.",
    },
    "R007": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "ordinary_or_unclear",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: Istanbul postal-code request aligns with Turkey Postal Codes, but Argentina tracking API for generic task ID is semantically questionable.",
    },
    "R008": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package tracking and address lookup are covered by distinct gold services.",
    },
    "R009": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package tracking plus venue address lookup require two services and gold aligns.",
    },
    "R010": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: tracking-data request and CEP address lookup align with Create Container Tracking plus CEP Brazil.",
    },
    "R011": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: Pack & Send reference tracking and address lookup are both represented by gold services.",
    },
    "R012": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "ordinary_or_unclear",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: shipment tracking aligns, but query asks for errors/error messages not clearly covered by gold APIs.",
    },
    "R013": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "ordinary_or_unclear",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: package tracking aligns, but office-space recommendation is not clearly covered by postal-code service.",
    },
    "R014": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: Brazil CEP aligns, but flight-leg carbon-emission wording may not match air-cargo AWB tracking.",
    },
    "R015": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: address lookup plus shipment carbon-emission tracking align with gold services.",
    },
    "R016": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: Turkey postal codes plus shipment carbon-emission tracking align with two gold services.",
    },
    "R017": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: postal-service information plus air-cargo CO2 sample tracking align sufficiently.",
    },
    "R018": {
        "manual_semantic_alignment": "semantic_mismatch_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "gold_wrong",
        "manual_task_type_check": "not_eligible",
        "manual_final_decision": "remove",
        "manual_decision_reason": "assistant draft: query asks Buenos Aires postal codes but gold uses Turkey Postal Codes; likely query-gold mismatch.",
    },
    "R019": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: delivery tracking and nearby post-office address lookup align with gold services.",
    },
    "R020": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: gift package tracking and restaurant address lookup align at service/API level.",
    },
    "R021": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: query needs tracking information plus carrier detection; gold APIs cover both concrete operations.",
    },
    "R022": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: postal-code API aligns, but Transitaires service geography/contact-number semantics are unclear for Istanbul.",
    },
    "R023": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "service_leak_only",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: API operations align, but query names GS1Parser and Argentine couriers, so service leak requires review.",
    },
    "R024": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: tracking data and city/state list are concrete API-level operations covered by gold APIs.",
    },
    "R025": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: address lookup and package tracking are concrete API-level operations.",
    },
    "R026": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: tracking plus address lookup map directly to two gold APIs.",
    },
    "R027": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: tracking aligns, but requested error/error-message information is not clearly covered by gold APIs.",
    },
    "R028": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package tracking and venue address lookup are covered by two concrete APIs.",
    },
    "R029": {
        "manual_semantic_alignment": "semantic_mismatch_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "gold_wrong",
        "manual_task_type_check": "not_eligible",
        "manual_final_decision": "remove",
        "manual_decision_reason": "assistant draft: query asks games, decorations, news, and hotels, while gold APIs are logistics/barcode/EDI/customs; clear mismatch.",
    },
    "R030": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: carrier detection, package tracking, and customs contact are covered by gold APIs.",
    },
    "R031": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "service_leak_only",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: operations align, but SQUAKE service name appears in query; keep as service-leak review.",
    },
    "R032": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "service_leak_only",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: tracking and health-check APIs align, but SQUAKE service name appears in query.",
    },
    "R033": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "service_leak_only",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: both tracking APIs align, but Pack & Send and Pridnestrovie Post are named directly.",
    },
    "R034": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "service_leak_only",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: tracking/reference/health-check APIs align, but service names are directly exposed.",
    },
    "R035": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: tracking information plus carrier detection align with two TrackingMore APIs.",
    },
    "R036": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: tracking information plus carrier detection align with two TrackingMore APIs.",
    },
    "R037": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package tracking details plus carrier detection are covered by gold APIs.",
    },
    "R038": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package tracking information plus carrier detection align with gold APIs.",
    },
    "R039": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "assistant draft: package tracking information plus carrier detection align with gold APIs.",
    },
    "R040": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "assistant draft: postal-code API aligns, but Transitaires service/contact semantics are unclear for Istanbul.",
    },
}


def load_review_data() -> List[Dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("gen", GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import generator: {GENERATOR}")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    multi_service = gen.sample_multi_service(gen.read_csv_rows(gen.REQUIRED_INPUTS["multi_service"]))
    multi_api = gen.sample_multi_api(gen.read_csv_rows(gen.REQUIRED_INPUTS["multi_api"]))
    return gen.build_review_data(multi_service, multi_api)


def json_for_csv(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_csv(review_data: List[Dict[str, Any]]) -> None:
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "review_id",
        "task_id",
        "task_type",
        "source_dataset",
        "source_group",
        "leak_status",
        "semantic_alignment_status",
        "cleaning_status",
        "task_eligibility",
        "task_bucket",
        "query_text",
        "query_text_zh",
        "candidate_services_json",
        "candidate_apis_json",
        "gold_services_json",
        "gold_services_zh_json",
        "gold_apis_json",
        "gold_apis_zh_json",
        "metadata_json",
        "manual_semantic_alignment",
        "manual_leak_check",
        "manual_candidate_gold_validity",
        "manual_task_type_check",
        "manual_final_decision",
        "manual_decision_reason",
        "review_completed",
        "review_source",
    ]
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in review_data:
            decision = DECISIONS[item["review_id"]]
            row = {
                "review_id": item["review_id"],
                "task_id": item["task_id"],
                "task_type": item["task_type"],
                "source_dataset": item["source_dataset"],
                "source_group": item["source_group"],
                "leak_status": item["leak_status"],
                "semantic_alignment_status": item["semantic_alignment_status"],
                "cleaning_status": item["cleaning_status"],
                "task_eligibility": item["task_eligibility"],
                "task_bucket": item["task_bucket"],
                "query_text": item["query_text"],
                "query_text_zh": item["query_text_zh"],
                "candidate_services_json": json_for_csv(item["candidate_services"]),
                "candidate_apis_json": json_for_csv(item["candidate_apis"]),
                "gold_services_json": json_for_csv(item["gold_services"]),
                "gold_services_zh_json": json_for_csv(item["gold_services_zh"]),
                "gold_apis_json": json_for_csv(item["gold_apis"]),
                "gold_apis_zh_json": json_for_csv(item["gold_apis_zh"]),
                "metadata_json": json_for_csv(item["metadata"]),
                **decision,
                "review_completed": "yes",
                "review_source": "assistant_draft_needs_user_confirmation",
            }
            writer.writerow(row)


def inject_prefill_into_html() -> None:
    if not APP_HTML.exists():
        raise FileNotFoundError(APP_HTML)
    if not BACKUP_HTML.exists():
        shutil.copy2(APP_HTML, BACKUP_HTML)

    html = APP_HTML.read_text(encoding="utf-8")
    marker = 'const STORAGE_KEY = "main_four_tasks_review_app_v0_2_decisions";'
    default_json = json.dumps(DECISIONS, ensure_ascii=False, indent=2)
    default_block = (
        marker
        + "\n"
        + "const DEFAULT_ASSISTANT_DRAFT_DECISIONS = "
        + default_json
        + ";\n"
    )

    if "DEFAULT_ASSISTANT_DRAFT_DECISIONS" not in html:
        html = html.replace(marker, default_block)

    old_fn = """function loadDecisions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    return {};
  }
}"""
    new_fn = """function loadDecisions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const stored = parsed && typeof parsed === "object" ? parsed : {};
    const hasStored = Object.keys(stored).length > 0;
    return hasStored
      ? Object.assign({}, DEFAULT_ASSISTANT_DRAFT_DECISIONS, stored)
      : Object.assign({}, DEFAULT_ASSISTANT_DRAFT_DECISIONS);
  } catch (error) {
    return Object.assign({}, DEFAULT_ASSISTANT_DRAFT_DECISIONS);
  }
}"""
    if old_fn in html:
        html = html.replace(old_fn, new_fn)
    elif "DEFAULT_ASSISTANT_DRAFT_DECISIONS" in html and "const hasStored = Object.keys(stored).length > 0;" not in html:
        raise RuntimeError("Could not replace loadDecisions function safely.")

    APP_HTML.write_text(html, encoding="utf-8")


def write_summary(review_data: List[Dict[str, Any]]) -> None:
    final_counts = Counter(d["manual_final_decision"] for d in DECISIONS.values())
    semantic_counts = Counter(d["manual_semantic_alignment"] for d in DECISIONS.values())
    task_type_counts = Counter(d["manual_task_type_check"] for d in DECISIONS.values())
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": len(review_data),
        "review_source": "assistant_draft_needs_user_confirmation",
        "csv_output": str(CSV_OUT.relative_to(PROJECT_ROOT)),
        "html_prefilled": str(APP_HTML.relative_to(PROJECT_ROOT)),
        "html_backup": str(BACKUP_HTML.relative_to(PROJECT_ROOT)),
        "manual_final_decision_distribution": dict(final_counts),
        "manual_semantic_alignment_distribution": dict(semantic_counts),
        "manual_task_type_check_distribution": dict(task_type_counts),
        "scope_guard": {
            "full_cleaning": False,
            "baseline": False,
            "model_training": False,
            "split": False,
            "top200_continuation": False,
            "full_g3_search": False,
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(
        f"""# Main Four Tasks Assistant Prefill Report

## 【本次做了什么】
为 `main_four_tasks_review_app_40.html` 的 40 条样本生成了 assistant draft 预填写结果，并把这些预填写结果注入到 HTML 的默认 decisions 中。同时生成了可直接后续分析的 CSV。

## 【重要说明】
这些结果是助手预审，不等同于用户本人完成的人工确认。建议你打开 HTML 快速复核，尤其是 `uncertain` 和 `remove` 样本。

## 【输出文件】
- HTML: `{APP_HTML.relative_to(PROJECT_ROOT)}`
- HTML 预填前备份: `{BACKUP_HTML.relative_to(PROJECT_ROOT)}`
- CSV: `{CSV_OUT.relative_to(PROJECT_ROOT)}`
- Summary: `{SUMMARY_JSON.relative_to(PROJECT_ROOT)}`

## 【决策分布】
```json
{json.dumps(dict(final_counts), ensure_ascii=False, indent=2)}
```

## 【如何使用】
刷新当前 HTML 页面。如果页面已经有你之前填写过的 localStorage 内容，它会优先保留你的填写；否则会显示 assistant draft 预填结果。审核完成后仍可点击 `Export decisions CSV` 导出。

## 【是否建议现在 full cleaning】
不建议。当前仍是人工审核/预审阶段，不应进入 full cleaning、baseline、训练、split、top200 或 full G3 重搜。
""",
        encoding="utf-8",
    )


def archive_outputs() -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    paths = [APP_HTML, BACKUP_HTML, CSV_OUT, SUMMARY_JSON, REPORT_MD, Path(__file__).resolve()]
    for path in paths:
        if not path.exists():
            continue
        target = ARCHIVE_ROOT / path.relative_to(PROJECT_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main() -> None:
    required = [APP_HTML, GENERATOR]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing required files: " + ", ".join(missing))
    review_data = load_review_data()
    review_ids = {item["review_id"] for item in review_data}
    missing_decisions = sorted(review_ids - set(DECISIONS))
    extra_decisions = sorted(set(DECISIONS) - review_ids)
    if missing_decisions or extra_decisions:
        raise RuntimeError(
            f"Decision mismatch. missing={missing_decisions}, extra={extra_decisions}"
        )
    write_csv(review_data)
    inject_prefill_into_html()
    write_summary(review_data)
    archive_outputs()
    print(
        json.dumps(
            {
                "row_count": len(review_data),
                "csv_output": str(CSV_OUT.relative_to(PROJECT_ROOT)),
                "html_prefilled": str(APP_HTML.relative_to(PROJECT_ROOT)),
                "summary": str(SUMMARY_JSON.relative_to(PROJECT_ROOT)),
                "archive": str(ARCHIVE_ROOT.relative_to(PROJECT_ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
