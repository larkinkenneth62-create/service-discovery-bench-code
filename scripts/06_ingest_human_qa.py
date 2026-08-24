#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.qa import (  # noqa: E402
    cohens_kappa,
    human_review_validation_errors,
    raw_agreement,
    wilson_interval,
)
from servicediscoverybench.signatures import stable_hash  # noqa: E402

csv.field_size_limit(2_147_483_647)

TASK_TYPES = (
    "single_service_discovery", "single_api_recommendation", "multi_service_discovery",
    "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation",
)
REVIEW_FIELDS = [
    "review_id", "benchmark_task_id", "review_round", "reviewer_id", "blind_pack_id",
    "content_fingerprint", "semantic_alignment_check", "gold_validity_check", "candidate_validity_check",
    "service_catalog_check", "task_type_check", "leakage_check", "dependency_check", "final_decision",
    "error_type", "severity", "notes", "reviewed_at",
]
SUMMARY_FIELDS = [
    "benchmark_task_id", "task_type", "sampling_stratum", "reviewer_1_id", "reviewer_1_decision",
    "reviewer_2_id", "reviewer_2_decision", "agreement", "adjudicated_decision", "adjudicator_id",
    "adjudication_notes", "final_qa_decision", "qa_resolution_status", "content_fingerprint",
]
ATTESTATION_FIELDS = [
    "reviewer_id", "human_reviewer_confirmed", "reviewed_independently",
    "did_not_see_other_reviewer_decisions", "did_not_use_ai_as_final_judge", "attested_at", "notes",
]
CHECK_FIELDS = [
    "semantic_alignment_check", "gold_validity_check", "candidate_validity_check", "service_catalog_check",
    "task_type_check", "leakage_check", "dependency_check",
]


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def read_review_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "policy_id",
        "review_mode",
        "authoritative_round",
        "secondary_review_role",
        "require_independent_secondary_reviewer",
        "require_adjudication",
    }
    missing = sorted(required - policy.keys())
    if missing:
        raise ValueError(f"review policy missing required fields: {missing}")
    if policy["review_mode"] not in {"single_human_review", "independent_double_review"}:
        raise ValueError(f"unsupported review_mode: {policy['review_mode']}")
    if policy["authoritative_round"] != "primary":
        raise ValueError("G4 currently supports only primary as the authoritative review round")
    if policy["review_mode"] == "single_human_review":
        if policy["secondary_review_role"] != "supplemental_non_gating":
            raise ValueError("single_human_review requires secondary_review_role=supplemental_non_gating")
        if policy["require_independent_secondary_reviewer"] or policy["require_adjudication"]:
            raise ValueError("single_human_review cannot require an independent secondary reviewer or adjudication")
    return policy


def is_blank_review(row: dict) -> bool:
    decision_fields = ["review_id", "reviewer_id", *CHECK_FIELDS, "final_decision", "error_type", "severity", "notes", "reviewed_at"]
    return not any((row.get(field) or "").strip() for field in decision_fields)


def complete_review(row: dict) -> bool:
    return bool((row.get("final_decision") or "").strip())


def review_disagrees(left: dict, right: dict, composable: bool) -> bool:
    fields = ["final_decision", "semantic_alignment_check", "gold_validity_check", "candidate_validity_check", "task_type_check", "leakage_check"]
    if composable:
        fields.append("dependency_check")
    return any(left.get(field) != right.get(field) for field in fields)


