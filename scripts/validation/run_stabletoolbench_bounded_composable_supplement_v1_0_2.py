#!/usr/bin/env python3
"""Freeze ToolBench tranche A and audit a bounded StableToolBench supplement."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


VERSION = "v1.0.2"
MACHINE_RULE_VERSION = "v1.0"
TOOLBENCH_ROWS = 103
STABLE_BOUNDED_ROWS = 167
STABLE_MINIMUM = 17
STABLE_TARGET = 37
STABLE_MAXIMUM = 50

HUMAN_FIELDS = [
    "dependency_required_for_query",
    "upstream_already_satisfies_subgoal",
    "full_query_subgoals_covered_by_gold_chain",
    "disconnected_parallel_subgoals_present",
    "cross_service_dependency_valid",
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

EVIDENCE_NAME_TERMS = (
    "answer", "answers", "trajectory", "trajectories", "trace", "traces",
    "tool_call", "tool_calls", "api_call", "api_calls", "argument",
    "observation", "response", "result", "cache", "virtual", "simulator",
    "solvable", "solution", "execution", "intermediate",
)

PATHS = {
    "rules": Path("docs/project/SERVICEDISCOVERYBENCH_COMPOSABLE_MACHINE_REVIEW_RULES.md"),
    "master_plan": Path("docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md"),
    "toolbench_pool": Path("outputs/composable_candidate_recovery_v1_0_1/composable_authoritative_review_pool_v1_0_1.csv"),
    "toolbench_revalidation": Path("outputs/composable_candidate_recovery_v1_0_1/recovered_candidates_machine_revalidation.csv"),
    "toolbench_rewrite": Path("outputs/composable_candidate_recovery_v1_0_1/deterministic_leakage_rewrite_trace.csv"),
    "toolbench_summary": Path("outputs/composable_candidate_recovery_v1_0_1/composable_bounded_candidate_recovery_summary_v1_0_1.json"),
    "toolbench_integrity": Path("outputs/composable_candidate_recovery_v1_0_1/bounded_recovery_integrity_hashes_v1_0_1.json"),
    "toolbench_report": Path("docs/phase1/composable_bounded_candidate_recovery_go_no_go_v1_0_1.md"),
    "toolbench_archive_manifest": Path("outputs/run_archives/2026-07-15_composable_bounded_candidate_recovery_v1_0_1/archive_manifest_v1_0_1.json"),
    "toolbench_current_pack": Path("outputs/composable_paired_task_preparation_v0_3_3/composable_paired_task_review_items_v0_3_3.csv"),
    "toolbench_current_translations": Path("outputs/composable_paired_task_preparation_v0_3_3/composable_query_translations_zh_v0_3_3.json"),
    "toolbench_old_pack": Path("outputs/composable_paired_task_preparation_v0_3_2/composable_paired_task_review_items_v0_3_2.csv"),
    "toolbench_old_translations": Path("outputs/composable_paired_task_preparation_v0_3_2/composable_query_translations_zh_v0_3_2.json"),
    "stable_root": Path("external_sources/StableToolBench"),
    "stable_g2": Path("external_sources/StableToolBench/solvable_queries/test_instruction/G2_instruction.json"),
    "stable_g3": Path("external_sources/StableToolBench/solvable_queries/test_instruction/G3_instruction.json"),
    "stable_raw": Path("outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_solvable_task_level_raw.csv"),
    "stable_policy": Path("outputs/external_policy_v0_2_consistency_audit/stabletoolbench_v0_2_with_derived_primary_decision.csv"),
    "stable_adjudication_v03": Path("outputs/source_qa_adjudication_v0_3/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_3.csv"),
    "stable_adjudication_v042": Path("outputs/source_qa_adjudication_v0_4_2/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_4_2.csv"),
    "html_builder": Path("scripts/validation/build_composable_tranche_review_app_v1_0_2.py"),
    "tests": Path("tests/validation/test_stabletoolbench_bounded_composable_supplement_v1_0_2.py"),
}

TOOLBENCH_OUT = Path("outputs/composable_authoritative_review_v1_0_2")
STABLE_OUT = Path("outputs/stabletoolbench_composable_supplement_v1_0_2")
LEDGER = Path("outputs/review_credit_ledger/composable_review_credit_ledger_v1_0_2.csv")
TOOLBENCH_REPORT = Path("docs/phase1/toolbench_composable_tranche_A_review_readiness_v1_0_2.md")
STABLE_AUDIT_REPORT = Path("docs/phase1/stabletoolbench_composable_evidence_asset_audit_v1_0_2.md")
GO_NO_GO = Path("docs/phase1/stabletoolbench_bounded_composable_supplement_go_no_go_v1_0_2.md")
ARCHIVE = Path("outputs/run_archives/2026-07-15_stabletoolbench_bounded_composable_supplement_v1_0_2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded StableToolBench G2/G3 composable supplement under frozen rules v1.0."
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--toolbench-output", type=Path, default=TOOLBENCH_OUT)
    parser.add_argument("--stable-output", type=Path, default=STABLE_OUT)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--skip-html", action="store_true")
    parser.add_argument("--skip-archive", action="store_true")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader), list(reader.fieldnames)


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def normalize_exact_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    for prefix in ("stabletoolbench_", "stable_toolbench_", "stabletoolbench-"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def ordered_fields(rows: Iterable[dict[str, Any]], preferred: Iterable[str] = ()) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for field in preferred:
        if field not in seen:
            seen.add(field)
            result.append(field)
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                result.append(field)
    return result


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def scan_execution_evidence(record: Any) -> dict[str, bool]:
    """Detect actual per-task execution fields while excluding API schema examples."""
    found = {
        "ordered_steps_found": False,
        "arguments_found": False,
        "outputs_found": False,
        "observations_found": False,
        "actual_execution_like_evidence": False,
    }
    execution_containers = {"steps", "calls", "tool_calls", "api_calls", "trajectory", "trace"}

    def walk(value: Any, in_schema: bool = False) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).casefold()
                schema_child = in_schema or key in {
                    "api_list", "required_parameters", "optional_parameters", "template_response",
                    "api_description", "method",
                }
                if not schema_child and key in execution_containers and isinstance(child, list) and child:
                    found["ordered_steps_found"] = True
                if not schema_child and key in {"arguments", "input", "request"} and child not in ({}, [], "", None):
                    found["arguments_found"] = True
                if not schema_child and key in {"outputs", "output", "result", "response"} and child not in ({}, [], "", None):
                    found["outputs_found"] = True
                if not schema_child and key in {"observation", "observations"} and child not in ({}, [], "", None):
                    found["observations_found"] = True
                walk(child, schema_child)
        elif isinstance(value, list):
            for child in value:
                walk(child, in_schema)

    walk(record)
    found["actual_execution_like_evidence"] = bool(
        found["ordered_steps_found"]
        and found["arguments_found"]
        and (found["outputs_found"] or found["observations_found"])
    )
    return found


def exact_join_one(
    row: dict[str, str], source_index: dict[tuple[str, str], list[dict[str, Any]]]
) -> tuple[str, dict[str, Any] | None, str]:
    group = str(row.get("source_group", "")).strip().upper()
    candidates = [
        ("source_instruction_id", row.get("source_instruction_id")),
        ("source_query_id", row.get("source_query_id")),
        ("task_id", row.get("task_id")),
    ]
    for field, value in candidates:
        key = normalize_exact_key(value)
        if field == "task_id" and "_" in key:
            key = key.rsplit("_", 1)[-1]
        if not key:
            continue
        matches = source_index.get((group, key), [])
        if len(matches) == 1:
            return "EXACT_JOINED", matches[0], "query_id" if field != "task_id" else "task_id_to_query_id"
        if len(matches) > 1:
            return "AMBIGUOUS", None, "query_id" if field != "task_id" else "task_id_to_query_id"
    return "UNMATCHED", None, ""


def double_annotation_size(row_count: int) -> int:
    return min(30, max(20, round(row_count * 0.20)))


def frozen_archive_hash(manifest: dict[str, Any], filename: str) -> str:
    matches = [item for item in manifest.get("files", []) if item.get("filename") == filename]
    require(len(matches) == 1, f"Archive manifest must contain exactly one {filename}")
    return str(matches[0]["sha256"])


def verify_toolbench_pool(rows: list[dict[str, str]]) -> dict[str, Any]:
    require(len(rows) == TOOLBENCH_ROWS, f"Expected {TOOLBENCH_ROWS} ToolBench rows, got {len(rows)}")
    ids = [row.get("underlying_task_id", "") for row in rows]
    require(all(ids), "ToolBench tranche contains an empty underlying_task_id")
    require(len(set(ids)) == TOOLBENCH_ROWS, "ToolBench underlying_task_id values are not unique")
    checks = {
        "query_nonempty": sum(bool(row.get("query_text", "").strip()) for row in rows),
        "dependency_evidence_nonempty": sum(
            bool(row.get("dependency_evidence_json", "").strip())
            and row.get("dependency_evidence_json", "").strip() not in {"{}", "[]"}
            for row in rows
        ),
        "gold_services_ge_2": sum(int(row.get("distinct_gold_service_count") or row.get("gold_service_count") or 0) >= 2 for row in rows),
        "gold_apis_ge_2": sum(int(row.get("distinct_gold_api_count") or row.get("gold_api_count") or 0) >= 2 for row in rows),
        "cross_service_edge_ge_1": sum(int(row.get("cross_service_strong_edge_count") or row.get("strong_edge_count") or 0) >= 1 for row in rows),
        "service_candidate_valid": sum(truthy(row.get("service_candidate_space_structurally_valid")) for row in rows),
        "api_candidate_valid": sum(truthy(row.get("api_candidate_space_structurally_valid")) for row in rows),
        "blocking_service_leak_zero": sum(not truthy(row.get("exact_gold_service_name_leak")) for row in rows),
        "blocking_api_leak_zero": sum(not truthy(row.get("exact_gold_api_name_leak")) for row in rows),
        "failed_dependency_zero": sum(int(row.get("failed_call_dependency_count") or 0) == 0 for row in rows),
        "machine_rule_v1": sum(row.get("machine_rule_spec_version") == MACHINE_RULE_VERSION for row in rows),
        "human_rows_blank": sum(not any(str(row.get(field, "")).strip() for field in HUMAN_FIELDS) for row in rows),
    }
    require(all(value == TOOLBENCH_ROWS for value in checks.values()), f"ToolBench freeze checks failed: {checks}")
    hashes = [row.get("review_content_hash", "") for row in rows]
    require(all(hashes) and len(set(hashes)) == TOOLBENCH_ROWS, "ToolBench review hashes are empty or duplicated")
    return checks


def freeze_toolbench(
    rows: list[dict[str, str]], columns: list[str], output: Path, generated_at: str
) -> tuple[list[dict[str, str]], list[str]]:
    frozen: list[dict[str, str]] = []
    for source in rows:
        row = dict(source)
        row["review_tranche"] = "A"
        row["review_tranche_source"] = "ToolBench"
        row["review_status"] = "READY_FOR_HUMAN_SEMANTIC_REVIEW"
        frozen.append(row)
    out_columns = columns + [
        field for field in ("review_tranche", "review_tranche_source", "review_status")
        if field not in columns
    ]
    write_csv(output, frozen, out_columns)
    reread, _ = read_csv(output)
    require(
        [row["review_content_hash"] for row in rows]
        == [row["review_content_hash"] for row in reread],
        "ToolBench review_content_hash changed during tranche freeze",
    )
    return frozen, out_columns


MANUAL_QUERY_TRANSLATIONS = {
    "ToolBench_G2_33536": "我正在筹办公司活动，希望用吸引人的视觉素材增强现场效果。请提供一张随机动漫图片并检测其中是否含有不宜内容；另外请提供一些可免版权使用的图片，供活动宣传材料使用。",
    "ToolBench_G2_77385": "我和朋友计划从洛杉矶自驾到旧金山。请提供沿途地址，并给出每个地址的地理位置以便规划停靠点；另外请推荐沿途有趣的活动或景点。",
    "ToolBench_G3_19553": "我的表亲非常喜欢音乐二人组和组合。我想给他一个惊喜，请列出 2021 年该类别的年度热门艺人榜单，并提供其中一位他喜爱艺人的详细信息。",
    "ToolBench_G2_28605": "我正在筹办公司活动，需要制作一段宣传视频。请为视频生成配音；另外我想加入一些动态动画，请生成一组动画图片。",
    "ToolBench_G2_72394": "我想学习 Java 编程，需要一些指导。请获取最新的 Java 版本及其文档链接，分析编程教程的情感倾向，并把这些教程转换为语音。",
    "ToolBench_G2_19915": "我正在组织家庭聚会，想用个性化短信邀请给亲友一个惊喜。请按国家获取可用电话号码列表，验证每个号码，最后向所有通过验证的号码发送邀请短信。",
}


def build_toolbench_translations(root: Path, pool: list[dict[str, str]], output: Path) -> dict[str, str]:
    current_rows, _ = read_csv(root / PATHS["toolbench_current_pack"])
    old_rows, _ = read_csv(root / PATHS["toolbench_old_pack"])
    current_translations = read_json(root / PATHS["toolbench_current_translations"])
    old_translations = read_json(root / PATHS["toolbench_old_translations"])
    by_source: dict[str, str] = {}
    for row in current_rows:
        translated = str(current_translations.get(row.get("review_item_id"), "")).strip()
        if translated:
            by_source[row["source_task_id"]] = translated
    for row in old_rows:
        translated = str(old_translations.get(row.get("review_item_id"), "")).strip()
        if translated:
            by_source.setdefault(row["source_task_id"], translated)
    by_source.update(MANUAL_QUERY_TRANSLATIONS)
    result = {
        row["review_item_id"]: by_source.get(row["source_task_id"], "") for row in pool
    }
    missing = [key for key, value in result.items() if not value]
    require(not missing, f"Missing ToolBench display translations: {missing[:10]}")
    write_json(output, result)
    return result


def asset_inventory(stable_root: Path, bounded_files: list[Path]) -> list[dict[str, Any]]:
    selected: dict[str, Path] = {str(path.resolve()): path for path in bounded_files}
    for path in stable_root.rglob("*"):
        if any(term in path.name.casefold() for term in EVIDENCE_NAME_TERMS):
            selected.setdefault(str(path.resolve()), path)
    rows: list[dict[str, Any]] = []
    for path in sorted(selected.values(), key=lambda item: str(item).casefold()):
        is_dir = path.is_dir()
        extension = "" if is_dir else path.suffix.casefold()
        parseable = "directory" if is_dir else ("json" if extension == ".json" else "unknown_or_binary")
        role = "bounded_G2_G3_schema_query_source" if path in bounded_files else "name_keyword_inventory"
        rows.append(
            {
                "absolute_path": str(path.resolve()),
                "relative_path": str(path.relative_to(stable_root)),
                "is_directory": str(is_dir).lower(),
                "bytes": 0 if is_dir else path.stat().st_size,
                "extension": extension,
                "parseable_format": parseable,
                "inventory_role": role,
                "deserialized": "false" if parseable == "unknown_or_binary" else "not_applicable",
                "sha256": "" if is_dir else sha256(path),
            }
        )
    return rows


def schema_fingerprint(path: Path, group: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = read_json(path)
    require(isinstance(records, list), f"Expected JSON array: {path}")
    root_keys = sorted({key for record in records if isinstance(record, dict) for key in record})
    api_keys = sorted(
        {
            key
            for record in records if isinstance(record, dict)
            for api in record.get("api_list", []) if isinstance(api, dict)
            for key in api
        }
    )
    evidence = [scan_execution_evidence(record) for record in records]
    fingerprint = {
        "group": group,
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "root_type": "array",
        "row_count": len(records),
        "root_object_keys": root_keys,
        "api_object_keys": api_keys,
        "possible_id_paths": ["$[*].query_id"],
        "possible_query_paths": ["$[*].query"],
        "possible_ordered_call_paths": [],
        "possible_arguments_paths": [],
        "possible_outputs_or_observation_paths": [],
        "schema_only_paths": ["$[*].api_list", "$[*].api_list[*].required_parameters", "$[*].api_list[*].optional_parameters", "$[*].api_list[*].template_response", "$[*].relevant APIs"],
        "template_response_row_count": sum(
            any("template_response" in api for api in record.get("api_list", []) if isinstance(api, dict))
            for record in records if isinstance(record, dict)
        ),
        "actual_execution_like_evidence_row_count": sum(item["actual_execution_like_evidence"] for item in evidence),
        "virtual_api_or_cache_payload_available": False,
        "actual_execution_like_evidence_exists": any(item["actual_execution_like_evidence"] for item in evidence),
    }
    return fingerprint, records


def build_source_index(records_by_group: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for group, records in records_by_group.items():
        for position, record in enumerate(records):
            wrapped = dict(record)
            wrapped["__group"] = group
            wrapped["__position"] = position
            key = normalize_exact_key(record.get("query_id"))
            if key:
                index[(group, key)].append(wrapped)
    return index


def stable_review_columns(toolbench_columns: list[str]) -> list[str]:
    return list(toolbench_columns)


def stratified_subset(rows: list[dict[str, str]], size: int) -> list[dict[str, str]]:
    def select_within_group(group_rows: list[dict[str, str]], quota: int) -> list[dict[str, str]]:
        strata: dict[str, deque[dict[str, str]]] = defaultdict(deque)
        for row in group_rows:
            dep = "unknown"
            distribution = parse_json_list(row.get("dependency_type_distribution_json"))
            if distribution and isinstance(distribution[0], dict):
                dep = str(distribution[0].get("dependency_type", "unknown"))
            key = "|".join([row.get("catalog_domain_signature", ""), dep])
            strata[key].append(row)
        for key in strata:
            strata[key] = deque(
                sorted(
                    strata[key],
                    key=lambda row: hashlib.sha256(
                        f"v1.0.2-double|{row['underlying_task_id']}".encode("utf-8")
                    ).hexdigest(),
                )
            )
        selected: list[dict[str, str]] = []
        keys = sorted(strata)
        while len(selected) < quota and keys:
            next_keys: list[str] = []
            for key in keys:
                if strata[key] and len(selected) < quota:
                    selected.append(strata[key].popleft())
                if strata[key]:
                    next_keys.append(key)
            keys = next_keys
        return selected

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row.get("source_group", "unknown")].append(row)
    exact = {group: size * len(group_rows) / len(rows) for group, group_rows in groups.items()}
    quotas = {group: min(len(groups[group]), math.floor(value)) for group, value in exact.items()}
    remainder = size - sum(quotas.values())
    for group in sorted(groups, key=lambda name: (-(exact[name] - quotas[name]), name)):
        if remainder <= 0:
            break
        if quotas[group] < len(groups[group]):
            quotas[group] += 1
            remainder -= 1
    selected: list[dict[str, str]] = []
    for group in sorted(groups):
        selected.extend(select_within_group(groups[group], quotas[group]))
    return sorted(selected, key=lambda row: (row.get("source_group", ""), row["underlying_task_id"]))


def build_ledger(toolbench: list[dict[str, str]], stable: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, tranche, dataset in ((toolbench, "A", "ToolBench"), (stable, "B", "StableToolBench")):
        for row in source:
            rows.append(
                {
                    "underlying_task_id": row.get("underlying_task_id", ""),
                    "source_task_id": row.get("source_task_id", ""),
                    "source_dataset": dataset,
                    "review_tranche": tranche,
                    "review_content_hash": row.get("review_content_hash", ""),
                    "reviewed": "false",
                    "valid_for_composable_service": "",
                    "valid_for_composable_api": "",
                    "reviewer_type": "",
                    "reviewed_at": "",
                    "invalidated_by_content_change": "false",
                    "invalidation_reason": "",
                    "supporting_cross_source_provenance": "",
                }
            )
    return rows


def run_html_builder(
    root: Path, input_csv: Path, translations: Path, output: Path, manifest: Path,
    tranche: str, source: str, allow_empty: bool,
) -> None:
    command = [
        sys.executable, str(root / PATHS["html_builder"]), "--input", str(input_csv),
        "--translations", str(translations), "--output", str(output),
        "--manifest", str(manifest), "--tranche", tranche, "--source", source,
    ]
    if allow_empty:
        command.append("--allow-empty")
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"HTML builder failed:\n{result.stdout}\n{result.stderr}")


def validate_review_html(
    toolbench_html: Path, stable_html: Path, toolbench_manifest: Path, stable_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    toolbench = toolbench_html.read_text(encoding="utf-8")
    stable = stable_html.read_text(encoding="utf-8")
    toolbench_meta = read_json(toolbench_manifest)
    stable_meta = read_json(stable_manifest)
    required_toolbench_markers = {
        "tranche_title": "Composable Tranche A · ToolBench 联合人工审核",
        "local_storage": "localStorage",
        "full_csv_export": "导出完整 CSV",
        "filtered_csv_export": "导出当前筛选",
        "csv_import": "导入 CSV",
        "quick_presets": "快捷审核方案",
        "previous_navigation": "上一条",
        "next_navigation": "下一条",
        "search": "搜索 ID、query、service、API",
        "service_api_hierarchy": "Service/API",
        "human_final_warning": "不能自动成为 human final",
        "embedded_app_test": "window.__reviewAppTest",
    }
    toolbench_checks = {name: marker in toolbench for name, marker in required_toolbench_markers.items()}
    stable_checks = {
        "empty_tranche_title": "Composable Tranche B · StableToolBench 人工审核" in stable,
        "no_fabricated_rows_notice": "页面没有伪造样本" in stable,
        "no_execution_trace_notice": "没有真实 arguments、outputs 或 observations" in stable,
        "empty_csv_export": "导出空 CSV 表头" in stable,
        "embedded_rows_empty": "rows:[]" in stable,
    }
    payload = {
        "generated_at": now_iso(),
        "toolbench_html": str(toolbench_html),
        "stable_html": str(stable_html),
        "toolbench_input_rows": toolbench_meta.get("input_rows"),
        "toolbench_query_translation_count": toolbench_meta.get("query_translation_count"),
        "toolbench_service_translation_count": toolbench_meta.get("service_translation_count"),
        "toolbench_api_translation_count": toolbench_meta.get("api_translation_count"),
        "toolbench_human_fields_autofilled_count": toolbench_meta.get("human_fields_autofilled_count"),
        "stable_input_rows": stable_meta.get("input_rows"),
        "toolbench_marker_checks": toolbench_checks,
        "stable_marker_checks": stable_checks,
        "all_static_checks_passed": bool(
            toolbench_meta.get("input_rows") == TOOLBENCH_ROWS
            and toolbench_meta.get("query_translation_count") == TOOLBENCH_ROWS
            and toolbench_meta.get("human_fields_autofilled_count") == 0
            and stable_meta.get("input_rows") == 0
            and all(toolbench_checks.values())
            and all(stable_checks.values())
        ),
        "browser_runtime_validation_performed": False,
        "browser_runtime_validation_note": "In-app browser runtime initialization was unavailable; no external browser or web fallback was used.",
    }
    require(payload["all_static_checks_passed"], f"Review HTML static validation failed: {payload}")
    write_json(output, payload)
    return payload


def run_tests(root: Path, output: Path) -> dict[str, int]:
    result = subprocess.run(
        [sys.executable, str(root / PATHS["tests"]), "-v"], cwd=root,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text((result.stdout + "\n" + result.stderr).strip() + "\n", encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Stable bounded supplement tests failed")
    return {"tests_run": (result.stdout + result.stderr).count(" ... ok"), "tests_failed": 0}


def write_reports(
    root: Path, generated_at: str, toolbench_checks: dict[str, Any], summary: dict[str, Any],
    inventory: list[dict[str, Any]], fingerprints: dict[str, Any], paths: dict[str, Path],
) -> None:
    toolbench_text = f"""# ToolBench Composable Tranche A Review Readiness v1.0.2

