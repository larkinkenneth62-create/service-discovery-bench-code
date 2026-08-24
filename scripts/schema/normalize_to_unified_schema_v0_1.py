from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from unified_schema_v0_1_common import (
    CANONICAL_FIELDS,
    ENUM_FIELD_TO_VALUES,
    ENUMS,
    FIELD_GROUPS,
    PACKAGE_VERSION,
    SCHEMA_VERSION,
    as_json_array,
    count_json,
    empty_canonical_row,
    ensure_dirs,
    find_inputs,
    names_from_json,
    norm_task_type,
    now_iso,
    policy_primary,
    quality_from_primary,
    read_csv_rows,
    repack_apis,
    repack_services,
    sha1_text,
    write_csv,
    write_json,
    write_jsonl,
)


def source_decision(row: Dict[str, str], branch: str) -> str:
    if branch == "ToolBench-core":
        return row.get("dryrun_decision_v1_5f") or row.get("dryrun_decision_v1_4c") or row.get("dryrun_decision_v1_4b") or "unknown"
    if branch == "MetaTool-single":
        return row.get("metatool_policy_decision") or "unknown"
    if branch == "StableToolBench-solvable":
        return row.get("stable_policy_decision") or "unknown"
    if branch == "ShortcutsBench-source-check":
        return "source_check_only"
    return "unknown"


def source_label(row: Dict[str, str], branch: str) -> str:
    if branch == "ToolBench-core":
        return row.get("dryrun_bucket_v1_5f") or row.get("dryrun_bucket_v1_4c") or ""
    if branch == "MetaTool-single":
        return row.get("metatool_leakage_policy_label") or ""
    if branch == "StableToolBench-solvable":
        return row.get("stable_policy_label") or ""
    return ""


