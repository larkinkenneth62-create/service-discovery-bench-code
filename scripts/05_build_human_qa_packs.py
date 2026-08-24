#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.catalogs import (  # noqa: E402
    resolve_toolbench_static_api,
    resolve_toolbench_static_service,
    stable_json,
)
from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
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
BLIND_FIELDS = [
    "blind_item_id", "blind_pack_id", "benchmark_task_id", "review_round", "task_type", "prediction_target",
    "query_text", "user_visible_context_json", "candidate_display_json", "gold_display_json",
    "acceptable_gold_sets_display_json", "source_catalog_evidence_json", "dependency_graph_json",
    "dependency_evidence_json", "content_fingerprint",
]
SAMPLING_FIELDS = [
    "task_type", "benchmark_task_id", "sampling_stratum", "selection_order", "selection_seed",
    "selection_reason", "source_dataset", "source_subset", "candidate_count", "gold_count",
    "candidate_count_bucket", "repair_status", "common_overlap_risk", "inherited_review_eligible",
    "primary_review_source", "secondary_review_selected", "content_fingerprint",
]
REVIEW_POLICY = {
    "schema_version": "1.0",
    "policy_id": "sdb-v0.1-g4-single-human-review-2026-07-22",
    "review_mode": "single_human_review",
    "authoritative_round": "primary",
    "secondary_review_role": "supplemental_non_gating",
    "require_independent_secondary_reviewer": False,
    "require_adjudication": False,
    "requires_human_reviewer_attestation": True,
    "effective_date": "2026-07-22",
    "authorized_by": "dataset_owner",
    "authorization_source": "Codex task instruction: 不需要这个双审，你修改一下",
    "rationale_zh": "本次 G4 采用单次人工审核。primary 审核结果是唯一权威结论；secondary 仅作为补充审计材料。",
}


def load_jsonl(path: Path, field: str) -> dict[str, dict]:
    result = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                result[row[field]] = row
    return result


def bucket(count: int) -> str:
    if count <= 5:
        return "2-5"
    if count <= 20:
        return "6-20"
    if count <= 50:
        return "21-50"
    if count <= 100:
        return "51-100"
    return "101+"


def round_robin_stratified(items: list[dict], count: int, seed: int) -> list[dict]:
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for item in items:
        key = (item["source_dataset"], item["source_subset"], item["candidate_count_bucket"], item["gold_count"], item["repair_status"])
        strata[key].append(item)
    for key in strata:
        strata[key].sort(key=lambda row: (stable_hash([seed, "stratum_item", row["benchmark_task_id"]]), row["benchmark_task_id"]))
    keys = sorted(strata, key=lambda key: stable_hash([seed, "stratum", key]))
    selected = []
    while len(selected) < count and keys:
        next_keys = []
        for key in keys:
            if strata[key] and len(selected) < count:
                selected.append(strata[key].pop(0))
            if strata[key]:
                next_keys.append(key)
        keys = next_keys
    return selected


def display_service(sid: str, services: dict[str, dict]) -> dict:
    row = services[sid]
    return {"service_id": sid, "service_name": row["canonical_name"], "service_description": row["description"], "provider": row["provider"], "host_or_base_url": row["host_or_base_url"]}


