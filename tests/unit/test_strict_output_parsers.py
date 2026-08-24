import unittest

from servicediscoverybench.strict_output_parsers import (
    OutputValidationError,
    parse_ranking_and_selected_set,
    parse_ranking_only,
)


class StrictParserTest(unittest.TestCase):
    def test_ranking_only_exact_permutation(self):
        parsed = parse_ranking_only('{"ranked_candidate_ids":["b","a"]}', ["a", "b"])
        self.assertEqual(parsed["ranked_candidate_ids"], ["b", "a"])

    def test_ranking_only_rejects_missing_candidate(self):
        with self.assertRaises(OutputValidationError):
            parse_ranking_only('{"ranked_candidate_ids":["a"]}', ["a", "b"])

    def test_ranking_selected_subset(self):
        parsed = parse_ranking_and_selected_set(
            '{"ranked_candidate_ids":["b","a"],"selected_candidate_ids":["a"]}', ["a", "b"]
        )
        self.assertEqual(parsed["selected_candidate_ids"], ["a"])


if __name__ == "__main__":
    unittest.main()
