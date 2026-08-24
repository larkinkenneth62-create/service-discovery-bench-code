#!/usr/bin/env python3
"""Finalize a user-selected valid split candidate up to (not including) LLM calls.

This script fixes the remaining downstream implementation errors:
- formal selected-set cardinality is fitted on dev and never reads test Gold size;
- MachineChallenge-v1.2 uses task-balanced query selection, N_i formula,
  evidence-source round-robin, and fixed-seed candidate shuffle;
- Global formal rows are rebuilt from the full passing population under the
  selected split, never intersected with an old 128-row manifest;
- Native/Global/Machine manifests include real candidate documents, two strict
  output protocols, an eight-field cache key, stratified smoke samples, and a
  real input-size estimate;
- formal generative LLM calls remain zero.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
import zipfile

SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.baselines import bm25_ranking, local_embedding_ranking, random_ranking  # noqa: E402
from servicediscoverybench.cardinality_policy import (  # noqa: E402
    acceptable_gold_sets,
    fit_dev_topk_policy,
    oracle_selected_set,
    selected_set_from_policy,
)
from servicediscoverybench.machine_challenge_v12 import (  # noqa: E402
    build_machine_challenge,
    select_task_balanced_queries,
    stream_evidence_for_queries,
)
from servicediscoverybench.metrics import (  # noqa: E402
    evaluate_acceptable_gold_sets,
    ndcg_at_k,
    reciprocal_rank,
)
from servicediscoverybench.pre_llm_builder import (  # noqa: E402
    build_machine_manifest,
    build_native_manifest,
    estimate_tokens_and_cost,
    load_catalog,
    rebuild_global_test_manifest,
    stratified_smoke,
    validate_llm_manifests,
)
from servicediscoverybench.split_identity_v3 import stable_hash  # noqa: E402
from servicediscoverybench.joint_split_optimizer_v3 import TASKS, candidate_bucket  # noqa: E402

csv.field_size_limit(2_147_483_647)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_global_visible_records(path: Path) -> dict[str, dict[str, object]]:
    """Load read-only task inputs used to materialize the passing population."""
    records: dict[str, dict[str, object]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            task_id = str(record.get("query_id") or record.get("benchmark_task_id") or "").strip()
            if not task_id:
                raise ValueError(f"visible-input record {line_number} has no task ID")
            if task_id in records:
                raise ValueError(f"duplicate visible-input task ID {task_id}")
            records[task_id] = record
    return records


def first_nonempty(row: dict[str, object], *fields: str) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def load_rows(release_root: Path, split_manifest_path: Path) -> list[dict[str, object]]:
    provenance = {row["benchmark_task_id"]: row for row in read_csv(release_root / "manifests" / "task_provenance.csv")}
    split = {row["benchmark_task_id"]: row for row in read_csv(split_manifest_path)}
    rows: list[dict[str, object]] = []
    for task_type in TASKS:
        for raw in read_csv(release_root / "tasks" / f"{task_type}.csv"):
            task_id = raw["benchmark_task_id"]
            if task_id not in split or task_id not in provenance:
                raise ValueError(f"missing split/provenance for {task_id}")
            row: dict[str, object] = dict(raw)
            row.update(
                {
                    "task_type": raw.get("task_type") or task_type,
                    "source_dataset": raw.get("source_dataset") or provenance[task_id].get("source_dataset"),
                    "split": split[task_id]["split"],
                    "split_identity_group_v3": split[task_id].get("split_identity_group_v3", ""),
                }
            )
            rows.append(row)
    if len(rows) != 60_078:
        raise ValueError(f"expected 60,078 rows, got {len(rows)}")
    return rows


def rank_only_metrics(ranking: list[str], alternatives: list[list[str]], ks=(1, 3, 5, 10)) -> dict[str, float]:
    alternatives = [list(dict.fromkeys(values)) for values in alternatives if values]
    if not alternatives:
        raise ValueError("empty reference Gold alternatives")
    per_alt: list[dict[str, float]] = []
    for gold in alternatives:
        gold_set = set(gold)
        metrics = {"mrr": reciprocal_rank(ranking, gold_set)}
        for k in ks:
            top = ranking[:k]
            overlap = len(set(top) & gold_set)
            metrics[f"reference_recall@{k}"] = overlap / len(gold_set)
            metrics[f"reference_completeness@{k}"] = float(gold_set.issubset(set(top)))
            metrics[f"reference_hit@{k}"] = float(overlap > 0)
            metrics[f"reference_ndcg@{k}"] = ndcg_at_k(ranking, gold_set, k)
        per_alt.append(metrics)
    return {key: max(metrics[key] for metrics in per_alt) for key in per_alt[0]}


def mean_dict(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    keys = sorted(set.intersection(*(set(row) for row in rows)))
    return {key: sum(row[key] for row in rows) / len(rows) for key in keys}


def candidate_ids(row: dict[str, object]) -> list[str]:
    field = "candidate_services_json" if row.get("prediction_target") == "service" else "candidate_apis_json"
    parsed = json.loads(str(row.get(field) or "[]"))
    return [str(item) for item in parsed]


def document_text(document: dict[str, object]) -> str:
    return " ".join(
        str(document.get(field) or "")
        for field in ("canonical_name", "description", "provider_or_host", "api_schema_summary")
    ).strip()


def evaluate_native_baselines(rows: list[dict[str, object]], catalog: dict[str, dict[str, object]], out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    dev_rows = [row for row in rows if row["split"] == "dev"]
    test_rows = [row for row in rows if row["split"] == "test"]
    documents = {candidate_id: document_text(document) for candidate_id, document in catalog.items()}
    methods = {
        "bm25": lambda row, ids: bm25_ranking(str(row.get("query_text") or ""), ids, documents),
        "local_hashing": lambda row, ids: local_embedding_ranking(str(row.get("query_text") or ""), ids, documents),
    }
    report: dict[str, object] = {"dev_rows": len(dev_rows), "test_rows": len(test_rows), "methods": {}}

    for method_name, ranker in methods.items():
        dev_rankings = {str(row["benchmark_task_id"]): ranker(row, candidate_ids(row)) for row in dev_rows}
        policy = fit_dev_topk_policy(dev_rows, dev_rankings, policy_name=f"{method_name}_dev_topk_v1")
        write_json(out / f"{method_name}_CARDINALITY_POLICY.json", policy.to_dict())
        predictions: list[dict[str, object]] = []
        formal_scores: list[dict[str, float]] = []
        oracle_scores: list[dict[str, float]] = []
        by_task: dict[str, list[dict[str, float]]] = defaultdict(list)
        by_source: dict[str, list[dict[str, float]]] = defaultdict(list)
        for row in test_rows:
            ids = candidate_ids(row)
            ranking = ranker(row, ids)
            selected = selected_set_from_policy(row, ranking, policy)
            oracle_selected = oracle_selected_set(row, ranking)
            alternatives = acceptable_gold_sets(row)
            formal = evaluate_acceptable_gold_sets(ranking, alternatives, ks=(1, 3, 5, 10), predicted_set=selected)
            oracle = evaluate_acceptable_gold_sets(ranking, alternatives, ks=(1, 3, 5, 10), predicted_set=oracle_selected)
            formal_scores.append(formal)
            oracle_scores.append(oracle)
            by_task[str(row["task_type"])].append(formal)
            by_source[str(row["source_dataset"])].append(formal)
            predictions.append(
                {
                    "benchmark_task_id": row["benchmark_task_id"],
                    "ranking_json": json.dumps(ranking, ensure_ascii=False),
                    "formal_selected_set_json": json.dumps(selected, ensure_ascii=False),
                    "formal_selected_k": len(selected),
                    "oracle_selected_set_json": json.dumps(oracle_selected, ensure_ascii=False),
                    "oracle_cardinality_diagnostic_only": True,
                }
            )
        write_csv(out / f"{method_name}_PREDICTIONS.csv", predictions)
        task_rows = [
            {"task_type": task, "n": len(scores), **mean_dict(scores)} for task, scores in sorted(by_task.items())
        ]
        source_rows = [
            {"source_dataset": source, "n": len(scores), **mean_dict(scores)}
            for source, scores in sorted(by_source.items())
        ]
        write_csv(out / f"{method_name}_RESULTS_BY_TASK.csv", task_rows)
        write_csv(out / f"{method_name}_RESULTS_BY_SOURCE.csv", source_rows)
        write_json(
            out / f"{method_name}_SUMMARY.json",
            {
                "formal_dev_frozen_metrics": mean_dict(formal_scores),
                "oracle_cardinality_diagnostic": mean_dict(oracle_scores),
                "six_task_macro_formal": mean_dict([
                    {key: value for key, value in mean_dict(scores).items()}
                    for scores in by_task.values()
                    if scores
                ]),
                "uses_test_gold_for_formal_selected_set": False,
            },
        )
        report["methods"][method_name] = {
            "formal": mean_dict(formal_scores),
            "oracle": mean_dict(oracle_scores),
        }

    # Random: one dev seed for the formal policy, 20 independent test seeds.
    dev_rankings = {
        str(row["benchmark_task_id"]): random_ranking(candidate_ids(row), seed=0, task_id=str(row["benchmark_task_id"]))
        for row in dev_rows
    }
    random_policy = fit_dev_topk_policy(dev_rows, dev_rankings, policy_name="random_dev_topk_v1")
    write_json(out / "random_CARDINALITY_POLICY.json", random_policy.to_dict())
    seed_summaries: list[dict[str, object]] = []
    all_formal: list[dict[str, float]] = []
    for seed in range(20):
        seed_scores: list[dict[str, float]] = []
        for row in test_rows:
            ranking = random_ranking(candidate_ids(row), seed=seed, task_id=str(row["benchmark_task_id"]))
            selected = selected_set_from_policy(row, ranking, random_policy)
            seed_scores.append(
                evaluate_acceptable_gold_sets(ranking, acceptable_gold_sets(row), ks=(1, 3, 5, 10), predicted_set=selected)
            )
        summary = mean_dict(seed_scores)
        seed_summaries.append({"seed": seed, **summary})
        all_formal.extend(seed_scores)
    write_csv(out / "RANDOM_20_SEED_RESULTS.csv", seed_summaries)
    report["methods"]["random"] = {"20_seed_micro_mean": mean_dict(all_formal)}
    write_json(out / "BASELINE_REPORT.json", report)
    return report


def build_machine_challenge_outputs(
    rows: list[dict[str, object]],
    catalog: dict[str, dict[str, object]],
    evidence_path: Path,
    out: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    test_rows = [row for row in rows if row["split"] == "test"]
    main, reserve, selection_status = select_task_balanced_queries(test_rows)
    query_ids = {str(row["benchmark_task_id"]) for row in main + reserve}
    evidence = stream_evidence_for_queries(evidence_path, query_ids)
    result = build_machine_challenge(main, reserve, evidence, catalog)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "TASKS.csv", result.tasks)
    write_csv(out / "CANDIDATES.csv", result.candidates)
    write_csv(out / "ATTRITION_LEDGER.csv", result.attrition)
    write_csv(out / "CANDIDATE_SOURCE_DISTRIBUTION.csv", result.source_distribution)
    write_json(out / "QUERY_SELECTION_STATUS.json", selection_status)
    write_json(out / "STATUS.json", result.status)
    return result.tasks, result.status


def build_machine_baselines(machine_tasks: list[dict[str, object]], out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    methods = {}
    for method_name in ("random", "bm25", "local_hashing"):
        scores: list[dict[str, float]] = []
        by_task: dict[str, list[dict[str, float]]] = defaultdict(list)
        predictions: list[dict[str, object]] = []
        for row in machine_tasks:
            documents = json.loads(str(row["candidate_documents_json"]))
            ids = [str(document["candidate_id"]) for document in documents]
            docs = {str(document["candidate_id"]): document_text(document) for document in documents}
            if method_name == "random":
                ranking = random_ranking(ids, seed=20260805, task_id=str(row["benchmark_task_id"]))
            elif method_name == "bm25":
                ranking = bm25_ranking(str(row.get("query_text") or ""), ids, docs)
            else:
                ranking = local_embedding_ranking(str(row.get("query_text") or ""), ids, docs)
            alternatives = json.loads(str(row["reference_gold_sets_json"]))
            metric = rank_only_metrics(ranking, alternatives)
            scores.append(metric)
            by_task[str(row["task_type"])].append(metric)
            predictions.append(
                {
                    "benchmark_task_id": row["benchmark_task_id"],
                    "ranking_json": json.dumps(ranking, ensure_ascii=False),
                    "candidate_order_hash": row["candidate_order_hash"],
                }
            )
        write_csv(out / f"{method_name}_PREDICTIONS.csv", predictions)
        write_csv(
            out / f"{method_name}_RESULTS_BY_TASK.csv",
            [
                {"task_type": task, "n": len(values), **mean_dict(values)}
                for task, values in sorted(by_task.items())
            ],
        )
        methods[method_name] = mean_dict(scores)
    write_json(
        out / "MACHINE_CHALLENGE_BASELINE_REPORT.json",
        {
            "metrics_are_reference_gold_ranking_only": True,
            "unjudged_candidates_are_not_treated_as_formal_negatives": True,
            "methods": methods,
        },
    )
    return methods


def write_output_schemas_and_parsers(out: Path) -> None:
    schemas = out / "OUTPUT_SCHEMAS"
    parsers = out / "STRICT_PARSERS"
    schemas.mkdir(parents=True, exist_ok=True)
    parsers.mkdir(parents=True, exist_ok=True)
    write_json(
        schemas / "ranking_only.json",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["ranked_candidate_ids"],
            "properties": {"ranked_candidate_ids": {"type": "array", "items": {"type": "string"}}},
        },
    )
    write_json(
        schemas / "ranking_and_selected_set.json",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["ranked_candidate_ids", "selected_candidate_ids"],
            "properties": {
                "ranked_candidate_ids": {"type": "array", "items": {"type": "string"}},
                "selected_candidate_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    )
    parser_source = (ROOT / "src" / "servicediscoverybench" / "strict_output_parsers.py").read_text(encoding="utf-8")
    (parsers / "strict_output_parsers.py").write_text(parser_source, encoding="utf-8")


def make_bundle(out: Path) -> tuple[Path, str]:
    bundle_dir = out / "bundles"
    bundle_dir.mkdir(exist_ok=True)
    bundle = bundle_dir / f"ServiceDiscoveryBench_PRE_LLM_FIXED_CODE_REVIEW_{out.name}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file() and "bundles" not in path.relative_to(out).parts:
                archive.write(path, path.relative_to(out).as_posix())
    with zipfile.ZipFile(bundle) as archive:
        bad = archive.testzip()
    if bad:
        raise RuntimeError(f"bundle integrity error: {bad}")
    digest = sha256_file(bundle)
    bundle.with_suffix(bundle.suffix + ".sha256.txt").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return bundle, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-run", type=Path, required=True)
    parser.add_argument("--candidate", required=True, choices=["A_PROPORTIONAL", "B_REPRESENTATIVE", "C_MINIMAL_CHANGE"])
    parser.add_argument(
        "--release-root",
        type=Path,
        default=ROOT / "outputs/runs/20260722_133000_final_release/ServiceDiscoveryBench-v0.1",
    )
    parser.add_argument(
        "--global-passing-population",
        type=Path,
        default=ROOT / "outputs/runs/20260804_135557_pre_llm_all_in_one_v1/05_GLOBAL_SOURCENATIVE_QUERY_MANIFEST.csv",
    )
    parser.add_argument(
        "--machine-evidence",
        type=Path,
        default=ROOT / "artifacts/full_benchmark_v1/hard/candidate_pool.jsonl",
    )
    parser.add_argument(
        "--global-visible-source",
        type=Path,
        default=ROOT / "artifacts/full_benchmark_v1/manifests/eligible_manifest.jsonl",
        help="Read-only full-task manifest used only to materialize Global visible inputs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / f"outputs/runs/{time.strftime('%Y%m%d_%H%M%S')}_pre_llm_fixed_code",
    )
    args = parser.parse_args()
    out = args.output.resolve()
    args.split_run = args.split_run.resolve()
    args.release_root = args.release_root.resolve()
    args.global_passing_population = args.global_passing_population.resolve()
    args.machine_evidence = args.machine_evidence.resolve()
    args.global_visible_source = args.global_visible_source.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)

    candidate_dir = args.split_run / "02_CANDIDATES" / args.candidate
    status_path = candidate_dir / "STATUS.json"
    split_path = candidate_dir / "SPLIT_MANIFEST.csv"
    if not status_path.exists() or not split_path.exists():
        raise FileNotFoundError("selected split candidate artifacts are missing")
    candidate_status = json.loads(status_path.read_text(encoding="utf-8"))
    if not candidate_status.get("valid") and not candidate_status.get("candidate_valid"):
        raise RuntimeError("selected split candidate is not valid")

    rows = load_rows(args.release_root, split_path)
    catalog = load_catalog(
        args.release_root / "catalogs" / "service_catalog.jsonl",
        args.release_root / "catalogs" / "api_catalog.jsonl",
    )
    if not args.global_visible_source.exists():
        raise FileNotFoundError(args.global_visible_source)
    global_visible_records = load_global_visible_records(args.global_visible_source)
    baseline_report = evaluate_native_baselines(rows, catalog, out / "01_NATIVE_BASELINES")
    machine_tasks, machine_status = build_machine_challenge_outputs(
        rows, catalog, args.machine_evidence, out / "02_MACHINE_CHALLENGE_V1_2"
    )
    machine_baselines = build_machine_baselines(machine_tasks, out / "03_MACHINE_CHALLENGE_BASELINES")

    row_to_split = {str(row["benchmark_task_id"]): str(row["split"]) for row in rows}
    native_manifest = build_native_manifest([row for row in rows if row["split"] == "test"], catalog)
    global_manifest = rebuild_global_test_manifest(
        args.global_passing_population,
        row_to_split,
        catalog=catalog,
        visible_input_records=global_visible_records,
    )
    machine_manifest = build_machine_manifest(machine_tasks)
    manifests = {"native": native_manifest, "global": global_manifest, "machine_challenge": machine_manifest}
    llm_out = out / "04_LLM_PREFLIGHT"
    for setting, manifest in manifests.items():
        write_jsonl(llm_out / "FORMAL_MANIFESTS" / f"{setting}.jsonl", manifest)
        write_jsonl(llm_out / "SMOKE_MANIFESTS" / f"{setting}.jsonl", stratified_smoke(manifest))
    write_output_schemas_and_parsers(llm_out)
    estimates = estimate_tokens_and_cost(manifests)
    write_csv(llm_out / "LLM_INPUT_SIZE_AND_COST_ESTIMATE.csv", estimates)
    validation = validate_llm_manifests(manifests)
    write_json(llm_out / "LLM_READY_VALIDATION.json", validation)
    write_csv(
        llm_out / "FINAL_MANIFEST_SUMMARY.csv",
        [
            {
                "setting": setting,
                "formal_rows": len(manifest),
                "task_types": len({str(row.get('task_type')) for row in manifest}),
                "sources": len({str(row.get('source_dataset')) for row in manifest}),
            }
            for setting, manifest in manifests.items()
        ],
    )
    (llm_out / "FORMAL_LLM_RUN_INSTRUCTIONS.md").write_text(
        "# Formal LLM run instructions\n\n"
        "Formal generative calls remain zero. Obtain explicit user authorization and configure provider/model/revision before running.\n"
        "Native single uses ranking-only; Native multi/composable uses ranking+selected-set; Global and Machine Challenge use ranking-only.\n",
        encoding="utf-8",
    )

    final_status = {
        "status": (
            "PRE_LLM_FIXED_CODE_READY_USER_APPROVAL_REQUIRED"
            if validation["ready"] and machine_status["machine_challenge_ready"]
            else "PRE_LLM_FIXED_CODE_PARTIAL"
        ),
        "selected_split_candidate": args.candidate,
        "split_candidate_assignment_hash": candidate_status.get("assignment_hash"),
        "native_baselines_completed": True,
        "machine_challenge_status": machine_status,
        "machine_challenge_baselines": machine_baselines,
        "native_baseline_report": baseline_report,
        "llm_ready_validation": validation,
        "global_formal_rows": len(global_manifest),
        "native_formal_rows": len(native_manifest),
        "machine_formal_rows": len(machine_manifest),
        "authoritative_split_overwritten": False,
        "authoritative_promotion": False,
        "formal_generative_llm_calls": 0,
        "recommended_next_step": "USER_REVIEW_THEN_EXPLICITLY_AUTHORIZE_FORMAL_LLM",
    }
    write_json(out / "RUN_STATUS.json", final_status)
    write_json(
        out / "VALIDATION_SUMMARY.json",
        {
            "candidate_status_valid": True,
            "dev_frozen_cardinality": True,
            "test_gold_cardinality_used_for_formal_metrics": False,
            "oracle_cardinality_reported_separately": True,
            "machine_unjudged_candidates_not_formal_negatives": True,
            "global_rebuilt_from_full_passing_population": True,
            "llm_validation": validation,
            "formal_generative_llm_calls": 0,
        },
    )
    write_json(
        out / "ENVIRONMENT.json",
        {"python": sys.version, "platform": platform.platform(), "script_sha256": sha256_file(SCRIPT_PATH)},
    )

    files = [path for path in out.rglob("*") if path.is_file() and "bundles" not in path.relative_to(out).parts]
    write_csv(
        out / "OUTPUT_MANIFEST.csv",
        [
            {
                "relative_path": path.relative_to(out).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(files)
        ],
    )
    (out / "SHA256SUMS.txt").write_text(
        "\n".join(f"{sha256_file(path)}  {path.relative_to(out).as_posix()}" for path in sorted(files)) + "\n",
        encoding="utf-8",
    )
    bundle, digest = make_bundle(out)
    final_status.update(
        {
            "review_bundle_path": (bundle.relative_to(ROOT).as_posix() if bundle.is_relative_to(ROOT) else str(bundle)),
            "review_bundle_sha256": digest,
            "review_bundle_integrity_pass": True,
        }
    )
    write_json(out / "RUN_STATUS.json", final_status)
    print(json.dumps(final_status, ensure_ascii=False, indent=2))
    return 0 if final_status["status"] == "PRE_LLM_FIXED_CODE_READY_USER_APPROVAL_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
