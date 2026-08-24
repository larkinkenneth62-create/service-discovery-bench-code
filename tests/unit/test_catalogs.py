import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.catalogs import (
    api_id,
    build_name_only_crosswalk,
    resolve_toolbench_static_api,
    resolve_toolbench_static_service,
    service_id,
)


class CatalogTest(unittest.TestCase):
    def test_ids_are_deterministic_and_hierarchical(self):
        sid = service_id("ToolBench", "example.p.rapidapi.com")
        self.assertEqual(sid, service_id("ToolBench", "example.p.rapidapi.com"))
        self.assertTrue(api_id(sid, "search").startswith(f"api::{sid}::"))

    def test_name_only_cross_source_match_does_not_merge(self):
        base = {"canonical_name": "Example", "host_or_base_url": "", "catalog_version": "v0.1"}
        services = [
            {**base, "service_id": "svc::a::1", "source_dataset": "A"},
            {**base, "service_id": "svc::b::1", "source_dataset": "B"},
        ]
        rows = build_name_only_crosswalk(services)
        self.assertEqual(rows[0]["alignment_status"], "ambiguous_no_merge")

    def test_exact_nonempty_host_can_align(self):
        base = {"canonical_name": "Example", "host_or_base_url": "api.example.com", "catalog_version": "v0.1"}
        services = [
            {**base, "service_id": "svc::a::1", "source_dataset": "A"},
            {**base, "service_id": "svc::b::1", "source_dataset": "B"},
        ]
        rows = build_name_only_crosswalk(services)
        self.assertEqual(rows[0]["alignment_status"], "exact_provider_host_match")

    def test_frozen_static_service_prefers_explicit_key(self):
        explicit = service_id("ToolBench", "static::weather_api")
        services = [
            {"service_id": "svc::toolbench::other", "source_dataset": "ToolBench", "canonical_name": "Weather"},
            {"service_id": explicit, "source_dataset": "ToolBench", "canonical_name": "Weather"},
        ]
        self.assertEqual(resolve_toolbench_static_service(services, "weather_api", "Weather"), explicit)

    def test_frozen_static_api_falls_back_to_exact_sibling_signature(self):
        apis = [{
            "api_id": "api::one",
            "parent_service_id": "svc::one",
            "source_api_id": "legacy_search",
            "canonical_name": "Search",
            "http_method": "GET",
        }]
        self.assertEqual(
            resolve_toolbench_static_api(apis, "svc::one", "search_for_weather", "Search", "GET"),
            "api::one",
        )


if __name__ == "__main__":
    unittest.main()
