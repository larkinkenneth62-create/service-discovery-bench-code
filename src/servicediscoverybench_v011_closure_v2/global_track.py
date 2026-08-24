from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from servicediscoverybench.baselines import tokens
from .common import read_csv, sha256_file, text, write_csv, write_json, write_jsonl
from .evaluation import mean_numeric, reference_metrics


FORBIDDEN_CATALOG_MARKERS = ("union", "16407", "cross_source")


class BM25Index:
    def __init__(self, documents: Mapping[str, str], *, k1: float = 1.5, b: float = 0.75):
        self.ids = sorted(documents)
        self.k1, self.b = k1, b
        self.terms = {candidate_id: tokens(documents[candidate_id]) for candidate_id in self.ids}
        self.avg_len = sum(len(values) for values in self.terms.values()) / max(len(self.terms), 1)
        self.df = Counter(term for values in self.terms.values() for term in set(values))
        self.tf = {candidate_id: Counter(values) for candidate_id, values in self.terms.items()}

    def rank(self, query: str, limit: int) -> list[str]:
        query_terms = tokens(query)
        n = len(self.ids)
        scores: dict[str, float] = {}
        for candidate_id in self.ids:
            values = self.terms[candidate_id]
            frequencies = self.tf[candidate_id]
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                df = self.df[term]
                idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                denominator = frequency + self.k1 * (1.0 - self.b + self.b * len(values) / max(self.avg_len, 1.0))
                score += idf * frequency * (self.k1 + 1.0) / denominator
            scores[candidate_id] = score
        return sorted(self.ids, key=lambda candidate_id: (-scores[candidate_id], candidate_id))[:limit]


def load_single_catalog(path: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            candidate_id = text(raw.get("canonical_candidate_id") or raw.get("canonical_id") or raw.get("candidate_id") or raw.get("service_id") or raw.get("api_id"))
            name = text(raw.get("candidate_name") or raw.get("canonical_name") or raw.get("name") or raw.get("service_name") or raw.get("api_name"))
            description = text(raw.get("candidate_description") or raw.get("description") or raw.get("retrieval_document") or raw.get("service_description") or raw.get("api_description"))
            if not candidate_id:
                raise ValueError(f"catalog record has no candidate ID: {path}:{line_number}")
            if not name and not description:
                raise ValueError(f"catalog record lacks name and description: {path}:{line_number}")
            document = {
                "candidate_id": candidate_id,
                "canonical_name": name,
                "description": description,
                "provider_or_host": text(raw.get("provider_or_host") or raw.get("provider") or raw.get("host_or_base_url") or raw.get("host")),
                "api_schema_summary": text(raw.get("response_schema_summary") or raw.get("api_schema_summary") or raw.get("parameter_schema_json") or raw.get("endpoint_or_operation"))[:2000],
            }
            candidate_id = text(document.get("candidate_id"))
            if candidate_id in result and result[candidate_id] != document:
                raise ValueError(f"conflicting candidate {candidate_id} in {path}:{line_number}")
            result[candidate_id] = document
    if not result:
        raise ValueError(f"empty source-native catalog: {path}")
    return result


def build_safe_registry(manifest_path: Path, output_path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, object]]]:
    safe: dict[str, dict[str, Any]] = {}
    audit: list[dict[str, object]] = []
    manifest_dir = manifest_path.parent
    for row in read_csv(manifest_path):
        catalog_id = text(row.get("catalog_id"))
        raw_path = text(row.get("catalog_path"))
        resolved = Path(raw_path)
        if not resolved.is_absolute():
            resolved = (manifest_dir / resolved).resolve()
        marker_text = f"{catalog_id} {resolved}".casefold()
        reasons: list[str] = []
        if any(marker in marker_text for marker in FORBIDDEN_CATALOG_MARKERS):
            reasons.append("FORBIDDEN_UNION_OR_CROSS_SOURCE_MARKER")
        if "servicediscoverybench-v0.1/catalogs" in resolved.as_posix().casefold():
            reasons.append("NATIVE_RELEASE_CATALOG_FORBIDDEN")
        if not resolved.exists():
            reasons.append("CATALOG_PATH_MISSING")
        if not text(row.get("scope_status")).startswith("PASS") or text(row.get("primary_global")).casefold() != "true":
            reasons.append("BRANCH_NOT_PRIMARY_PASSING")
        actual_sha = sha256_file(resolved) if resolved.exists() else ""
        if text(row.get("catalog_sha256")) and actual_sha != text(row.get("catalog_sha256")):
            reasons.append("CATALOG_SHA256_MISMATCH")
        documents: dict[str, dict[str, object]] = {}
        if not reasons:
            try:
                documents = load_single_catalog(resolved)
            except Exception as exc:
                reasons.append(f"CATALOG_SCHEMA_INVALID:{type(exc).__name__}")
        if documents and int(text(row.get("catalog_size")) or -1) != len(documents):
            reasons.append("CATALOG_SIZE_MISMATCH")
        status = "SAFE_SOURCE_NATIVE" if not reasons else "BLOCKED"
        audit_row = {
            **row, "resolved_catalog_path": str(resolved), "actual_sha256": actual_sha,
            "actual_catalog_size": len(documents), "registry_status": status,
            "blocking_reasons_json": json.dumps(reasons, ensure_ascii=False),
            "union_catalog_used": False, "native_candidate_copying": False,
        }
        audit.append(audit_row)
        if not reasons:
            safe[catalog_id] = {"path": resolved, "documents": documents, "row": dict(row)}
    # The frozen exact-identity blocker remains explicit even though the passing population omits this branch.
    audit.append({
        "source_dataset": "ToolBench", "prediction_target": "api", "catalog_id": "toolbench-api-blocked",
        "catalog_size": "", "catalog_path": "", "catalog_sha256": "", "scope_status": "BLOCKED_EXACT_SOURCE_NAMESPACE_GAP_V1",
        "primary_global": "false", "public_candidate_packaging": "false", "resolved_catalog_path": "",
        "actual_sha256": "", "actual_catalog_size": 0, "registry_status": "BLOCKED",
        "blocking_reasons_json": json.dumps(["BLOCKED_EXACT_SOURCE_NAMESPACE_GAP_V1"]),
        "union_catalog_used": False, "native_candidate_copying": False,
    })
    write_csv(output_path, audit)
    return safe, audit


