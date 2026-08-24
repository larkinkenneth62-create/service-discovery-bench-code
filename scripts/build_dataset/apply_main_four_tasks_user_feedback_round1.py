#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Apply user feedback round 1 to the 40-row main-four-tasks review draft.

This script does not run full cleaning, baseline, model training, data split,
top200 expansion, or full G3 search. It only creates a revised manual-review
CSV from the existing assistant draft, preserving the original file.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2"
INPUT_CSV = REVIEW_DIR / "main_four_tasks_manual_decisions_40_assistant_prefilled.csv"
OUTPUT_CSV = REVIEW_DIR / "main_four_tasks_manual_decisions_40_user_feedback_round1.csv"
CHANGES_CSV = REVIEW_DIR / "main_four_tasks_user_feedback_round1_changes.csv"
SUMMARY_JSON = REVIEW_DIR / "main_four_tasks_user_feedback_round1_summary.json"
APP_HTML = REVIEW_DIR / "main_four_tasks_review_app_40.html"
HTML_BACKUP = REVIEW_DIR / "main_four_tasks_review_app_40.before_user_feedback_round1.html"
ARCHIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-26_main_four_tasks_user_feedback_round1_v0_2"
)


REVISION_SOURCE = "assistant_draft_revised_with_user_feedback_round1"


REVISIONS: dict[str, dict[str, str]] = {
    "R011": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: generic postal code/address request does not specify Brazil, so CEP Brazil as gold is suspicious; keep for review instead of clean.",
    },
    "R019": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: query asks generic package tracking and nearby post-office address lookup; gold uses Australia tracking plus CEP Brazil without location evidence.",
    },
    "R020": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_service_discovery",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: query asks generic gift package tracking and restaurant address lookup; gold uses Australia tracking plus CEP Brazil without clear geographic grounding.",
    },
    "R021": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: only one candidate service, so it is not suitable for service discovery; keep because this row is API-level and has multiple candidate APIs.",
    },
    "R022": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: all candidate services are gold services, so service-level choice space is absent; API-level may still exist but Transitaires geography remains uncertain.",
    },
    "R024": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: original query describes invitation/mail tracking, but one gold service is container tracking; possible query-gold mismatch.",
    },
    "R025": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: package location/carrier is not specified; Pridnestrovie Post as gold may be underdetermined by the query.",
    },
    "R026": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: package location/carrier is not specified; Pridnestrovie Post as gold may be underdetermined by the query.",
    },
    "R027": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: query asks gift/package shipment status and errors, but one gold service is container tracking; keep uncertain.",
    },
    "R028": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: package location/carrier is not specified; Australia tracking plus CEP Brazil gold may be underdetermined by the query.",
    },
    "R032": {
        "manual_semantic_alignment": "semantic_alignment_uncertain",
        "manual_leak_check": "service_leak_only",
        "manual_candidate_gold_validity": "uncertain",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "uncertain",
        "manual_decision_reason": "user feedback round1: query asks package tracking while gold service is container tracking; SQUAKE service is also directly named, so keep uncertain.",
    },
    "R035": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: only one candidate service, so not service discovery; still valid for API-level because there are multiple candidate APIs under TrackingMore.",
    },
    "R036": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: only one candidate service, so not service discovery; still valid for API-level because there are multiple candidate APIs under TrackingMore.",
    },
    "R037": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: only one candidate service, so not service discovery; still valid for API-level because there are multiple candidate APIs under TrackingMore.",
    },
    "R038": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: only one candidate service, so not service discovery; still valid for API-level because there are multiple candidate APIs under TrackingMore.",
    },
    "R039": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: only one candidate service, so not service discovery; still valid for API-level because there are multiple candidate APIs under TrackingMore.",
    },
    "R040": {
        "manual_semantic_alignment": "semantic_alignment_ok",
        "manual_leak_check": "no_blocking_leak",
        "manual_candidate_gold_validity": "valid",
        "manual_task_type_check": "valid_multi_api_recommendation",
        "manual_final_decision": "keep_for_cleaning_candidate",
        "manual_decision_reason": "user feedback round1: user judged this sample acceptable; Turkey postal-code request and freight-forwarder/contact request align sufficiently for API-level review.",
    },
}


