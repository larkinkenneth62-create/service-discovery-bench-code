import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.qa import (
    cohens_kappa,
    human_review_validation_errors,
    raw_agreement,
    wilson_interval,
)


class QAMetricsTest(unittest.TestCase):
    def test_wilson_bounds(self):
        low, high = wilson_interval(70, 70)
        self.assertGreater(low, 0.94)
        self.assertEqual(high, 1.0)
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))

    def test_kappa_perfect_and_chance(self):
        self.assertEqual(cohens_kappa(["keep", "remove"], ["keep", "remove"]), 1.0)
        self.assertAlmostEqual(cohens_kappa(["keep", "keep", "remove", "remove"], ["keep", "remove", "keep", "remove"]), 0.0)

    def test_raw_agreement(self):
        self.assertEqual(raw_agreement(["a", "b"], ["a", "a"]), 0.5)

    def valid_review(self):
        return {
            "semantic_alignment_check": "aligned",
            "gold_validity_check": "true",
            "candidate_validity_check": "true",
            "service_catalog_check": "pass",
            "task_type_check": "pass",
            "leakage_check": "no_blocking_leak",
            "dependency_check": "not_applicable_parallel_multi",
            "final_decision": "keep",
            "severity": "none",
            "error_type": "",
            "notes": "",
            "reviewed_at": "2026-07-20T12:00:00.000Z",
        }

    def test_completed_ordinary_review_contract(self):
        self.assertEqual(human_review_validation_errors(self.valid_review(), composable=False), [])

    def test_composable_dependency_contract(self):
        row = self.valid_review()
        row["dependency_check"] = "true"
        self.assertEqual(human_review_validation_errors(row, composable=True), [])
        row["dependency_check"] = "not_applicable_parallel_multi"
        self.assertTrue(any("composable" in issue for issue in human_review_validation_errors(row, composable=True)))

    def test_remove_and_uncertain_require_explanation(self):
        row = self.valid_review()
        row["final_decision"] = "remove"
        row["severity"] = "major"
        self.assertTrue(any("error_type" in issue for issue in human_review_validation_errors(row, composable=False)))
        row["final_decision"] = "uncertain"
        row["error_type"] = "insufficient_evidence"
        self.assertTrue(any("notes" in issue for issue in human_review_validation_errors(row, composable=False)))

    def test_invalid_enum_timestamp_and_severe_keep_fail_closed(self):
        row = self.valid_review()
        row["gold_validity_check"] = "yes"
        row["severity"] = "critical"
        row["reviewed_at"] = "yesterday"
        errors = human_review_validation_errors(row, composable=False)
        self.assertTrue(any("gold_validity_check" in issue for issue in errors))
        self.assertTrue(any("major or critical" in issue for issue in errors))
        self.assertTrue(any("ISO-8601" in issue for issue in errors))


if __name__ == "__main__":
    unittest.main()