def normalize_row(row: Dict[str, str], branch: str, source_file: Path, index: int, reaudit_by_task: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    source_dataset = row.get("source_dataset") or ("ShortcutsBench" if branch.startswith("ShortcutsBench") else "Unknown")
    task_id = row.get("task_id") or row.get("source_task_id") or row.get("id") or f"{branch}_{index + 1}"
    source_group = row.get("source_group") or row.get("stable_group") or ""
    task_source = row.get("task_type") or row.get("task_type_guess") or source_group
    task_type, target_level, cardinality, comp_type = norm_task_type(task_source or source_group, branch)

    out = empty_canonical_row()
    out["candidate_row_id"] = f"{branch.replace('-', '_')}::{task_id}::{index + 1}"
    out["canonical_task_id"] = f"{branch}::{task_id}"
    out["canonical_query_id"] = sha1_text((row.get("query_text") or "") + "::" + branch)
    out["canonical_source_id"] = f"{branch}::{row.get('source_row_id') or row.get('source_query_id') or index + 1}"
    out["source_dataset"] = source_dataset
    out["source_branch"] = branch
    out["source_file"] = str(source_file)
    out["source_row_id"] = row.get("source_row_id") or row.get("source_row_index") or str(index + 1)
    out["source_task_id"] = task_id
    out["source_query_id"] = row.get("source_query_id") or row.get("source_row_id") or ""
    out["source_group"] = source_group
    out["source_subgroup"] = row.get("stable_group") or ""
    out["task_type"] = task_type
    out["task_type_source"] = task_source
    out["task_type_canonical"] = task_type
    out["prediction_target_level"] = target_level
    out["task_cardinality"] = cardinality
    out["compositionality_type"] = comp_type
    out["composable_dependency_status"] = "pending_dependency_review" if comp_type == "dependency_uncertain" else "not_applicable"
    out["service_or_api_level"] = target_level
    out["language"] = "en"
    out["query_text"] = row.get("query_text") or row.get("name") or row.get("description") or ""
    out["query_text_zh"] = row.get("query_text_zh") or row.get("query_text_zh_auto") or ""
    out["query_text_normalized"] = " ".join(out["query_text"].lower().split())
    out["query_context_status"] = "standalone" if out["query_text"] else "missing_context"

    cand_services_raw = row.get("candidate_services_json") or "[]"
    gold_services_raw = row.get("gold_services_json") or "[]"
    cand_apis_raw = row.get("candidate_apis_json") or row.get("available_tools_or_apis_json") or "[]"
    gold_apis_raw = row.get("gold_apis_json") or row.get("gold_tools_or_apis_json") or "[]"

    out["candidate_services_json"] = repack_services(cand_services_raw, source_dataset)
    out["gold_services_json"] = repack_services(gold_services_raw, source_dataset)
    out["candidate_apis_json"] = repack_apis(cand_apis_raw, source_dataset)
    out["gold_apis_json"] = repack_apis(gold_apis_raw, source_dataset)

    cand_service_names = names_from_json(out["candidate_services_json"], ["service_name", "name"])
    gold_service_names = names_from_json(out["gold_services_json"], ["service_name", "name"])
    cand_api_names = names_from_json(out["candidate_apis_json"], ["api_name", "name"])
    gold_api_names = names_from_json(out["gold_apis_json"], ["api_name", "name"])
    out["candidate_service_names_json"] = as_json_array(cand_service_names)
    out["gold_service_names_json"] = as_json_array(gold_service_names)
    out["candidate_api_names_json"] = as_json_array(cand_api_names)
    out["gold_api_names_json"] = as_json_array(gold_api_names)
    out["candidate_service_ids_json"] = as_json_array([])
    out["gold_service_ids_json"] = as_json_array([])
    out["candidate_api_ids_json"] = as_json_array([])
    out["gold_api_ids_json"] = as_json_array([])
    out["candidate_service_count"] = row.get("candidate_service_count") or str(count_json(out["candidate_services_json"]))
    out["gold_service_count"] = row.get("gold_service_count") or str(count_json(out["gold_services_json"]))
    out["candidate_api_count"] = row.get("candidate_api_count") or str(count_json(out["candidate_apis_json"]))
    out["gold_api_count"] = row.get("gold_api_count") or str(count_json(out["gold_apis_json"]))
    out["service_catalog_size"] = row.get("service_catalog_size") or out["candidate_service_count"]
    out["service_catalog_status"] = "valid_catalog" if int(out["candidate_service_count"] or 0) > 0 else "catalog_uncertain"
    out["gold_service_in_candidate_set"] = row.get("gold_in_candidate_services") or ("yes" if set(gold_service_names).issubset(set(cand_service_names)) and gold_service_names else "unknown")
    out["gold_api_in_candidate_set"] = row.get("gold_in_candidate_apis") or ("yes" if set(gold_api_names).issubset(set(cand_api_names)) and gold_api_names else "unknown")
    out["service_choice_space_status"] = choice_space(out["candidate_service_count"], out["gold_service_count"])
    out["api_choice_space_status"] = choice_space(out["candidate_api_count"], out["gold_api_count"], api=True)
    out["api_parent_services_json"] = as_json_array(sorted({item.get("parent_service_name", "") for item in json.loads(out["candidate_apis_json"]) if isinstance(item, dict) and item.get("parent_service_name")}))

    decision = source_decision(row, branch)
    primary = policy_primary(decision)
    quality, bucket = quality_from_primary(primary, decision)
    out["source_policy_name"] = source_policy_name(branch)
    out["source_policy_version"] = source_policy_version(branch)
    out["source_policy_decision"] = normalize_policy_decision(decision, primary)
    out["source_policy_label"] = source_label(row, branch)
    out["source_policy_primary_decision"] = primary
    out["source_policy_blocking_rules_json"] = row.get("metatool_blocking_rules_json") or row.get("stable_blocking_rules_json") or row.get("blocking_reasons_v1_5f") or row.get("blocking_reasons_v1_4c") or "[]"
    out["source_policy_warning_rules_json"] = row.get("metatool_warning_rules_json") or row.get("stable_warning_rules_json") or row.get("warning_reasons_v1_5f") or row.get("warning_reasons_v1_4c") or "[]"
    out["source_policy_evidence_json"] = row.get("metatool_policy_evidence_json") or row.get("stable_policy_evidence_json") or "[]"
    out["source_policy_notes"] = row.get("metatool_policy_notes") or row.get("stable_policy_notes") or ""
    out["source_policy_requires_human_review"] = row.get("metatool_requires_human_review") or row.get("stable_requires_human_review") or row.get("requires_final_qa_v1_5f") or row.get("requires_final_qa_v1_4c") or "unknown"

    out["quality_tier"] = quality
    out["inclusion_bucket"] = bucket
    out["release_eligibility_status"] = release_status(primary)
    out["final_release_status"] = "not_final"
    out["requires_rewrite"] = row.get("metatool_rewrite_needed") or row.get("stable_rewrite_needed") or ("yes" if primary == "rewrite_pool" else "no")
    out["requires_candidate_space_reconstruction"] = row.get("stable_reconstruction_needed") or ("yes" if primary == "reconstruction_pool" else "no")
    out["requires_composable_dependency_review"] = row.get("stable_requires_composable_dependency_review") or row.get("requires_composable_dependency_check") or ("yes" if primary == "composable_review_pool" else "no")
    out["requires_human_review"] = out["source_policy_requires_human_review"]

    out["leakage_status"] = leakage_status(row, branch)
    out["service_leak_status"] = service_leak_status(row, out["leakage_status"])
    out["api_leak_status"] = api_leak_status(row, out["leakage_status"])
    out["candidate_space_status"] = row.get("candidate_space_status") or out["service_choice_space_status"]
    out["gold_alignment_status"] = row.get("v12_gold_set_integrity_status") or ""
    out["capability_coverage_status"] = row.get("v12_capability_coverage_pred") or ""
    out["semantic_alignment_status"] = row.get("v12_semantic_alignment_pred") or ""
    out["task_type_validity_status"] = row.get("task_type_eligibility_status") or row.get("qa_task_type_check") or ""
    out["duplicate_status"] = "unknown"
    out["split_leakage_risk_status"] = "unknown"

    map_qa(out, row, source_file)
    map_reaudit(out, reaudit_by_task.get(task_id), source_file)

    out["task_signature"] = row.get("task_signature") or sha1_text(out["query_text"] + out["gold_services_json"] + out["gold_apis_json"])
    out["query_signature"] = row.get("query_signature") or sha1_text(out["query_text"])
    out["canonical_dedup_key"] = sha1_text(out["query_text_normalized"] + out["gold_service_names_json"] + out["gold_api_names_json"])
    out["proposed_split"] = "unassigned"
    out["split_status"] = "not_split"
    out["split_notes"] = "Schema preview only; no split generated."
    out["baseline_ready_status"] = "not_ready"
    out["evaluation_target_json"] = as_json_array([target_level])
    out["baseline_notes"] = "Baseline is blocked until final clean dataset and split exist."
    return {k: str(out.get(k, "")) for k in CANONICAL_FIELDS}


def choice_space(candidate_count: str, gold_count: str, api: bool = False) -> str:
    try:
        c, g = int(candidate_count or 0), int(gold_count or 0)
    except Exception:
        return "candidate_space_uncertain"
    if api and c == 0 and g == 0:
        return "no_api_level_data"
    if c == 0 or g == 0:
        return "candidate_space_uncertain"
    if c == 1:
        return "singleton_candidate_only"
    if c <= g:
        return "no_negative_distractor"
    return "valid_choice_space"


def source_policy_name(branch: str) -> str:
    return {
        "ToolBench-core": "toolbench_v1_5f_or_v1_4c_dryrun_policy",
        "MetaTool-single": "metatool_leakage_policy_v0_2",
        "StableToolBench-solvable": "stabletoolbench_filter_policy_v0_2",
        "ShortcutsBench-source-check": "shortcutsbench_source_check_policy",
    }.get(branch, "unknown")


def source_policy_version(branch: str) -> str:
    return {
        "ToolBench-core": "v1_5f_if_present_else_v1_4c",
        "MetaTool-single": "v0_2",
        "StableToolBench-solvable": "v0_2",
        "ShortcutsBench-source-check": "source_check_only",
    }.get(branch, "unknown")


def normalize_policy_decision(decision: str, primary: str) -> str:
    if decision in ENUMS["source_policy_decision"]:
        return decision
    return {
        "keep_candidate": "source_specific_keep_candidate",
        "keep_candidate_as_is": "source_specific_keep_candidate_as_is",
        "uncertain": "source_specific_uncertain",
        "remove": "source_specific_remove",
        "rewrite_pool": "rewrite_pool_only",
        "reconstruction_pool": "candidate_space_reconstruction_pool",
        "composable_review_pool": "composable_dependency_review_pool",
        "source_check_only": "source_check_only",
    }.get(primary, "unknown")


def release_status(primary: str) -> str:
    if primary in {"remove"}:
        return "excluded"
    if primary in {"rewrite_pool", "reconstruction_pool", "composable_review_pool", "uncertain"}:
        return "pending_review"
    if primary in {"keep_candidate", "keep_candidate_as_is"}:
        return "candidate_only"
    if primary == "source_check_only":
        return "future_work"
    return "not_final"


def leakage_status(row: Dict[str, str], branch: str) -> str:
    label = row.get("metatool_leakage_policy_label") or row.get("stable_policy_label") or row.get("leakage_check_status") or ""
    label_low = label.lower()
    if "api_leak" in label_low:
        return "api_leak_blocking"
    if "service_leak" in label_low:
        return "service_leak_blocking"
    if "no_blocking" in label_low or "no_service_name_leak" in label_low:
        return "no_obvious_leak"
    if row.get("query_mentions_any_gold_api") == "1":
        return "api_leak_blocking"
    if row.get("query_mentions_any_gold_service") == "1":
        return "service_leak_blocking"
    return "unknown" if branch == "ToolBench-core" else "no_obvious_leak"


def service_leak_status(row: Dict[str, str], overall: str) -> str:
    if overall in {"service_leak_blocking", "service_and_api_leak_blocking"}:
        return "service_leak_blocking"
    if row.get("query_mentions_any_gold_service") == "1":
        return "service_leak_uncertain"
    return "no_service_leak"


def api_leak_status(row: Dict[str, str], overall: str) -> str:
    if overall in {"api_leak_blocking", "service_and_api_leak_blocking"}:
        return "api_leak_blocking"
    if row.get("query_mentions_any_gold_api") == "1":
        return "api_leak_uncertain"
    return "no_api_leak"


def map_qa(out: Dict[str, str], row: Dict[str, str], source_file: Path) -> None:
    final = row.get("qa_final_decision") or ""
    out["qa_review_status"] = "reviewed_complete" if final else "not_reviewed"
    out["qa_final_decision"] = final or "empty"
    for field in [
        "qa_semantic_alignment_check",
        "qa_capability_coverage_check",
        "qa_candidate_validity_check",
        "qa_service_catalog_check",
        "qa_task_type_check",
        "qa_leakage_check",
        "qa_error_type",
        "qa_severity",
        "qa_notes",
        "reviewer_id",
        "reviewed_at",
    ]:
        out[field] = row.get(field, "")
    out["reviewer_type"] = infer_reviewer_type(row)
    out["review_round_id"] = "source_policy_review_v0_2" if "external_qa_v0_2" in str(source_file) else "source_policy_or_dryrun"
    out["review_source_file"] = str(source_file)


def infer_reviewer_type(row: Dict[str, str]) -> str:
    rid = (row.get("reviewer_id") or "").lower()
    if "gpt" in rid:
        return "gpt55pro_assisted"
    if row.get("qa_final_decision"):
        return "pending_human_confirmation" if not rid else "human_confirmed"
    return "not_applicable"


def map_reaudit(out: Dict[str, str], rr: Optional[Dict[str, str]], source_file: Path) -> None:
    if not rr:
        out["reaudit_available"] = "no"
        out["reaudit_review_status"] = "not_applicable"
        out["reaudit_disagreement_type"] = "not_applicable"
        out["reaudit_reviewer_type"] = "not_applicable"
        return
    out["reaudit_available"] = "yes"
    out["reaudit_review_status"] = "reviewed_complete" if rr.get("reaudit_final_decision") else "reviewed_partial"
    for field in [
        "reaudit_final_decision",
        "reaudit_semantic_alignment_check",
        "reaudit_capability_coverage_check",
        "reaudit_candidate_validity_check",
        "reaudit_leakage_check",
        "reaudit_error_type",
        "reaudit_severity",
        "reaudit_disagreement_reason_detailed",
        "reaudit_notes",
        "reaudit_reviewer_id",
        "reaudit_reviewed_at",
    ]:
        out[field] = rr.get(field, "")
    out["reaudit_disagreement_type"] = "major_decision_disagreement" if str(rr.get("reaudit_disagreement_with_user", "")).lower() == "yes" else "no_disagreement"
    out["reaudit_disagreement_severity"] = rr.get("reaudit_disagreement_level", "")
    out["reaudit_recommended_correction_json"] = as_json_array(
        {
            "recommended_action": rr.get("reaudit_recommended_action", ""),
            "quality_tier": rr.get("reaudit_quality_tier", ""),
            "inclusion_bucket": rr.get("reaudit_inclusion_bucket", ""),
            "disagreement_fields": rr.get("reaudit_disagreement_fields_json", "[]"),
        }
    )
    out["reaudit_reviewer_type"] = rr.get("reaudit_reviewer_type") or "gpt55pro_assisted"
    out["reaudit_source_file"] = "outputs/manual_reaudit/metatool_v0_2_reaudit_by_gpt55pro_schema.csv"


def load_reaudit(inputs: Dict[str, Optional[Path]]) -> Dict[str, Dict[str, str]]:
    path = inputs.get("metatool_reaudit")
    if not path:
        return {}
    rows = read_csv_rows(path)
    return {r.get("task_id", ""): r for r in rows if r.get("task_id")}


def normalize_branch(branch: str, path: Path, preview_rows: int, full_preview: bool, reaudit_by_task: Dict[str, Dict[str, str]]) -> List[Dict[str, str]]:
    limit = None if full_preview else preview_rows
    rows = read_csv_rows(path, limit=limit) if path.suffix.lower() == ".csv" else load_shortcuts_json(path, limit)
    return [normalize_row(r, branch, path, i, reaudit_by_task) for i, r in enumerate(rows)]


def load_shortcuts_json(path: Path, limit: Optional[int]) -> List[Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    records = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list) else [data])
    out = []
    for i, item in enumerate(records[:limit]):
        if isinstance(item, dict):
            query = item.get("query") or item.get("description") or item.get("name") or json.dumps(item, ensure_ascii=False)[:500]
            out.append({"task_id": f"ShortcutsBench_{i+1}", "source_dataset": "ShortcutsBench", "task_type": "source_check_only", "query_text": query, "metadata_json": json.dumps(item, ensure_ascii=False)})
    return out