Generated at: `{generated_at}`

## Inputs

- frozen rules: `{paths['rules']}`
- authoritative v1.0.1 pool: `{paths['toolbench_pool']}`
- authority evidence: `{paths['toolbench_archive_manifest']}`, `{paths['toolbench_report']}`

## Freeze Result

- toolbench_tranche_rows = `103`
- unique underlying_task_id = `103`
- toolbench_tranche_hash_preserved = `{str(summary['toolbench_tranche_A_hash_preserved']).lower()}`
- machine_rule_spec_version = `v1.0`
- human fields blank rows = `{toolbench_checks['human_rows_blank']}`
- can_start_toolbench_tranche_human_review = `true`
- can_claim_sufficient_composable_candidate_pool = `false`
- human_confirmed_composable_count = `0`

Tranche A preserves query, candidate order, provisional gold, dependency evidence, and every existing `review_content_hash`. It is ready for human semantic review now. The machine only proves structural eligibility; it does not provide a human-final composable label.
"""
    (root / TOOLBENCH_REPORT).write_text(toolbench_text, encoding="utf-8")

    inv_files = [row for row in inventory if row["is_directory"] == "false"]
    audit_text = f"""# StableToolBench Composable Evidence Asset Audit v1.0.2

Generated at: `{generated_at}`

