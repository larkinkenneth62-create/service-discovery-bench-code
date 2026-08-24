from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = "SDB_UNIFIED_SCHEMA_V0_1"
ROW_SCHEMA_VERSION = "SDB_ROW_SCHEMA_V0_1"
PACKAGE_VERSION = "v0_1_candidate_schema_only"


ENUMS: Dict[str, List[str]] = {
    "source_dataset": ["ToolBench", "MetaTool", "StableToolBench", "ShortcutsBench", "Mixed", "Unknown"],
    "source_branch": [
        "ToolBench-core",
        "MetaTool-single",
        "StableToolBench-solvable",
        "ShortcutsBench-source-check",
        "ShortcutsBench-strict-future",
        "Unknown",
    ],
    "task_type": [
        "single_service_discovery",
        "single_api_recommendation",
        "multi_service_discovery",
        "multi_api_recommendation",
        "composable_service_discovery",
        "composable_api_recommendation",
        "source_check_only",
        "reconstruction_candidate",
        "rewrite_candidate",
        "unknown",
    ],
    "prediction_target_level": ["service", "api", "service_and_api", "source_check_only", "unknown"],
    "task_cardinality": ["single", "multi", "composable", "source_check_only", "unknown"],
    "compositionality_type": ["not_composable", "weak_parallel", "strong_dependency", "dependency_uncertain", "not_applicable"],
    "composable_dependency_status": [
        "dependency_evidence_confirmed",
        "pending_dependency_review",
        "not_strong_dependency",
        "not_applicable",
        "unknown",
    ],
    "query_context_status": ["standalone", "missing_context", "context_uncertain", "not_applicable"],
    "service_catalog_status": ["valid_catalog", "catalog_uncertain", "invalid_catalog", "not_applicable"],
    "service_choice_space_status": [
        "valid_choice_space",
        "singleton_candidate_only",
        "no_negative_distractor",
        "gold_not_in_candidates",
        "candidate_space_invalid",
        "candidate_space_uncertain",
        "not_applicable",
    ],
    "api_choice_space_status": [
        "valid_choice_space",
        "singleton_candidate_only",
        "no_negative_distractor",
        "gold_not_in_candidates",
        "candidate_space_invalid",
        "candidate_space_uncertain",
        "no_api_level_data",
        "not_applicable",
    ],
    "source_policy_decision": [
        "source_specific_keep_candidate",
        "source_specific_keep_candidate_as_is",
        "source_specific_uncertain",
        "source_specific_remove",
        "rewrite_pool_only",
        "leakage_rewrite_pool",
        "candidate_space_reconstruction_pool",
        "composable_dependency_review_pool",
        "source_check_only",
        "not_applicable",
        "unknown",
    ],
    "source_policy_primary_decision": [
        "keep_candidate",
        "keep_candidate_as_is",
        "uncertain",
        "remove",
        "rewrite_pool",
        "reconstruction_pool",
        "composable_review_pool",
        "source_check_only",
        "unknown",
    ],
    "quality_tier": [
        "hq_reviewed_keep",
        "source_specific_keep_candidate",
        "source_specific_keep_candidate_as_is",
        "candidate_pool_flagged",
        "rewrite_pool",
        "reconstruction_pool",
        "composable_review_pool",
        "uncertain_needs_review",
        "remove_excluded",
        "source_check_only",
        "not_applicable",
    ],
    "inclusion_bucket": [
        "main_candidate_pool",
        "hq_evaluated_subset",
        "rewrite_candidate_pool",
        "reconstruction_candidate_pool",
        "composable_dependency_review_pool",
        "excluded_pool",
        "future_source_check_pool",
        "not_applicable",
    ],
    "release_eligibility_status": ["candidate_only", "pending_review", "reviewed_candidate", "blocked", "excluded", "future_work", "not_final"],
    "final_release_status": ["not_final", "candidate_package_only", "eligible_after_review", "final_keep", "final_remove", "unknown"],
    "leakage_status": [
        "no_obvious_leak",
        "leak_uncertain",
        "service_leak_blocking",
        "api_leak_blocking",
        "service_and_api_leak_blocking",
        "not_applicable",
        "unknown",
    ],
    "qa_review_status": ["not_reviewed", "pending_review", "reviewed_complete", "reviewed_partial", "reviewed_draft", "not_applicable"],
    "qa_final_decision": ["keep_for_cleaning_candidate", "uncertain", "remove", "empty", "not_applicable"],
    "reviewer_type": ["human_confirmed", "gpt55pro_assisted", "pilot_model_review", "pending_human_confirmation", "unknown", "not_applicable"],
    "reaudit_disagreement_type": [
        "no_disagreement",
        "major_decision_disagreement",
        "substantive_field_disagreement",
        "schema_noncompliance",
        "provenance_issue",
        "note_only",
        "not_applicable",
    ],
    "proposed_split": ["train", "dev", "test", "unassigned", "not_applicable"],
    "split_status": ["not_split", "split_candidate", "split_validated", "split_blocked", "not_applicable"],
    "baseline_ready_status": ["not_ready", "candidate_ready_after_review", "final_ready_after_split", "not_applicable"],
}