def write_schema_docs(project_root: Path, inputs: Dict[str, Optional[Path]], summary: Dict[str, Any]) -> None:
    docs = project_root / "docs" / "schema"
    out = project_root / "outputs" / "unified_schema_v0_1"
    schema_dir = out / "schema"
    scope = {
        "generated_at": now_iso(),
        "objective": "design unified schema, field dictionary, enum registry, source mapping, validation scripts, and schema preview only",
        "non_goals": ["no final dataset", "no merge", "no split", "no baseline", "no training", "no Qwen", "no external API"],
        "canonical_path": "task-level canonical row; candidate-level rows are construction intermediates only",
        "source_branches": ["ToolBench-core", "MetaTool-single", "StableToolBench-solvable", "ShortcutsBench-source-check"],
        "review_layers": ["source policy layer", "human/GPT-assisted QA layer", "optional re-audit layer", "final release layer remains not_final"],
    }
    write_json(out / "unified_schema_scope_lock.json", scope)
    (docs / "UNIFIED_SCHEMA_SCOPE_LOCK_V0_1.md").write_text(
        "# Unified Schema Scope Lock V0.1\n\n"
        f"Generated at: {now_iso()}\n\n"
        "This round defines schema artifacts only. It does not create a final clean dataset, merge sources, split, baseline, train, call Qwen, or call external APIs.\n\n"
        "Canonical path: task-level rows, one row per user task / service recommendation instance. Candidate/gold services/APIs are JSON arrays. Candidate-level rows remain construction intermediates.\n\n"
        "Branches: ToolBench-core, MetaTool-single, StableToolBench-solvable, ShortcutsBench-source-check.\n\n"
        "Review layers are preserved separately: source policy, QA, optional re-audit, and final release status. All preview rows remain `final_release_status=not_final`.\n",
        encoding="utf-8",
    )

    field_rows = [
        {"field_group": group, "field_name": name, "field_type": typ, "description": desc, "allowed_values": json.dumps(ENUM_FIELD_TO_VALUES.get(name, []), ensure_ascii=False)}
        for group, fields in FIELD_GROUPS.items()
        for name, typ, desc in fields
    ]
    write_csv(schema_dir / "service_discovery_bench_unified_schema_v0_1.fields.csv", field_rows)
    schema_obj = {
        "schema_version": SCHEMA_VERSION,
        "package_version": PACKAGE_VERSION,
        "generated_at": now_iso(),
        "fields": field_rows,
        "required_fields": ["schema_version", "candidate_row_id", "canonical_task_id", "source_dataset", "source_branch", "task_type", "query_text", "final_release_status"],
        "json_fields": [r["field_name"] for r in field_rows if r["field_type"] == "json"],
        "enum_fields": ENUM_FIELD_TO_VALUES,
    }
    write_json(schema_dir / "service_discovery_bench_unified_schema_v0_1.json", schema_obj)
    write_json(schema_dir / "service_discovery_bench_unified_schema_v0_1.enums.json", ENUM_FIELD_TO_VALUES)
    write_json(schema_dir / "enum_registry_unified_v0_1.json", {"generated_at": now_iso(), "enums": ENUMS, "mapping_examples": enum_mapping_examples()})

    (docs / "SCHEMA_UNIFIED_V0_1.md").write_text(schema_markdown(field_rows), encoding="utf-8")
    (docs / "FIELD_DICTIONARY_UNIFIED_V0_1.md").write_text(field_dictionary_markdown(field_rows), encoding="utf-8")
    (docs / "ENUM_REGISTRY_UNIFIED_V0_1.md").write_text(enum_markdown(), encoding="utf-8")
    write_mapping(project_root, inputs)
    write_json_object_schemas(project_root)
    write_decision_log(docs)
    write_readme(project_root)
    write_go_no_go(project_root, summary)


