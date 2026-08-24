from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from servicediscoverybench.pre_llm_builder import rebuild_global_test_manifest, validate_llm_manifests


class PreLLMBuilderTest(unittest.TestCase):
    def test_global_population_can_materialize_documents_from_catalog(self) -> None:
        catalog = {
            "api_a": {
                "candidate_id": "api_a",
                "canonical_name": "API A",
                "description": "Does A",
                "provider_or_host": "example.test",
                "api_schema_summary": "x:string",
            },
            "api_b": {
                "candidate_id": "api_b",
                "canonical_name": "API B",
                "description": "Does B",
                "provider_or_host": "example.test",
                "api_schema_summary": "y:string",
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "global.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "benchmark_task_id",
                        "task_type",
                        "source_dataset",
                        "prediction_target",
                        "query_text",
                        "top20_candidate_ids_json",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "benchmark_task_id": "q1",
                        "task_type": "single_api_recommendation",
                        "source_dataset": "ToolBench",
                        "prediction_target": "api",
                        "query_text": "find A",
                        "top20_candidate_ids_json": json.dumps(["api_a", "api_b"]),
                    }
                )
                writer.writerow(
                    {
                        "benchmark_task_id": "q2",
                        "task_type": "single_api_recommendation",
                        "source_dataset": "ToolBench",
                        "prediction_target": "api",
                        "query_text": "find B",
                        "top20_candidate_ids_json": json.dumps(["api_b", "api_a"]),
                    }
                )
            manifest = rebuild_global_test_manifest(path, {"q1": "test", "q2": "train"}, catalog=catalog)
        self.assertEqual(len(manifest), 1)
        visible = manifest[0]["model_visible_input"]
        self.assertEqual([doc["candidate_id"] for doc in visible["candidate_documents"]], ["api_a", "api_b"])
        self.assertTrue(validate_llm_manifests({"global": manifest})["ready"])

    def test_missing_catalog_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "global.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["benchmark_task_id", "task_type", "query_text", "candidate_ids_json"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "benchmark_task_id": "q1",
                        "task_type": "single_api_recommendation",
                        "query_text": "find A",
                        "candidate_ids_json": json.dumps(["missing"]),
                    }
                )
            with self.assertRaises(ValueError):
                rebuild_global_test_manifest(path, {"q1": "test"}, catalog={})

    def test_global_population_can_use_signature_checked_visible_input_source(self) -> None:
        catalog = {"api_a": {"candidate_id": "api_a", "canonical_name": "API A", "description": "Does A"}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "global.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["benchmark_task_id", "source_dataset", "prediction_target", "query_text_hash", "catalog_size"])
                writer.writeheader()
                writer.writerow({"benchmark_task_id": "q1", "source_dataset": "ToolBench", "prediction_target": "api", "query_text_hash": "sig", "catalog_size": "1"})
            visible_source = {"q1": {"source": "ToolBench", "target_level": "api", "query_signature": "sig", "query": "find A", "source_task_type": "single_api_recommendation", "candidate_ids": ["api_a"]}}
            manifest = rebuild_global_test_manifest(path, {"q1": "test"}, catalog=catalog, visible_input_records=visible_source)
        self.assertEqual(manifest[0]["model_visible_input"]["query"], "find A")
        self.assertEqual(manifest[0]["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
