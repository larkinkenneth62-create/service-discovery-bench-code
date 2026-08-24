import json
import unittest

from servicediscoverybench.cardinality_policy import fit_dev_topk_policy, selected_set_from_policy


class CardinalityPolicyTest(unittest.TestCase):
    def test_policy_is_fit_on_dev_and_does_not_use_test_gold(self):
        dev = []
        rankings = {}
        for index in range(10):
            task_id = f"d{index}"
            dev.append(
                {
                    "benchmark_task_id": task_id,
                    "task_type": "multi_api_recommendation",
                    "prediction_target": "api",
                    "candidate_count": "5",
                    "gold_apis_json": json.dumps(["a", "b"]),
                    "acceptable_gold_api_sets_json": json.dumps([["a", "b"]]),
                }
            )
            rankings[task_id] = ["a", "b", "c", "d", "e"]
        policy = fit_dev_topk_policy(dev, rankings, policy_name="test")
        self.assertFalse(policy.uses_test_gold)
        test_row = {
            "benchmark_task_id": "t",
            "task_type": "multi_api_recommendation",
            "prediction_target": "api",
            "candidate_count": "5",
            # Deliberately give test a different Gold cardinality; prediction K
            # must still be inherited from dev.
            "gold_apis_json": json.dumps(["a", "b", "c", "d"]),
            "acceptable_gold_api_sets_json": json.dumps([["a", "b", "c", "d"]]),
        }
        selected = selected_set_from_policy(test_row, ["a", "b", "c", "d", "e"], policy)
        self.assertEqual(len(selected), 2)


if __name__ == "__main__":
    unittest.main()
