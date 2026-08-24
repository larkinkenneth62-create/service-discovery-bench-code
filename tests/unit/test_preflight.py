import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "00_preflight.py"
SPEC = importlib.util.spec_from_file_location("sdbench_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PreflightHelpersTest(unittest.TestCase):
    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_bytes(b"abc")
            self.assertEqual(MODULE.sha256_file(path), hashlib.sha256(b"abc").hexdigest())

    def test_count_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.csv"
            path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
            count, columns = MODULE.count_csv(path)
            self.assertEqual(count, 2)
            self.assertEqual(columns, ["a", "b"])

    def test_stream_json_array_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text(json.dumps([{"x": "a,b"}, {"x": [1, 2]}, 3]), encoding="utf-8")
            count, kind = MODULE.inspect_json_bytes(path)
            self.assertEqual(count, 3)
            self.assertIn("array", kind)

    def test_zip_magic_is_not_parsed_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "encrypted.json"
            path.write_bytes(b"PK\x03\x04" + b"not-json")
            count, kind = MODULE.inspect_file(path)
            self.assertIsNone(count)
            self.assertIn("PKZIP", kind)

    def test_directory_listing_hash_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b").write_text("2", encoding="utf-8")
            (root / "a").write_text("1", encoding="utf-8")
            first = MODULE.directory_listing_hash(root)
            second = MODULE.directory_listing_hash(root)
            self.assertEqual(first, second)
            self.assertEqual(first[1], 2)

    def test_composable_review_requires_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["adjudicator_type", "adjudicator_id", "adjudicated_at"])
                writer.writeheader()
                writer.writerow({"adjudicator_type": "human_confirmed", "adjudicator_id": "", "adjudicated_at": "2026-07-19"})
            row = MODULE.InventoryRow("composable_reviewed_pack", str(path), True, "csv", path.stat().st_size, MODULE.sha256_file(path), 1, "", "", "human_review_evidence", "")
            status, issues = MODULE.inspect_composable_review(row)
            self.assertEqual(status["semantic_decision_status"], "RESOLVED_BY_OWNER")
            self.assertTrue(any(item["blocker_code"] == "MISSING_COMPOSABLE_REVIEWER_ID" for item in issues))
            self.assertEqual(status["review_content_hash_unique"], 0)


if __name__ == "__main__":
    unittest.main()