def display_api(aid: str, apis: dict[str, dict], services: dict[str, dict]) -> dict:
    row = apis[aid]
    return {"api_id": aid, "api_name": row["canonical_name"], "api_description": row["description"], "parent_service_id": row["parent_service_id"], "parent_service_name": services[row["parent_service_id"]]["canonical_name"], "endpoint": row["endpoint"], "http_method": row["http_method"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--service-catalog", required=True)
    parser.add_argument("--api-catalog", required=True)
    parser.add_argument("--frozen-composable-review", required=True)
    parser.add_argument("--frozen-resolution", required=True)
    parser.add_argument("--seed", type=int, default=20260719)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    candidate_root = Path(args.candidate_root).resolve()
    service_path = Path(args.service_catalog).resolve()
    api_path = Path(args.api_catalog).resolve()
    frozen_path = Path(args.frozen_composable_review).resolve()
    resolution_path = Path(args.frozen_resolution).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    qa = output / "qa"
    for directory in (qa / "blind_packs", qa / "review_templates", qa / "reviews", qa / "adjudication", qa / "reports", qa / "inherited"):
        directory.mkdir(parents=True, exist_ok=True)

    services = load_jsonl(service_path, "service_id")
    apis = load_jsonl(api_path, "api_id")
    provenance_path = candidate_root / "manifests" / "task_provenance.csv"
    with provenance_path.open("r", encoding="utf-8-sig", newline="") as handle:
        provenance = {row["benchmark_task_id"]: row for row in csv.DictReader(handle)}
    evidence_path = candidate_root / "manifests" / "dependency_evidence.jsonl"
    evidence = load_jsonl(evidence_path, "benchmark_task_id")
    common_path = candidate_root / "manifests" / "common_overlap_hits.csv"
    with common_path.open("r", encoding="utf-8-sig", newline="") as handle:
        common_g2_ids = {row["g2_row_id"] for row in csv.DictReader(handle)}
    with frozen_path.open("r", encoding="utf-8-sig", newline="") as handle:
        frozen = {row["review_item_id"]: row for row in csv.DictReader(handle)}
    service_values = list(services.values())
    api_values = list(apis.values())
    static_service_cache: dict[tuple[str, str], str | None] = {}
    static_api_cache: dict[tuple[str, str, str, str], str | None] = {}

    def frozen_service(value: dict) -> str | None:
        key = (value.get("service_key", ""), value.get("service_name", ""))
        if key not in static_service_cache:
            static_service_cache[key] = resolve_toolbench_static_service(service_values, key[0], key[1])
        return static_service_cache[key]

    def frozen_api_id(value: dict) -> str | None:
        sid = frozen_service(value)
        key = (sid or "", value.get("function_key", ""), value.get("api_name") or value.get("function_name", ""), value.get("method", ""))
        if key not in static_api_cache:
            static_api_cache[key] = resolve_toolbench_static_api(api_values, *key) if sid else None
        return static_api_cache[key]

    task_rows: dict[str, list[dict]] = {}
    all_public: dict[str, dict] = {}
    for task_type in TASK_TYPES:
        path = candidate_root / "tasks" / f"{task_type}.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        task_rows[task_type] = rows
        all_public.update({row["benchmark_task_id"]: row for row in rows})

    inheritance_rows, inherited_reviews = [], []
    inheritance_pass: set[str] = set()
    for task_id, prov in provenance.items():
        if prov["source_subset"] != "frozen_composable":
            continue
        public = all_public[task_id]
        source_provenance = json.loads(prov["source_provenance_json"])
        review_item_id = source_provenance.get("review_item_id", "")
        reviewed = frozen.get(review_item_id)
        reasons = []
        if not reviewed:
            reasons.append("review_item_missing")
        else:
            action = reviewed["composable_release_action"]
            expected_types = {
                "keep_both_levels": {"composable_service_discovery", "composable_api_recommendation"},
                "keep_service_only": {"composable_service_discovery"},
                "reclassify_as_multi": {"multi_service_discovery", "multi_api_recommendation"},
            }.get(action, set())
            if public["task_type"] not in expected_types:
                reasons.append("task_type_changed")
            expected_query = reviewed.get("final_model_facing_query_text") or reviewed["query_text"]
            if public["query_text"] != expected_query:
                reasons.append("query_changed")
            candidate_service_objects = json.loads(reviewed["candidate_services_json"])
            gold_service_objects = json.loads(reviewed["provisional_gold_services_json"])
            expected_services = list(dict.fromkeys(frozen_service(value) for value in candidate_service_objects))
            expected_gold_services = list(dict.fromkeys(frozen_service(value) for value in gold_service_objects))
            expected_apis = list(dict.fromkeys(frozen_api_id(value) for value in json.loads(reviewed["candidate_apis_json"])))
            expected_gold_apis = list(dict.fromkeys(frozen_api_id(value) for value in json.loads(reviewed["provisional_gold_apis_json"])))
            if any(value is None for value in expected_services + expected_gold_services + expected_apis + expected_gold_apis):
                reasons.append("canonical_mapping_failed")
            if public["prediction_target"] == "service":
                if json.loads(public["candidate_services_json"]) != expected_services or json.loads(public["gold_services_json"]) != expected_gold_services:
                    reasons.append("service_candidate_or_gold_changed")
            else:
                expected_parent_metadata = sorted(set(expected_services) | {apis[aid]["parent_service_id"] for aid in expected_apis if aid in apis})
                if json.loads(public["candidate_services_json"]) != expected_parent_metadata:
                    reasons.append("api_parent_metadata_changed")
                if json.loads(public["candidate_apis_json"]) != expected_apis or json.loads(public["gold_apis_json"]) != expected_gold_apis:
                    reasons.append("api_candidate_or_gold_changed")
            if json.loads(public["dependency_graph_json"]) != json.loads(reviewed["dependency_edges_json"]):
                reasons.append("dependency_graph_changed")
            if reviewed["review_status"] != "HUMAN_REVIEW_COMPLETED" or reviewed["adjudicator_type"] != "human_confirmed" or not reviewed["adjudicator_id"]:
                reasons.append("human_reviewer_provenance_invalid")
            if source_provenance.get("prior_review_content_hash") != reviewed["review_content_hash"]:
                reasons.append("prior_review_hash_mismatch")
        status = "INHERITANCE_ELIGIBLE" if not reasons else "REVIEW_INVALIDATED"
        if not reasons:
            inheritance_pass.add(task_id)
        inheritance_rows.append({
            "benchmark_task_id": task_id, "review_item_id": review_item_id, "task_type": public["task_type"],
            "prior_review_content_hash": reviewed["review_content_hash"] if reviewed else "",
            "current_content_fingerprint": prov["review_content_fingerprint"], "semantic_content_equivalence": str(not reasons).lower(),
            "reviewer_id": reviewed["adjudicator_id"] if reviewed else "", "reviewed_at": reviewed["adjudicated_at"] if reviewed else "",
            "adjudicator_type": reviewed["adjudicator_type"] if reviewed else "", "review_status": reviewed["review_status"] if reviewed else "",
            "blindness_provenance_status": "accepted_by_authoritative_frozen_resolution",
            "inheritance_status": status, "invalidation_reasons_json": stable_json(reasons),
        })
        if not reasons:
            target = public["prediction_target"]
            inherited_reviews.append({
                "review_id": f"inherited::{stable_hash([task_id, reviewed['adjudicator_id']])[:24]}",
                "benchmark_task_id": task_id, "review_round": "inherited_primary", "reviewer_id": reviewed["adjudicator_id"],
                "blind_pack_id": f"inherited::{review_item_id}", "content_fingerprint": prov["review_content_fingerprint"],
                "semantic_alignment_check": reviewed["query_gold_chain_alignment"],
                "gold_validity_check": reviewed["service_gold_complete"] if target == "service" else reviewed["api_gold_complete"],
                "candidate_validity_check": reviewed["service_candidate_space_valid"] if target == "service" else reviewed["api_candidate_space_valid"],
                "service_catalog_check": "pass", "task_type_check": "pass",
                "leakage_check": reviewed["service_leakage_final"] if target == "service" else reviewed["api_leakage_final"],
                "dependency_check": reviewed["dependency_edge_valid"] if public["task_type"].startswith("composable_") else "not_applicable_parallel_multi",
                "final_decision": "keep", "error_type": "", "severity": "none",
                "notes": "Inherited from authoritative frozen human review after exact semantic-equivalence audit.",
                "reviewed_at": reviewed["adjudicated_at"],
            })

    write_csv(qa / "inherited" / "composable_review_inheritance.csv", inheritance_rows, [
        "benchmark_task_id", "review_item_id", "task_type", "prior_review_content_hash", "current_content_fingerprint",
        "semantic_content_equivalence", "reviewer_id", "reviewed_at", "adjudicator_type", "review_status",
        "blindness_provenance_status", "inheritance_status", "invalidation_reasons_json",
    ])

    sampling_rows, selected_by_task, secondary_by_task = [], {}, {}
    for task_index, task_type in enumerate(TASK_TYPES):
        rows = task_rows[task_type]
        items = []
        for row in rows:
            prov = provenance[row["benchmark_task_id"]]
            count, gold_count = int(row["candidate_count"]), int(row["gold_count"])
            graph_size = len(json.loads(row["dependency_graph_json"]))
            acceptable = json.loads(row["acceptable_gold_service_sets_json"] if row["prediction_target"] == "service" else row["acceptable_gold_api_sets_json"])
            risk_reasons = []
            if prov["repair_status"] == "reconstructed": risk_reasons.append("reconstructed_candidates")
            if count - gold_count <= 1: risk_reasons.append("candidate_nearly_equals_gold")
            if count > 100: risk_reasons.append("large_candidate_catalog")
            if acceptable: risk_reasons.append("multiple_acceptable_gold_sets")
            if graph_size >= 3: risk_reasons.append("dependency_edge_complexity")
            if prov["g2_row_id"] in common_g2_ids: risk_reasons.append("common_exact_surface_overlap")
            items.append({
                "benchmark_task_id": row["benchmark_task_id"], "source_dataset": row["source_dataset"],
                "source_subset": row["source_subset"], "candidate_count": count, "gold_count": gold_count,
                "candidate_count_bucket": bucket(count), "repair_status": prov["repair_status"],
                "common_overlap_risk": str(prov["g2_row_id"] in common_g2_ids).lower(),
                "risk_score": len(risk_reasons) + (2 if prov["g2_row_id"] in common_g2_ids else 0),
                "risk_reasons": risk_reasons, "content_fingerprint": prov["review_content_fingerprint"],
            })
        task_seed = args.seed + task_index * 1009
        if len(items) < 100:
            selected = sorted(items, key=lambda item: item["benchmark_task_id"])
            strata = {item["benchmark_task_id"]: ("census_inherited" if item["benchmark_task_id"] in inheritance_pass else "census_new_primary") for item in selected}
            reasons = {item["benchmark_task_id"]: "task_frame_under_100_full_census" for item in selected}
        else:
            population = sorted(items, key=lambda item: item["benchmark_task_id"])
            rng = random.Random(task_seed)
            random_part = rng.sample(population, 70)
            used = {item["benchmark_task_id"] for item in random_part}
            remaining = [item for item in population if item["benchmark_task_id"] not in used]
            stratified = round_robin_stratified(remaining, 20, task_seed)
            used.update(item["benchmark_task_id"] for item in stratified)
            remaining = [item for item in remaining if item["benchmark_task_id"] not in used]
            risk = sorted(remaining, key=lambda item: (-item["risk_score"], stable_hash([task_seed, "risk", item["benchmark_task_id"]])))[:10]
            selected = random_part + stratified + risk
            strata = {item["benchmark_task_id"]: "random_representative" for item in random_part}
            strata.update({item["benchmark_task_id"]: "stratified_coverage" for item in stratified})
            strata.update({item["benchmark_task_id"]: "risk_targeted" for item in risk})
            reasons = {item["benchmark_task_id"]: "uniform_without_replacement" for item in random_part}
            reasons.update({item["benchmark_task_id"]: "round_robin_source_bucket_gold_repair_strata" for item in stratified})
            reasons.update({item["benchmark_task_id"]: stable_json(item["risk_reasons"]) for item in risk})
        selected_by_task[task_type] = {item["benchmark_task_id"] for item in selected}
        secondary_required = 30 if task_type.startswith("composable_") else 20
        rng2 = random.Random(task_seed + 37)
        secondary = set(rng2.sample(sorted(selected_by_task[task_type]), min(secondary_required, len(selected_by_task[task_type]))))
        secondary_by_task[task_type] = secondary
        for order, item in enumerate(selected, start=1):
            task_id = item["benchmark_task_id"]
            inherited = task_id in inheritance_pass
            sampling_rows.append({
                "task_type": task_type, "benchmark_task_id": task_id, "sampling_stratum": strata[task_id],
                "selection_order": order, "selection_seed": task_seed, "selection_reason": reasons[task_id],
                "source_dataset": item["source_dataset"], "source_subset": item["source_subset"],
                "candidate_count": item["candidate_count"], "gold_count": item["gold_count"],
                "candidate_count_bucket": item["candidate_count_bucket"], "repair_status": item["repair_status"],
                "common_overlap_risk": item["common_overlap_risk"], "inherited_review_eligible": str(inherited).lower(),
                "primary_review_source": "inherited_human" if inherited else "new_blind_pack",
                "secondary_review_selected": str(task_id in secondary).lower(), "content_fingerprint": item["content_fingerprint"],
            })
    write_csv(qa / "final_task_qa_sampling_manifest.csv", sampling_rows, SAMPLING_FIELDS)
    write_csv(
        qa / "reviewer_attestations.csv",
        [],
        [
            "reviewer_id", "human_reviewer_confirmed", "reviewed_independently",
            "did_not_see_other_reviewer_decisions", "did_not_use_ai_as_final_judge",
            "attested_at", "notes",
        ],
    )

    def blind_row(public: dict, review_round: str) -> dict:
        task_id = public["benchmark_task_id"]
        target = public["prediction_target"]
        candidates = json.loads(public["candidate_services_json"] if target == "service" else public["candidate_apis_json"])
        gold = json.loads(public["gold_services_json"] if target == "service" else public["gold_apis_json"])
        display = [display_service(value, services) for value in candidates] if target == "service" else [display_api(value, apis, services) for value in candidates]
        gold_display = [display_service(value, services) for value in gold] if target == "service" else [display_api(value, apis, services) for value in gold]
        catalog_records = [services[value] for value in json.loads(public["candidate_services_json"])] + [apis[value] for value in json.loads(public["candidate_apis_json"])]
        catalog_evidence = sorted({(record["source_path"], record["source_sha256"], record["catalog_version"]) for record in catalog_records})
        prov = provenance[task_id]
        return {
            "blind_item_id": f"blind::{stable_hash([task_id, review_round])[:24]}",
            "blind_pack_id": f"{public['task_type']}::{review_round}::v0.1",
            "benchmark_task_id": task_id, "review_round": review_round, "task_type": public["task_type"],
            "prediction_target": target, "query_text": public["query_text"], "user_visible_context_json": public["user_visible_context_json"],
            "candidate_display_json": stable_json(display), "gold_display_json": stable_json(gold_display),
            "acceptable_gold_sets_display_json": public["acceptable_gold_service_sets_json"] if target == "service" else public["acceptable_gold_api_sets_json"],
            "source_catalog_evidence_json": stable_json([{"source_path": value[0], "source_sha256": value[1], "catalog_version": value[2]} for value in catalog_evidence]),
            "dependency_graph_json": public["dependency_graph_json"],
            "dependency_evidence_json": stable_json(evidence.get(task_id, {}).get("evidence", [])),
            "content_fingerprint": prov["review_content_fingerprint"],
        }

    def blank_review(blind: dict, review_round: str) -> dict:
        return {
            "review_id": "", "benchmark_task_id": blind["benchmark_task_id"], "review_round": review_round,
            "reviewer_id": "", "blind_pack_id": blind["blind_pack_id"], "content_fingerprint": blind["content_fingerprint"],
            "semantic_alignment_check": "", "gold_validity_check": "", "candidate_validity_check": "",
            "service_catalog_check": "", "task_type_check": "", "leakage_check": "", "dependency_check": "",
            "final_decision": "", "error_type": "", "severity": "", "notes": "", "reviewed_at": "",
        }

    summary_rows = []
    for task_type in TASK_TYPES:
        pack_dir = qa / "blind_packs" / task_type
        template_dir = qa / "review_templates" / task_type
        pack_dir.mkdir(); template_dir.mkdir()
        primary_ids = selected_by_task[task_type] - inheritance_pass
        secondary_ids = secondary_by_task[task_type]
        primary_blind = [blind_row(row, "primary") for row in task_rows[task_type] if row["benchmark_task_id"] in primary_ids]
        secondary_blind = [blind_row(row, "secondary") for row in task_rows[task_type] if row["benchmark_task_id"] in secondary_ids]
        write_csv(pack_dir / "primary_blind_pack.csv", primary_blind, BLIND_FIELDS)
        write_csv(pack_dir / "secondary_blind_pack.csv", secondary_blind, BLIND_FIELDS)
        write_csv(template_dir / "primary_reviews.csv", [blank_review(row, "primary") for row in primary_blind], REVIEW_FIELDS)
        write_csv(template_dir / "secondary_reviews.csv", [blank_review(row, "secondary") for row in secondary_blind], REVIEW_FIELDS)
        selected_inherited = len(selected_by_task[task_type] & inheritance_pass)
        summary_rows.append({
            "task_type": task_type, "frame_rows": len(task_rows[task_type]), "required_unique_primary_reviews": min(100, len(task_rows[task_type])),
            "selected_unique_rows": len(selected_by_task[task_type]), "inherited_primary_reviews": selected_inherited,
            "new_primary_reviews_pending": len(primary_blind), "required_double_reviews": 0,
            "secondary_reviews_pending": 0, "adjudications_pending": 0, "qa_status": "HUMAN_REVIEW_PENDING",
        })

    write_csv(qa / "reviews" / "human_reviews_long.csv", inherited_reviews, REVIEW_FIELDS)
    write_json(qa / "review_policy.json", REVIEW_POLICY)
    write_csv(qa / "reviews" / "human_reviews_summary.csv", summary_rows, list(summary_rows[0]))
    write_csv(qa / "adjudication" / "adjudication.csv", [], REVIEW_FIELDS)
    write_csv(qa / "reports" / "qa_summary_by_task.csv", summary_rows, list(summary_rows[0]))
    write_csv(qa / "reports" / "qa_random_layer_quality.csv", [], ["task_type", "reviewed_n", "kept_n", "pass_rate", "wilson_lower_95", "wilson_upper_95", "status"])
    write_csv(qa / "reports" / "qa_stratified_findings.csv", [], ["task_type", "stratum", "reviewed_n", "finding", "status"])
    write_csv(qa / "reports" / "qa_risk_findings.csv", [], ["task_type", "risk_type", "reviewed_n", "finding", "status"])
    write_csv(qa / "reports" / "inter_annotator_agreement.csv", [], ["task_type", "double_reviewed_n", "raw_agreement", "cohens_kappa", "task_label_agreement", "dependency_edge_agreement", "status"])
    status = {
        "stage": "G4",
        "review_policy_id": REVIEW_POLICY["policy_id"],
        "review_mode": REVIEW_POLICY["review_mode"],
        "authoritative_review_round": REVIEW_POLICY["authoritative_round"],
        "secondary_reviews_gating": False,
        "status": "HUMAN_REVIEW_PENDING",
        "sampling_manifest_rows": len(sampling_rows),
        "inherited_review_rows": len(inherited_reviews),
        "inheritance_invalidated_rows": sum(row["inheritance_status"] != "INHERITANCE_ELIGIBLE" for row in inheritance_rows),
        "new_primary_reviews_pending": sum(int(row["new_primary_reviews_pending"]) for row in summary_rows),
        "secondary_reviews_pending": 0,
        "ai_final_labels_written": 0,
        "g4_gate_passed": False,
    }
    write_json(qa / "reports" / "QA_STATUS.json", status)
    (qa / "reports" / "qa_go_no_go.md").write_text(
        "# G4 QA go/no-go\n\nStatus: **HUMAN_REVIEW_PENDING**.\n\nBlind packs and blank primary templates are ready. One completed human primary review is authoritative under `review_policy.json`; secondary packs are supplemental and non-gating. No AI-generated final labels were written.\n",
        encoding="utf-8",
    )
    (qa / "QA_PROTOCOL.md").write_text(
        "# Human-only QA protocol\n\nThe effective G4 policy is recorded in `review_policy.json`. One completed human `primary_reviews.csv` decision is sufficient and is the sole authoritative QA decision. Allowed final decisions are `keep`, `remove`, and `uncertain`. Secondary reviews are supplemental, non-gating, and do not trigger adjudication or IAA requirements. Every primary reviewer must attest human identity and that AI was not used as the final judge.\n",
        encoding="utf-8",
    )
    write_json(output / "RUN_STATUS.json", status)
    inputs = [candidate_root / "tasks" / f"{name}.csv" for name in TASK_TYPES] + [provenance_path, evidence_path, common_path, service_path, api_path, frozen_path, resolution_path]
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs], ["resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    (output / "RUN_CONFIG.yaml").write_text(f"stage: G4_human_qa_packs\nrandom_seed: {args.seed}\nreview_mode: single_human_review\nsecondary_reviews_gating: false\nprimary_ai_labels_allowed: false\n", encoding="utf-8")
    manifest = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        manifest.append({"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
