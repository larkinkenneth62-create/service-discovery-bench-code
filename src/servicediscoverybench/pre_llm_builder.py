"""Build Native, Global, and Machine-Challenge manifests before formal LLM calls.

The builder is provider-neutral and keeps formal generative calls at zero.  It
fails closed if candidate documents are incomplete or if a Global population
row cannot be reconstructed from the full passing-population artifact.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .joint_split_optimizer_v3 import candidate_bucket
from .split_identity_v3 import stable_hash


FORBIDDEN_VISIBLE_KEYS = {
    "gold",
    "gold_ids",
    "gold_services_json",
    "gold_apis_json",
    "acceptable_gold_service_sets_json",
    "acceptable_gold_api_sets_json",
    "qa_decision",
    "human_label",
    "human_reason",
    "reviewer_id",
    "split",
    "is_gold",
    "expected_answer",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _json_value(value: object, default):
    if isinstance(value, (dict, list)):
        return value
    text = _text(value)
    if not text:
        return default
    return json.loads(text)


def candidate_document(record: Mapping[str, object]) -> dict[str, object]:
    candidate_id = _text(record.get("candidate_id") or record.get("service_id") or record.get("api_id"))
    if not candidate_id:
        raise ValueError("catalog record has no candidate/service/API ID")
    name = _text(record.get("canonical_name") or record.get("name") or record.get("service_name") or record.get("api_name"))
    description = _text(record.get("description") or record.get("service_description") or record.get("api_description"))
    provider = _text(record.get("provider") or record.get("host_or_base_url") or record.get("host") or record.get("endpoint"))
    schema = _text(
        record.get("api_schema_summary")
        or record.get("parameter_schema_json")
        or record.get("parameters_json")
        or record.get("endpoint_summary")
    )
    if not name and not description:
        raise ValueError(f"candidate {candidate_id} lacks both canonical name and description")
    return {
        "candidate_id": candidate_id,
        "canonical_name": name,
        "description": description,
        "provider_or_host": provider,
        "api_schema_summary": schema[:2_000],
    }


def load_catalog(service_catalog_path: Path, api_catalog_path: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in (service_catalog_path, api_catalog_path):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                document = candidate_document(record)
                candidate_id = str(document["candidate_id"])
                if candidate_id in result and result[candidate_id] != document:
                    raise ValueError(f"conflicting catalog documents for {candidate_id}")
                result[candidate_id] = document
    return result


def _candidate_ids(row: Mapping[str, object]) -> list[str]:
    target = _text(row.get("prediction_target"))
    field = "candidate_services_json" if target == "service" else "candidate_apis_json"
    parsed = _json_value(row.get(field), [])
    if not isinstance(parsed, list):
        raise ValueError(f"{field} must be a JSON list")
    ids = [str(item) for item in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate candidate ID in {_text(row.get('benchmark_task_id'))}")
    return ids


def _output_schema_name(task_type: str, setting: str) -> str:
    if setting in {"global", "machine_challenge"}:
        return "ranking_only"
    return "ranking_only" if task_type.startswith("single_") else "ranking_and_selected_set"


def _prompt_template(setting: str, output_schema: str) -> str:
    instruction = (
        "Rank every candidate from most to least suitable for the user request. "
        "Use only candidate IDs present in INPUT_JSON and return strict JSON."
    )
    if output_schema == "ranking_and_selected_set":
        instruction += " Also select the complete set of candidates needed to satisfy the request."
    return f"SETTING={setting}\n{instruction}\nOUTPUT_SCHEMA={output_schema}\nINPUT_JSON={{input_payload_json}}\n"


def _cache_key_fields(
    *,
    setting: str,
    task_id: str,
    query_hash: str,
    candidate_order_hash: str,
    prompt_hash: str,
    output_schema_hash: str,
    model_name: str,
    model_revision: str,
    decoding_config_hash: str,
) -> dict[str, str]:
    return {
        "setting": setting,
        "benchmark_task_id": task_id,
        "query_hash": query_hash,
        "candidate_order_hash": candidate_order_hash,
        "prompt_hash": prompt_hash,
        "output_schema_hash": output_schema_hash,
        "model_identity_hash": stable_hash([model_name, model_revision]),
        "decoding_config_hash": decoding_config_hash,
    }


def build_native_manifest(
    test_rows: Sequence[Mapping[str, object]],
    catalog: Mapping[str, Mapping[str, object]],
    *,
    model_name: str = "__USER_AUTHORIZED_MODEL_REQUIRED__",
    model_revision: str = "__USER_AUTHORIZED_REVISION_REQUIRED__",
) -> list[dict[str, object]]:
    decoding = {"temperature": 0, "top_p": 1, "seed": "MODEL_SUPPORT_DEPENDENT"}
    decoding_hash = stable_hash(decoding)
    manifests: list[dict[str, object]] = []
    for row in test_rows:
        task_id = _text(row.get("benchmark_task_id"))
        task_type = _text(row.get("task_type"))
        ids = _candidate_ids(row)
        missing = [candidate_id for candidate_id in ids if candidate_id not in catalog]
        if missing:
            raise ValueError(f"Native manifest {task_id} has candidates missing from catalog: {missing[:5]}")
        documents = [dict(catalog[candidate_id]) for candidate_id in ids]
        output_schema = _output_schema_name(task_type, "native")
        prompt = _prompt_template("native", output_schema)
        prompt_hash = stable_hash(prompt)
        schema_hash = stable_hash(output_schema)
        visible = {
            "query": _text(row.get("query_text")),
            "task_type": task_type,
            "prediction_target": _text(row.get("prediction_target")),
            "candidate_documents": documents,
        }
        order_hash = stable_hash(ids)
        query_hash = stable_hash(visible["query"])
        cache_fields = _cache_key_fields(
            setting="native",
            task_id=task_id,
            query_hash=query_hash,
            candidate_order_hash=order_hash,
            prompt_hash=prompt_hash,
            output_schema_hash=schema_hash,
            model_name=model_name,
            model_revision=model_revision,
            decoding_config_hash=decoding_hash,
        )
        manifests.append(
            {
                "benchmark_task_id": task_id,
                "task_type": task_type,
                "source_dataset": _text(row.get("source_dataset")),
                "setting": "native",
                "candidate_count": len(ids),
                "candidate_count_bucket": candidate_bucket(len(ids)),
                "output_schema": output_schema,
                "prompt_template": prompt,
                "prompt_hash": prompt_hash,
                "model_visible_input": visible,
                "candidate_order_hash": order_hash,
                "query_hash": query_hash,
                "data_hash": stable_hash(visible),
                "decoding_config": decoding,
                "decoding_config_hash": decoding_hash,
                "model_name": model_name,
                "model_revision": model_revision,
                "cache_key_fields": cache_fields,
                "cache_key": stable_hash(cache_fields),
            }
        )
    return manifests


def _iter_rows(path: Path) -> Iterator[dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError(f"non-object row in {path}")
                    yield value
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
    else:
        raise ValueError(f"unsupported manifest format {path}")


def _candidate_id_list_from_global_row(row: Mapping[str, object]) -> list[str]:
    """Extract a ranked candidate-ID list from a full Global population row.

    Historical Global artifacts used several field names.  This routine only
    accepts explicit ranked/candidate ID lists; it never derives identities by
    fuzzy text matching.  Duplicate IDs are rejected by the caller.
    """

    for field in (
        "candidate_ids_json",
        "top_k_candidate_ids_json",
        "top20_candidate_ids_json",
        "retrieved_candidate_ids_json",
        "ranking_json",
        "candidate_ids",
    ):
        value = row.get(field)
        if value in (None, ""):
            continue
        parsed = _json_value(value, [])
        if isinstance(parsed, list) and parsed:
            result: list[str] = []
            for item in parsed:
                if isinstance(item, Mapping):
                    candidate_id = _text(item.get("candidate_id") or item.get("id"))
                else:
                    candidate_id = _text(item)
                if candidate_id:
                    result.append(candidate_id)
            if result:
                return result
    return []


def _extract_global_visible_input(
    row: Mapping[str, object],
    catalog: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    visible = row.get("model_visible_input")
    if isinstance(visible, str) and visible.strip():
        visible = json.loads(visible)
    if isinstance(visible, dict):
        result = dict(visible)
    else:
        documents = _json_value(row.get("candidate_documents_json"), [])
        if not isinstance(documents, list):
            documents = []
        if not documents:
            candidate_ids = _candidate_id_list_from_global_row(row)
            if not candidate_ids:
                raise ValueError(
                    f"Global row {_text(row.get('benchmark_task_id'))} has no explicit candidate documents or candidate ID list"
                )
            if catalog is None:
                raise ValueError(
                    f"Global row {_text(row.get('benchmark_task_id'))} requires catalog-backed candidate materialization"
                )
            missing = [candidate_id for candidate_id in candidate_ids if candidate_id not in catalog]
            if missing:
                raise ValueError(
                    f"Global row {_text(row.get('benchmark_task_id'))} has candidate IDs missing from catalog: {missing[:5]}"
                )
            documents = [dict(catalog[candidate_id]) for candidate_id in candidate_ids]
        result = {
            "query": _text(row.get("query_text") or row.get("query")),
            "task_type": _text(row.get("task_type")),
            "prediction_target": _text(row.get("prediction_target")),
            "candidate_documents": documents,
        }
    if not isinstance(result.get("candidate_documents"), list) or not result["candidate_documents"]:
        raise ValueError("Global visible input has no candidate documents")
    for document in result["candidate_documents"]:
        if not isinstance(document, Mapping):
            raise ValueError("Global visible input contains a non-object candidate document")
        if not _text(document.get("candidate_id")):
            raise ValueError("Global visible input contains a candidate document without candidate_id")
        if not (_text(document.get("canonical_name")) or _text(document.get("description"))):
            raise ValueError(
                f"Global candidate {_text(document.get('candidate_id'))} lacks both canonical_name and description"
            )
    return result


def rebuild_global_test_manifest(
    global_passing_population_path: Path,
    row_to_split: Mapping[str, str],
    *,
    catalog: Mapping[str, Mapping[str, object]] | None = None,
    visible_input_records: Mapping[str, Mapping[str, object]] | None = None,
    model_name: str = "__USER_AUTHORIZED_MODEL_REQUIRED__",
    model_revision: str = "__USER_AUTHORIZED_REVISION_REQUIRED__",
) -> list[dict[str, object]]:
    decoding = {"temperature": 0, "top_p": 1, "seed": "MODEL_SUPPORT_DEPENDENT"}
    decoding_hash = stable_hash(decoding)
    prompt = _prompt_template("global", "ranking_only")
    prompt_hash = stable_hash(prompt)
    schema_hash = stable_hash("ranking_only")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in _iter_rows(global_passing_population_path):
        task_id = _text(row.get("benchmark_task_id") or row.get("query_id"))
        if not task_id or task_id in seen:
            if task_id in seen:
                raise ValueError(f"duplicate Global passing-population task ID {task_id}")
            continue
        seen.add(task_id)
        if row_to_split.get(task_id) != "test":
            continue
        # The 20,612-row passing-population CSV deliberately carries only
        # hashes and provenance, so it cannot itself expose candidate text.
        # When supplied, the read-only eligible manifest contributes only the
        # matching task's query and explicit candidate IDs.  Membership still
        # comes exclusively from the passing population above.
        materialized_row: Mapping[str, object] = row
        if not _candidate_id_list_from_global_row(row) and visible_input_records is not None:
            source = visible_input_records.get(task_id)
            if source is None:
                raise ValueError(f"Global row {task_id} is missing from the visible-input source")
            if _text(row.get("source_dataset")) != _text(source.get("source")):
                raise ValueError(f"Global row {task_id} source mismatch in visible-input source")
            if _text(row.get("prediction_target")) != _text(source.get("target_level")):
                raise ValueError(f"Global row {task_id} target mismatch in visible-input source")
            if _text(row.get("query_text_hash")) != _text(source.get("query_signature")):
                raise ValueError(f"Global row {task_id} query signature mismatch in visible-input source")
            candidate_ids = source.get("candidate_ids")
            if not isinstance(candidate_ids, list) or not candidate_ids:
                raise ValueError(f"Global row {task_id} has no explicit candidate IDs in visible-input source")
            # `catalog_size` identifies the source catalog/snapshot (for
            # example, 1,427 APIs), whereas `candidate_ids` is the explicit
            # ranked candidate list supplied for this retrieval task.  These
            # intentionally have different cardinalities.  Candidate identity
            # is instead checked against the catalog below.
            materialized_row = dict(row)
            materialized_row.update(
                {
                    "query_text": _text(source.get("query")),
                    "task_type": _text(source.get("source_task_type") or source.get("task_type")),
                    "candidate_ids": candidate_ids,
                }
            )
        visible = _extract_global_visible_input(materialized_row, catalog)
        candidate_ids = [
            _text(document.get("candidate_id"))
            for document in visible["candidate_documents"]
            if isinstance(document, Mapping)
        ]
        if not candidate_ids or any(not candidate_id for candidate_id in candidate_ids):
            raise ValueError(f"Global row {task_id} has invalid candidate documents")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"Global row {task_id} has duplicate candidate IDs")
        order_hash = stable_hash(candidate_ids)
        query_hash = stable_hash(visible.get("query", ""))
        cache_fields = _cache_key_fields(
            setting="global",
            task_id=task_id,
            query_hash=query_hash,
            candidate_order_hash=order_hash,
            prompt_hash=prompt_hash,
            output_schema_hash=schema_hash,
            model_name=model_name,
            model_revision=model_revision,
            decoding_config_hash=decoding_hash,
        )
        result.append(
            {
                "benchmark_task_id": task_id,
                "task_type": _text(row.get("task_type") or visible.get("task_type")),
                "source_dataset": _text(row.get("source_dataset")),
                "setting": "global",
                "candidate_count": len(candidate_ids),
                "candidate_count_bucket": candidate_bucket(len(candidate_ids)),
                "output_schema": "ranking_only",
                "prompt_template": prompt,
                "prompt_hash": prompt_hash,
                "model_visible_input": visible,
                "candidate_order_hash": order_hash,
                "query_hash": query_hash,
                "data_hash": stable_hash(visible),
                "decoding_config": decoding,
                "decoding_config_hash": decoding_hash,
                "model_name": model_name,
                "model_revision": model_revision,
                "cache_key_fields": cache_fields,
                "cache_key": stable_hash(cache_fields),
            }
        )
    return result


def build_machine_manifest(
    machine_tasks: Sequence[Mapping[str, object]],
    *,
    model_name: str = "__USER_AUTHORIZED_MODEL_REQUIRED__",
    model_revision: str = "__USER_AUTHORIZED_REVISION_REQUIRED__",
) -> list[dict[str, object]]:
    decoding = {"temperature": 0, "top_p": 1, "seed": "MODEL_SUPPORT_DEPENDENT"}
    decoding_hash = stable_hash(decoding)
    prompt = _prompt_template("machine_challenge", "ranking_only")
    prompt_hash = stable_hash(prompt)
    schema_hash = stable_hash("ranking_only")
    result: list[dict[str, object]] = []
    for row in machine_tasks:
        task_id = _text(row.get("benchmark_task_id"))
        documents = _json_value(row.get("candidate_documents_json"), [])
        if not isinstance(documents, list) or not documents:
            raise ValueError(f"Machine Challenge row {task_id} has no candidate documents")
        candidate_ids = [_text(document.get("candidate_id")) for document in documents if isinstance(document, Mapping)]
        if len(candidate_ids) != len(documents) or len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"Machine Challenge row {task_id} has invalid/duplicate candidate IDs")
        visible = {
            "query": _text(row.get("query_text")),
            "task_type": _text(row.get("task_type")),
            "prediction_target": _text(row.get("prediction_target")),
            "candidate_documents": documents,
        }
        order_hash = stable_hash(candidate_ids)
        query_hash = stable_hash(visible["query"])
        cache_fields = _cache_key_fields(
            setting="machine_challenge",
            task_id=task_id,
            query_hash=query_hash,
            candidate_order_hash=order_hash,
            prompt_hash=prompt_hash,
            output_schema_hash=schema_hash,
            model_name=model_name,
            model_revision=model_revision,
            decoding_config_hash=decoding_hash,
        )
        result.append(
            {
                "benchmark_task_id": task_id,
                "task_type": visible["task_type"],
                "source_dataset": _text(row.get("source_dataset")),
                "setting": "machine_challenge",
                "candidate_count": len(candidate_ids),
                "candidate_count_bucket": candidate_bucket(len(candidate_ids)),
                "output_schema": "ranking_only",
                "prompt_template": prompt,
                "prompt_hash": prompt_hash,
                "model_visible_input": visible,
                "candidate_order_hash": order_hash,
                "query_hash": query_hash,
                "data_hash": stable_hash(visible),
                "decoding_config": decoding,
                "decoding_config_hash": decoding_hash,
                "model_name": model_name,
                "model_revision": model_revision,
                "cache_key_fields": cache_fields,
                "cache_key": stable_hash(cache_fields),
            }
        )
    return result


def stratified_smoke(manifest: Sequence[Mapping[str, object]], *, maximum_rows: int = 60) -> list[Mapping[str, object]]:
    by_stratum: dict[tuple[str, str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in manifest:
        key = (
            _text(row.get("task_type")),
            _text(row.get("source_dataset")),
            _text(row.get("candidate_count_bucket")),
            _text(row.get("output_schema")),
        )
        by_stratum[key].append(row)
    selected: list[Mapping[str, object]] = []
    for key in sorted(by_stratum):
        values = sorted(by_stratum[key], key=lambda row: stable_hash(["smoke", _text(row.get("benchmark_task_id"))]))
        selected.append(values[0])
        if len(selected) >= maximum_rows:
            return selected
    remaining = [row for row in manifest if row not in selected]
    remaining.sort(key=lambda row: stable_hash(["smoke-fill", _text(row.get("benchmark_task_id"))]))
    selected.extend(remaining[: max(0, maximum_rows - len(selected))])
    return selected


def estimate_tokens_and_cost(manifests: Mapping[str, Sequence[Mapping[str, object]]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for setting, rows in manifests.items():
        rendered = [
            row.get("prompt_template", "").replace(
                "{input_payload_json}",
                json.dumps(row.get("model_visible_input"), ensure_ascii=False, separators=(",", ":")),
            )
            for row in rows
        ]
        characters = sum(len(text) for text in rendered)
        estimated_input_tokens = math.ceil(characters / 4)
        estimated_output_tokens = sum(max(64, int(row.get("candidate_count", 1)) * 8) for row in rows)
        result.append(
            {
                "setting": setting,
                "rows": len(rows),
                "rendered_characters": characters,
                "estimated_input_tokens": estimated_input_tokens,
                "estimated_output_tokens": estimated_output_tokens,
                "input_unit_price_placeholder": "USER_SUPPLIED",
                "output_unit_price_placeholder": "USER_SUPPLIED",
                "total_cost_formula": "input_tokens*input_unit_price + output_tokens*output_unit_price",
            }
        )
    return result


def validate_llm_manifests(manifests: Mapping[str, Sequence[Mapping[str, object]]]) -> dict[str, object]:
    errors: list[dict[str, object]] = []
    cache_keys: set[str] = set()
    for setting, rows in manifests.items():
        for row in rows:
            task_id = _text(row.get("benchmark_task_id"))
            visible = row.get("model_visible_input")
            if not isinstance(visible, Mapping):
                errors.append({"setting": setting, "task_id": task_id, "error": "model_visible_input_not_object"})
                continue
            leaked = sorted(FORBIDDEN_VISIBLE_KEYS & set(visible))
            if leaked:
                errors.append({"setting": setting, "task_id": task_id, "error": "forbidden_visible_keys", "keys": leaked})
            documents = visible.get("candidate_documents")
            if not isinstance(documents, list) or not documents:
                errors.append({"setting": setting, "task_id": task_id, "error": "candidate_documents_missing"})
            else:
                for document in documents:
                    if not isinstance(document, Mapping) or not _text(document.get("candidate_id")):
                        errors.append({"setting": setting, "task_id": task_id, "error": "invalid_candidate_document"})
                        break
            cache_key = _text(row.get("cache_key"))
            fields = row.get("cache_key_fields")
            if not isinstance(fields, Mapping) or len(fields) != 8 or stable_hash(fields) != cache_key:
                errors.append({"setting": setting, "task_id": task_id, "error": "invalid_8_field_cache_key"})
            if cache_key in cache_keys:
                errors.append({"setting": setting, "task_id": task_id, "error": "duplicate_cache_key"})
            cache_keys.add(cache_key)
    return {
        "manifest_row_counts": {setting: len(rows) for setting, rows in manifests.items()},
        "error_count": len(errors),
        "errors": errors,
        "candidate_documents_present": not any(error["error"] == "candidate_documents_missing" for error in errors),
        "prompt_leakage_errors": sum(error["error"] == "forbidden_visible_keys" for error in errors),
        "cache_contract_pass": not any(error["error"] in {"invalid_8_field_cache_key", "duplicate_cache_key"} for error in errors),
        "formal_generative_llm_calls": 0,
        "ready": not errors,
    }
