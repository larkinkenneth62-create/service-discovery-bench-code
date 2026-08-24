import json
import unittest

from scripts.provider.provider_boundary import parse_prompt_input, validate_provider_request


def request(query: str = "current and historical gold prices") -> dict:
    payload = {
        "query": query,
        "task_type": "single_service_discovery",
        "prediction_target": "service",
        "candidate_documents": [{"candidate_id": "service-1", "document": "price lookup"}],
        "instructions": "Return candidate IDs only.",
    }
    return {
        "request_id": "task-1",
        "prompt": f"INPUT_JSON={json.dumps(payload, separators=(',', ':'))}\n",
        "candidate_ids": ["service-1"],
        "decoding_config": {"temperature": 0},
        "timeout_seconds": 30.0,
    }


class ProviderBoundaryTests(unittest.TestCase):
    def test_ordinary_sensitive_words_are_allowed_in_values(self) -> None:
        self.assertEqual(
            validate_provider_request(request()),
            ["candidate_ids", "decoding_config", "prompt", "request_id", "timeout_seconds"],
        )

    def test_forbidden_structured_key_is_rejected(self) -> None:
        value = request()
        value["reference_gold_ids"] = ["service-1"]
        with self.assertRaisesRegex(ValueError, "invalid provider top-level contract"):
            validate_provider_request(value)

    def test_candidate_order_must_match_prompt(self) -> None:
        value = request()
        value["candidate_ids"] = ["service-2"]
        with self.assertRaisesRegex(ValueError, "candidate_ids do not exactly match"):
            validate_provider_request(value)

    def test_prompt_has_exact_visible_schema(self) -> None:
        self.assertEqual(
            parse_prompt_input(request()["prompt"])["candidate_documents"][0]["candidate_id"],
            "service-1",
        )


if __name__ == "__main__":
    unittest.main()
