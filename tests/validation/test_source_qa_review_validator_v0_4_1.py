"""Regression tests for source-QA validator v0.4.1."""

from __future__ import annotations

import json
import unittest

from scripts.validation.source_qa_review_validator_v0_4_1 import validate_rows


def json_cell(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def core_row(**updates: str) -> dict[str, str]:
    row = {
        "task_id": "synthetic-1",
        "source_dataset": "ToolBench",
        "task_type": "single_service_discovery",
        "source_group": "G1",
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
        "adjudicator_id": "human-reviewer",
        "adjudicated_at": "2026-07-10T20:00:00+08:00",
        "adjudication_notes": "",
    }
    row.update(updates)
    return row


def run_one(row: dict[str, str]):
    return validate_rows([row], filename="synthetic.csv", source_hint=row.get("source_dataset", ""))


def issue_types(issues: list[dict[str, str]]) -> set[str]:
    return {item["issue_type"] for item in issues}


class SourceQAReviewValidatorV041Tests(unittest.TestCase):
    def test_01_shortcuts_service_level_ignores_api_no_choice(self) -> None:
        services = [f"service-{index:02d}" for index in range(29)]
        row = core_row(
            source_dataset="ShortcutsBench",
            task_type="single_service_discovery_source_candidate",
            candidate_services_json=json_cell(services),
            gold_services_json=json_cell([services[0]]),
            candidate_apis_json=json_cell(
                [{"service_name": services[0], "api_name": "only-api"}]
            ),
            gold_apis_json=json_cell(
                [{"service_name": services[0], "api_name": "only-api"}]
            ),
            candidate_equals_gold="true",
            negative_distractor_count="0",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["validation_target_distribution"], {"service": 1})
        self.assertEqual(summary["fatal_count"], 0)
        self.assertNotIn("api_candidate_space_invalid_for_keep_as_is", issue_types(issues))

    def test_02_api_recommendation_equal_candidate_and_gold_fails(self) -> None:
        api = [{"service_name": "svc", "api_name": "only-api"}]
        row = core_row(
            prediction_level="api",
            task_type="single_api_recommendation",
            candidate_apis_json=json_cell(api),
            gold_apis_json=json_cell(api),
            candidate_equals_gold="true",
            negative_distractor_count="0",
        )
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("api_candidate_space_invalid_for_keep_as_is", issue_types(issues))

    def test_03_service_discovery_without_service_distractor_fails(self) -> None:
        row = core_row(
            candidate_services_json=json_cell(["gold-service"]),
            gold_services_json=json_cell(["gold-service"]),
        )
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("service_candidate_space_invalid_for_keep_as_is", issue_types(issues))

    def test_04_optional_fields_blank_do_not_make_clean_row_pending(self) -> None:
        row = core_row(
            qa_repair_reason="",
            qa_dependency_chain_evidence="",
            adjudication_notes="",
        )
        summary, _, _ = run_one(row)
        self.assertEqual(summary["pending_count"], 0)
        self.assertEqual(summary["conditional_missing_count"], 0)
        self.assertEqual(summary["fatal_count"], 0)

    def test_05_rewrite_requires_repair_reason(self) -> None:
        row = core_row(
            qa_semantic_usability="uncertain",
            qa_release_action="rewrite_then_reaudit",
            qa_main_benchmark_eligible_now="false",
            qa_repair_required="true",
            qa_repair_reason="",
            adjudicated_final_decision="rewrite_then_reaudit",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["conditional_missing_count"], 1)
        self.assertIn("conditional_required_field_missing", issue_types(issues))

    def test_06_reconstruct_requires_repair_reason(self) -> None:
        row = core_row(
            qa_semantic_usability="unusable",
            qa_release_action="reconstruct_then_reaudit",
            qa_main_benchmark_eligible_now="false",
            qa_repair_required="true",
            qa_repair_reason="",
            adjudicated_final_decision="reconstruct_then_reaudit",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["conditional_missing_count"], 1)
        self.assertIn("conditional_required_field_missing", issue_types(issues))

    def test_07_composable_keep_requires_dependency_evidence(self) -> None:
        row = core_row(
            task_type="composable_service_discovery_raw",
            source_group="G3",
            qa_dependency_chain_evidence="",
        )
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("composable_dependency_evidence_missing", issue_types(issues))

    def test_08_composable_dependency_review_allows_blank_evidence(self) -> None:
        row = core_row(
            task_type="composable_service_discovery_raw",
            source_group="G3",
            qa_semantic_usability="uncertain",
            qa_release_action="dependency_review",
            qa_main_benchmark_eligible_now="false",
            qa_repair_required="true",
            qa_repair_reason="Need evidence that an upstream output controls the downstream call.",
            qa_dependency_chain_evidence="",
            adjudicated_final_decision="dependency_review",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["pending_count"], 0)
        self.assertEqual(summary["conditional_missing_count"], 0)
        self.assertNotIn("composable_dependency_evidence_missing", issue_types(issues))
        self.assertEqual(summary["fatal_count"], 0)

    def test_09_remove_with_blank_optional_fields_is_complete(self) -> None:
        row = core_row(
            qa_semantic_usability="unusable",
            qa_release_action="remove",
            qa_main_benchmark_eligible_now="false",
            qa_repair_required="false",
            qa_repair_reason="",
            qa_dependency_chain_evidence="",
            adjudicated_final_decision="remove",
            adjudication_notes="",
        )
        summary, _, _ = run_one(row)
        self.assertEqual(summary["pending_count"], 0)
        self.assertEqual(summary["conditional_missing_count"], 0)
        self.assertEqual(summary["fatal_count"], 0)

    def test_10_action_and_final_decision_mismatch_fails(self) -> None:
        row = core_row(adjudicated_final_decision="hold")
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("final_decision_action_mismatch", issue_types(issues))

    def test_11_blocking_service_leak_cannot_be_eligible(self) -> None:
        row = core_row(qa_leakage_check="service_leak_blocking")
        summary, issues, _ = run_one(row)
        self.assertGreater(summary["fatal_count"], 0)
        self.assertIn("blocking_leak_marked_eligible", issue_types(issues))

    def test_12_unfilled_original_pack_is_pending_not_invalid(self) -> None:
        row = core_row(**{field: "" for field in [
            "qa_semantic_usability",
            "qa_release_action",
            "qa_main_benchmark_eligible_now",
            "qa_repair_required",
            "adjudicated_final_decision",
            "adjudicator_id",
            "adjudicated_at",
        ]})
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["pending_count"], 1)
        self.assertFalse(summary["invalid_reviewed_input"])
        self.assertEqual(summary["fatal_count"], 0)
        self.assertIn("pending_core_fields", issue_types(issues))

    def test_13_stabletoolbench_g2_is_mixed_not_toolbench_unknown(self) -> None:
        row = core_row(
            source_dataset="StableToolBench",
            source_group="G2",
            stable_group="G2",
            task_type="",
            task_type_guess="multi_service_or_multi_api_candidate",
            qa_semantic_usability="",
            qa_release_action="",
            qa_main_benchmark_eligible_now="",
            qa_repair_required="",
            adjudicated_final_decision="",
            adjudicator_id="",
            adjudicated_at="",
        )
        summary, issues, _ = run_one(row)
        self.assertEqual(summary["validation_target_distribution"], {"mixed": 1})
        self.assertIn("validation_target_ambiguous", issue_types(issues))


if __name__ == "__main__":
    unittest.main()
