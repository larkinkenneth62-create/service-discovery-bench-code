#!/usr/bin/env python
"""Regression tests for the role-aware composable dependency extractor."""

from __future__ import annotations

import csv
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "validation"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import composable_dependency_extractor_v0_3_2 as extractor  # noqa: E402


def step(index: int, arguments: dict, outputs: object, **extra: object) -> dict:
    return {
        "step_index": index,
        "service_name": extra.pop("service_name", f"service_{index}"),
        "api_name": extra.pop("api_name", f"api_{index}"),
        "function_name": extra.pop("function_name", f"api_{index}_for_service_{index}"),
        "arguments": arguments,
        "outputs": outputs,
        "source_json_path": f"$.steps[{index}]",
        **extra,
    }


def record(query: str, steps: list[dict]) -> dict:
    return {
        "trace_record_id": "fixture",
        "source_dataset": "ToolBench",
        "source_group": "G3",
        "source_task_id": "fixture_task",
        "instruction_query_id": "fixture",
        "query_text": query,
        "source_file": "fixture.json",
        "source_record_path": "$",
        "parse_summary": {"parse_status": "ok"},
        "steps": steps,
    }


class DependencyExtractorV032Tests(unittest.TestCase):
    def test_01_shared_coordinates_are_not_strong(self) -> None:
        data = record("Compare solar position and weather for this location", [
            step(1, {"lat": 40.7128, "lon": -74.0060}, {"error": "", "response": {"azimuth": 120, "elevation": 45}}),
            step(2, {"lat": 40.7128, "lon": -74.0060}, {"error": "", "response": {"temperature": 20}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 0)
        self.assertGreaterEqual(result["edge_source_type_counts"].get("shared_input_only", 0), 2)
        self.assertNotEqual(result["evidence_status"], "strong_objective_evidence_available")
        self.assertIn(result["suggested_class"], {"parallel_multi", "no_dependency"})

    def test_02_true_output_to_input(self) -> None:
        data = record("Find a hotel and check nearby weather", [
            step(1, {"city": "Boston"}, {"error": "", "response": {"hotel_address": "123 Main Street"}}),
            step(2, {"location": "123 Main Street"}, {"error": "", "response": {"temperature": 20}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 1)
        self.assertEqual(result["strong_edges"][0]["edge_source_type"], "upstream_output_to_downstream_input")

    def test_03_query_known_value_is_filtered(self) -> None:
        data = record("Use 123 Main Street to find a hotel and weather", [
            step(1, {"city": "Boston"}, {"error": "", "response": {"hotel_address": "123 Main Street"}}),
            step(2, {"location": "123 Main Street"}, {"error": "", "response": {"temperature": 20}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 0)
        self.assertGreater(result["edge_source_type_counts"].get("query_known_value_reuse", 0), 0)

    def test_04_upstream_input_echo_is_filtered(self) -> None:
        data = record("Compare two city services", [
            step(1, {"city": "Boston"}, {"error": "", "response": {"city": "Boston", "temperature": 20}}),
            step(2, {"city": "Boston"}, {"error": "", "response": {"air_quality": "good"}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 0)
        self.assertGreater(result["edge_source_type_counts"].get("echoed_upstream_input", 0), 0)

    def test_05_failed_call_cannot_supply_dependency(self) -> None:
        data = record("Look up an entity then use it", [
            step(1, {}, {"error": "Invalid API key", "response": ""}),
            step(2, {"value": "Invalid API key"}, {"error": "", "response": {"ok": "done"}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["record"]["steps"][0]["call_execution_status"], "error_only")
        self.assertEqual(result["strong_edge_count"], 0)
        self.assertTrue(result["failed_calls"])

    def test_06_multiple_weather_shared_parameters_are_parallel(self) -> None:
        args = {"lat": 51.5, "lon": -0.12, "date": "2026-07-15"}
        data = record("Compare weather and sunlight", [
            step(1, args, {"error": "", "response": {"temperature": 18}}),
            step(2, args, {"error": "", "response": {"sunrise": "05:00"}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 0)
        self.assertEqual(result["suggested_class"], "parallel_multi")

    def test_07_explicit_control_flow_dependency(self) -> None:
        data = record("Recommend an activity based on weather", [
            step(1, {"city": "Boston"}, {"error": "", "response": {"weather": "rain"}}),
            step(2, {"category": "indoor"}, {"error": "", "response": {"activity": "museum"}}, branch_condition={"prior_weather": "rain"}),
        ])
        result = extractor.assess_record(data)
        matches = [e for e in result["strong_edges"] if e["edge_source_type"] == "upstream_result_to_branch_condition"]
        self.assertEqual(len(matches), 1)

    def test_08_sequence_only_is_not_strong(self) -> None:
        data = record("Do two separate tasks", [
            step(1, {"a": "alpha"}, {"error": "", "response": {"x": "first result"}}),
            step(2, {"b": "bravo"}, {"error": "", "response": {"y": "second result"}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 0)
        self.assertGreater(result["edge_source_type_counts"].get("sequence_only", 0), 0)

    def test_09_arguments_never_become_upstream_strong_source(self) -> None:
        data = record("Use the same coordinates twice", [
            step(1, {"location": "shared place"}, {"error": "", "response": {"other": "unrelated"}}),
            step(2, {"location": "shared place"}, {"error": "", "response": {"done": "complete"}}),
        ])
        result = extractor.assess_record(data)
        self.assertFalse(any(e["strong_edge_eligible"] and e["upstream_field_role"] == "argument" for e in result["edges"]))

    def test_10_stringified_json_response_is_safely_parsed(self) -> None:
        payload = json.dumps({"error": "", "response": json.dumps({"hotel_address": "123 Main Street"})})
        data = record("Find a hotel then route to it", [
            step(1, {"city": "Boston"}, payload),
            step(2, {"destination": "123 Main Street"}, {"error": "", "response": {"route": "ready"}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["strong_edge_count"], 1)
        self.assertFalse(result["parse_errors"])
        bad = extractor.decode_embedded_structure('{"broken": ]}', "$.bad", errors := [])
        self.assertEqual(bad, '{"broken": ]}')
        self.assertEqual(errors[0]["error_type"], "embedded_structure_parse_failed")

    def test_11_error_with_valid_payload_is_partial_success(self) -> None:
        mixed = step(1, {}, {"error": "One optional field failed", "response": {"hotel_id": "H-1234"}})
        self.assertEqual(extractor.classify_call_execution_status(mixed), "partial_success")

    def test_12_same_step_internal_values_do_not_form_edges(self) -> None:
        data = record("One API call only", [
            step(1, {"city": "Boston"}, {"error": "", "response": {"city": "Boston"}}),
        ])
        result = extractor.assess_record(data)
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["strong_edge_count"], 0)

    def test_13_original_eight_strong_fixtures_are_reclassified_without_preservation_constraint(self) -> None:
        path = ROOT / "outputs/composable_corpus_mining_v0_2/composable_evidence_review_items_v0_2.csv"
        if not path.exists():
            self.skipTest("private generated fixture is intentionally omitted from the code-only mirror")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if extractor.truthy(row.get("current_322_member"))]
        self.assertEqual(len(rows), 8)
        statuses: dict[str, str] = {}
        for row in rows:
            data = record(row.get("query_text", ""), json.loads(row.get("ordered_steps_json") or "[]"))
            data["source_task_id"] = row.get("source_task_id", "")
            result = extractor.assess_record(data)
            statuses[data["source_task_id"]] = result["evidence_status"]
            self.assertIn(result["evidence_status"], {
                "strong_objective_evidence_available", "no_dependency_evidence", "sequence_only", "parse_failed",
            })
        print("original_8_v0_3_2_statuses=" + json.dumps(statuses, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
