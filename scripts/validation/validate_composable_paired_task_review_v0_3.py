#!/usr/bin/env python
"""Validate a future reviewed v0.3 composable paired-task CSV.

When the reviewed CSV does not exist, the script reports
WAITING_FOR_HUMAN_REVIEW and does not create final benchmark rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


HUMAN_FIELDS = [
    "dependency_edge_valid",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "query_gold_chain_alignment",
    "service_gold_complete",
    "service_candidate_space_valid",
    "service_leakage_final",
    "service_level_eligible",
    "api_gold_complete",
    "api_candidate_space_valid",
    "api_parent_mapping_valid",
    "api_leakage_final",
    "api_level_eligible",
    "composable_release_action",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]

HASH_FIELDS = [
    "query_text",
    "candidate_services_json",
    "provisional_gold_services_json",
    "candidate_apis_json",
    "provisional_gold_apis_json",
    "service_api_map_json",
    "dependency_edges_json",
    "dependency_evidence_json",
]

ENUMS = {
    "dependency_edge_valid": {"true", "false", "uncertain"},
    "dependency_evidence_sufficient": {"true", "false", "uncertain"},
    "composition_final_label": {
        "true_composable", "parallel_multi", "hybrid_composable_multi",
        "insufficient_evidence", "invalid_task",
    },
    "query_gold_chain_alignment": {"aligned", "partially_aligned", "misaligned", "uncertain"},
    "service_gold_complete": {"true", "false", "uncertain"},
    "service_candidate_space_valid": {"true", "false", "uncertain"},
    "service_leakage_final": {"no_blocking_leak", "blocking_leak", "uncertain"},
    "service_level_eligible": {"true", "false"},
    "api_gold_complete": {"true", "false", "uncertain"},
    "api_candidate_space_valid": {"true", "false", "uncertain"},
    "api_parent_mapping_valid": {"true", "false", "uncertain"},
    "api_leakage_final": {"no_blocking_leak", "blocking_leak", "uncertain"},
    "api_level_eligible": {"true", "false"},
    "composable_release_action": {
        "keep_both_levels", "keep_service_only", "reconstruct_api_then_reaudit",
        "reconstruct_service_then_reaudit", "rewrite_query_then_reaudit",
        "reclassify_as_multi", "hold", "remove",
    },
    "adjudicator_type": {"human_confirmed", "human_with_model_assistance", "model_pilot_only"},
}

SERVICE_ELIGIBILITY_REQUIREMENTS = {
    "composition_final_label": "true_composable",
    "dependency_edge_valid": "true",
    "dependency_evidence_sufficient": "true",
    "query_gold_chain_alignment": "aligned",
    "service_gold_complete": "true",
    "service_candidate_space_valid": "true",
    "service_leakage_final": "no_blocking_leak",
}

API_ELIGIBILITY_REQUIREMENTS = {
    "composition_final_label": "true_composable",
    "dependency_edge_valid": "true",
    "dependency_evidence_sufficient": "true",
    "query_gold_chain_alignment": "aligned",
    "api_gold_complete": "true",
    "api_candidate_space_valid": "true",
    "api_parent_mapping_valid": "true",
    "api_leakage_final": "no_blocking_leak",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_json(value: Any) -> Any:
    raw = text(value)
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def review_hash(row: dict[str, Any]) -> str:
    payload = {
        field: parse_json(row.get(field)) if field.endswith("_json") else text(row.get(field))
        for field in HASH_FIELDS
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_rows(base_rows: list[dict[str, str]], reviewed_rows: list[dict[str, str]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    base_by_id = {text(row.get("review_item_id")): row for row in base_rows}
    reviewed_by_id = {text(row.get("review_item_id")): row for row in reviewed_rows}
    duplicate_ids = len(reviewed_rows) - len(reviewed_by_id)
    if len(base_rows) != 200:
        issues.append({"issue_type": "base_row_count", "details": f"expected=200 actual={len(base_rows)}"})
    if len(reviewed_rows) != 200:
        issues.append({"issue_type": "reviewed_row_count", "details": f"expected=200 actual={len(reviewed_rows)}"})
    if duplicate_ids:
        issues.append({"issue_type": "duplicate_review_item_id", "details": str(duplicate_ids)})
    missing_ids = sorted(set(base_by_id) - set(reviewed_by_id))
    extra_ids = sorted(set(reviewed_by_id) - set(base_by_id))
    for item_id in missing_ids:
        issues.append({"issue_type": "missing_review_item", "review_item_id": item_id, "details": ""})
    for item_id in extra_ids:
        issues.append({"issue_type": "unexpected_review_item", "review_item_id": item_id, "details": ""})
    immutable_fields = [field for field in base_rows[0] if field not in HUMAN_FIELDS] if base_rows else []
    pending_rows: list[str] = []
    invalid_values: Counter[str] = Counter()
    immutable_mismatch_count = 0
    hash_mismatch_count = 0
    eligibility_violation_count = 0
    provenance_incomplete_count = 0
    for item_id in sorted(set(base_by_id) & set(reviewed_by_id)):
        base = base_by_id[item_id]
        reviewed = reviewed_by_id[item_id]
        changed = [field for field in immutable_fields if text(reviewed.get(field)) != text(base.get(field))]
        if changed:
            immutable_mismatch_count += 1
            issues.append({
                "issue_type": "immutable_field_modified",
                "review_item_id": item_id,
                "details": "|".join(changed),
            })
        computed_hash = review_hash(reviewed)
        if computed_hash != text(base.get("review_content_hash")) or text(reviewed.get("review_content_hash")) != text(base.get("review_content_hash")):
            hash_mismatch_count += 1
            issues.append({"issue_type": "review_content_hash_mismatch", "review_item_id": item_id, "details": computed_hash})
        required_decisions = [field for field in HUMAN_FIELDS if field not in {"adjudication_notes"}]
        missing = [field for field in required_decisions if not text(reviewed.get(field))]
        if missing:
            pending_rows.append(item_id)
        for field, allowed in ENUMS.items():
            value = text(reviewed.get(field))
            if value and value not in allowed:
                invalid_values[f"{field}={value}"] += 1
                issues.append({"issue_type": "invalid_enum", "review_item_id": item_id, "details": f"{field}={value}"})
        if not missing and any(not text(reviewed.get(field)) for field in ("adjudicator_id", "adjudicator_type", "adjudicated_at")):
            provenance_incomplete_count += 1
            issues.append({"issue_type": "reviewer_provenance_incomplete", "review_item_id": item_id, "details": ""})
        service_should_be_true = all(text(reviewed.get(field)) == expected for field, expected in SERVICE_ELIGIBILITY_REQUIREMENTS.items())
        api_should_be_true = all(text(reviewed.get(field)) == expected for field, expected in API_ELIGIBILITY_REQUIREMENTS.items())
        if text(reviewed.get("service_level_eligible")) and (text(reviewed.get("service_level_eligible")) == "true") != service_should_be_true:
            eligibility_violation_count += 1
            issues.append({"issue_type": "service_eligibility_hard_condition_violation", "review_item_id": item_id, "details": ""})
        if text(reviewed.get("api_level_eligible")) and (text(reviewed.get("api_level_eligible")) == "true") != api_should_be_true:
            eligibility_violation_count += 1
            issues.append({"issue_type": "api_eligibility_hard_condition_violation", "review_item_id": item_id, "details": ""})
        if text(reviewed.get("composition_final_label")) == "true_composable" and (
            text(reviewed.get("dependency_edge_valid")) != "true"
            or text(reviewed.get("dependency_evidence_sufficient")) != "true"
        ):
            issues.append({"issue_type": "composition_label_dependency_inconsistency", "review_item_id": item_id, "details": ""})
    return {
        "generated_at": now_iso(),
        "status": "VALID" if not issues and not pending_rows else ("PENDING" if pending_rows and not issues else "INVALID"),
        "base_rows": len(base_rows),
        "reviewed_rows": len(reviewed_rows),
        "pending": len(set(pending_rows)),
        "duplicate_review_item_id_count": duplicate_ids,
        "missing_review_item_count": len(missing_ids),
        "extra_review_item_count": len(extra_ids),
        "immutable_mismatch_count": immutable_mismatch_count,
        "hash_mismatch_count": hash_mismatch_count,
        "invalid_enum_count": sum(invalid_values.values()),
        "invalid_values": dict(sorted(invalid_values.items())),
        "provenance_incomplete_count": provenance_incomplete_count,
        "eligibility_violation_count": eligibility_violation_count,
        "issue_count": len(issues),
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Validate the future reviewed composable v0.3 CSV.")
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument("--base-csv", type=Path, default=Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_items_v0_3.csv"))
    parser.add_argument("--reviewed-csv", type=Path, default=Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_items_v0_3_reviewed.csv"))
    parser.add_argument("--output-json", type=Path, default=None)
    return parser.parse_args()


def resolve(project_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    base_path = resolve(root, args.base_csv)
    reviewed_path = resolve(root, args.reviewed_csv)
    if not base_path.exists():
        raise FileNotFoundError(f"Base review CSV does not exist: {base_path}")
    if not reviewed_path.exists():
        print(json.dumps({
            "status": "WAITING_FOR_HUMAN_REVIEW",
            "base_csv": str(base_path),
            "reviewed_csv": str(reviewed_path),
            "final_rows_generated": False,
        }, ensure_ascii=False, indent=2))
        return 0
    result = validate_rows(read_csv(base_path), read_csv(reviewed_path))
    if args.output_json:
        output_path = resolve(root, args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "VALID" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
