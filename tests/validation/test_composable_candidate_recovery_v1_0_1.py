#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "validation"
sys.path.insert(0, str(SCRIPT_DIR))

import composable_candidate_recovery_v1_0_1 as recovery  # noqa: E402


def service(name: str, key: str) -> dict[str, str]:
    return {"service_name": name, "service_key": key, "service_description": name, "category": "Tools", "catalog_source_path": "catalog", "catalog_origin": "toolbench_static_catalog"}


def api(name: str, function: str, service_name: str, service_key: str) -> dict[str, str]:
    return {"api_name": name, "function_name": function, "function_key": function, "service_name": service_name, "service_key": service_key, "api_description": name, "category": "Tools", "catalog_source_path": "catalog", "catalog_origin": "toolbench_static_catalog", "is_test_like": "false"}


class RecoveryTests(unittest.TestCase):
    def test_primary_status_precedence_is_mutually_exclusive(self) -> None:
        self.assertEqual(
            recovery.classify_recovery({"exact_blocking_api_name_leak"}),
            "REPAIRABLE_EXACT_LEAK_ONLY",
        )
        self.assertEqual(
            recovery.classify_recovery({"exact_blocking_api_name_leak", "service_candidate_space_invalid"}),
            "REPAIRABLE_LEAK_AND_CANDIDATE_SPACE",
        )
        self.assertEqual(
            recovery.classify_recovery({"exact_blocking_api_name_leak", "gold_service_count_lt_2"}),
            "HARD_UNRECOVERABLE_GOLD_SERVICE_COUNT_LT_2",
        )

    def test_deterministic_rewrite_accepts_only_allowed_connector_pattern(self) -> None:
        services = [service("Dream Diffusion", "dream_diffusion")]
        result = recovery.deterministic_leakage_rewrite(
            "Generate animated images using the Dream Diffusion API for a wedding video.",
            services,
            [],
        )
        self.assertEqual(result["deterministic_rewrite_status"], "REWRITE_VALID")
        self.assertNotIn("Dream Diffusion", result["proposed_rewritten_query_text"])
        self.assertTrue(result["query_action_object_preserved"])

    def test_deterministic_rewrite_holds_natural_language_overlap(self) -> None:
        apis = [api("Timezone", "timezone", "Clock", "clock")]
        result = recovery.deterministic_leakage_rewrite(
            "Find a city and tell me its timezone.", [], apis
        )
        self.assertEqual(result["deterministic_rewrite_status"], "REWRITE_NOT_APPLICABLE")

    def test_api_name_after_with_is_not_a_rewrite_connector(self) -> None:
        apis = [api("Whois", "whois", "Domain", "domain")]
        result = recovery.deterministic_leakage_rewrite(
            "Check domain availability and provide me with the WHOIS details.", [], apis
        )
        self.assertEqual(result["deterministic_rewrite_status"], "REWRITE_NOT_APPLICABLE")
        self.assertEqual(result["proposed_rewritten_query_text"], result["original_query_text"])

    def test_catalog_reconstruction_filters_test_negative_and_maps_parents(self) -> None:
        s1, s2, s3 = service("Hotel", "hotel"), service("Restaurant", "restaurant"), service("Maps", "maps")
        a1 = api("Find hotel", "find_hotel", "Hotel", "hotel")
        a2 = api("Find restaurant", "find_restaurant", "Restaurant", "restaurant")
        a3 = api("Map details", "map_details", "Maps", "maps")
        test_api = api("Healthcheck test", "healthcheck_test", "Maps", "maps")
        test_api["is_test_like"] = "true"
        services, apis, mapping = recovery.filtered_catalog(
            {x["service_key"]: x for x in (s1, s2, s3)},
            {x["function_key"]: x for x in (a1, a2, a3, test_api)},
        )
        row = {
            "source_task_id": "fixture",
            "query_text": "Find a hotel, then use its address to find a restaurant.",
            "provisional_gold_services_json": recovery.json_dumps([s1, s2]),
            "provisional_gold_apis_json": recovery.json_dumps([a1, a2]),
            "candidate_services_json": recovery.json_dumps([s1, s2]),
            "candidate_apis_json": recovery.json_dumps([a1, a2]),
        }
        updated, trace = recovery.reconstruct_candidate_space(row, services, apis, mapping)
        self.assertIsNotNone(updated)
        self.assertEqual(trace["reconstruction_status"], "RECONSTRUCTED_VALID")
        self.assertNotIn("healthcheck_test", updated["candidate_apis_json"])
        self.assertNotIn('"service_key":""', updated["service_api_map_json"])

    def test_review_hash_changes_with_model_facing_query(self) -> None:
        row = {
            "final_model_facing_query_text": "Find a hotel.",
            "candidate_services_json": "[]",
            "provisional_gold_services_json": "[]",
            "candidate_apis_json": "[]",
            "provisional_gold_apis_json": "[]",
            "service_api_map_json": "[]",
            "dependency_edges_json": "[]",
            "dependency_evidence_json": "{}",
        }
        first = recovery.recovery_review_hash(row)
        row["final_model_facing_query_text"] = "Find a restaurant."
        self.assertNotEqual(first, recovery.recovery_review_hash(row))


if __name__ == "__main__":
    unittest.main()
