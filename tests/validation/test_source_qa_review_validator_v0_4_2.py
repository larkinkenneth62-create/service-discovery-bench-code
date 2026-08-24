"""Regression tests for source-QA validator v0.4.2."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.validation.source_qa_review_validator_v0_4_2 import (
    CORE_REQUIRED_FIELDS,
    NEW_HUMAN_FIELDS,
    validate_rows,
)


def json_cell(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def core_row(**updates: str) -> dict[str, str]:
    row = {
        "task_id": "v042-synthetic-1",
        "source_dataset": "ToolBench",
        "source_group": "G2",
        "task_type": "multi_service_discovery_raw",
        "prediction_level": "service",
        "candidate_services_json": json_cell(["gold-service", "distractor-service"]),
        "gold_services_json": json_cell(["gold-service"]),
        "candidate_apis_json": json_cell(
            [
                {"service_name": "gold-service", "api_name": "gold-api"},
                {"service_name": "gold-service", "api_name": "distractor-api"},
            ]
        ),
        "gold_apis_json": json_cell(
            [{"service_name": "gold-service", "api_name": "gold-api"}]
        ),
        "candidate_equals_gold": "false",
        "negative_distractor_count": "1",
        "explicit_service_leak_detected": "false",
        "explicit_api_leak_detected": "false",
        "qa_semantic_usability": "usable",
        "qa_release_action": "keep_as_is",
        "qa_main_benchmark_eligible_now": "true",
        "qa_repair_required": "false",
        "qa_repair_reason": "",
        "qa_dependency_chain_evidence": "",
        "adjudicated_final_decision": "keep_as_is",
        "adjudicator_id": "reviewer-1",
        "adjudicated_at": "2026-07-10T22:00:00+08:00",
        "adjudication_notes": "",
        "adjudicated_leakage_status": "no_obvious_leak",
        "adjudicated_prediction_target": "service",
        "adjudicator_type": "human_confirmed",
    }
    row.update(updates)
    return row


def run_one(row: dict[str, str], reviewed_mode: bool = True):
    return validate_rows(
        [row],
        filename="v042-synthetic.csv",
        source_hint=row.get("source_dataset", ""),
        reviewed_mode=reviewed_mode,
    )


def issue_types(issues: list[dict[str, str]]) -> set[str]:
    return {item["issue_type"] for item in issues}


class SourceQAReviewValidatorV042Tests(unittest.TestCase):
    def test_01_human_override_prior_blocking_with_notes_passes(self) -> None:
        row = core_row(
            prior_qa_leakage_check="service_leak_blocking",
            adjudication_notes="Human inspection found the phrase generic and not a service-name leak.",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["fatal_count"], 0)
        self.assertIn("adjudication_overrides_prior_blocking_evidence", issue_types(issues))

    def test_02_human_override_prior_blocking_without_notes_fails(self) -> None:
        row = core_row(
            prior_qa_leakage_check="service_leak_blocking",
            adjudication_notes="",
        )
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("adjudication_override_explanation_missing", issue_types(issues))

    def test_03_final_blocking_leak_cannot_keep_or_be_eligible(self) -> None:
        row = core_row(adjudicated_leakage_status="service_leak_blocking")
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("final_blocking_leak_release_violation", issue_types(issues))

    def test_04_stable_mixed_both_ineligible_can_be_source_retained(self) -> None:
        row = core_row(
            source_dataset="StableToolBench",
            source_group="G2",
            stable_group="G2",
            task_type="",
            task_type_guess="multi_service_or_multi_api_candidate",
            adjudicated_prediction_target="both",
            qa_main_benchmark_eligible_now="false",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["fatal_count"], 0)
        self.assertNotIn("source_level_target_marked_benchmark_eligible", issue_types(issues))

    def test_05_stable_mixed_both_cannot_be_eligible(self) -> None:
        row = core_row(
            source_dataset="StableToolBench",
            source_group="G2",
            stable_group="G2",
            task_type="",
            task_type_guess="multi_service_or_multi_api_candidate",
            adjudicated_prediction_target="both",
            qa_main_benchmark_eligible_now="true",
        )
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("source_level_target_marked_benchmark_eligible", issue_types(issues))

    def test_06_service_keep_with_valid_service_space_passes(self) -> None:
        row = core_row(adjudicated_prediction_target="service")
        summary, _, _ = run_one(row)
        self.assertEqual(summary["fatal_count"], 0)

    def test_07_api_keep_with_invalid_api_space_fails(self) -> None:
        only_api = [{"service_name": "gold-service", "api_name": "gold-api"}]
        row = core_row(
            task_type="single_api_recommendation",
            prediction_level="api",
            adjudicated_prediction_target="api",
            candidate_apis_json=json_cell(only_api),
            gold_apis_json=json_cell(only_api),
            candidate_equals_gold="true",
            negative_distractor_count="0",
        )
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("api_candidate_space_invalid_for_keep_as_is", issue_types(issues))

    def test_08_started_review_missing_adjudicator_type_is_invalid(self) -> None:
        row = core_row(adjudicator_type="")
        summary, issues, _ = run_one(row, reviewed_mode=True)
        self.assertEqual(summary["pending_count"], 1)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("completed_row_missing_v0_4_2_fields", issue_types(issues))

    def test_09_model_pilot_cannot_map_to_human_confirmed_gold(self) -> None:
        row = core_row(adjudicator_type="model_pilot_only", human_confirmed_gold="true")
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("model_pilot_cannot_be_human_confirmed_gold", issue_types(issues))

    def test_10_wholly_unfilled_original_row_is_pending_not_invalid(self) -> None:
        row = core_row(**{field: "" for field in CORE_REQUIRED_FIELDS})
        summary, issues, _ = run_one(row, reviewed_mode=False)
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["fatal_count"], 0)
        self.assertFalse(summary["invalid_reviewed_input"])
        self.assertIn("pending_core_fields_v0_4_2", issue_types(issues))

    def test_11_generated_packs_are_immutable_and_blank(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest_path = root / "outputs/source_qa_adjudication_v0_4_2/review_pack_manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["all_validation_pass"])
        self.assertEqual(manifest["total_rows"], 351)
        self.assertEqual(manifest["immutable_cell_change_total"], 0)
        self.assertEqual(manifest["review_field_nonempty_total"], 0)
        self.assertEqual(manifest["new_fields"], NEW_HUMAN_FIELDS)


if __name__ == "__main__":
    unittest.main()