def enum_mapping_examples() -> Dict[str, Dict[str, str]]:
    return {
        "MetaTool": {
            "source_specific_keep_candidate": "keep_candidate",
            "rewrite_pool_only": "rewrite_pool",
            "source_specific_remove": "remove",
        },
        "StableToolBench": {
            "source_specific_keep_candidate_as_is": "keep_candidate_as_is",
            "leakage_rewrite_pool": "rewrite_pool",
            "candidate_space_reconstruction_pool": "reconstruction_pool",
            "composable_dependency_review_pool": "composable_review_pool",
            "source_specific_remove": "remove",
        },
        "ToolBench v1.5f": {
            "still_clean_candidate": "keep_candidate",
            "downgrade_to_uncertain": "uncertain",
            "dryrun_remove": "remove",
        },
        "QA": {
            "keep_for_cleaning_candidate": "reviewed candidate, not final_keep",
            "uncertain": "uncertain_needs_review",
            "remove": "remove_excluded",
        },
    }


def schema_markdown(field_rows: List[Dict[str, str]]) -> str:
    lines = ["# Unified Schema V0.1", "", f"Generated at: {now_iso()}", "", "This schema defines task-level canonical rows. It is not a final dataset.", ""]
    for group in FIELD_GROUPS:
        lines.extend([f"## {group}", "", "| field | type | description |", "|---|---|---|"])
        for row in field_rows:
            if row["field_group"] == group:
                lines.append(f"| `{row['field_name']}` | {row['field_type']} | {row['description']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def field_dictionary_markdown(field_rows: List[Dict[str, str]]) -> str:
    lines = ["# Field Dictionary Unified V0.1", "", f"Generated at: {now_iso()}", "", "| group | field | type | allowed values | description |", "|---|---|---|---|---|"]
    for row in field_rows:
        lines.append(f"| {row['field_group']} | `{row['field_name']}` | {row['field_type']} | `{row['allowed_values']}` | {row['description']} |")
    return "\n".join(lines) + "\n"


def enum_markdown() -> str:
    lines = ["# Enum Registry Unified V0.1", "", f"Generated at: {now_iso()}", ""]
    for name, values in sorted(ENUMS.items()):
        lines.append(f"## {name}")
        for v in values:
            lines.append(f"- `{v}`")
        lines.append("")
    lines.append("## Mapping Examples")
    for source, mapping in enum_mapping_examples().items():
        lines.append(f"### {source}")
        for k, v in mapping.items():
            lines.append(f"- `{k}` -> `{v}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_mapping(project_root: Path, inputs: Dict[str, Optional[Path]]) -> None:
    rows = []
    def add(branch: str, pattern: str, src: str, dst: str, t: str, rule: str = "", req: str = "yes", default: str = "", notes: str = "") -> None:
        rows.append(
            {
                "source_branch": branch,
                "source_file_pattern": pattern,
                "source_column": src,
                "canonical_field": dst,
                "transform_type": t,
                "transform_rule": rule,
                "required_for_branch": req,
                "default_value_if_missing": default,
                "validation_rule": "",
                "notes": notes,
            }
        )
    for branch, pattern in [
        ("ToolBench-core", "outputs/policy_v1_5f_tightening_dryrun/*.csv"),
        ("MetaTool-single", "outputs/external_source_policy_v0_2/metatool/*.csv"),
        ("StableToolBench-solvable", "outputs/external_source_policy_v0_2/stabletoolbench/*.csv"),
        ("ShortcutsBench-source-check", "external_sources/ShortcutsBench/*.json"),
    ]:
        for src, dst in [
            ("task_id", "source_task_id"),
            ("query_text", "query_text"),
            ("candidate_services_json", "candidate_services_json"),
            ("gold_services_json", "gold_services_json"),
            ("candidate_apis_json", "candidate_apis_json"),
            ("gold_apis_json", "gold_apis_json"),
            ("task_type", "task_type_source"),
            ("task_type_guess", "task_type_source"),
            ("source_group", "source_group"),
            ("stable_group", "source_group"),
            ("query_signature", "query_signature"),
            ("task_signature", "task_signature"),
        ]:
            add(branch, pattern, src, dst, "rename" if src != dst else "direct_copy", notes="Optional if source does not expose this field.")
    add("MetaTool-single", "metatool_single_service_with_leakage_policy_v0_2.csv", "metatool_policy_decision", "source_policy_decision", "enum_map", "MetaTool v0.2 decision copied to source_policy_decision")
    add("MetaTool-single", "metatool_single_service_with_leakage_policy_v0_2.csv", "source_tool_or_plugin_name", "gold_service_names_json", "json_parse_and_repack", "Stored as source metadata and service name when needed")
    add("MetaTool-single", "*reaudit*metatool*.csv", "reaudit_*", "reaudit_*", "optional_passthrough", "Second-opinion layer; never overwrites qa_*", req="no", notes="User-supplied re-audit file integrated if present.")
    add("StableToolBench-solvable", "stabletoolbench_solvable_with_filter_policy_v0_2.csv", "stable_policy_decision", "source_policy_decision", "enum_map", "Use exclusive stable_policy_decision only")
    add("ToolBench-core", "clean_candidates_v1_4c_with_v1_5f_annotations.csv", "dryrun_decision_v1_5f or dryrun_decision_v1_4c", "source_policy_decision", "enum_map", "Map dry-run policy to canonical primary decision")
    out = project_root / "outputs" / "unified_schema_v0_1"
    docs = project_root / "docs" / "schema"
    write_csv(out / "source_to_canonical_mapping_matrix.csv", rows)
    write_json(out / "source_to_canonical_mapping_matrix.json", {"generated_at": now_iso(), "mappings": rows})
    md = ["# Source To Canonical Mapping Matrix V0.1", "", f"Generated at: {now_iso()}", "", "| source_branch | source_column | canonical_field | transform_type | rule | notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['source_branch']} | `{r['source_column']}` | `{r['canonical_field']}` | {r['transform_type']} | {r['transform_rule']} | {r['notes']} |")
    (docs / "SOURCE_TO_CANONICAL_MAPPING_MATRIX_V0_1.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def write_json_object_schemas(project_root: Path) -> None:
    schemas = {
        "service_object": ["service_id", "service_name", "service_description", "source_dataset", "source_service_id", "aliases_json", "category", "metadata"],
        "api_object": ["api_id", "api_name", "api_description", "parent_service_id", "parent_service_name", "method", "endpoint", "source_api_id", "metadata"],
        "policy_rule_object": ["rule_id", "rule_name", "severity", "triggered", "evidence", "notes"],
        "policy_evidence_object": ["evidence_type", "evidence_text", "evidence_source_field", "supports_decision", "notes"],
        "limitation_object": ["limitation_type", "description", "source", "notes"],
        "exclusion_reason_object": ["reason_type", "severity", "description", "source_rule", "notes"],
        "metric_object": ["metric_name", "k", "target_level", "notes"],
        "domain_label_object": ["domain", "confidence", "source", "notes"],
    }
    write_json(project_root / "outputs" / "unified_schema_v0_1" / "schema" / "canonical_json_object_schemas_v0_1.json", {"generated_at": now_iso(), "schemas": schemas})
    lines = ["# Canonical JSON Object Schemas V0.1", "", f"Generated at: {now_iso()}", ""]
    for name, fields in schemas.items():
        lines.append(f"## {name}")
        for field in fields:
            lines.append(f"- `{field}`")
        lines.append("")
    lines.append("These schemas can be checked by Python standard-library JSON parsing; jsonschema is optional and not required.")
    (project_root / "docs" / "schema" / "CANONICAL_JSON_OBJECT_SCHEMAS_V0_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_decision_log(docs: Path) -> None:
    decisions = [
        "Task-level is canonical because benchmark evaluation asks one query to choose service/API candidates.",
        "Candidate-level rows are construction intermediates and would overcount one user task.",
        "Service-level and API-level must be separated because they test different prediction targets.",
        "Six task types remain the canonical taxonomy for future v0.1 planning.",
        "Source policy, QA, and re-audit are separate layers and must not overwrite each other.",
        "MetaTool, StableToolBench, and ToolBench QA are source-specific and must not be averaged as one pass rate.",
        "StableToolBench uses exclusive stable_policy_decision, not non-exclusive pool counts.",
        "rewrite_pool/reconstruction_pool/composable_review_pool are not clean labels.",
        "final_release_status defaults to not_final to prevent accidental release claims.",
        "This round does not split or run baseline because no final clean dataset exists.",
        "Reviewer provenance is explicit so GPT-assisted re-audit is not mistaken for human final.",
        "GPT-assisted re-audit is a second-opinion layer and requires later human-confirmed reconciliation before becoming formal QA.",
    ]
    lines = ["# Unified Schema Decision Log V0.1", "", f"Generated at: {now_iso()}", ""]
    for i, item in enumerate(decisions, 1):
        lines.append(f"{i}. {item}")
    (docs / "UNIFIED_SCHEMA_DECISION_LOG_V0_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_readme(project_root: Path) -> None:
    text = f"""# Unified Schema V0.1 README

Generated at: {now_iso()}

This package defines a schema and preview normalization path for ServiceDiscoveryBench. It is not a final dataset.

## Commands

```powershell
python scripts/schema/inventory_source_fields_v0_1.py --project-root "C:\\Users\\CpeterX\\Documents\\IEEE邀稿"
python scripts/schema/normalize_to_unified_schema_v0_1.py --project-root "C:\\Users\\CpeterX\\Documents\\IEEE邀稿" --preview-rows 100
python scripts/schema/validate_unified_schema_v0_1.py --input outputs/unified_schema_v0_1/previews/metatool_unified_schema_preview.csv
```

## Boundaries

- preview CSVs are schema checks only;
- reviewed CSV and re-audit CSV remain separate layers;
- no source merge is final;
- no split, baseline, training, Qwen, or external API was run.

## Next Step

Use this schema to plan a v0.1 candidate package only after reviewed CSV and re-audit decisions are reconciled.
"""
    (project_root / "docs" / "schema" / "README_UNIFIED_SCHEMA_V0_1.md").write_text(text, encoding="utf-8")
    (project_root / "outputs" / "unified_schema_v0_1" / "README.md").write_text(text, encoding="utf-8")


def write_go_no_go(project_root: Path, summary: Dict[str, Any]) -> None:
    go = {
        "generated_at": now_iso(),
        "unified_schema_defined": True,
        "field_dictionary_generated": True,
        "enum_registry_generated": True,
        "source_mapping_matrix_generated": True,
        "validation_script_generated": (project_root / "scripts" / "schema" / "validate_unified_schema_v0_1.py").exists(),
        "normalization_preview_generated": True,
        "re_audit_layer_integrated_if_present": "true" if summary.get("reaudit_rows", 0) else "not_present",
        "can_use_schema_for_v0_1_candidate_package_planning": True,
        "can_call_any_preview_final_dataset": False,
        "can_merge_sources_now": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "qwen_called": False,
        "external_api_called": False,
        "recommended_next_step": "use unified schema to build source-complete v0.1-candidate package after reviewed CSV/re-audit decisions are reconciled",
        "preview_summary": summary,
    }
    write_json(project_root / "outputs" / "unified_schema_v0_1" / "unified_schema_go_no_go_v0_1.json", go)
    lines = ["# Unified Schema Go / No-Go V0.1", "", f"Generated at: {now_iso()}", ""]
    for k, v in go.items():
        if k != "preview_summary":
            lines.append(f"- `{k}`: `{v}`")
    (project_root / "docs" / "schema" / "UNIFIED_SCHEMA_GO_NO_GO_V0_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_examples(project_root: Path, previews: Dict[str, List[Dict[str, str]]]) -> None:
    examples = []
    for branch, rows in previews.items():
        take = rows[:3]
        for r in take:
            examples.append({"source_branch": branch, "source_row": r.get("source_task_id", ""), "canonical_fields": {k: r.get(k, "") for k in ["candidate_row_id", "task_type", "source_policy_decision", "quality_tier", "inclusion_bucket", "final_release_status"]}, "why": "Preview row preserves source policy/QA layers; final_release_status remains not_final."})
    write_jsonl(project_root / "outputs" / "unified_schema_v0_1" / "examples" / "unified_schema_examples.jsonl", examples)
    lines = ["# Unified Schema Row Examples V0.1", "", f"Generated at: {now_iso()}", ""]
    for ex in examples:
        lines.append(f"## {ex['source_branch']} / {ex['source_row']}")
        lines.append(f"- canonical fields: `{json.dumps(ex['canonical_fields'], ensure_ascii=False)}`")
        lines.append(f"- why: {ex['why']}")
        lines.append("")
    (project_root / "docs" / "schema" / "UNIFIED_SCHEMA_ROW_EXAMPLES_V0_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize current sources into unified schema v0.1 preview CSVs.")
    parser.add_argument("--project-root", default=".", help="Project root path.")
    parser.add_argument("--preview-rows", type=int, default=100, help="Max preview rows per branch.")
    parser.add_argument("--full-preview", action="store_true", help="Generate full normalized preview. Still not final dataset.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    ensure_dirs(project_root)
    inputs = find_inputs(project_root)
    reaudit_by_task = load_reaudit(inputs)
    branch_specs = {
        "ToolBench-core": inputs.get("toolbench_core"),
        "MetaTool-single": inputs.get("metatool_policy"),
        "StableToolBench-solvable": inputs.get("stable_policy"),
        "ShortcutsBench-source-check": inputs.get("shortcuts_source"),
    }
    previews: Dict[str, List[Dict[str, str]]] = {}
    missing = []
    for branch, path in branch_specs.items():
        if not path:
            previews[branch] = []
            missing.append(branch)
            continue
        previews[branch] = normalize_branch(branch, path, args.preview_rows, args.full_preview, reaudit_by_task)

    preview_dir = project_root / "outputs" / "unified_schema_v0_1" / "previews"
    filename_map = {
        "ToolBench-core": "toolbench_core_unified_schema_preview.csv",
        "MetaTool-single": "metatool_unified_schema_preview.csv",
        "StableToolBench-solvable": "stabletoolbench_unified_schema_preview.csv",
        "ShortcutsBench-source-check": "shortcutsbench_source_check_unified_schema_preview.csv",
    }
    combined: List[Dict[str, str]] = []
    for branch, rows in previews.items():
        write_csv(preview_dir / filename_map[branch], rows, CANONICAL_FIELDS)
        combined.extend(rows[: min(25, len(rows))])
    write_csv(preview_dir / "combined_preview_sample_unified_schema.csv", combined, CANONICAL_FIELDS)
    summary = {
        "generated_at": now_iso(),
        "preview_rows_per_branch": args.preview_rows,
        "full_preview": bool(args.full_preview),
        "missing_branches": missing,
        "toolbench_preview_rows": len(previews["ToolBench-core"]),
        "metatool_preview_rows": len(previews["MetaTool-single"]),
        "stabletoolbench_preview_rows": len(previews["StableToolBench-solvable"]),
        "shortcutsbench_preview_rows": len(previews["ShortcutsBench-source-check"]),
        "reaudit_rows": len(reaudit_by_task),
        "can_generate_final_clean_dataset_now": False,
    }
    write_json(preview_dir / "normalization_preview_summary.json", summary)
    write_schema_docs(project_root, inputs, summary)
    write_examples(project_root, previews)
    write_preview_report(project_root, summary, inputs)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def write_preview_report(project_root: Path, summary: Dict[str, Any], inputs: Dict[str, Optional[Path]]) -> None:
    lines = [
        "# Unified Schema Normalization Preview Report V0.1",
        "",
        f"Generated at: {now_iso()}",
        "",
        "This is a schema preview only. It is not a merged dataset and not a final clean dataset.",
        "",
        "## Inputs",
    ]
    for k, p in inputs.items():
        lines.append(f"- `{k}`: `{p if p else 'missing'}`")
    lines.extend(
        [
            "",
            "## Preview Rows",
            f"- ToolBench-core: {summary['toolbench_preview_rows']}",
            f"- MetaTool-single: {summary['metatool_preview_rows']}",
            f"- StableToolBench-solvable: {summary['stabletoolbench_preview_rows']}",
            f"- ShortcutsBench-source-check: {summary['shortcutsbench_preview_rows']}",
            f"- re-audit layer rows available: {summary['reaudit_rows']}",
            "",
            "## Fixed Boundaries",
            "- can_generate_final_clean_dataset_now=false",
            "- can_merge_sources_now=false",
            "- can_create_split_now=false",
            "- can_run_baseline_now=false",
            "- can_train_model_now=false",
            "- qwen_called=false",
            "- external_api_called=false",
        ]
    )
    (project_root / "docs" / "schema" / "UNIFIED_SCHEMA_NORMALIZATION_PREVIEW_REPORT_V0_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