DECISION_KEYS = [
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "manual_decision_reason",
]


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def make_decisions(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    decisions: dict[str, dict[str, str]] = {}
    for row in rows:
        decisions[row["review_id"]] = {key: row.get(key, "") for key in DECISION_KEYS}
    return decisions


def update_html_defaults(rows: list[dict[str, str]]) -> bool:
    if not APP_HTML.exists():
        return False
    if not HTML_BACKUP.exists():
        shutil.copy2(APP_HTML, HTML_BACKUP)

    html = APP_HTML.read_text(encoding="utf-8")
    decisions_json = json.dumps(make_decisions(rows), ensure_ascii=False, indent=2)
    replacement = f"const DEFAULT_ASSISTANT_DRAFT_DECISIONS = {decisions_json};"
    pattern = re.compile(
        r"const DEFAULT_ASSISTANT_DRAFT_DECISIONS = \{.*?\};",
        flags=re.DOTALL,
    )
    new_html, count = pattern.subn(replacement, html, count=1)
    if count != 1:
        raise RuntimeError("Could not replace DEFAULT_ASSISTANT_DRAFT_DECISIONS in HTML.")
    APP_HTML.write_text(new_html, encoding="utf-8")
    return True


def main() -> None:
    rows, fieldnames = read_csv(INPUT_CSV)
    by_id = {row["review_id"]: row for row in rows}
    missing = sorted(set(REVISIONS) - set(by_id))
    if missing:
        raise RuntimeError(f"Revision IDs missing from input CSV: {missing}")

    changes: list[dict[str, str]] = []
    for review_id, revision in REVISIONS.items():
        row = by_id[review_id]
        old_snapshot = {key: row.get(key, "") for key in DECISION_KEYS}
        for key, value in revision.items():
            row[key] = value
        row["review_completed"] = "yes"
        row["review_source"] = REVISION_SOURCE
        for key in DECISION_KEYS:
            old_value = old_snapshot.get(key, "")
            new_value = row.get(key, "")
            if old_value != new_value:
                changes.append(
                    {
                        "review_id": review_id,
                        "task_id": row.get("task_id", ""),
                        "field": key,
                        "old_value": old_value,
                        "new_value": new_value,
                    }
                )

    write_csv(OUTPUT_CSV, rows, fieldnames)
    write_csv(CHANGES_CSV, changes, ["review_id", "task_id", "field", "old_value", "new_value"])
    html_updated = update_html_defaults(rows)

    final_decision_dist = Counter(row.get("manual_final_decision", "") for row in rows)
    semantic_dist = Counter(row.get("manual_semantic_alignment", "") for row in rows)
    source_dist = Counter(row.get("review_source", "") for row in rows)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_csv": str(INPUT_CSV),
        "output_csv": str(OUTPUT_CSV),
        "changes_csv": str(CHANGES_CSV),
        "html_updated": html_updated,
        "html_backup": str(HTML_BACKUP),
        "row_count": len(rows),
        "revised_review_ids": sorted(REVISIONS),
        "revised_row_count": len(REVISIONS),
        "change_count": len(changes),
        "manual_final_decision_distribution": dict(sorted(final_decision_dist.items())),
        "manual_semantic_alignment_distribution": dict(sorted(semantic_dist.items())),
        "review_source_distribution": dict(sorted(source_dist.items())),
        "guardrails": {
            "full_cleaning": False,
            "baseline": False,
            "training": False,
            "split": False,
            "top200": False,
            "full_g3_search": False,
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [OUTPUT_CSV, CHANGES_CSV, SUMMARY_JSON, APP_HTML, HTML_BACKUP, Path(__file__)]:
        if path.exists():
            shutil.copy2(path, ARCHIVE_DIR / path.name)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
