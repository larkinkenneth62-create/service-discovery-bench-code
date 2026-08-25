from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "code" / "adjudicate_q1_length_failures_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_q1_length_adjudication", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LengthClassificationTests(unittest.TestCase):
    def test_duplicate_before_cutoff_is_model_format_failure(self) -> None:
        result = MODULE.classify_candidate_stream(
            '{"ranked_candidate_ids":["svc::x::001","svc::x::002","svc::x::001"',
            ["svc::x::001", "svc::x::002"],
        )
        self.assertEqual(result["classification"], "MODEL_FORMAT_FAILURE_BEFORE_LENGTH")
        self.assertEqual(result["first_duplicate_position"], 3)

    def test_out_of_pool_before_cutoff_is_model_format_failure(self) -> None:
        result = MODULE.classify_candidate_stream(
            '{"ranked_candidate_ids":["svc::x::001","svc::x::999"',
            ["svc::x::001", "svc::x::002"],
        )
        self.assertEqual(result["classification"], "MODEL_FORMAT_FAILURE_BEFORE_LENGTH")
        self.assertEqual(result["first_out_of_pool_position"], 2)

    def test_clean_legal_prefix_remains_blocking(self) -> None:
        result = MODULE.classify_candidate_stream(
            '{"ranked_candidate_ids":["svc::x::001"',
            ["svc::x::001", "svc::x::002"],
        )
        self.assertEqual(result["classification"], "POTENTIAL_CLEAN_PREFIX_BUDGET_TRUNCATION")
        self.assertFalse(result["schema_violation_before_cutoff"])


if __name__ == "__main__":
    unittest.main()
