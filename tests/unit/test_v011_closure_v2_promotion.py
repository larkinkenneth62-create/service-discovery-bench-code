import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench_v011_closure_v2.evaluation import CardinalityPolicy, CardinalityRule
from servicediscoverybench_v011_closure_v2.global_track import build_safe_registry, load_single_catalog
from servicediscoverybench_v011_closure_v2.promotion import ALLOWED_ROW_CHANGES


class V011ClosureV2PromotionTests(unittest.TestCase):
    def test_allowed_changes_are_only_split_management_fields(self):
        self.assertEqual(ALLOWED_ROW_CHANGES, {
            "split", "split_group_id", "split_identity_group_v3", "split_version", "legacy_split", "legacy_split_group_id"
        })

    def test_single_api_is_not_forced_to_one(self):
        rule = CardinalityRule("single_api_recommendation", "6-20", 3, 10, 0.8, "DEV_F1_OPTIMIZED")
        policy = CardinalityPolicy({("single_api_recommendation", "6-20"): rule}, {"single_api_recommendation": 3}, 1)
        self.assertEqual(policy.predict_k({"task_type": "single_api_recommendation"}, 10), 3)
        self.assertEqual(policy.predict_k({"task_type": "single_service_discovery"}, 10), 1)

    def test_registry_rejects_union_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "union_16407.jsonl"
            catalog.write_text(json.dumps({"candidate_id": "x", "canonical_name": "X"}) + "\n", encoding="utf-8")
            import hashlib
            digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
            manifest = root / "registry.csv"
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["source_dataset", "prediction_target", "catalog_id", "catalog_size", "catalog_path", "catalog_sha256", "scope_status", "primary_global"])
                writer.writeheader()
                writer.writerow({"source_dataset": "X", "prediction_target": "service", "catalog_id": "union-16407", "catalog_size": 1, "catalog_path": catalog.name, "catalog_sha256": digest, "scope_status": "PASS", "primary_global": "true"})
            safe, audit = build_safe_registry(manifest, root / "safe.csv")
            self.assertFalse(safe)
            self.assertEqual(audit[0]["registry_status"], "BLOCKED")
            self.assertFalse(audit[0]["union_catalog_used"])

    def test_source_native_catalog_schema_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source-native.jsonl"
            path.write_text(json.dumps({
                "canonical_candidate_id": "svc::x", "candidate_name": "Service X",
                "candidate_description": "Does X", "provider": "example.test"
            }) + "\n", encoding="utf-8")
            catalog = load_single_catalog(path)
            self.assertEqual(catalog["svc::x"]["canonical_name"], "Service X")
            self.assertEqual(catalog["svc::x"]["provider_or_host"], "example.test")

    def test_safe_source_native_registry_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "source-native.jsonl"
            catalog.write_text(json.dumps({"canonical_candidate_id": "svc::x", "candidate_name": "X", "candidate_description": "Does X"}) + "\n", encoding="utf-8")
            import hashlib
            digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
            manifest = root / "registry.csv"
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["source_dataset", "prediction_target", "catalog_id", "catalog_size", "catalog_path", "catalog_sha256", "scope_status", "primary_global"])
                writer.writeheader()
                writer.writerow({"source_dataset": "X", "prediction_target": "service", "catalog_id": "source-native-v1", "catalog_size": 1, "catalog_path": catalog.name, "catalog_sha256": digest, "scope_status": "PASS_SOURCE_FULL", "primary_global": "true"})
            safe, audit = build_safe_registry(manifest, root / "safe.csv")
            self.assertIn("source-native-v1", safe)
            self.assertEqual(audit[0]["registry_status"], "SAFE_SOURCE_NATIVE")


if __name__ == "__main__":
    unittest.main()
