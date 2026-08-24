#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json


TASK_TYPES = (
    "single_service_discovery", "single_api_recommendation", "multi_service_discovery",
    "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation",
)


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    qa = Path(args.qa_root).resolve()
    candidate = Path(args.candidate_root).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)

    _, sampling = read_csv(qa / "final_task_qa_sampling_manifest.csv")
    _, inheritance = read_csv(qa / "inherited" / "composable_review_inheritance.csv")
    _, human_reviews = read_csv(qa / "reviews" / "human_reviews_long.csv")
    attestation_fields, attestations = read_csv(qa / "reviewer_attestations.csv")
    expected_attestation_fields = ["reviewer_id", "human_reviewer_confirmed", "reviewed_independently", "did_not_see_other_reviewer_decisions", "did_not_use_ai_as_final_judge", "attested_at", "notes"]
    if attestation_fields != expected_attestation_fields or attestations:
        issues = [{"severity": "ERROR", "code": "INVALID_BLANK_ATTESTATION_TEMPLATE", "record_id": "reviewer_attestations.csv", "detail": json.dumps(attestation_fields)}]
    else:
        issues = []
    provenance_fields, provenance_rows = read_csv(candidate / "manifests" / "task_provenance.csv")
    provenance = {row["benchmark_task_id"]: row for row in provenance_rows}
    inheritance_eligible = {row["benchmark_task_id"] for row in inheritance if row["inheritance_status"] == "INHERITANCE_ELIGIBLE"}
    counts = []

    by_task: dict[str, list[dict]] = defaultdict(list)
    for row in sampling:
        by_task[row["task_type"]].append(row)
        if row["benchmark_task_id"] not in provenance:
            issues.append({"severity": "ERROR", "code": "UNKNOWN_SAMPLED_TASK", "record_id": row["benchmark_task_id"], "detail": ""})
        elif row["content_fingerprint"] != provenance[row["benchmark_task_id"]]["review_content_fingerprint"]:
            issues.append({"severity": "ERROR", "code": "SAMPLING_FINGERPRINT_MISMATCH", "record_id": row["benchmark_task_id"], "detail": ""})

    for task_type in TASK_TYPES:
        rows = by_task[task_type]
        ids = [row["benchmark_task_id"] for row in rows]
        if len(ids) != len(set(ids)):
            issues.append({"severity": "ERROR", "code": "DUPLICATE_QA_SAMPLE", "record_id": task_type, "detail": ""})
        strata = Counter(row["sampling_stratum"] for row in rows)
        if task_type.startswith("composable_"):
            if any(not key.startswith("census_") for key in strata):
                issues.append({"severity": "ERROR", "code": "COMPOSABLE_NOT_CENSUS", "record_id": task_type, "detail": json.dumps(strata)})
        elif strata != Counter({"random_representative": 70, "stratified_coverage": 20, "risk_targeted": 10}):
            issues.append({"severity": "ERROR", "code": "INVALID_70_20_10", "record_id": task_type, "detail": json.dumps(strata)})

        primary_fields, primary_blind = read_csv(qa / "blind_packs" / task_type / "primary_blind_pack.csv")
        secondary_fields, secondary_blind = read_csv(qa / "blind_packs" / task_type / "secondary_blind_pack.csv")
        _, primary_templates = read_csv(qa / "review_templates" / task_type / "primary_reviews.csv")
        _, secondary_templates = read_csv(qa / "review_templates" / task_type / "secondary_reviews.csv")
        forbidden = {"policy_label", "failure_reason", "model_label", "expected_final_decision", "reviewer_id", "final_decision"}
        for fieldset, pack_name in ((primary_fields, "primary"), (secondary_fields, "secondary")):
            leaked = forbidden.intersection(fieldset)
            if leaked:
                issues.append({"severity": "ERROR", "code": "BLIND_PACK_FORBIDDEN_FIELD", "record_id": f"{task_type}:{pack_name}", "detail": json.dumps(sorted(leaked))})
        selected = set(ids)
        inherited_selected = selected & inheritance_eligible
        expected_primary = selected - inherited_selected
        actual_primary = {row["benchmark_task_id"] for row in primary_blind}
        actual_secondary = {row["benchmark_task_id"] for row in secondary_blind}
        expected_secondary = {row["benchmark_task_id"] for row in rows if row["secondary_review_selected"] == "true"}
        if actual_primary != expected_primary:
            issues.append({"severity": "ERROR", "code": "PRIMARY_PACK_COVERAGE_MISMATCH", "record_id": task_type, "detail": json.dumps({"missing": sorted(expected_primary - actual_primary), "extra": sorted(actual_primary - expected_primary)})})
        if actual_secondary != expected_secondary:
            issues.append({"severity": "ERROR", "code": "SECONDARY_PACK_COVERAGE_MISMATCH", "record_id": task_type, "detail": ""})
        required_secondary = 30 if task_type.startswith("composable_") else 20
        if len(actual_secondary) != min(required_secondary, len(rows)):
            issues.append({"severity": "ERROR", "code": "SECONDARY_COUNT_MISMATCH", "record_id": task_type, "detail": str(len(actual_secondary))})
        if len(primary_templates) != len(primary_blind) or len(secondary_templates) != len(secondary_blind):
            issues.append({"severity": "ERROR", "code": "TEMPLATE_PACK_COUNT_MISMATCH", "record_id": task_type, "detail": ""})
        for template in primary_templates + secondary_templates:
            decision_fields = ["review_id", "reviewer_id", "semantic_alignment_check", "gold_validity_check", "candidate_validity_check", "service_catalog_check", "task_type_check", "leakage_check", "dependency_check", "final_decision", "error_type", "severity", "notes", "reviewed_at"]
            if any(template[field] for field in decision_fields):
                issues.append({"severity": "ERROR", "code": "NONBLANK_HUMAN_TEMPLATE_DECISION", "record_id": template["benchmark_task_id"], "detail": ""})
        counts.append({"task_type": task_type, "sampled_unique": len(selected), "inherited_primary": len(inherited_selected), "new_primary_pack": len(primary_blind), "secondary_pack": len(secondary_blind)})

    invalidated = [row for row in inheritance if row["inheritance_status"] != "INHERITANCE_ELIGIBLE"]
    if invalidated:
        issues.append({"severity": "ERROR", "code": "INVALIDATED_INHERITED_REVIEW", "record_id": "", "detail": str(len(invalidated))})
    inherited_review_ids = {row["benchmark_task_id"] for row in human_reviews if row["review_round"] == "inherited_primary"}
    if inherited_review_ids != inheritance_eligible:
        issues.append({"severity": "ERROR", "code": "INHERITED_LONG_FORM_COVERAGE_MISMATCH", "record_id": "", "detail": ""})
    for row in human_reviews:
        if row["review_round"] == "inherited_primary" and (not row["reviewer_id"] or row["final_decision"] not in {"keep", "remove", "uncertain"}):
            issues.append({"severity": "ERROR", "code": "INVALID_INHERITED_HUMAN_REVIEW", "record_id": row["benchmark_task_id"], "detail": ""})

    summary = {
        "stage": "G4_pack_validation",
        "status": "PACKS_VALID_HUMAN_REVIEW_PENDING" if not issues else "BLOCKED",
        "sampling_rows": len(sampling),
        "inherited_human_reviews": len(human_reviews),
        "errors": len(issues),
        "g4_gate_passed": False,
        "task_pack_counts": counts,
    }
    write_json(output / "VALIDATION_SUMMARY.json", summary)
    write_csv(output / "VALIDATION_ISSUES.csv", issues, ["severity", "code", "record_id", "detail"])
    inputs = [qa / "final_task_qa_sampling_manifest.csv", qa / "reviewer_attestations.csv", qa / "inherited" / "composable_review_inheritance.csv", qa / "reviews" / "human_reviews_long.csv", candidate / "manifests" / "task_provenance.csv"]
    for task_type in TASK_TYPES:
        inputs.extend([qa / "blind_packs" / task_type / "primary_blind_pack.csv", qa / "blind_packs" / task_type / "secondary_blind_pack.csv", qa / "review_templates" / task_type / "primary_reviews.csv", qa / "review_templates" / task_type / "secondary_reviews.csv"])
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs], ["resolved_path", "size_bytes", "sha256"])
    manifest = []
    for path in sorted((p for p in output.iterdir() if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.name):
        manifest.append({"relative_path": path.name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