def load_visible_queries(path: Path, wanted_ids: set[str]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            task_id = text(row.get("query_id") or row.get("benchmark_task_id"))
            if task_id in wanted_ids:
                if task_id in result:
                    raise ValueError(f"duplicate visible query {task_id}")
                result[task_id] = row
    missing = wanted_ids - set(result)
    if missing:
        raise ValueError(f"missing visible Global queries: {sorted(missing)[:5]}")
    return result


def _gold_sets(row: Mapping[str, object]) -> list[list[str]]:
    values = json.loads(text(row.get("gold_ids_json")) or "[]")
    if not isinstance(values, list) or not values:
        raise ValueError("Global passing row missing gold_ids_json")
    return [[str(item) for item in values]]


def run_global_track(
    passing_path: Path,
    registry_manifest: Path,
    visible_path: Path,
    row_to_split: Mapping[str, str],
    output: Path,
) -> tuple[list[dict[str, object]], dict[str, Any]]:
    output.mkdir(parents=True, exist_ok=True)
    safe, registry_audit = build_safe_registry(registry_manifest, output / "05_GLOBAL_SOURCE_NATIVE_REGISTRY_V0_1_1.csv")
    passing = read_csv(passing_path)
    if len(passing) != 20_612:
        raise ValueError(f"Global passing population must contain 20,612 rows, got {len(passing)}")
    eligible = [row for row in passing if row_to_split.get(text(row.get("benchmark_task_id"))) in {"dev", "test"}]
    visible = load_visible_queries(visible_path, {text(row.get("benchmark_task_id")) for row in eligible})
    indexes: dict[str, BM25Index] = {}
    dev_metrics: list[dict[str, object]] = []
    test_rankings: list[dict[str, object]] = []
    blocked_rows: list[dict[str, object]] = []
    for row in eligible:
        task_id = text(row.get("benchmark_task_id"))
        catalog_id = text(row.get("catalog_id"))
        if catalog_id not in safe:
            blocked_rows.append({"benchmark_task_id": task_id, "catalog_id": catalog_id, "reason": "NO_SAFE_SOURCE_NATIVE_REGISTRY"})
            continue
        source = visible[task_id]
        if text(row.get("source_dataset")) != text(source.get("source")):
            raise ValueError(f"Global source mismatch for {task_id}")
        if text(row.get("prediction_target")) != text(source.get("target_level")):
            raise ValueError(f"Global target mismatch for {task_id}")
        if text(row.get("query_text_hash")) != text(source.get("query_signature")):
            raise ValueError(f"Global query signature mismatch for {task_id}")
        documents = safe[catalog_id]["documents"]
        if catalog_id not in indexes:
            indexes[catalog_id] = BM25Index({candidate_id: " ".join(text(doc.get(field)) for field in ("canonical_name", "description", "provider_or_host", "api_schema_summary")) for candidate_id, doc in documents.items()})
        ranking50 = indexes[catalog_id].rank(text(source.get("query")), min(50, len(documents)))
        split = row_to_split[task_id]
        if split == "dev":
            metrics = reference_metrics(ranking50, _gold_sets(row), ks=(5, 10, 20, 50))
            dev_metrics.append({"benchmark_task_id": task_id, "catalog_id": catalog_id, "source_dataset": text(row.get("source_dataset")), **metrics})
        else:
            test_rankings.append({"row": row, "source": source, "ranking50": ranking50, "documents": documents})
    write_csv(output / "GLOBAL_BLOCKED_ROWS.csv", blocked_rows, ["benchmark_task_id", "catalog_id", "reason"])
    write_csv(output / "GLOBAL_DEV_METRICS_BY_QUERY.csv", dev_metrics)
    dev_summary = mean_numeric(dev_metrics)
    candidate_ks = (5, 10, 20, 50)
    target_recall = dev_summary.get("recall@50", 0.0) * 0.95
    target_completeness = dev_summary.get("completeness@50", 0.0) * 0.95
    selected_k = next((k for k in candidate_ks if dev_summary.get(f"recall@{k}", 0.0) >= target_recall and dev_summary.get(f"completeness@{k}", 0.0) >= target_completeness), 50)
    formal_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    for item in test_rankings:
        row, source, documents = item["row"], item["source"], item["documents"]
        ranking = item["ranking50"][: min(selected_k, len(item["ranking50"]))]
        metrics = reference_metrics(item["ranking50"], _gold_sets(row), ks=(5, 10, 20, 50))
        metric_rows.append({"benchmark_task_id": text(row.get("benchmark_task_id")), "catalog_id": text(row.get("catalog_id")), **metrics})
        formal_rows.append({
            "benchmark_task_id": text(row.get("benchmark_task_id")),
            "task_type": text(source.get("source_task_type")), "source_dataset": text(row.get("source_dataset")),
            "prediction_target": text(row.get("prediction_target")), "query_text": text(source.get("query")),
            "catalog_id": text(row.get("catalog_id")), "catalog_size": len(documents), "selected_k": len(ranking),
            "candidate_ids": ranking, "candidate_documents": [documents[candidate_id] for candidate_id in ranking],
            "retriever": "BM25_SOURCE_NATIVE_REGISTRY", "gold_injection": False,
            "native_candidate_copying": False, "union_catalog_used": False,
        })
    write_jsonl(output / "GLOBAL_FORMAL_TEST_ROWS.jsonl", formal_rows)
    write_csv(output / "GLOBAL_TEST_RETRIEVAL_METRICS_BY_QUERY.csv", metric_rows)
    unsafe_registry_rows = sum(row.get("registry_status") == "BLOCKED" for row in registry_audit)
    status = {
        "status": "GLOBAL_LLM_MANIFEST_PARTIAL" if unsafe_registry_rows or blocked_rows else "GLOBAL_LLM_MANIFEST_READY",
        "passing_population_rows": len(passing), "safe_registry_branches": len(safe),
        "blocked_registry_branches": unsafe_registry_rows, "blocked_split_rows": len(blocked_rows),
        "dev_rows": len(dev_metrics), "formal_test_rows": len(formal_rows), "dev_metrics": dev_summary,
        "selected_k": selected_k, "selection_rule": "smallest K reaching 95% of Dev K=50 recall and completeness",
        "toolbench_api_status": "BLOCKED_EXACT_SOURCE_NAMESPACE_GAP_V1", "six_task_global_macro": "NOT_AVAILABLE",
        "native_candidate_copying": False, "union_catalog_used": False, "gold_injection": False,
    }
    write_json(output / "GLOBAL_RETRIEVAL_SUMMARY.json", status)
    return formal_rows, status
