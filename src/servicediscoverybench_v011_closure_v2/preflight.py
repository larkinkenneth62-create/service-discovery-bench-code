from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from servicediscoverybench.joint_split_optimizer_v3 import candidate_bucket
from servicediscoverybench.pre_llm_builder import estimate_tokens_and_cost, stratified_smoke, validate_llm_manifests
from servicediscoverybench.split_identity_v3 import stable_hash

from .common import json_list, text, write_csv, write_json, write_jsonl


def _schema(task_type: str, setting: str) -> str:
    if setting in {"global", "machine_challenge"} or task_type == "single_service_discovery":
        return "ranking_only"
    return "ranking_and_selected_set"


def _prompt(setting: str, schema: str) -> str:
    instruction = "Rank every candidate from most to least suitable. Use only candidate IDs in INPUT_JSON and return strict JSON."
    if schema == "ranking_and_selected_set":
        instruction += " Also select the complete candidate set needed to satisfy the request."
    return f"SETTING={setting}\n{instruction}\nOUTPUT_SCHEMA={schema}\nINPUT_JSON={{input_payload_json}}\n"


def _manifest_row(task_id: str, task_type: str, source: str, target: str, query: str, documents: Sequence[Mapping[str, object]], setting: str) -> dict[str, object]:
    ids = [text(document.get("candidate_id")) for document in documents]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"{setting}/{task_id}: invalid candidate IDs")
    for document in documents:
        if not (text(document.get("canonical_name")) or text(document.get("description"))):
            raise ValueError(f"{setting}/{task_id}: candidate lacks name and description")
    schema = _schema(task_type, setting)
    prompt = _prompt(setting, schema)
    visible = {
        "query": query, "task_type": task_type, "prediction_target": target,
        "candidate_documents": [dict(document) for document in documents],
    }
    query_hash = stable_hash(query)
    order_hash = stable_hash(ids)
    prompt_hash = stable_hash(prompt)
    schema_hash = stable_hash(schema)
    decoding_hash = stable_hash({"temperature": 0, "top_p": 1, "seed": "MODEL_SUPPORT_DEPENDENT"})
    fields = {
        "setting": setting, "benchmark_task_id": task_id, "query_hash": query_hash,
        "candidate_order_hash": order_hash, "prompt_hash": prompt_hash,
        "output_schema_hash": schema_hash,
        "model_identity_hash": stable_hash(["__USER_AUTHORIZED_MODEL_REQUIRED__", "__USER_AUTHORIZED_REVISION_REQUIRED__"]),
        "decoding_config_hash": decoding_hash,
    }
    return {
        "benchmark_task_id": task_id, "task_type": task_type, "source_dataset": source,
        "setting": setting, "candidate_count": len(ids), "candidate_count_bucket": candidate_bucket(len(ids)),
        "output_schema": schema, "prompt_template": prompt, "prompt_hash": prompt_hash,
        "model_visible_input": visible, "candidate_order_hash": order_hash, "query_hash": query_hash,
        "data_hash": stable_hash(visible), "decoding_config": {"temperature": 0, "top_p": 1, "seed": "MODEL_SUPPORT_DEPENDENT"},
        "decoding_config_hash": decoding_hash, "model_name": "__USER_AUTHORIZED_MODEL_REQUIRED__",
        "model_revision": "__USER_AUTHORIZED_REVISION_REQUIRED__", "cache_key_fields": fields,
        "cache_key": stable_hash(fields),
    }


