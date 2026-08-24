from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench_v011_closure_v2 import ALLOWED_FINAL_STATUSES
from servicediscoverybench_v011_closure_v2.preflight import _manifest_row, _schema, RUNNER_TEMPLATE


class V011ClosureV2PreflightTests(unittest.TestCase):
    def test_output_protocols(self):
        self.assertEqual(_schema("single_service_discovery", "native"), "ranking_only")
        self.assertEqual(_schema("single_api_recommendation", "native"), "ranking_and_selected_set")
        self.assertEqual(_schema("multi_api_recommendation", "native"), "ranking_and_selected_set")
        self.assertEqual(_schema("single_api_recommendation", "global"), "ranking_only")

    def test_manifest_has_exact_eight_field_cache_key(self):
        row = _manifest_row("t", "single_api_recommendation", "S", "api", "query", [{"candidate_id": "a", "canonical_name": "A", "description": "", "provider_or_host": "", "api_schema_summary": ""}], "native")
        self.assertEqual(len(row["cache_key_fields"]), 8)
        self.assertNotIn("gold", row["model_visible_input"])
        self.assertEqual(row["output_schema"], "ranking_and_selected_set")

    def test_runner_contains_resume_and_retry_without_provider_calls(self):
        self.assertIn("completed", RUNNER_TEMPLATE)
        self.assertIn("max-parse-retries", RUNNER_TEMPLATE)
        self.assertNotIn("API_KEY", RUNNER_TEMPLATE)

    def test_status_vocabulary(self):
        self.assertEqual(ALLOWED_FINAL_STATUSES, {
            "V0_1_1_THREE_TRACK_PRE_LLM_READY",
            "V0_1_1_NATIVE_MACHINE_PRE_LLM_READY_GLOBAL_PARTIAL",
            "V0_1_1_PROMOTION_OR_PREFLIGHT_NO_GO",
        })

    def test_review_bundle_excludes_release_and_passes_crc(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "17_promote_v011_and_build_pre_llm_v2.py"
        spec = importlib.util.spec_from_file_location("v011_runner_v2", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            (run / "release").mkdir(parents=True)
            (run / "release" / "large.txt").write_text("excluded", encoding="utf-8")
            (run / "RUN_STATUS.json").write_text("{}", encoding="utf-8")
            bundle, digest = module.bundle_run(run)
            self.assertTrue(bundle.exists())
            self.assertEqual(len(digest), 64)
            import zipfile
            with zipfile.ZipFile(bundle) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(), ["RUN_STATUS.json"])


if __name__ == "__main__":
    unittest.main()
