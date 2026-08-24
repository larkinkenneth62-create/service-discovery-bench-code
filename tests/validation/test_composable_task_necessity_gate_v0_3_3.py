#!/usr/bin/env python3
"""Regression fixtures for the composable task-necessity gate v0.3.3."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts/validation"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import composable_task_necessity_gate_v0_3_3 as gate  # noqa: E402


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def step(
    index: int,
    service: str,
    api: str,
    outputs: object,
    status: str = "success",
) -> dict:
    return {
        "step_index": index,
        "service_name": service,
        "api_name": api,
        "function_name": f"{api}_for_{service}",
        "arguments": {},
        "outputs": outputs,
        "call_execution_status": status,
    }


def edge(from_step: int, to_step: int, status: str = "success") -> dict:
    return {
        "from_step": from_step,
        "to_step": to_step,
        "strong_edge_eligible": True,
        "edge_source_type": "upstream_output_to_downstream_input",
        "upstream_field_role": "output",
        "downstream_field_role": "argument",
        "upstream_call_execution_status": status,
        "downstream_call_execution_status": status,
    }


def base_row(
    query: str,
    steps: list[dict],
    edges: list[dict],
    gold_services: list[str],
    gold_apis: list[tuple[str, str]],
    disconnected: list[dict] | None = None,
) -> dict:
    services = [{"service_key": name, "service_name": name} for name in gold_services]
    services.append({"service_key": "service_negative", "service_name": "Service Negative"})
    apis = [
        {"function_key": api, "api_name": api, "service_key": service, "service_name": service}
        for service, api in gold_apis
    ]
    apis.append(
        {
            "function_key": "negative_api",
            "api_name": "Negative API",
            "service_key": "service_negative",
            "service_name": "Service Negative",
        }
    )
    return {
        "query_text": query,
        "ordered_steps_json": dumps(steps),
        "dependency_edges_json": dumps(edges),
        "disconnected_calls_json": dumps(disconnected or []),
        "connected_dependency_component_count": 1,
        "dependency_graph_is_dag": "true",
        "provisional_gold_services_json": dumps(
            [{"service_key": name, "service_name": name} for name in gold_services]
        ),
        "provisional_gold_apis_json": dumps(
            [
                {"function_key": api, "api_name": api, "service_key": service, "service_name": service}
                for service, api in gold_apis
            ]
        ),
        "candidate_services_json": dumps(services),
        "candidate_apis_json": dumps(apis),
        "service_candidate_space_status": "valid",
        "api_candidate_space_status": "valid",
        "service_negative_distractor_count": 1,
        "api_negative_distractor_count": 1,
        "service_leak_status": "no_obvious_leak",
        "api_leak_status": "no_obvious_leak",
    }


class TaskNecessityGateV033Tests(unittest.TestCase):
    def test_01_distance_currency_skyscanner_style_is_excluded(self) -> None:
        steps = [
            step(1, "skyscanner", "distance", {"distance": "371.72 mi"}),
            step(2, "skyscanner", "distance_retry", {}, "error_only"),
        ]
        row = base_row(
            "Give me distance, currency, and Skyscanner markets.",
            steps,
            [edge(1, 2)],
            ["skyscanner"],
            [("skyscanner", "distance"), ("skyscanner", "distance_retry")],
        )
        row["service_leak_status"] = "exact_unique_name_leak"
        result = gate.assess_task(row)
        self.assertFalse(result["structural_hard_gate_pass"])
        self.assertIn("gold_service_count_lt_2", result["structural_ineligibility_reasons"])
        self.assertIn("no_cross_service_strong_edge", result["structural_ineligibility_reasons"])
        self.assertIn("failed_or_error_dependency_edge", result["structural_ineligibility_reasons"])
        self.assertIn("exact_blocking_service_name_leak", result["structural_ineligibility_reasons"])

    def test_02_true_hotel_to_restaurant_cross_service_is_eligible(self) -> None:
        steps = [
            step(1, "hotel_search", "find_hotel", {"address": "123 Main Street"}),
            step(2, "restaurant_search", "find_restaurant", {"restaurants": ["A"]}),
        ]
        row = base_row(
            "Find a hotel and then find a nearby restaurant.",
            steps,
            [edge(1, 2)],
            ["hotel_search", "restaurant_search"],
            [("hotel_search", "find_hotel"), ("restaurant_search", "find_restaurant")],
        )
        result = gate.assess_task(row)
        self.assertTrue(result["structural_hard_gate_pass"])
        self.assertEqual(result["cross_service_strong_edge_count"], 1)
        self.assertEqual(result["machine_review_status"], "STRUCTURALLY_ELIGIBLE_FOR_REVIEW")

    def test_03_single_service_api_chain_routes_to_api_only(self) -> None:
        steps = [
            step(1, "travel", "lookup_city", {"city_id": "C-42"}),
            step(2, "travel", "city_weather", {"forecast": "sunny"}),
        ]
        row = base_row(
            "Look up a city and retrieve its weather.",
            steps,
            [edge(1, 2)],
            ["travel"],
            [("travel", "lookup_city"), ("travel", "city_weather")],
        )
        result = gate.assess_task(row)
        self.assertFalse(result["structural_hard_gate_pass"])
        self.assertTrue(result["api_only_workflow_candidate"])
        self.assertEqual(result["machine_review_status"], "API_ONLY_WORKFLOW_CANDIDATE")

    def test_04_dependency_plus_independent_translation_is_hybrid_risk(self) -> None:
        steps = [
            step(1, "hotel_search", "find_hotel", {"address": "123 Main Street"}),
            step(2, "restaurant_search", "find_restaurant", {"restaurants": ["A"]}),
            step(3, "translator", "translate", {"translation": "bonjour"}),
        ]
        row = base_row(
            "Find a hotel and nearby restaurant. Additionally translate hello to French.",
            steps,
            [edge(1, 2)],
            ["hotel_search", "restaurant_search"],
            [("hotel_search", "find_hotel"), ("restaurant_search", "find_restaurant")],
            disconnected=[steps[2]],
        )
        result = gate.assess_task(row)
        self.assertTrue(result["structural_hard_gate_pass"])
        self.assertTrue(result["parallel_subgoal_risk"])
        self.assertTrue(result["hybrid_composable_multi_risk"])
        self.assertEqual(result["machine_review_status"], "STRUCTURALLY_ELIGIBLE_WITH_RISK")

    def test_05_upstream_distance_then_recalculation_is_redundancy_hold(self) -> None:
        steps = [
            step(1, "geo_lookup", "locate_and_measure_distance", {"distance": "371.72 mi", "latitude": 1.2, "longitude": 3.4}),
            step(2, "distance_calculator", "calculate_distance", {"distance": "371.72 mi"}),
        ]
        row = base_row(
            "Calculate the distance between the two places.",
            steps,
            [edge(1, 2)],
            ["geo_lookup", "distance_calculator"],
            [("geo_lookup", "locate_and_measure_distance"), ("distance_calculator", "calculate_distance")],
        )
        result = gate.assess_task(row)
        self.assertTrue(result["possible_redundant_recomputation"])
        self.assertTrue(result["upstream_already_returns_requested_result"])
        self.assertTrue(result["only_redundant_recomputation_dependency"])
        self.assertFalse(result["structural_hard_gate_pass"])


if __name__ == "__main__":
    unittest.main()
