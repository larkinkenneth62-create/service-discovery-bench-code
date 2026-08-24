import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.metrics import (
    evaluate_acceptable_gold_sets,
    evaluate_against_gold,
    mean_metrics,
    ndcg_at_k,
    precision_recall_f1,
)


class MetricsTest(unittest.TestCase):
    def test_precision_recall_f1(self):
        precision, recall, f1 = precision_recall_f1(["a", "x"], ["a", "b"])
        self.assertEqual((precision, recall, f1), (0.5, 0.5, 0.5))

    def test_ranking_metrics(self):
        values = evaluate_against_gold(["x", "a", "b"], ["a", "b"], ks=(1, 3))
        self.assertEqual(values["mrr"], 0.5)
        self.assertEqual(values["recall@1"], 0.0)
        self.assertEqual(values["recall@3"], 1.0)
        self.assertGreater(ndcg_at_k(["x", "a", "b"], ["a", "b"], 3), 0.6)

    def test_alternative_gold_sets_are_not_unioned(self):
        values = evaluate_acceptable_gold_sets(
            ["c", "d", "a", "b"],
            [["a", "b"], ["c", "d"]],
            ks=(1, 2),
            predicted_set=["c", "d"],
        )
        self.assertEqual(values["exact_set_match"], 1.0)
        self.assertEqual(values["recall@2"], 1.0)

    def test_duplicate_ranked_items_do_not_gain_credit(self):
        values = evaluate_against_gold(["a", "a", "x"], ["a", "b"], ks=(2,))
        self.assertEqual(values["precision@2"], 0.5)
        self.assertEqual(values["recall@2"], 0.5)

    def test_mean_metrics(self):
        self.assertEqual(mean_metrics([{"x": 0.0}, {"x": 1.0}]), {"x": 0.5})


if __name__ == "__main__":
    unittest.main()