def build_native(rows: Sequence[Mapping[str, object]], catalog: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        target = text(row.get("prediction_target"))
        field = "candidate_services_json" if target == "service" else "candidate_apis_json"
        ids = json_list(row.get(field))
        missing = [candidate_id for candidate_id in ids if candidate_id not in catalog]
        if missing:
            raise ValueError(f"Native manifest missing catalog docs: {missing[:5]}")
        result.append(_manifest_row(
            text(row.get("benchmark_task_id")), text(row.get("task_type")), text(row.get("source_dataset")),
            target, text(row.get("query_text")), [catalog[candidate_id] for candidate_id in ids], "native",
        ))
    return result


def build_global(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [_manifest_row(
        text(row.get("benchmark_task_id")), text(row.get("task_type")), text(row.get("source_dataset")),
        text(row.get("prediction_target")), text(row.get("query_text")), row.get("candidate_documents", []), "global",
    ) for row in rows]


def build_machine(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        documents = json.loads(text(row.get("candidate_documents_json")) or "[]")
        result.append(_manifest_row(
            text(row.get("benchmark_task_id")), text(row.get("task_type")), text(row.get("source_dataset")),
            text(row.get("prediction_target")), text(row.get("query_text")), documents, "machine_challenge",
        ))
    return result


RUNNER_TEMPLATE = '''#!/usr/bin/env python3
"""Provider-neutral offline response validator with resume/retry state.

This scaffold never calls a model or reads API keys. A separately authorized
provider adapter may write response JSONL; this runner validates and checkpoints it.
"""
import argparse, json
from pathlib import Path
from strict_output_parsers import parse_ranking_only, parse_ranking_and_selected_set

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--responses", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-parse-retries", type=int, default=2)
    args=p.parse_args()
    manifest=[json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    completed={}
    if args.output.exists():
        completed={row["cache_key"]:row for row in (json.loads(line) for line in args.output.read_text(encoding="utf-8").splitlines() if line.strip())}
    responses={}
    if args.responses and args.responses.exists():
        responses={row["cache_key"]:row for row in (json.loads(line) for line in args.responses.read_text(encoding="utf-8").splitlines() if line.strip())}
    output=list(completed.values())
    for row in manifest:
        if row["cache_key"] in completed:
            continue
        response=responses.get(row["cache_key"])
        if response is None:
            continue
        ids=[doc["candidate_id"] for doc in row["model_visible_input"]["candidate_documents"]]
        parser=parse_ranking_only if row["output_schema"]=="ranking_only" else parse_ranking_and_selected_set
        error=None
        for attempt in range(args.max_parse_retries+1):
            try:
                parsed=parser(response.get("payload", ""), ids)
                output.append({"cache_key":row["cache_key"],"benchmark_task_id":row["benchmark_task_id"],"parsed":parsed,"parse_attempt":attempt})
                error=None
                break
            except Exception as exc:
                error=str(exc)
        if error:
            output.append({"cache_key":row["cache_key"],"benchmark_task_id":row["benchmark_task_id"],"parse_error":error,"retry_exhausted":True})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\\n" for row in output),encoding="utf-8")
if __name__ == "__main__": main()
'''


def write_preflight(native_rows, global_rows, machine_rows, catalog, output: Path, strict_parser_source: Path) -> tuple[dict[str, list[dict[str, object]]], dict[str, Any]]:
    manifests = {
        "native": build_native(native_rows, catalog),
        "global": build_global(global_rows),
        "machine_challenge": build_machine(machine_rows),
    }
    for setting, rows in manifests.items():
        write_jsonl(output / "FORMAL_MANIFESTS" / f"{setting}.jsonl", rows)
        write_jsonl(output / "SMOKE_MANIFESTS" / f"{setting}.jsonl", stratified_smoke(rows))
    schema_dir = output / "OUTPUT_SCHEMAS"
    write_json(schema_dir / "ranking_only.schema.json", {"type": "object", "required": ["ranked_candidate_ids"], "additionalProperties": False, "properties": {"ranked_candidate_ids": {"type": "array", "items": {"type": "string"}}}})
    write_json(schema_dir / "ranking_and_selected_set.schema.json", {"type": "object", "required": ["ranked_candidate_ids", "selected_candidate_ids"], "additionalProperties": False, "properties": {"ranked_candidate_ids": {"type": "array", "items": {"type": "string"}}, "selected_candidate_ids": {"type": "array", "items": {"type": "string"}}}})
    parser_dir = output / "STRICT_PARSERS"
    parser_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(strict_parser_source, parser_dir / "strict_output_parsers.py")
    runner_dir = output / "RUNNER"
    runner_dir.mkdir(parents=True, exist_ok=True)
    (runner_dir / "provider_neutral_preflight_runner.py").write_text(RUNNER_TEMPLATE, encoding="utf-8")
    estimates = estimate_tokens_and_cost(manifests)
    write_csv(output / "LLM_INPUT_SIZE_AND_COST_ESTIMATE.csv", estimates)
    validation = validate_llm_manifests(manifests)
    protocol_errors = []
    for row in manifests["native"]:
        expected = _schema(text(row.get("task_type")), "native")
        if row.get("output_schema") != expected:
            protocol_errors.append({"task_id": row.get("benchmark_task_id"), "expected": expected})
    validation["protocol_errors"] = protocol_errors
    validation["ready"] = bool(validation.get("ready") and not protocol_errors and manifests["native"] and manifests["machine_challenge"])
    validation["formal_generative_llm_calls"] = 0
    validation["retry_resume_implemented"] = True
    write_json(output / "LLM_READY_VALIDATION.json", validation)
    (output / "FORMAL_LLM_RUN_INSTRUCTIONS.md").write_text(
        "# Formal LLM run instructions\n\nNo model call was made. Formal execution requires a separate explicit authorization, exact provider/model/revision, and budget.\n",
        encoding="utf-8",
    )
    return manifests, validation
