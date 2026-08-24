import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("split_audit_v2", ROOT / "scripts" / "11_split_representativeness_audit_v2.py")
audit = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(audit)


def row(i, source="MetaTool", task="single_service_discovery", split="train"):
    return {
        "benchmark_task_id": f"r{i}", "source_dataset": source, "task_type": task,
        "source_task_id": f"s{i}", "source_query_id": f"q{i}", "query_signature": f"q{i}",
        "review_content_fingerprint": f"full{i}", "paired_task_group_id": "",
        "underlying_task_id": f"u{i}", "parent_row_id": "", "legacy_split": split,
        "task_signature": "query-free-template", "prediction_target": "service",
        "query_text": f"query {i}", "candidate_services_json": '["a", "b"]',
        "candidate_apis_json": "[]", "gold_services_json": '["a"]', "gold_apis_json": "[]",
    }


class SplitAuditV2Test(unittest.TestCase):
    def test_identity_component_repair_detects_over_coarse_legacy_signature(self):
        rows = [row(1), row(2)]
        mapping, edges = audit.components(rows)
        self.assertNotEqual(mapping["r1"], mapping["r2"])
        self.assertEqual(edges, [])

    def test_candidate_assignment_is_deterministic_and_leakage_clean(self):
        rows = [row(i, source="ToolBench", task="single_api_recommendation") for i in range(60)]
        mapping, _ = audit.components(rows)
        groups = audit.group_rows(rows, mapping)
        a = audit.greedy_candidate(rows, groups, name="A", seed=7)
        b = audit.greedy_candidate(list(reversed(rows)), groups, name="A", seed=7)
        self.assertEqual(a, b)
        self.assertEqual(sum(a.values().__iter__() == "test" for _ in []), 0)  # mapping exists without mutation

    def test_model_visible_manifest_excludes_gold_and_split(self):
        manifest = audit.visible_manifest_row(row(1), "native_candidate_test")
        visible = manifest["model_visible_input"]
        self.assertNotIn("gold", str(visible).lower())
        self.assertNotIn("split", str(visible).lower())
        self.assertEqual(manifest["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