FIELD_GROUPS: Dict[str, List[Tuple[str, str, str]]] = {
    "schema_identity": [
        ("schema_version", "string", "Fixed schema id."),
        ("row_schema_version", "string", "Fixed row schema id."),
        ("package_version", "string", "Schema package version; not a final dataset version."),
        ("candidate_row_id", "string", "Globally unique preview row id."),
        ("canonical_task_id", "string", "Future dedup/split key candidate, not identical to source_task_id."),
        ("canonical_query_id", "string", "Canonical query id candidate."),
        ("canonical_source_id", "string", "Stable source id candidate."),
        ("source_dataset", "enum", "Original source dataset."),
        ("source_branch", "enum", "Source-specific branch."),
        ("source_file", "string", "Source file path."),
        ("source_row_id", "string", "Source row id."),
        ("source_task_id", "string", "Source task id."),
        ("source_query_id", "string", "Source query id."),
        ("source_group", "string", "Source group such as G1/G2/G3."),
        ("source_subgroup", "string", "Optional source subgroup."),
    ],
    "task_definition": [
        ("task_type", "enum", "Canonical task type."),
        ("task_type_source", "string", "Raw/source task type."),
        ("task_type_canonical", "enum", "Normalized task type."),
        ("prediction_target_level", "enum", "Service/API target level."),
        ("task_cardinality", "enum", "Single/multi/composable/source-check."),
        ("compositionality_type", "enum", "Dependency structure."),
        ("composable_dependency_status", "enum", "Composable dependency review state."),
        ("service_or_api_level", "string", "Human-readable level summary."),
        ("task_intent_type", "string", "Optional domain intent."),
        ("domain_primary", "string", "Optional primary domain."),
        ("domain_secondary_json", "json", "Optional secondary domains."),
        ("language", "string", "Query language."),
        ("query_text", "string", "User natural-language request."),
        ("query_text_zh", "string", "Optional Chinese translation."),
        ("query_text_normalized", "string", "Normalized query for future dedup."),
        ("query_context_status", "enum", "Whether query is standalone."),
    ],
    "services": [
        ("candidate_services_json", "json", "JSON array of candidate service objects."),
        ("gold_services_json", "json", "JSON array of gold service objects."),
        ("candidate_service_count", "integer", "Number of candidate services."),
        ("gold_service_count", "integer", "Number of gold services."),
        ("candidate_service_ids_json", "json", "JSON array of candidate service ids."),
        ("gold_service_ids_json", "json", "JSON array of gold service ids."),
        ("candidate_service_names_json", "json", "JSON array of candidate service names."),
        ("gold_service_names_json", "json", "JSON array of gold service names."),
        ("service_catalog_id", "string", "Optional catalog id."),
        ("service_catalog_size", "integer", "Service catalog size when available."),
        ("service_catalog_status", "enum", "Catalog validity."),
        ("gold_service_in_candidate_set", "string", "yes/no/unknown."),
        ("service_choice_space_status", "enum", "Service candidate choice-space validity."),
    ],
    "apis": [
        ("candidate_apis_json", "json", "JSON array of candidate API objects."),
        ("gold_apis_json", "json", "JSON array of gold API objects."),
        ("candidate_api_count", "integer", "Number of candidate APIs."),
        ("gold_api_count", "integer", "Number of gold APIs."),
        ("candidate_api_ids_json", "json", "JSON array of candidate API ids."),
        ("gold_api_ids_json", "json", "JSON array of gold API ids."),
        ("candidate_api_names_json", "json", "JSON array of candidate API names."),
        ("gold_api_names_json", "json", "JSON array of gold API names."),
        ("api_choice_space_status", "enum", "API candidate choice-space validity."),
        ("gold_api_in_candidate_set", "string", "yes/no/unknown."),
        ("api_parent_services_json", "json", "Parent service metadata for APIs."),
    ],
    "source_policy": [
        ("source_policy_name", "string", "Source-specific policy name."),
        ("source_policy_version", "string", "Source-specific policy version."),
        ("source_policy_decision", "enum", "Source-specific raw decision."),
        ("source_policy_label", "string", "Source-specific policy label."),
        ("source_policy_primary_decision", "enum", "Normalized primary policy decision."),
        ("source_policy_secondary_labels_json", "json", "Optional secondary policy labels."),
        ("source_policy_blocking_rules_json", "json", "Blocking rules."),
        ("source_policy_warning_rules_json", "json", "Warning rules."),
        ("source_policy_evidence_json", "json", "Evidence objects."),
        ("source_policy_confidence", "string", "Optional confidence."),
        ("source_policy_notes", "string", "Policy notes."),
        ("source_policy_requires_human_review", "string", "yes/no/unknown."),
    ],
    "quality_inclusion": [
        ("quality_tier", "enum", "Candidate quality tier, not final clean label."),
        ("inclusion_bucket", "enum", "Planning bucket, not train/dev/test."),
        ("release_eligibility_status", "enum", "Release eligibility status."),
        ("final_release_status", "enum", "Always not_final in this round."),
        ("exclusion_reason_json", "json", "Reasons for exclusion/blocking."),
        ("known_limitations_json", "json", "Known limitations."),
        ("requires_rewrite", "string", "yes/no/unknown."),
        ("requires_candidate_space_reconstruction", "string", "yes/no/unknown."),
        ("requires_composable_dependency_review", "string", "yes/no/unknown."),
        ("requires_human_review", "string", "yes/no/unknown."),
    ],
    "validity": [
        ("leakage_status", "enum", "Overall leakage status."),
        ("service_leak_status", "string", "Service leak status."),
        ("api_leak_status", "string", "API leak status."),
        ("candidate_space_status", "string", "Candidate-space status."),
        ("gold_alignment_status", "string", "Gold semantic/capability alignment."),
        ("capability_coverage_status", "string", "Capability coverage status."),
        ("semantic_alignment_status", "string", "Semantic alignment status."),
        ("task_type_validity_status", "string", "Task type validity."),
        ("duplicate_status", "string", "Dedup status."),
        ("split_leakage_risk_status", "string", "Future split leakage risk."),
    ],
    "qa": [
        ("qa_review_status", "enum", "QA status."),
        ("qa_final_decision", "enum", "QA decision, not final release label."),
        ("qa_semantic_alignment_check", "string", "QA semantic check."),
        ("qa_capability_coverage_check", "string", "QA capability check."),
        ("qa_candidate_validity_check", "string", "QA candidate validity."),
        ("qa_service_catalog_check", "string", "QA service catalog check."),
        ("qa_task_type_check", "string", "QA task type check."),
        ("qa_leakage_check", "string", "QA leakage check."),
        ("qa_error_type", "string", "QA error type."),
        ("qa_severity", "string", "QA severity."),
        ("qa_notes", "string", "QA notes."),
        ("reviewer_type", "enum", "Reviewer provenance."),
        ("reviewer_id", "string", "Reviewer id."),
        ("reviewed_at", "string", "Review timestamp."),
        ("review_round_id", "string", "Review round id."),
        ("review_source_file", "string", "Review file path."),
    ],
    "reaudit": [
        ("reaudit_available", "string", "yes/no."),
        ("reaudit_review_status", "string", "Re-audit status."),
        ("reaudit_final_decision", "string", "Re-audit decision, separate from qa_final_decision."),
        ("reaudit_semantic_alignment_check", "string", "Re-audit semantic check."),
        ("reaudit_capability_coverage_check", "string", "Re-audit capability check."),
        ("reaudit_candidate_validity_check", "string", "Re-audit candidate validity."),
        ("reaudit_leakage_check", "string", "Re-audit leakage check."),
        ("reaudit_error_type", "string", "Re-audit error type."),
        ("reaudit_severity", "string", "Re-audit severity."),
        ("reaudit_disagreement_type", "enum", "Re-audit disagreement type."),
        ("reaudit_disagreement_severity", "string", "Re-audit disagreement severity."),
        ("reaudit_disagreement_reason_detailed", "string", "Detailed disagreement reason."),
        ("reaudit_recommended_correction_json", "json", "Recommended correction object."),
        ("reaudit_notes", "string", "Re-audit notes."),
        ("reaudit_reviewer_type", "enum", "Re-audit reviewer type."),
        ("reaudit_reviewer_id", "string", "Re-audit reviewer id."),
        ("reaudit_reviewed_at", "string", "Re-audit timestamp."),
        ("reaudit_source_file", "string", "Re-audit file path."),
    ],
    "dedup_split": [
        ("task_signature", "string", "Task signature."),
        ("query_signature", "string", "Query signature."),
        ("canonical_dedup_key", "string", "Canonical dedup key."),
        ("source_query_family_id", "string", "Future source query family id."),
        ("duplicate_group_id", "string", "Future duplicate group id."),
        ("split_group_id", "string", "Future split group id."),
        ("proposed_split", "enum", "Always unassigned in this round."),
        ("split_status", "enum", "Always not_split in this round."),
        ("split_notes", "string", "Split notes."),
    ],
    "baseline_future": [
        ("baseline_ready_status", "enum", "Always not_ready in this round."),
        ("evaluation_target_json", "json", "Future evaluation target."),
        ("recommended_metrics_json", "json", "Future baseline metrics."),
        ("baseline_notes", "string", "Baseline notes."),
    ],
}

