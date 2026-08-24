#!/usr/bin/env python3
"""Run reproducible local baselines after G5; never bypass the split gate."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.baselines import bm25_ranking, local_embedding_ranking, random_ranking  # noqa: E402
from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.metrics import evaluate_acceptable_gold_sets, mean_metrics  # noqa: E402
from servicediscoverybench.splits import candidate_bucket  # noqa: E402

csv.field_size_limit(2_147_483_647)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def load_catalog(path: Path, id_field: str) -> dict[str, dict]:
    result = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                result[row[id_field]] = row
    return result


def blocked(output: Path, split_status: dict) -> int:
    output.mkdir(parents=True, exist_ok=False)
    value = {
        "stage": "G6", "status": "BLOCKED_G5_NOT_PASSED",
        "g5_status": split_status.get("status", "MISSING"), "baseline_runs_written": 0,
    }
    write_json(output / "RUN_STATUS.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 3


def metric_rows(grouped: dict[str, list[dict[str, float]]], group_field: str) -> list[dict]:
    output = []
    for group, scores in sorted(grouped.items()):
        output.append({group_field: group, "n": len(scores), **mean_metrics(scores)})
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rc1-root", required=True)
    parser.add_argument("--split-run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=20260719)
    args = parser.parse_args()
    root = Path(args.rc1_root).resolve()
    split_run = Path(args.split_run).resolve()
    output = Path(args.output).resolve()
    split_status_path = split_run / "RUN_STATUS.json"
    split_status = json.loads(split_status_path.read_text(encoding="utf-8")) if split_status_path.exists() else {}
    if split_status.get("status") != "GATE_PASSED" or split_status.get("g5_gate_passed") is not True:
        return blocked(output, split_status)

    output.mkdir(parents=True, exist_ok=False)
    results_root = output / "baseline" / "results"
    results_root.mkdir(parents=True)
    _, test_rows = read_csv(split_run / "splits" / "test.csv")
    services = load_catalog(root / "catalogs" / "service_catalog.jsonl", "service_id")
    apis = load_catalog(root / "catalogs" / "api_catalog.jsonl", "api_id")
    service_docs = {identifier: " ".join(str(row.get(key, "")) for key in ("canonical_name", "description", "provider", "host_or_base_url")) for identifier, row in services.items()}
    api_docs = {}
    for identifier, row in apis.items():
        parent = services.get(row.get("parent_service_id", ""), {})
        api_docs[identifier] = " ".join(str(value) for value in (
            row.get("canonical_name", ""), row.get("description", ""), row.get("endpoint", ""),
            row.get("http_method", ""), parent.get("canonical_name", ""), parent.get("description", ""),
        ))

    algorithms = {
        "random": lambda row, candidates, docs: random_ranking(candidates, seed=args.seed, task_id=row["benchmark_task_id"]),
        "bm25": lambda row, candidates, docs: bm25_ranking(row["query_text"], candidates, docs),
        "local_hashing_embedding": lambda row, candidates, docs: local_embedding_ranking(row["query_text"], candidates, docs),
    }
    comparison = []
    for name, ranker in algorithms.items():
        started = time.perf_counter()
        destination = results_root / name
        destination.mkdir()
        predictions, errors, all_scores = [], [], []
        by_task: dict[str, list[dict[str, float]]] = defaultdict(list)
        by_source: dict[str, list[dict[str, float]]] = defaultdict(list)
        by_bucket: dict[str, list[dict[str, float]]] = defaultdict(list)
        for row in test_rows:
            try:
                target = row["prediction_target"]
                candidates = json.loads(row["candidate_services_json"] if target == "service" else row["candidate_apis_json"])
                gold = json.loads(row["gold_services_json"] if target == "service" else row["gold_apis_json"])
                acceptable = json.loads(row["acceptable_gold_service_sets_json"] if target == "service" else row["acceptable_gold_api_sets_json"])
                acceptable = [values for values in acceptable if values] if isinstance(acceptable, list) else []
                if not acceptable:
                    acceptable = [gold]
                docs = service_docs if target == "service" else api_docs
                ranking = ranker(row, candidates, docs)
                predicted_set = ranking[: len(gold)]
                scores = evaluate_acceptable_gold_sets(ranking, acceptable, predicted_set=predicted_set)
                all_scores.append(scores)
                by_task[row["task_type"]].append(scores)
                by_source[row["source_dataset"]].append(scores)
                by_bucket[candidate_bucket(row["candidate_count"])].append(scores)
                predictions.append({
                    "benchmark_task_id": row["benchmark_task_id"], "task_type": row["task_type"],
                    "prediction_target": target, "source_dataset": row["source_dataset"],
                    "candidate_count_bucket": candidate_bucket(row["candidate_count"]),
                    "ranking_json": json.dumps(ranking, ensure_ascii=False, separators=(",", ":")),
                    "predicted_set_json": json.dumps(predicted_set, ensure_ascii=False, separators=(",", ":")),
                })
            except Exception as exc:  # fail ledger, never silently skip
                errors.append({"benchmark_task_id": row.get("benchmark_task_id", ""), "error_type": type(exc).__name__, "message": str(exc)})
        fields = ["benchmark_task_id", "task_type", "prediction_target", "source_dataset", "candidate_count_bucket", "ranking_json", "predicted_set_json"]
        write_csv(destination / "predictions.csv", predictions, fields)
        write_csv(destination / "errors.csv", errors, ["benchmark_task_id", "error_type", "message"])
        overall = {"baseline": name, "n": len(all_scores), "failure_count": len(errors), **mean_metrics(all_scores)}
        write_json(destination / "metrics_overall.json", overall)
        for filename, values, field in (
            ("metrics_by_task.csv", by_task, "task_type"),
            ("metrics_by_source.csv", by_source, "source_dataset"),
            ("metrics_by_candidate_bucket.csv", by_bucket, "candidate_count_bucket"),
        ):
            rows_out = metric_rows(values, field)
            write_csv(destination / filename, rows_out, list(rows_out[0]) if rows_out else [field, "n"])
        write_csv(destination / "metrics_by_domain.csv", [], ["domain", "n", "status"])
        manifest = {
            "baseline": name,
            "algorithm_version": {"random": "python_deterministic_shuffle_v1", "bm25": "bm25_local_candidate_v1", "local_hashing_embedding": "char_ngram_hash_cosine_v1"}[name],
            "python_version": platform.python_version(), "platform": platform.platform(), "random_seed": args.seed,
            "candidate_representation": "catalog canonical_name + description + identity metadata",
            "prompt_template_hash": "not_applicable_non_llm", "batching_or_chunking_strategy": "per_task_full_natural_candidate_space",
            "hardware": platform.machine(), "runtime_seconds": time.perf_counter() - started,
            "failure_count": len(errors), "split_manifest_sha256": sha256_file(split_run / "splits" / "split_manifest.csv"),
        }
        write_json(destination / "run_manifest.json", manifest)
        comparison.append(overall)

    write_json(results_root / "LLM_BASELINE_STATUS.json", {
        "zero_shot_ranking": "BLOCKED_MISSING_MODEL_CREDENTIALS",
        "llm_rerank": "BLOCKED_MISSING_MODEL_CREDENTIALS",
        "note": "No credential or model endpoint is assumed. Runner and local baselines remain reproducible without external transmission.",
    })
    comparison_fields = list(comparison[0]) if comparison else ["baseline", "n", "failure_count"]
    write_csv(results_root / "baseline_comparison.csv", comparison, comparison_fields)
    (results_root / "BASELINE_COMPARISON.md").write_text(
        "# Baseline comparison\n\n" + "\n".join(f"- {row['baseline']}: n={row['n']}, failures={row['failure_count']}, MRR={row.get('mrr', 0):.4f}" for row in comparison) + "\n\nLLM baselines are recorded as `BLOCKED_MISSING_MODEL_CREDENTIALS` unless an authorized reproducible endpoint is configured.\n",
        encoding="utf-8",
    )
    failures = sum(row["failure_count"] for row in comparison)
    status = {
        "stage": "G6", "status": "GATE_PASSED_LOCAL_BASELINES_LLM_BLOCKED" if failures == 0 else "BLOCKED_BASELINE_ERRORS",
        "g6_local_gate_passed": failures == 0, "test_rows": len(test_rows), "baselines_completed": list(algorithms),
        "llm_status": "BLOCKED_MISSING_MODEL_CREDENTIALS", "failure_count": failures,
    }
    write_json(output / "RUN_STATUS.json", status)
    inputs = [split_status_path, split_run / "splits" / "test.csv", split_run / "splits" / "split_manifest.csv", root / "catalogs" / "service_catalog.jsonl", root / "catalogs" / "api_catalog.jsonl"]
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs], ["resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    files = [path for path in output.rglob("*") if path.is_file() and path.name != "OUTPUT_MANIFEST.csv"]
    write_csv(output / "OUTPUT_MANIFEST.csv", [{"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in sorted(files)], ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