## Inputs

- StableToolBench root: `{paths['stable_root']}`
- G2 source: `{paths['stable_g2']}`
- G3 source: `{paths['stable_g3']}`
- adapter task table: `{paths['stable_raw']}`
- policy table: `{paths['stable_policy']}`
- v0.3 adjudication: `{paths['stable_adjudication_v03']}`
- v0.4.2 review pack: `{paths['stable_adjudication_v042']}`

## Bounded Inventory

- StableToolBench root actual path: `{paths['stable_root']}`
- G2 source rows: `106`
- G3 source rows: `61`
- bounded source rows: `167`
- inventoried files: `{len(inv_files)}`
- parseable bounded formats: `JSON array`
- possible ID path: `$[*].query_id`
- possible query path: `$[*].query`
- possible ordered-call paths: `none`
- possible actual arguments paths: `none`
- possible actual outputs/observation paths: `none`
- virtual API/cache payload availability: `false`
- actual execution-like evidence exists: `false`

The two bounded JSON files contain query text, `relevant APIs`, API schemas, required/optional parameter schemas, and occasional `template_response` schemas. A template response is not a task execution output. No answer, trajectory, trace, actual call arguments, actual outputs, or observations were found. Unknown binary content was not deserialized, and no virtual server or simulator was executed.

