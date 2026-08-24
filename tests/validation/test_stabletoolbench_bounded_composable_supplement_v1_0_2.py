#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "validation"
sys.path.insert(0, str(SCRIPT_DIR))

import run_stabletoolbench_bounded_composable_supplement_v1_0_2 as runner  # noqa: E402


class StableSupplementTests(unittest.TestCase):
    def test_normalized_exact_key(self) -> None:
        self.assertEqual(runner.normalize_exact_key(" StableToolBench_G2_1643 "), "g2_1643")
        self.assertEqual(runner.normalize_exact_key("1643"), "1643")

    def test_schema_only_is_not_execution_trace(self) -> None:
        record = {
            "query_id": 1,
            "query": "Find a word.",
            "api_list": [{"required_parameters": [], "template_response": {"word": "str"}}],
            "relevant APIs": [["Words", "Get Word"]],
        }
        scan = runner.scan_execution_evidence(record)
        self.assertFalse(scan["actual_execution_like_evidence"])
        self.assertFalse(scan["arguments_found"])
        self.assertFalse(scan["outputs_found"])

    def test_exact_join_rejects_ambiguity(self) -> None:
        source_index = {("G2", "7"): [{"query_id": 7}, {"query_id": 7}]}
        status, _, key = runner.exact_join_one(
            {"source_group": "G2", "source_instruction_id": "7"}, source_index
        )
        self.assertEqual(status, "AMBIGUOUS")
        self.assertEqual(key, "query_id")

    def test_double_annotation_size(self) -> None:
        self.assertEqual(runner.double_annotation_size(103), 21)
        self.assertEqual(runner.double_annotation_size(120), 24)
        self.assertEqual(runner.double_annotation_size(200), 30)

    def test_stratified_subset_preserves_source_group_balance(self) -> None:
        rows = [
            {
                "underlying_task_id": f"G2-{index}",
                "source_group": "G2",
                "catalog_domain_signature": f"D{index % 9}",
                "dependency_type_distribution_json": "[]",
            }
            for index in range(54)
        ] + [
            {
                "underlying_task_id": f"G3-{index}",
                "source_group": "G3",
                "catalog_domain_signature": f"D{index % 9}",
                "dependency_type_distribution_json": "[]",
            }
            for index in range(49)
        ]
        selected = runner.stratified_subset(rows, 21)
        self.assertEqual(sum(row["source_group"] == "G2" for row in selected), 11)
        self.assertEqual(sum(row["source_group"] == "G3" for row in selected), 10)


if __name__ == "__main__":
    unittest.main()