CANONICAL_FIELDS: List[str] = [name for fields in FIELD_GROUPS.values() for name, _, _ in fields]
JSON_FIELDS = {name for fields in FIELD_GROUPS.values() for name, typ, _ in fields if typ == "json"}
INT_FIELDS = {name for fields in FIELD_GROUPS.values() for name, typ, _ in fields if typ == "integer"}
ENUM_FIELD_TO_VALUES = {field: ENUMS[field] for field in CANONICAL_FIELDS if field in ENUMS}


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def ensure_dirs(project_root: Path) -> None:
    for d in [
        project_root / "docs" / "schema",
        project_root / "outputs" / "unified_schema_v0_1",
        project_root / "outputs" / "unified_schema_v0_1" / "schema",
        project_root / "outputs" / "unified_schema_v0_1" / "previews",
        project_root / "outputs" / "unified_schema_v0_1" / "validation",
        project_root / "outputs" / "unified_schema_v0_1" / "examples",
    ]:
        d.mkdir(parents=True, exist_ok=True)


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def read_csv_rows(path: Path, limit: Optional[int] = None) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return rows


def read_csv_header(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f).fieldnames or [])


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({k for row in rows for k in row})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def try_json(value: str) -> Tuple[bool, Any]:
    if value is None or str(value).strip() == "":
        return False, None
    try:
        return True, json.loads(value)
    except Exception:
        return False, None