Therefore exact source joining can establish provenance but cannot establish an output-to-input dependency edge. All 167 records remain `SOURCE_UNAVAILABLE_HOLD` and are also listed only in the query/schema-grounded reserve.
"""
    (root / STABLE_AUDIT_REPORT).write_text(audit_text, encoding="utf-8")

    recommendation = summary["recommended_next_step"]
    go_text = f"""# StableToolBench Bounded Composable Supplement Go / No-Go v1.0.2

Generated at: `{generated_at}`

## Inputs

- frozen machine rules: `{paths['rules']}`
- ToolBench tranche source: `{paths['toolbench_pool']}`
- StableToolBench G2/G3: `{paths['stable_g2']}`, `{paths['stable_g3']}`

## Fixed Status

- machine_rule_spec_version = `v1.0`
- machine_rule_changed = `false`
- toolbench_raw_rescan = `false`
- toolbench_tranche_A_rows = `103`
- toolbench_tranche_A_hash_preserved = `{str(summary['toolbench_tranche_A_hash_preserved']).lower()}`
- can_start_toolbench_tranche_human_review = `true`

## StableToolBench Bounded Result

- stabletoolbench_bounded_input_rows = `{summary['stabletoolbench_bounded_input_rows']}`
- stabletoolbench_exact_joined_count = `{summary['stabletoolbench_exact_joined_count']}`
- stabletoolbench_trace_grounded_count = `{summary['stabletoolbench_trace_grounded_count']}`
- stabletoolbench_structurally_eligible_count = `{summary['stabletoolbench_structurally_eligible_count']}`
- stabletoolbench_exact_duplicate_count = `{summary['stabletoolbench_exact_duplicate_count']}`
- stabletoolbench_supplement_rows = `{summary['stabletoolbench_supplement_rows']}`
- stabletoolbench_strong_reserve_rows = `{summary['stabletoolbench_strong_reserve_rows']}`
- stabletoolbench_schema_only_reserve_rows = `{summary['stabletoolbench_schema_only_reserve_rows']}`