def validate_review(
    row: dict,
    expected_round: str,
    expected_fingerprint: str,
    expected_pack_id: str,
    composable: bool,
    issues: list[dict],
) -> None:
    task_id = row.get("benchmark_task_id", "")
    if row.get("review_round") != expected_round:
        issues.append({"severity": "ERROR", "code": "REVIEW_ROUND_MISMATCH", "record_id": task_id, "detail": row.get("review_round", "")})
    if row.get("content_fingerprint") != expected_fingerprint:
        issues.append({"severity": "ERROR", "code": "REVIEW_FINGERPRINT_MISMATCH", "record_id": task_id, "detail": ""})
    if row.get("blind_pack_id") != expected_pack_id:
        issues.append({"severity": "ERROR", "code": "BLIND_PACK_ID_MISMATCH", "record_id": task_id, "detail": row.get("blind_pack_id", "")})
    if not row.get("review_id") or not row.get("reviewer_id") or not row.get("reviewed_at"):
        issues.append({"severity": "ERROR", "code": "MISSING_REVIEW_PROVENANCE", "record_id": task_id, "detail": ""})
    if row.get("final_decision") not in {"keep", "remove", "uncertain"}:
        issues.append({"severity": "ERROR", "code": "INVALID_FINAL_DECISION", "record_id": task_id, "detail": row.get("final_decision", "")})
    missing_checks = [field for field in CHECK_FIELDS if not (row.get(field) or "").strip()]
    if missing_checks:
        issues.append({"severity": "ERROR", "code": "MISSING_HUMAN_CHECK", "record_id": task_id, "detail": json.dumps(missing_checks)})
    for detail in human_review_validation_errors(row, composable=composable):
        issues.append({"severity": "ERROR", "code": "INVALID_HUMAN_REVIEW_VALUE", "record_id": task_id, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--review-policy")
    parser.add_argument("--adjudication")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    qa_root = Path(args.qa_root).resolve()
    candidate_root = Path(args.candidate_root).resolve()
    review_policy_path = Path(args.review_policy).resolve() if args.review_policy else qa_root / "review_policy.json"
    review_policy = read_review_policy(review_policy_path)
    single_review_mode = review_policy["review_mode"] == "single_human_review"
    adjudication_path = Path(args.adjudication).resolve() if args.adjudication else qa_root / "adjudication" / "adjudication.csv"
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    out_qa = output / "qa"
    for directory in (out_qa / "reviews", out_qa / "adjudication", out_qa / "reports"):
        directory.mkdir(parents=True, exist_ok=True)

    sampling_fields, sampling = read_csv(qa_root / "final_task_qa_sampling_manifest.csv")
    sample_by_id = {row["benchmark_task_id"]: row for row in sampling}
    if len(sample_by_id) != len(sampling):
        raise ValueError("sampling manifest contains duplicate benchmark_task_id")
    _, inherited_all = read_csv(qa_root / "reviews" / "human_reviews_long.csv")
    inherited = {row["benchmark_task_id"]: row for row in inherited_all if row["benchmark_task_id"] in sample_by_id and row["review_round"] == "inherited_primary"}
    attestation_fields, attestation_rows = read_csv(qa_root / "reviewer_attestations.csv")
    issues = []
    if attestation_fields != ATTESTATION_FIELDS:
        issues.append({"severity": "ERROR", "code": "ATTESTATION_SCHEMA_MISMATCH", "record_id": "reviewer_attestations.csv", "detail": json.dumps(attestation_fields)})
    attestations = {}
    for row in attestation_rows:
        reviewer = row["reviewer_id"]
        if not reviewer or reviewer in attestations:
            issues.append({"severity": "ERROR", "code": "INVALID_OR_DUPLICATE_ATTESTATION", "record_id": reviewer, "detail": ""})
        attestations[reviewer] = row

    public_rows = {}
    for task_type in TASK_TYPES:
        _, rows = read_csv(candidate_root / "tasks" / f"{task_type}.csv")
        public_rows.update({row["benchmark_task_id"]: row for row in rows})
    _, provenance_rows = read_csv(candidate_root / "manifests" / "task_provenance.csv")
    provenance = {row["benchmark_task_id"]: row for row in provenance_rows}

    primary_rows, secondary_rows = [], []
    for task_type in TASK_TYPES:
        primary_fields, primary = read_csv(qa_root / "review_templates" / task_type / "primary_reviews.csv")
        secondary_fields, secondary = read_csv(qa_root / "review_templates" / task_type / "secondary_reviews.csv")
        if primary_fields != REVIEW_FIELDS or secondary_fields != REVIEW_FIELDS:
            issues.append({"severity": "ERROR", "code": "REVIEW_TEMPLATE_SCHEMA_MISMATCH", "record_id": task_type, "detail": ""})
        primary_rows.extend(primary)
        secondary_rows.extend(secondary)
    expected_primary = {row["benchmark_task_id"] for row in sampling if row["primary_review_source"] == "new_blind_pack"}
    expected_secondary_manifest = {row["benchmark_task_id"] for row in sampling if row["secondary_review_selected"] == "true"}
    expected_secondary = set() if single_review_mode else expected_secondary_manifest
    if {row["benchmark_task_id"] for row in primary_rows} != expected_primary:
        issues.append({"severity": "ERROR", "code": "PRIMARY_TEMPLATE_COVERAGE_MISMATCH", "record_id": "", "detail": ""})
    if {row["benchmark_task_id"] for row in secondary_rows} != expected_secondary_manifest:
        issues.append({"severity": "ERROR", "code": "SECONDARY_TEMPLATE_COVERAGE_MISMATCH", "record_id": "", "detail": ""})

    completed_primary, completed_secondary = {}, {}
    partial_rows = []
    for expected_round, rows, destination in (("primary", primary_rows, completed_primary), ("secondary", secondary_rows, completed_secondary)):
        for row in rows:
            task_id = row["benchmark_task_id"]
            if is_blank_review(row):
                continue
            if not complete_review(row):
                partial_rows.append(task_id)
                issues.append({"severity": "ERROR", "code": "PARTIAL_REVIEW_WITHOUT_DECISION", "record_id": task_id, "detail": expected_round})
                continue
            expected_fingerprint = sample_by_id[task_id]["content_fingerprint"]
            expected_pack = f"{sample_by_id[task_id]['task_type']}::{expected_round}::v0.1"
            validate_review(
                row,
                expected_round,
                expected_fingerprint,
                expected_pack,
                sample_by_id[task_id]["task_type"].startswith("composable_"),
                issues,
            )
            if task_id in destination:
                issues.append({"severity": "ERROR", "code": "DUPLICATE_REVIEW_FOR_ROUND", "record_id": task_id, "detail": expected_round})
            destination[task_id] = row

    review_ids = [row["review_id"] for row in inherited_all + list(completed_primary.values()) + list(completed_secondary.values()) if row.get("review_id")]
    if len(review_ids) != len(set(review_ids)):
        issues.append({"severity": "ERROR", "code": "DUPLICATE_REVIEW_ID", "record_id": "", "detail": ""})

    gating_reviews = list(completed_primary.values())
    if not single_review_mode:
        gating_reviews += list(completed_secondary.values())
    for row in gating_reviews:
        attestation = attestations.get(row["reviewer_id"])
        if not attestation:
            issues.append({"severity": "ERROR", "code": "MISSING_REVIEWER_ATTESTATION", "record_id": row["reviewer_id"], "detail": row["benchmark_task_id"]})
            continue
        required_true = ["human_reviewer_confirmed", "did_not_use_ai_as_final_judge"]
        if not single_review_mode:
            required_true += ["reviewed_independently", "did_not_see_other_reviewer_decisions"]
        if any(attestation[field].strip().lower() != "true" for field in required_true) or not attestation["attested_at"]:
            issues.append({"severity": "ERROR", "code": "INVALID_REVIEWER_ATTESTATION", "record_id": row["reviewer_id"], "detail": ""})

    primary_for_task = dict(inherited)
    primary_for_task.update(completed_primary)
    disagreements = set()
    for task_id in expected_secondary & primary_for_task.keys() & completed_secondary.keys():
        left, right = primary_for_task[task_id], completed_secondary[task_id]
        if left["reviewer_id"] == right["reviewer_id"]:
            issues.append({"severity": "ERROR", "code": "NONINDEPENDENT_DOUBLE_REVIEW", "record_id": task_id, "detail": left["reviewer_id"]})
        if review_disagrees(left, right, sample_by_id[task_id]["task_type"].startswith("composable_")):
            disagreements.add(task_id)

    adjudication_fields, adjudication_rows = read_csv(adjudication_path)
    if adjudication_fields != REVIEW_FIELDS:
        issues.append({"severity": "ERROR", "code": "ADJUDICATION_SCHEMA_MISMATCH", "record_id": str(adjudication_path), "detail": json.dumps(adjudication_fields)})
    completed_adjudication = {}
    for row in adjudication_rows:
        if is_blank_review(row):
            continue
        task_id = row["benchmark_task_id"]
        if task_id not in disagreements:
            issues.append({"severity": "ERROR", "code": "ADJUDICATION_FOR_NONDISAGREEMENT", "record_id": task_id, "detail": ""})
            continue
        if not complete_review(row):
            issues.append({"severity": "ERROR", "code": "PARTIAL_ADJUDICATION_WITHOUT_DECISION", "record_id": task_id, "detail": ""})
            continue
        validate_review(
            row,
            "adjudication",
            sample_by_id[task_id]["content_fingerprint"],
            f"adjudication::{task_id}::v0.1",
            sample_by_id[task_id]["task_type"].startswith("composable_"),
            issues,
        )
        if row["reviewer_id"] in {primary_for_task[task_id]["reviewer_id"], completed_secondary[task_id]["reviewer_id"]}:
            issues.append({"severity": "ERROR", "code": "ADJUDICATOR_NOT_INDEPENDENT", "record_id": task_id, "detail": row["reviewer_id"]})
        attestation = attestations.get(row["reviewer_id"])
        if not attestation or any(attestation[field].strip().lower() != "true" for field in ["human_reviewer_confirmed", "reviewed_independently", "did_not_use_ai_as_final_judge"]):
            issues.append({"severity": "ERROR", "code": "INVALID_ADJUDICATOR_ATTESTATION", "record_id": task_id, "detail": row["reviewer_id"]})
        completed_adjudication[task_id] = row

    summary_rows = []
    final_decisions = {}
    unresolved_disagreements = disagreements - completed_adjudication.keys()
    for sample in sampling:
        task_id = sample["benchmark_task_id"]
        primary = primary_for_task.get(task_id)
        secondary = completed_secondary.get(task_id) if task_id in expected_secondary else None
        adjudicated = completed_adjudication.get(task_id) if not single_review_mode else None
        if not primary:
            final, resolution = "", "primary_pending"
        elif task_id not in expected_secondary:
            final, resolution = primary["final_decision"], "single_human_review_complete"
        elif not secondary:
            final, resolution = "", "secondary_pending"
        elif task_id in disagreements and not adjudicated:
            final, resolution = "", "adjudication_pending"
        elif adjudicated:
            final, resolution = adjudicated["final_decision"], "human_adjudicated"
        else:
            final, resolution = primary["final_decision"], "double_review_agreement"
        if final:
            final_decisions[task_id] = final
        summary_rows.append({
            "benchmark_task_id": task_id, "task_type": sample["task_type"], "sampling_stratum": sample["sampling_stratum"],
            "reviewer_1_id": primary["reviewer_id"] if primary else "", "reviewer_1_decision": primary["final_decision"] if primary else "",
            "reviewer_2_id": secondary["reviewer_id"] if secondary else "", "reviewer_2_decision": secondary["final_decision"] if secondary else "",
            "agreement": str(bool(primary and secondary and task_id not in disagreements)).lower() if secondary else "",
            "adjudicated_decision": adjudicated["final_decision"] if adjudicated else "", "adjudicator_id": adjudicated["reviewer_id"] if adjudicated else "",
            "adjudication_notes": adjudicated["notes"] if adjudicated else "", "final_qa_decision": final,
            "qa_resolution_status": resolution, "content_fingerprint": sample["content_fingerprint"],
        })

    combined_reviews = inherited_all + list(completed_primary.values()) + list(completed_secondary.values()) + list(completed_adjudication.values())
    write_csv(out_qa / "reviews" / "human_reviews_long.csv", combined_reviews, REVIEW_FIELDS)
    write_csv(out_qa / "reviews" / "human_reviews_summary.csv", summary_rows, SUMMARY_FIELDS)

    disagreement_context, adjudication_template = [], []
    for task_id in sorted(unresolved_disagreements):
        public = public_rows[task_id]
        disagreement_context.append({
            "benchmark_task_id": task_id, "task_type": public["task_type"], "query_text": public["query_text"],
            "candidate_services_json": public["candidate_services_json"], "candidate_apis_json": public["candidate_apis_json"],
            "gold_services_json": public["gold_services_json"], "gold_apis_json": public["gold_apis_json"],
            "dependency_graph_json": public["dependency_graph_json"], "reviewer_1_review_json": json.dumps(primary_for_task[task_id], ensure_ascii=False),
            "reviewer_2_review_json": json.dumps(completed_secondary[task_id], ensure_ascii=False),
            "content_fingerprint": sample_by_id[task_id]["content_fingerprint"],
        })
        adjudication_template.append({
            "review_id": "", "benchmark_task_id": task_id, "review_round": "adjudication", "reviewer_id": "",
            "blind_pack_id": f"adjudication::{task_id}::v0.1", "content_fingerprint": sample_by_id[task_id]["content_fingerprint"],
            **{field: "" for field in CHECK_FIELDS}, "final_decision": "", "error_type": "", "severity": "", "notes": "", "reviewed_at": "",
        })
    write_csv(out_qa / "adjudication" / "disagreement_context.csv", disagreement_context, [
        "benchmark_task_id", "task_type", "query_text", "candidate_services_json", "candidate_apis_json",
        "gold_services_json", "gold_apis_json", "dependency_graph_json", "reviewer_1_review_json",
        "reviewer_2_review_json", "content_fingerprint",
    ])
    write_csv(out_qa / "adjudication" / "adjudication_reviews_template.csv", adjudication_template, REVIEW_FIELDS)

    by_task_summary = []
    for task_type in TASK_TYPES:
        task_samples = [row for row in sampling if row["task_type"] == task_type]
        ids = {row["benchmark_task_id"] for row in task_samples}
        required_secondary = 0 if single_review_mode else (30 if task_type.startswith("composable_") else 20)
        decisions = Counter(final_decisions[task_id] for task_id in ids if task_id in final_decisions)
        primary_completed = len(ids & primary_for_task.keys())
        secondary_completed = len(ids & completed_secondary.keys())
        task_disagreements = len(ids & disagreements)
        adjudicated_count = len(ids & completed_adjudication.keys())
        pending = len(ids - final_decisions.keys())
        if pending:
            task_status = "HUMAN_REVIEW_PENDING"
        elif decisions.get("uncertain", 0):
            task_status = "QA_ACTION_REQUIRED"
        elif decisions.get("remove", 0):
            task_status = "QA_REVIEW_COMPLETE_WITH_EXCLUSIONS"
        else:
            task_status = "QA_REVIEW_COMPLETE"
        by_task_summary.append({
            "task_type": task_type, "sampled_unique_rows": len(ids), "required_primary_reviews": len(ids),
            "primary_reviews_completed": primary_completed, "required_double_reviews": min(required_secondary, len(ids)),
            "secondary_reviews_completed": secondary_completed, "disagreements": task_disagreements,
            "adjudications_completed": adjudicated_count, "final_keep": decisions.get("keep", 0),
            "final_remove": decisions.get("remove", 0), "final_uncertain": decisions.get("uncertain", 0),
            "unresolved_rows": pending, "qa_status": task_status,
        })
    write_csv(out_qa / "reports" / "qa_summary_by_task.csv", by_task_summary, list(by_task_summary[0]))

    random_reports = []
    for task_type in TASK_TYPES:
        random_ids = {row["benchmark_task_id"] for row in sampling if row["task_type"] == task_type and row["sampling_stratum"] == "random_representative"}
        resolved = [final_decisions[task_id] for task_id in random_ids if task_id in final_decisions]
        kept = sum(value == "keep" for value in resolved)
        low, high = wilson_interval(kept, len(resolved))
        random_reports.append({"task_type": task_type, "reviewed_n": len(resolved), "kept_n": kept, "pass_rate": kept / len(resolved) if resolved else "", "wilson_lower_95": low if resolved else "", "wilson_upper_95": high if resolved else "", "status": "complete" if len(resolved) == len(random_ids) and random_ids else "pending_or_census"})
    write_csv(out_qa / "reports" / "qa_random_layer_quality.csv", random_reports, list(random_reports[0]))

    def findings_for(stratum_name: str) -> list[dict]:
        result = []
        for task_type in TASK_TYPES:
            ids = {row["benchmark_task_id"] for row in sampling if row["task_type"] == task_type and row["sampling_stratum"] == stratum_name}
            decisions = Counter(final_decisions[task_id] for task_id in ids if task_id in final_decisions)
            result.append({"task_type": task_type, "stratum": stratum_name, "reviewed_n": sum(decisions.values()), "keep": decisions.get("keep", 0), "remove": decisions.get("remove", 0), "uncertain": decisions.get("uncertain", 0), "status": "complete" if sum(decisions.values()) == len(ids) and ids else "pending_or_census"})
        return result
    write_csv(out_qa / "reports" / "qa_stratified_findings.csv", findings_for("stratified_coverage"), ["task_type", "stratum", "reviewed_n", "keep", "remove", "uncertain", "status"])
    write_csv(out_qa / "reports" / "qa_risk_findings.csv", findings_for("risk_targeted"), ["task_type", "stratum", "reviewed_n", "keep", "remove", "uncertain", "status"])

    agreement_reports = []
    for task_type in TASK_TYPES:
        double_ids = [] if single_review_mode else sorted({row["benchmark_task_id"] for row in sampling if row["task_type"] == task_type and row["secondary_review_selected"] == "true"} & primary_for_task.keys() & completed_secondary.keys())
        left = [primary_for_task[task_id]["final_decision"] for task_id in double_ids]
        right = [completed_secondary[task_id]["final_decision"] for task_id in double_ids]
        task_left = [primary_for_task[task_id]["task_type_check"] for task_id in double_ids]
        task_right = [completed_secondary[task_id]["task_type_check"] for task_id in double_ids]
        dep_left = [primary_for_task[task_id]["dependency_check"] for task_id in double_ids]
        dep_right = [completed_secondary[task_id]["dependency_check"] for task_id in double_ids]
        required = 0 if single_review_mode else min(30 if task_type.startswith("composable_") else 20, len([row for row in sampling if row["task_type"] == task_type]))
        agreement_reports.append({
            "task_type": task_type, "double_reviewed_n": len(double_ids), "required_n": required,
            "raw_agreement": raw_agreement(left, right) if double_ids else "", "cohens_kappa": cohens_kappa(left, right) if double_ids else "",
            "task_label_agreement": raw_agreement(task_left, task_right) if double_ids else "",
            "dependency_edge_agreement": raw_agreement(dep_left, dep_right) if double_ids and task_type.startswith("composable_") else "",
            "status": "not_required_single_review" if single_review_mode else ("complete" if len(double_ids) >= required else "pending"),
        })
    write_csv(out_qa / "reports" / "inter_annotator_agreement.csv", agreement_reports, list(agreement_reports[0]))

    nonkeep = {task_id: decision for task_id, decision in final_decisions.items() if decision != "keep"}
    remove_decisions = {task_id: decision for task_id, decision in nonkeep.items() if decision == "remove"}
    uncertain_decisions = {task_id: decision for task_id, decision in nonkeep.items() if decision == "uncertain"}
    pending_primary = len(expected_primary - completed_primary.keys())
    pending_secondary = len(expected_secondary - completed_secondary.keys())
    attestation_errors = sum(row["code"] in {"MISSING_REVIEWER_ATTESTATION", "INVALID_REVIEWER_ATTESTATION", "INVALID_ADJUDICATOR_ATTESTATION"} for row in issues)
    if issues:
        status = "BLOCKED_INVALID_REVIEW_INPUT"
    elif pending_primary or pending_secondary:
        status = "HUMAN_REVIEW_PENDING"
    elif unresolved_disagreements:
        status = "HUMAN_ADJUDICATION_PENDING"
    elif uncertain_decisions:
        status = "QA_ACTION_REQUIRED"
    else:
        status = "GATE_PASSED"
    gate_passed = status == "GATE_PASSED"
    gate = {
        "stage": "G4",
        "review_policy_id": review_policy["policy_id"],
        "review_mode": review_policy["review_mode"],
        "authoritative_review_round": review_policy["authoritative_round"],
        "secondary_reviews_gating": not single_review_mode,
        "status": status,
        "g4_gate_passed": gate_passed,
        "sampled_unique_rows": len(sampling),
        "inherited_primary_reviews": len(inherited),
        "new_primary_reviews_completed": len(completed_primary),
        "new_primary_reviews_pending": pending_primary,
        "secondary_reviews_completed": len(completed_secondary),
        "secondary_reviews_pending": pending_secondary,
        "disagreements": len(disagreements),
        "adjudications_pending": len(unresolved_disagreements),
        "remove_decisions_scheduled_for_rc1_exclusion": len(remove_decisions),
        "uncertain_final_decisions_requiring_closure": len(uncertain_decisions),
        "nonkeep_final_decisions_requiring_closure": len(uncertain_decisions),
        "validation_errors": len(issues),
        "reviewer_attestation_errors": attestation_errors,
        "ai_final_labels_written": 0,
    }
    write_json(out_qa / "reports" / "QA_STATUS.json", gate)
    write_json(out_qa / "reports" / "review_policy_effective.json", review_policy)
    write_csv(out_qa / "reports" / "QA_VALIDATION_ISSUES.csv", issues, ["severity", "code", "record_id", "detail"])
    write_csv(out_qa / "reports" / "qa_nonkeep_actions.csv", [{"benchmark_task_id": task_id, "final_qa_decision": decision, "required_action": "exclude_at_rc1_freeze" if decision == "remove" else "repair_then_targeted_human_recheck"} for task_id, decision in sorted(nonkeep.items())], ["benchmark_task_id", "final_qa_decision", "required_action"])
    (out_qa / "reports" / "qa_go_no_go.md").write_text(
        f"# G4 QA go/no-go\n\nStatus: **{status}**.\n\nReview mode: `{review_policy['review_mode']}`; authoritative round: `{review_policy['authoritative_round']}`; secondary reviews gating: `{str(not single_review_mode).lower()}`.\n\nPrimary pending: {pending_primary}; secondary pending: {pending_secondary}; adjudications pending: {len(unresolved_disagreements)}; remove decisions scheduled for RC1 exclusion: {len(remove_decisions)}; uncertain decisions requiring closure: {len(uncertain_decisions)}; validation errors: {len(issues)}.\n",
        encoding="utf-8",
    )
    write_json(output / "RUN_STATUS.json", gate)
    input_paths = [review_policy_path, qa_root / "final_task_qa_sampling_manifest.csv", qa_root / "reviewer_attestations.csv", qa_root / "reviews" / "human_reviews_long.csv", adjudication_path, candidate_root / "manifests" / "task_provenance.csv"]
    for task_type in TASK_TYPES:
        input_paths.extend([qa_root / "review_templates" / task_type / "primary_reviews.csv", qa_root / "review_templates" / task_type / "secondary_reviews.csv", candidate_root / "tasks" / f"{task_type}.csv"])
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in input_paths], ["resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    manifest = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        manifest.append({"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