def json_array(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        ok, parsed = try_json(value)
        if ok:
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        return [value]
    return [value]


def as_json_array(value: Any) -> str:
    if isinstance(value, str):
        ok, parsed = try_json(value)
        if ok:
            value = parsed
        elif value.strip() == "":
            value = []
        else:
            value = [value]
    if value is None:
        value = []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        value = [value]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def names_from_json(value: str, name_keys: List[str]) -> List[str]:
    names: List[str] = []
    for item in json_array(value):
        if isinstance(item, dict):
            for k in name_keys:
                if item.get(k):
                    names.append(str(item[k]))
                    break
        elif item not in (None, ""):
            names.append(str(item))
    return names


def repack_services(value: str, source_dataset: str) -> str:
    out = []
    for item in json_array(value):
        if isinstance(item, dict):
            out.append(
                {
                    "service_id": item.get("service_id") or item.get("id") or item.get("source_service_id") or "",
                    "service_name": item.get("service_name") or item.get("name") or "",
                    "service_description": item.get("service_description") or item.get("description") or "",
                    "source_dataset": source_dataset,
                    "source_service_id": item.get("service_id") or item.get("id") or "",
                    "aliases_json": [],
                    "category": item.get("category_name") or item.get("category") or "",
                    "metadata": {k: v for k, v in item.items() if k not in {"service_id", "service_name", "service_description", "name", "description"}},
                }
            )
        elif item not in (None, ""):
            out.append(
                {
                    "service_id": "",
                    "service_name": str(item),
                    "service_description": "",
                    "source_dataset": source_dataset,
                    "source_service_id": "",
                    "aliases_json": [],
                    "category": "",
                    "metadata": {},
                }
            )
    return as_json_array(out)


def repack_apis(value: str, source_dataset: str) -> str:
    out = []
    for item in json_array(value):
        if isinstance(item, dict):
            out.append(
                {
                    "api_id": item.get("api_id") or item.get("id") or "",
                    "api_name": item.get("api_name") or item.get("name") or "",
                    "api_description": item.get("api_description") or item.get("description") or "",
                    "parent_service_id": item.get("service_id") or "",
                    "parent_service_name": item.get("service_name") or item.get("parent_service_name") or "",
                    "method": item.get("method") or "",
                    "endpoint": item.get("endpoint") or item.get("url") or "",
                    "source_api_id": item.get("api_id") or item.get("id") or "",
                    "metadata": {k: v for k, v in item.items() if k not in {"api_id", "api_name", "api_description", "service_id", "service_name", "method", "endpoint", "url"}},
                    "source_dataset": source_dataset,
                }
            )
        elif item not in (None, ""):
            out.append(
                {
                    "api_id": "",
                    "api_name": str(item),
                    "api_description": "",
                    "parent_service_id": "",
                    "parent_service_name": "",
                    "method": "",
                    "endpoint": "",
                    "source_api_id": "",
                    "metadata": {},
                    "source_dataset": source_dataset,
                }
            )
    return as_json_array(out)


def count_json(value: str) -> int:
    return len(json_array(value))


def norm_task_type(source_task_type: str, branch: str) -> Tuple[str, str, str, str]:
    s = (source_task_type or "").lower()
    if branch == "ShortcutsBench-source-check":
        return "source_check_only", "source_check_only", "source_check_only", "not_applicable"
    if "composable" in s or "g3" in s:
        return "composable_service_discovery", "service_and_api", "composable", "dependency_uncertain"
    if "multi_api" in s:
        return "multi_api_recommendation", "api", "multi", "weak_parallel"
    if "multi" in s or "g2" in s:
        return "multi_service_discovery", "service", "multi", "weak_parallel"
    if "api" in s:
        return "single_api_recommendation", "api", "single", "not_composable"
    if "single" in s or "g1" in s or "external" in s:
        return "single_service_discovery", "service", "single", "not_composable"
    return "unknown", "unknown", "unknown", "dependency_uncertain"


def policy_primary(decision: str) -> str:
    mapping = {
        "source_specific_keep_candidate": "keep_candidate",
        "source_specific_keep_candidate_as_is": "keep_candidate_as_is",
        "source_specific_uncertain": "uncertain",
        "source_specific_remove": "remove",
        "rewrite_pool_only": "rewrite_pool",
        "leakage_rewrite_pool": "rewrite_pool",
        "candidate_space_reconstruction_pool": "reconstruction_pool",
        "composable_dependency_review_pool": "composable_review_pool",
        "source_check_only": "source_check_only",
        "still_clean_candidate": "keep_candidate",
        "downgrade_to_uncertain": "uncertain",
        "dryrun_remove": "remove",
    }
    return mapping.get(decision or "", "unknown")


def quality_from_primary(primary: str, decision: str = "") -> Tuple[str, str]:
    if primary == "keep_candidate":
        return "source_specific_keep_candidate", "main_candidate_pool"
    if primary == "keep_candidate_as_is":
        return "source_specific_keep_candidate_as_is", "main_candidate_pool"
    if primary == "rewrite_pool":
        return "rewrite_pool", "rewrite_candidate_pool"
    if primary == "reconstruction_pool":
        return "reconstruction_pool", "reconstruction_candidate_pool"
    if primary == "composable_review_pool":
        return "composable_review_pool", "composable_dependency_review_pool"
    if primary == "remove":
        return "remove_excluded", "excluded_pool"
    if primary == "source_check_only":
        return "source_check_only", "future_source_check_pool"
    return "uncertain_needs_review", "not_applicable"


def find_inputs(project_root: Path) -> Dict[str, Optional[Path]]:
    candidates: Dict[str, List[Path]] = {
        "toolbench_core": [
            project_root / "outputs" / "policy_v1_5f_tightening_dryrun" / "clean_candidates_v1_4c_with_v1_5f_annotations.csv",
            project_root / "outputs" / "full_clean_dryrun_v1_4c" / "full_clean_task_trace_v1_4c.csv",
        ],
        "metatool_policy": [
            project_root / "outputs" / "external_source_policy_v0_2" / "metatool" / "metatool_single_service_with_leakage_policy_v0_2.csv",
        ],
        "metatool_reviewed": [
            project_root / "outputs" / "external_qa_v0_2" / "metatool" / "metatool_leakage_policy_review_items_v0_2_reviewed.csv",
            project_root / "outputs" / "external_qa_v0_2" / "metatool" / "metatool_leakage_policy_review_items_v0_2.csv",
        ],
        "metatool_reaudit": [
            project_root / "outputs" / "manual_reaudit" / "metatool_v0_2_reaudit_by_gpt55pro_schema.csv",
            project_root / "outputs" / "external_qa_v0_2" / "metatool" / "metatool_v0_2_reaudit_by_gpt55pro_schema.csv",
        ],
        "stable_policy": [
            project_root / "outputs" / "external_source_policy_v0_2" / "stabletoolbench" / "stabletoolbench_solvable_with_filter_policy_v0_2.csv",
        ],
        "stable_reviewed": [
            project_root / "outputs" / "external_qa_v0_2" / "stabletoolbench" / "stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv",
            project_root / "outputs" / "external_qa_v0_2" / "stabletoolbench" / "stabletoolbench_filter_policy_review_items_v0_2.csv",
        ],
        "shortcuts_source": [
            project_root / "external_sources" / "ShortcutsBench" / "generated_success_queries.json.extracted",
            project_root / "external_sources" / "ShortcutsBench" / "_extracted_tmp" / "generated_success_queries" / "generated_success_queries.json",
            project_root / "external_sources" / "ShortcutsBench" / "generated_success_queries.json",
            project_root / "external_sources" / "ShortcutsBench" / "1_final_detailed_records_filter_apis_leq_30.json.extracted",
            project_root / "external_sources" / "ShortcutsBench" / "1_final_detailed_records_filter_apis_leq_30.json",
        ],
    }
    found: Dict[str, Optional[Path]] = {}
    for key, paths in candidates.items():
        found[key] = next((p for p in paths if p.exists()), None)
    return found


def empty_canonical_row() -> Dict[str, str]:
    row = {field: "" for field in CANONICAL_FIELDS}
    row.update(
        {
            "schema_version": SCHEMA_VERSION,
            "row_schema_version": ROW_SCHEMA_VERSION,
            "package_version": PACKAGE_VERSION,
            "domain_secondary_json": "[]",
            "candidate_services_json": "[]",
            "gold_services_json": "[]",
            "candidate_service_ids_json": "[]",
            "gold_service_ids_json": "[]",
            "candidate_service_names_json": "[]",
            "gold_service_names_json": "[]",
            "candidate_apis_json": "[]",
            "gold_apis_json": "[]",
            "candidate_api_ids_json": "[]",
            "gold_api_ids_json": "[]",
            "candidate_api_names_json": "[]",
            "gold_api_names_json": "[]",
            "api_parent_services_json": "[]",
            "source_policy_secondary_labels_json": "[]",
            "source_policy_blocking_rules_json": "[]",
            "source_policy_warning_rules_json": "[]",
            "source_policy_evidence_json": "[]",
            "exclusion_reason_json": "[]",
            "known_limitations_json": "[]",
            "reaudit_recommended_correction_json": "[]",
            "evaluation_target_json": "[]",
            "recommended_metrics_json": as_json_array(["Recall@1", "Recall@3", "Recall@5", "MRR", "nDCG@K", "Precision@K", "Exact Set Match", "multi-label F1"]),
            "final_release_status": "not_final",
            "proposed_split": "unassigned",
            "split_status": "not_split",
            "baseline_ready_status": "not_ready",
            "query_context_status": "standalone",
            "language": "en",
        }
    )
    return row


def summarize_counts(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    return dict(Counter(str(r.get(key, "")) for r in rows))