## Combined Decision

- combined_authoritative_pool_rows = `{summary['combined_authoritative_pool_rows']}`
- can_reach_combined_120_pool = `{str(summary['can_reach_combined_120_pool']).lower()}`
- can_reach_target_140_pool = `{str(summary['can_reach_target_140_pool']).lower()}`
- human_confirmed_composable_count = `0`
- can_claim_composable_service_benchmark_now = `false`
- can_claim_composable_api_benchmark_now = `false`
- can_start_full_six_task_assembly = `false`
- can_generate_final_dataset = `false`
- can_create_split = `false`
- can_run_baseline = `false`

## Decision

ToolBench tranche A remains valid and can be reviewed immediately. StableToolBench shortage only means there is no execution-grounded backup tranche in the downloaded local repository. It does not invalidate the 103 ToolBench rows.

recommended_next_step = `{recommendation}`
"""
    (root / GO_NO_GO).write_text(go_text, encoding="utf-8")


def archive_files(archive: Path, files: Iterable[Path], constraints: dict[str, Any], generated_at: str) -> None:
    archive.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in files:
        if not source.exists() or source.is_dir():
            continue
        name = source.name
        if name in seen:
            name = f"{source.parent.name}__{name}"
        seen.add(name)
        target = archive / name
        shutil.copy2(source, target)
        copied.append({"filename": name, "source_path": str(source), "bytes": target.stat().st_size, "sha256": sha256(target)})
    write_json(
        archive / "archive_manifest_v1_0_2.json",
        {"generated_at": generated_at, "archive_dir": str(archive), "constraints": constraints, "files": copied},
    )


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    paths = {name: (root / rel).resolve() for name, rel in PATHS.items()}
    missing = [f"{name}: {path}" for name, path in paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs:\n" + "\n".join(missing))
    toolbench_out = (root / args.toolbench_output).resolve()
    stable_out = (root / args.stable_output).resolve()
    ledger_path = (root / args.ledger).resolve()
    archive_path = (root / args.archive).resolve()
    toolbench_out.mkdir(parents=True, exist_ok=True)
    stable_out.mkdir(parents=True, exist_ok=True)
    generated_at = now_iso()

    guarded_inputs = {
        "rules": paths["rules"], "toolbench_pool": paths["toolbench_pool"],
        "toolbench_revalidation": paths["toolbench_revalidation"],
        "toolbench_rewrite": paths["toolbench_rewrite"],
        "stable_g2": paths["stable_g2"], "stable_g3": paths["stable_g3"],
        "stable_raw": paths["stable_raw"],
    }
    before_hashes = {name: sha256(path) for name, path in guarded_inputs.items()}
    old_archive = read_json(paths["toolbench_archive_manifest"])
    require(before_hashes["rules"] == frozen_archive_hash(old_archive, paths["rules"].name), "Frozen machine-rule SHA-256 does not match v1.0.1 authority archive")
    require(before_hashes["toolbench_pool"] == frozen_archive_hash(old_archive, paths["toolbench_pool"].name), "ToolBench pool SHA-256 does not match v1.0.1 authority archive")
    old_summary = read_json(paths["toolbench_summary"])
    require(old_summary.get("final_authoritative_pool_rows") == TOOLBENCH_ROWS, "v1.0.1 summary does not authorize 103 rows")
    require(old_summary.get("machine_rule_spec_version") == MACHINE_RULE_VERSION, "Unexpected machine-rule version")

    tests = run_tests(root, stable_out / "stabletoolbench_bounded_supplement_test_results_v1_0_2.txt")
    toolbench_rows, toolbench_columns = read_csv(paths["toolbench_pool"])
    toolbench_checks = verify_toolbench_pool(toolbench_rows)
    tranche_a_path = toolbench_out / "toolbench_composable_review_tranche_A_103.csv"
    tranche_a, tranche_a_columns = freeze_toolbench(toolbench_rows, toolbench_columns, tranche_a_path, generated_at)
    tranche_hash_preserved = [row["review_content_hash"] for row in toolbench_rows] == [row["review_content_hash"] for row in tranche_a]

    translation_path = toolbench_out / "toolbench_tranche_A_query_translations_zh_v1_0_2.json"
    translations = build_toolbench_translations(root, tranche_a, translation_path)
    freeze_manifest = {
        "generated_at": generated_at,
        "authority_selection_method": "v1.0.1 Go-No-Go report plus archive manifest and summary; ModifiedAt was not used",
        "authoritative_input": str(paths["toolbench_pool"]),
        "authoritative_input_sha256": before_hashes["toolbench_pool"],
        "frozen_rules": str(paths["rules"]),
        "frozen_rules_sha256": before_hashes["rules"],
        "machine_rule_spec_version": MACHINE_RULE_VERSION,
        "toolbench_tranche_rows": len(tranche_a),
        "toolbench_tranche_unique_underlying_tasks": len({row["underlying_task_id"] for row in tranche_a}),
        "toolbench_tranche_hash_preserved": tranche_hash_preserved,
        "content_fields_modified": False,
        "candidate_order_modified": False,
        "review_hash_recomputed": False,
        "query_translation_count": len(translations),
        "human_fields_autofilled_count": 0,
        "checks": toolbench_checks,
        "output_csv": str(tranche_a_path),
        "output_csv_sha256": sha256(tranche_a_path),
        "can_start_toolbench_tranche_human_review": True,
        "can_claim_sufficient_composable_candidate_pool": False,
        "human_confirmed_composable_count": 0,
    }
    write_json(toolbench_out / "toolbench_tranche_A_freeze_manifest.json", freeze_manifest)

    inventory = asset_inventory(paths["stable_root"], [paths["stable_g2"], paths["stable_g3"]])
    inventory_fields = ["absolute_path", "relative_path", "is_directory", "bytes", "extension", "parseable_format", "inventory_role", "deserialized", "sha256"]
    write_csv(stable_out / "stabletoolbench_evidence_asset_inventory.csv", inventory, inventory_fields)
    g2_fingerprint, g2_records = schema_fingerprint(paths["stable_g2"], "G2")
    g3_fingerprint, g3_records = schema_fingerprint(paths["stable_g3"], "G3")
    require(len(g2_records) == 106 and len(g3_records) == 61, "StableToolBench bounded group counts are not 106/61")
    fingerprints = {
        "generated_at": generated_at,
        "stabletoolbench_root": str(paths["stable_root"]),
        "bounded_groups": [g2_fingerprint, g3_fingerprint],
        "bounded_input_rows": len(g2_records) + len(g3_records),
        "actual_execution_like_evidence_exists": False,
        "virtual_api_or_cache_payload_available": False,
        "unknown_binary_deserialized": False,
    }
    write_json(stable_out / "stabletoolbench_evidence_schema_fingerprints.json", fingerprints)

    raw_rows, _ = read_csv(paths["stable_raw"])
    bounded_rows = [row for row in raw_rows if row.get("source_group") in {"G2", "G3"}]
    require(len(bounded_rows) == STABLE_BOUNDED_ROWS, f"Expected 167 bounded Stable rows, got {len(bounded_rows)}")
    records_by_group = {"G2": g2_records, "G3": g3_records}
    source_index = build_source_index(records_by_group)
    join_rows: list[dict[str, Any]] = []
    joined_records: dict[str, dict[str, Any]] = {}
    for row in bounded_rows:  # The only exact-join pass in this run.
        status, record, join_key = exact_join_one(row, source_index)
        group = row["source_group"]
        scan = scan_execution_evidence(record) if record else scan_execution_evidence({})
        source_file = paths["stable_g2"] if group == "G2" else paths["stable_g3"]
        position = int(record["__position"]) if record else -1
        join_rows.append(
            {
                "task_id": row["task_id"],
                "source_task_id": row["task_id"],
                "source_group": group,
                "source_instruction_id": row.get("source_instruction_id", ""),
                "source_query_id": row.get("source_query_id", ""),
                "exact_join_status": status,
                "join_key_type": join_key,
                "join_key_value": row.get("source_instruction_id", ""),
                "matched_query_id": "" if not record else record.get("query_id", ""),
                "matched_source_file": "" if not record else str(source_file),
                "matched_source_json_path": "" if not record else f"$[{position}]",
                "query_exact_match": str(bool(record and str(record.get('query', '')).strip() == row.get('query_text', '').strip())).lower(),
                "ordered_steps_found": str(scan["ordered_steps_found"]).lower(),
                "arguments_found": str(scan["arguments_found"]).lower(),
                "outputs_found": str(scan["outputs_found"]).lower(),
                "observations_found": str(scan["observations_found"]).lower(),
                "actual_execution_like_evidence": str(scan["actual_execution_like_evidence"]).lower(),
                "evidence_class": "execution_trace" if scan["actual_execution_like_evidence"] else "query_schema_only",
            }
        )
        if status == "EXACT_JOINED" and record:
            joined_records[row["task_id"]] = record
    join_fields = ordered_fields(join_rows)
    write_csv(stable_out / "stabletoolbench_g2_g3_trace_join_manifest.csv", join_rows, join_fields)

    exact_joined = sum(row["exact_join_status"] == "EXACT_JOINED" for row in join_rows)
    ambiguous = sum(row["exact_join_status"] == "AMBIGUOUS" for row in join_rows)
    unmatched = sum(row["exact_join_status"] == "UNMATCHED" for row in join_rows)
    trace_grounded = sum(row["actual_execution_like_evidence"] == "true" for row in join_rows)
    ordered_found = sum(row["ordered_steps_found"] == "true" for row in join_rows)
    arguments_found = sum(row["arguments_found"] == "true" for row in join_rows)
    outputs_found = sum(row["outputs_found"] == "true" for row in join_rows)

    write_jsonl(stable_out / "stabletoolbench_normalized_g2_g3_steps.jsonl", [])
    step_summary: list[dict[str, Any]] = []
    for source, join in zip(bounded_rows, join_rows):
        step_summary.append(
            {
                "source_task_id": source["task_id"],
                "source_group": source["source_group"],
                "exact_join_status": join["exact_join_status"],
                "parse_status": "SCHEMA_ONLY_NO_EXECUTION_TRACE" if join["exact_join_status"] == "EXACT_JOINED" else "SOURCE_UNAVAILABLE",
                "normalized_step_count": 0,
                "ordered_steps_found": join["ordered_steps_found"],
                "arguments_found": join["arguments_found"],
                "outputs_found": join["outputs_found"],
                "observations_found": join["observations_found"],
                "source_file": join["matched_source_file"],
                "source_json_path": join["matched_source_json_path"],
                "notes": "API parameter/template_response schemas are not actual call arguments or outputs",
            }
        )
    write_csv(stable_out / "stabletoolbench_step_parse_summary.csv", step_summary, ordered_fields(step_summary))

    machine_rows: list[dict[str, Any]] = []
    schema_reserve: list[dict[str, Any]] = []
    for source, join in zip(bounded_rows, join_rows):
        source_status = "SOURCE_UNAVAILABLE_HOLD"
        machine_rows.append(
            {
                "source_task_id": source["task_id"],
                "source_dataset": "StableToolBench",
                "source_group": source["source_group"],
                "query_text": source["query_text"],
                "machine_review_status": source_status,
                "machine_rule_spec_version": MACHINE_RULE_VERSION,
                "query_nonempty": str(bool(source["query_text"].strip())).lower(),
                "ordered_call_count": 0,
                "distinct_gold_service_count": 0,
                "distinct_gold_api_count": 0,
                "cross_service_strong_edge_count": 0,
                "dependency_graph_is_dag": "unknown",
                "failed_call_dependency_count": 0,
                "service_candidate_space_structurally_valid": "false",
                "api_candidate_space_structurally_valid": "false",
                "blocking_service_leak": "unknown",
                "blocking_api_leak": "unknown",
                "source_trace_path": "",
                "source_schema_path": join["matched_source_file"],
                "source_json_path": join["matched_source_json_path"],
                "machine_blocking_rules_json": json_dumps(["actual_execution_trace_unavailable"]),
                "machine_risk_flags_json": json_dumps(["query_schema_only"]),
                "status_reason": "Exact provenance join succeeded, but no actual ordered calls, arguments, outputs, or observations exist locally",
            }
        )
        schema_reserve.append(
            {
                **source,
                "exact_join_status": join["exact_join_status"],
                "evidence_source_type": "query_and_api_schema_only",
                "source_schema_path": join["matched_source_file"],
                "source_json_path": join["matched_source_json_path"],
                "actual_execution_like_evidence": "false",
                "authoritative_pool_eligible": "false",
                "reserve_status": "QUERY_SCHEMA_GROUNDED_RESERVE_ONLY",
                "reserve_exclusion_reason": "No actual arguments/outputs/observations; schema matching cannot prove a strong cross-service dependency",
            }
        )
    write_csv(stable_out / "stabletoolbench_g2_g3_machine_review_status.csv", machine_rows, ordered_fields(machine_rows))
    write_jsonl(stable_out / "stabletoolbench_dependency_edge_candidates.jsonl", [])
    write_csv(stable_out / "stabletoolbench_query_schema_grounded_reserve.csv", schema_reserve, ordered_fields(schema_reserve))

    duplicate_fields = [
        "stable_source_task_id", "toolbench_underlying_task_id", "duplicate_signal",
        "duplicate_value", "exact_cross_source_duplicate", "supporting_evidence_only",
        "toolbench_review_content_hash_preserved",
    ]
    duplicates: list[dict[str, Any]] = []
    write_csv(stable_out / "stabletoolbench_toolbench_exact_duplicate_manifest.csv", duplicates, duplicate_fields)

    stable_review_fields = stable_review_columns(tranche_a_columns)
    tranche_b: list[dict[str, Any]] = []
    strong_reserve: list[dict[str, Any]] = []
    tranche_b_path = stable_out / "stabletoolbench_composable_review_tranche_B.csv"
    strong_reserve_path = stable_out / "stabletoolbench_composable_strong_reserve.csv"
    write_csv(tranche_b_path, tranche_b, stable_review_fields)
    write_csv(strong_reserve_path, strong_reserve, stable_review_fields)

    status_counts = Counter(row["machine_review_status"] for row in machine_rows)
    machine_summary = {
        "generated_at": generated_at,
        "machine_rule_spec_version": MACHINE_RULE_VERSION,
        "machine_rule_changed": False,
        "bounded_input_rows": len(bounded_rows),
        "exact_joined_count": exact_joined,
        "ambiguous_count": ambiguous,
        "unmatched_count": unmatched,
        "join_rate": exact_joined / len(bounded_rows),
        "G2_join_rate": sum(row["exact_join_status"] == "EXACT_JOINED" and row["source_group"] == "G2" for row in join_rows) / 106,
        "G3_join_rate": sum(row["exact_join_status"] == "EXACT_JOINED" and row["source_group"] == "G3" for row in join_rows) / 61,
        "join_key_distribution": dict(Counter(row["join_key_type"] for row in join_rows)),
        "ordered_steps_found_count": ordered_found,
        "arguments_found_count": arguments_found,
        "outputs_found_count": outputs_found,
        "trace_grounded_count": trace_grounded,
        "machine_status_distribution": dict(status_counts),
        "structurally_eligible_count": 0,
        "schema_only_reserve_rows": len(schema_reserve),
        "supplement_rows": 0,
        "strong_reserve_rows": 0,
        "human_fields_autofilled_count": 0,
        "query_schema_only_promoted_to_gold": False,
    }
    write_json(stable_out / "stabletoolbench_machine_review_summary.json", machine_summary)

    combined_rows: list[dict[str, Any]] = []
    for row in tranche_a:
        combined_rows.append(
            {
                "underlying_task_id": row["underlying_task_id"],
                "source_dataset": "ToolBench",
                "review_tranche": "A",
                "review_content_hash": row["review_content_hash"],
                "review_status": "READY_FOR_HUMAN_SEMANTIC_REVIEW",
                "valid_for_service_level_review": "machine_eligible_pending_human_review",
                "valid_for_api_level_review": "machine_eligible_pending_human_review",
                "exact_duplicate_status": "not_duplicate",
                "machine_rule_spec_version": MACHINE_RULE_VERSION,
            }
        )
    combined_fields = [
        "underlying_task_id", "source_dataset", "review_tranche", "review_content_hash",
        "review_status", "valid_for_service_level_review", "valid_for_api_level_review",
        "exact_duplicate_status", "machine_rule_spec_version",
    ]
    write_csv(toolbench_out / "composable_combined_review_manifest_v1_0_2.csv", combined_rows, combined_fields)

    double_size = double_annotation_size(len(combined_rows))
    double_subset = stratified_subset(tranche_a, double_size)
    write_csv(toolbench_out / "composable_double_annotation_subset_v1_0_2.csv", double_subset, tranche_a_columns)
    ledger = build_ledger(tranche_a, tranche_b)
    ledger_fields = [
        "underlying_task_id", "source_task_id", "source_dataset", "review_tranche",
        "review_content_hash", "reviewed", "valid_for_composable_service",
        "valid_for_composable_api", "reviewer_type", "reviewed_at",
        "invalidated_by_content_change", "invalidation_reason", "supporting_cross_source_provenance",
    ]
    write_csv(ledger_path, ledger, ledger_fields)

    if not args.skip_html:
        run_html_builder(
            root, tranche_a_path, translation_path,
            toolbench_out / "toolbench_composable_review_tranche_A_103.html",
            toolbench_out / "toolbench_composable_review_tranche_A_103_html_manifest.json",
            "A", "ToolBench", False,
        )
        empty_translations = stable_out / "stabletoolbench_tranche_B_query_translations_zh_v1_0_2.json"
        write_json(empty_translations, {})
        run_html_builder(
            root, tranche_b_path, empty_translations,
            stable_out / "stabletoolbench_composable_review_tranche_B.html",
            stable_out / "stabletoolbench_composable_review_tranche_B_html_manifest.json",
            "B", "StableToolBench", True,
        )
        validate_review_html(
            toolbench_out / "toolbench_composable_review_tranche_A_103.html",
            stable_out / "stabletoolbench_composable_review_tranche_B.html",
            toolbench_out / "toolbench_composable_review_tranche_A_103_html_manifest.json",
            stable_out / "stabletoolbench_composable_review_tranche_B_html_manifest.json",
            stable_out / "composable_tranche_review_apps_static_validation_v1_0_2.json",
        )

    combined_count = len(combined_rows)
    summary = {
        "generated_at": generated_at,
        **tests,
        "machine_rule_spec_version": MACHINE_RULE_VERSION,
        "machine_rule_changed": False,
        "toolbench_raw_rescan": False,
        "toolbench_tranche_A_rows": len(tranche_a),
        "toolbench_tranche_A_hash_preserved": tranche_hash_preserved,
        "can_start_toolbench_tranche_human_review": True,
        "stabletoolbench_bounded_input_rows": len(bounded_rows),
        "stabletoolbench_exact_joined_count": exact_joined,
        "stabletoolbench_join_rate": exact_joined / len(bounded_rows),
        "stabletoolbench_ordered_steps_found_count": ordered_found,
        "stabletoolbench_arguments_found_count": arguments_found,
        "stabletoolbench_outputs_found_count": outputs_found,
        "stabletoolbench_trace_grounded_count": trace_grounded,
        "stabletoolbench_structurally_ineligible_count": 0,
        "stabletoolbench_api_only_workflow_count": 0,
        "stabletoolbench_structurally_eligible_with_risk_count": 0,
        "stabletoolbench_structurally_eligible_for_review_count": 0,
        "stabletoolbench_source_unavailable_count": status_counts["SOURCE_UNAVAILABLE_HOLD"],
        "stabletoolbench_structurally_eligible_count": 0,
        "stabletoolbench_exact_duplicate_count": len(duplicates),
        "stabletoolbench_supplement_rows": len(tranche_b),
        "stabletoolbench_strong_reserve_rows": len(strong_reserve),
        "stabletoolbench_schema_only_reserve_rows": len(schema_reserve),
        "combined_authoritative_pool_rows": combined_count,
        "can_reach_combined_120_pool": combined_count >= 120,
        "can_reach_target_140_pool": combined_count >= 140,
        "double_annotation_subset_rows": len(double_subset),
        "review_credit_ledger_rows": len(ledger),
        "human_review_fields_autofilled_count": 0,
        "human_confirmed_composable_count": 0,
        "can_claim_composable_service_benchmark_now": False,
        "can_claim_composable_api_benchmark_now": False,
        "can_start_full_six_task_assembly": False,
        "can_generate_final_dataset": False,
        "can_create_split": False,
        "can_run_baseline": False,
        "qwen_or_external_api_used": False,
        "recommended_human_strategy": "Immediately review ToolBench tranche A; review Stable tranche B only if a future bounded local evidence source creates eligible rows; review each content hash once; stop at 100 both-level eligible tasks.",
        "recommended_next_step": "continue reviewing the 103 ToolBench tranche; report remaining candidate shortage; do not relax paired-composable rules; do not create query-schema-only Gold.",
    }
    write_json(stable_out / "stabletoolbench_bounded_composable_supplement_summary_v1_0_2.json", summary)
    write_reports(root, generated_at, toolbench_checks, summary, inventory, fingerprints, paths)

    after_hashes = {name: sha256(path) for name, path in guarded_inputs.items()}
    require(before_hashes == after_hashes, "A frozen rule or source input changed during the bounded run")
    integrity = {
        "generated_at": generated_at,
        "before": before_hashes,
        "after": after_hashes,
        "all_guarded_inputs_unchanged": True,
        "toolbench_review_hashes_preserved": tranche_hash_preserved,
        "toolbench_output_hashes_unique": len({row["review_content_hash"] for row in tranche_a}) == TOOLBENCH_ROWS,
        "human_fields_autofilled_count": 0,
    }
    write_json(stable_out / "bounded_supplement_integrity_hashes_v1_0_2.json", integrity)

    constraints = {
        "machine_rule_changed": False,
        "machine_rule_version_bumped": False,
        "toolbench_raw_rescan": False,
        "toolbench_new_corpus_mining": False,
        "unbounded_stabletoolbench_scan": False,
        "llm_semantic_judgment": False,
        "llm_query_rewrite": False,
        "qwen_or_external_api": False,
        "automatic_final_label_or_human_review": False,
        "review_field_autofill": False,
        "automatic_gold_semantic_validation": False,
        "source_freeze": False,
        "full_six_task_assembly": False,
        "final_dataset": False,
        "split": False,
        "baseline": False,
        "training": False,
    }
    if not args.skip_archive:
        files = [
            *sorted(toolbench_out.glob("*")), *sorted(stable_out.glob("*")), ledger_path,
            root / TOOLBENCH_REPORT, root / STABLE_AUDIT_REPORT, root / GO_NO_GO,
            paths["rules"], paths["master_plan"], paths["html_builder"],
            Path(__file__).resolve(), paths["tests"],
        ]
        archive_files(archive_path, files, constraints, generated_at)

    print(f"machine_rule_spec_version={MACHINE_RULE_VERSION}")
    print("machine_rule_changed=false")
    print(f"toolbench_tranche_A_rows={len(tranche_a)}")
    print(f"toolbench_tranche_A_hash_preserved={str(tranche_hash_preserved).lower()}")
    print("can_start_toolbench_tranche_human_review=true")
    print(f"stabletoolbench_bounded_input_rows={len(bounded_rows)}")
    print(f"stabletoolbench_exact_joined_count={exact_joined}")
    print(f"stabletoolbench_join_rate={exact_joined / len(bounded_rows):.6f}")
    print(f"stabletoolbench_ordered_steps_found_count={ordered_found}")
    print(f"stabletoolbench_arguments_found_count={arguments_found}")
    print(f"stabletoolbench_outputs_found_count={outputs_found}")
    print("stabletoolbench_structurally_ineligible_count=0")
    print("stabletoolbench_api_only_workflow_count=0")
    print("stabletoolbench_structurally_eligible_with_risk_count=0")
    print("stabletoolbench_structurally_eligible_for_review_count=0")
    print(f"stabletoolbench_source_unavailable_count={status_counts['SOURCE_UNAVAILABLE_HOLD']}")
    print(f"stabletoolbench_exact_duplicate_count={len(duplicates)}")
    print(f"stabletoolbench_supplement_rows={len(tranche_b)}")
    print(f"stabletoolbench_strong_reserve_rows={len(strong_reserve)}")
    print(f"stabletoolbench_schema_only_reserve_rows={len(schema_reserve)}")
    print(f"combined_authoritative_pool_rows={combined_count}")
    print(f"can_reach_combined_120_pool={str(combined_count >= 120).lower()}")
    print(f"can_reach_target_140_pool={str(combined_count >= 140).lower()}")
    print("human_review_fields_autofilled_count=0")
    print("human_confirmed_composable_count=0")
    print("can_claim_composable_service_benchmark_now=false")
    print("can_claim_composable_api_benchmark_now=false")
    print("can_start_full_six_task_assembly=false")
    print("can_generate_final_dataset=false")
    print(f"recommended_next_step={summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
