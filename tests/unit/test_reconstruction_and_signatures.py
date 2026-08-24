import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.reconstruction import deterministic_negatives, validate_candidate_space
from servicediscoverybench.signatures import review_content_fingerprint


class ReconstructionAndSignatureTest(unittest.TestCase):
    def test_candidate_hard_rule(self):
        self.assertEqual(validate_candidate_space(["a", "b"], ["a"]), (True, "valid"))
        self.assertEqual(validate_candidate_space(["a"], ["a"]), (False, "no_non_gold_candidate"))
        self.assertEqual(validate_candidate_space(["b"], ["a"]), (False, "gold_not_subset"))

    def test_negative_selection_is_deterministic_and_excludes_gold(self):
        first = deterministic_negatives(["a", "b", "c"], ["a"], "row", 2)
        self.assertEqual(first, deterministic_negatives(["c", "b", "a"], ["a"], "row", 2))
        self.assertNotIn("a", first)

    def test_management_metadata_does_not_change_review_fingerprint(self):
        row = {"prediction_target": "service", "task_type": "single_service_discovery", "query_text": "Find weather", "candidate_services_json": '["a","b"]', "gold_services_json": '["a"]'}
        changed = dict(row, reviewer_id="r2", run_id="new")
        self.assertEqual(review_content_fingerprint(row), review_content_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
