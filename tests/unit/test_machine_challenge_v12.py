import json
import unittest

from servicediscoverybench.machine_challenge_v12 import select_machine_candidates


class MachineChallengeV12Test(unittest.TestCase):
    def test_single_gold_builds_ten_candidates_and_shuffles(self):
        row = {
            "benchmark_task_id": "q1",
            "prediction_target": "api",
            "gold_apis_json": json.dumps(["gold"]),
            "acceptable_gold_api_sets_json": json.dumps([["gold"]]),
        }
        evidence = []
        for index in range(20):
            evidence.append(
                {
                    "query_id": "q1",
                    "candidate_id": f"c{index}",
                    "retrieval_sources": [{"method": "bm25", "rank": index + 1}],
                }
            )
        catalog = {"gold"} | {f"c{i}" for i in range(20)}
        ids, info = select_machine_candidates(row, evidence, catalog)
        self.assertEqual(info["status"], "READY")
        self.assertEqual(len(ids), 10)
        self.assertIn("gold", ids)
        self.assertEqual(len(set(ids)), 10)
        self.assertNotEqual(ids[0], "gold")  # fixed shuffle, not Gold-first

    def test_insufficient_candidates_fail_closed(self):
        row = {
            "benchmark_task_id": "q2",
            "prediction_target": "api",
            "gold_apis_json": json.dumps(["gold"]),
            "acceptable_gold_api_sets_json": json.dumps([["gold"]]),
        }
        evidence = [{"query_id": "q2", "candidate_id": "c1", "method": "bm25"}]
        ids, info = select_machine_candidates(row, evidence, {"gold", "c1"})
        self.assertEqual(ids, [])
        self.assertEqual(info["status"], "INSUFFICIENT_MACHINE_CANDIDATES")


if __name__ == "__main__":
    unittest.main()
