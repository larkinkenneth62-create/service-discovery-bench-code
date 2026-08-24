import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.baselines import bm25_ranking, local_embedding_ranking, random_ranking


class BaselineTest(unittest.TestCase):
    def test_random_is_deterministic_per_task(self):
        candidates = ["a", "b", "c", "d"]
        self.assertEqual(random_ranking(candidates, seed=7, task_id="x"), random_ranking(candidates, seed=7, task_id="x"))
        self.assertEqual(set(random_ranking(candidates, seed=7, task_id="x")), set(candidates))

    def test_bm25_prefers_matching_document(self):
        docs = {"weather": "weather forecast temperature", "movie": "movie ratings cinema"}
        self.assertEqual(bm25_ranking("tomorrow weather", list(docs), docs)[0], "weather")

    def test_local_embedding_prefers_matching_phrase(self):
        docs = {"geo": "reverse geocoding coordinates to address", "music": "music playlist and albums"}
        self.assertEqual(local_embedding_ranking("reverse geocode these coordinates", list(docs), docs)[0], "geo")


if __name__ == "__main__":
    unittest.main()
