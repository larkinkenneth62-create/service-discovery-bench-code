import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validation" / "finalize_composable_review_metadata_v1_0.py"
SPEC = importlib.util.spec_from_file_location("finalize_composable_metadata", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MetadataRepairTest(unittest.TestCase):
    def test_only_two_fields_change(self):
        before = [{"x": "kept", "review_content_hash": "h", "adjudicator_id": "", "review_status": "READY_FOR_HUMAN_SEMANTIC_REVIEW"}]
        after = MODULE.repair(before, "primary_human_reviewer_01")
        MODULE.assert_only_metadata_changed(before, after)
        self.assertEqual(after[0]["x"], "kept")
        self.assertEqual(after[0]["adjudicator_id"], "primary_human_reviewer_01")
        self.assertEqual(after[0]["review_status"], "HUMAN_REVIEW_COMPLETED")

    def test_empty_reviewer_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.repair([], " ")


if __name__ == "__main__":
    unittest.main()
