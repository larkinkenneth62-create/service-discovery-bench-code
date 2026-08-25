from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import httpx


RUNNER_PATH = Path(__file__).resolve().parents[1] / "code" / "run_qwen_sse_formal_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_qwen_sse_runner_tested", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


class SSEContractTests(unittest.TestCase):
    def test_heartbeat_detection(self) -> None:
        self.assertTrue(RUNNER._is_heartbeat("heartbeat", "{}", {}))
        self.assertTrue(RUNNER._is_heartbeat(None, '{"type":"heartbeat"}', {"type": "heartbeat"}))
        self.assertFalse(RUNNER._is_heartbeat(None, '{"choices":[]}', {"choices": []}))

    def test_complete_stream_requires_finish_and_done(self) -> None:
        body = (
            b": heartbeat\n\n"
            b"data: {\"id\":\"r1\",\"model\":\"Qwen3.6-35B-A3B-APEX-I-Compact.gguf\","
            b"\"choices\":[{\"delta\":{\"content\":\"{\\\"ranked_candidate_ids\\\":[\\\"c1\\\"]}\"},"
            b"\"finish_reason\":\"stop\"}]}\n\n"
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["accept"], "text/event-stream")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body))

        item = RUNNER.FROZEN.RequestItem(
            request_id="unit-test",
            track="native",
            task_type="single_service_recommendation",
            prediction_target="service",
            candidate_ids=["c1"],
            require_selected=False,
            payload={"model": RUNNER.MODEL, "messages": [], "stream": True, "max_tokens": 32},
            source_row_sha256="a" * 64,
            candidate_order_sha256="b" * 64,
        )
        with tempfile.TemporaryDirectory() as temporary:
            runner = RUNNER.FormalSSERunner("https://example.test/v1", ["secret"], Path(temporary), 1, 0, {})
            runner.slots[0].client.close()
            runner.slots[0].client = httpx.Client(transport=httpx.MockTransport(handler))
            try:
                outcome = runner.send_stream(runner.slots[0], item, 1, Path(temporary) / "raw.jsonl", time.perf_counter() + 60)
            finally:
                runner.close()
            self.assertTrue(outcome.done_received)
            self.assertTrue(outcome.terminal_event_received)
            self.assertEqual(outcome.heartbeat_count, 1)
            self.assertEqual(outcome.finish_reason, "stop")
            parsed = RUNNER.FROZEN.parse_response(outcome.final_response, ["c1"], False)
            self.assertTrue(parsed.valid)

    def test_strict_parser_does_not_complete_partial_ranking(self) -> None:
        response = {
            "choices": [{"message": {"content": json.dumps({"ranked_candidate_ids": ["c1"]})}}]
        }
        parsed = RUNNER.FROZEN.parse_response(response, ["c1", "c2"], False)
        self.assertFalse(parsed.valid)
        self.assertEqual(parsed.error_code, "INCOMPLETE_RANKING")

    def test_fixed_retry_contract(self) -> None:
        self.assertEqual(RUNNER.RETRY_BACKOFF_SECONDS, (15, 30, 60))
        self.assertEqual(RUNNER.CONNECT_TIMEOUT_SECONDS, 30.0)
        self.assertEqual(RUNNER.READ_TIMEOUT_SECONDS, 45.0)
        self.assertEqual(RUNNER.MAX_WALL_SECONDS, 7500.0)

    def test_event_name_resets_at_empty_frame(self) -> None:
        body = (
            b"event: heartbeat\n\n"
            b"data: {\"model\":\"Qwen3.6-35B-A3B-APEX-I-Compact.gguf\",\"choices\":[{\"delta\":{\"content\":\"{}\"},\"finish_reason\":\"stop\"}]}\n\n"
            b"data: [DONE]\n\n"
        )
        outcome = self._stream_outcome(body)
        self.assertEqual(outcome.sse_event_count, 1)
        self.assertTrue(outcome.done_received)
        self.assertEqual(outcome.final_response["choices"][0]["message"]["content"], "{}")

    def test_multiline_data_frame_is_joined(self) -> None:
        body = (
            b"data: {\"model\":\"Qwen3.6-35B-A3B-APEX-I-Compact.gguf\",\"choices\":\n"
            b"data: [{\"delta\":{\"content\":\"{}\"},\"finish_reason\":\"stop\"}]}\n\n"
            b"data: [DONE]\n\n"
        )
        outcome = self._stream_outcome(body)
        self.assertIsNone(outcome.error_code)
        self.assertTrue(outcome.terminal_event_received)
        self.assertTrue(outcome.done_received)

    def test_missing_done_is_retryable_infrastructure_failure(self) -> None:
        body = (
            b"data: {\"model\":\"Qwen3.6-35B-A3B-APEX-I-Compact.gguf\",\"choices\":[{\"delta\":{\"content\":\"{}\"},\"finish_reason\":\"stop\"}]}\n\n"
        )
        outcome = self._stream_outcome(body)
        self.assertEqual(outcome.error_code, "INCOMPLETE_SSE_TERMINATION")
        self.assertTrue(outcome.retryable)

    def test_reasoning_is_separate_from_answer(self) -> None:
        body = (
            b"data: {\"model\":\"Qwen3.6-35B-A3B-APEX-I-Compact.gguf\",\"choices\":[{\"delta\":{\"reasoning_content\":\"private\",\"content\":\"{}\"},\"finish_reason\":\"stop\"}]}\n\n"
            b"data: [DONE]\n\n"
        )
        outcome = self._stream_outcome(body)
        message = outcome.final_response["choices"][0]["message"]
        self.assertEqual(message["content"], "{}")
        self.assertEqual(message["reasoning_content"], "private")

    def test_raw_sse_redacts_api_key(self) -> None:
        key = "unit-secret-key"
        body = (
            f'data: {{"model":"{RUNNER.MODEL}","choices":[{{"delta":{{"content":"{key}"}},"finish_reason":"stop"}}]}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        with tempfile.TemporaryDirectory() as temporary:
            raw_path = Path(temporary) / "raw.jsonl"
            outcome = self._stream_outcome(body, key=key, raw_path=raw_path)
            self.assertTrue(outcome.done_received)
            raw_text = raw_path.read_text(encoding="utf-8")
            self.assertNotIn(key, raw_text)
            self.assertIn("[REDACTED_API_KEY]", raw_text)

    def test_budget_binding_for_smoke_is_exact(self) -> None:
        root = Path(__file__).resolve().parents[3]
        args = Namespace(
            track="smoke",
            input=root / "experiments" / "retriever_v0_2_prellm_fix_v2" / "SDB_V0_2_UNIFIED_PRE_LLM_PROTOCOL_FIX_V2" / "05_SMOKE" / "DEV_SMOKE_60_INPUT.jsonl",
            budget_freeze=root / "experiments" / "llm_v0_2_qwen_sse_formal_v1_4" / "00_RUNTIME" / "TRACK_TOKEN_BUDGET_FREEZE.json",
            smoke_budget_validation=root / "experiments" / "llm_v0_2_qwen_sse_formal_v1_4" / "00_RUNTIME" / "SMOKE_TOKEN_BUDGET_VALIDATION.json",
        )
        binding = RUNNER.load_budget_binding(args)
        self.assertEqual(binding["frozen_max_tokens"], 3291)
        self.assertEqual(binding["tokenizer_revision"], RUNNER.TOKENIZER_REVISION)
        self.assertEqual(binding["adapter_sha256"], RUNNER.FROZEN_ADAPTER_SHA256)

    def test_pretty_safe_budget_binding_for_smoke_is_exact(self) -> None:
        root = Path(__file__).resolve().parents[3]
        args = Namespace(
            track="smoke",
            input=root / "experiments" / "retriever_v0_2_prellm_fix_v2" / "SDB_V0_2_UNIFIED_PRE_LLM_PROTOCOL_FIX_V2" / "05_SMOKE" / "DEV_SMOKE_60_INPUT.jsonl",
            budget_freeze=root / "experiments" / "llm_v0_2_qwen_sse_formal_v1_4" / "00_RUNTIME" / "TRACK_TOKEN_BUDGET_FREEZE_V2_PRETTY_SAFE.json",
            smoke_budget_validation=root / "experiments" / "llm_v0_2_qwen_sse_formal_v1_4" / "00_RUNTIME" / "SMOKE_TOKEN_BUDGET_VALIDATION_V2_PRETTY_SAFE.json",
        )
        binding = RUNNER.load_budget_binding(args)
        self.assertEqual(binding["frozen_max_tokens"], 3957)
        self.assertEqual(binding["budget_track"], "native")

    def test_resume_rejects_missing_artifact(self) -> None:
        item = self._item()
        previous = {"status": "succeeded", "request_sha256": item.request_sha256, "attempts": []}
        with tempfile.TemporaryDirectory() as temporary:
            runner = RUNNER.FormalSSERunner("https://example.test/v1", ["secret"], Path(temporary), 1, 0, {})
            try:
                with self.assertRaises(SystemExit):
                    runner.validate_reusable(item, previous)
            finally:
                runner.close()

    def test_response_model_mismatch_is_api_error(self) -> None:
        body = (
            b"data: {\"model\":\"wrong-model\",\"choices\":[{\"delta\":{\"content\":\"{\\\"ranked_candidate_ids\\\":[\\\"c1\\\"]}\"},\"finish_reason\":\"stop\"}]}\n\n"
            b"data: [DONE]\n\n"
        )

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body))

        with tempfile.TemporaryDirectory() as temporary:
            runner = RUNNER.FormalSSERunner("https://example.test/v1", ["secret"], Path(temporary), 1, 0, {})
            runner.slots[0].client.close()
            runner.slots[0].client = httpx.Client(transport=httpx.MockTransport(handler))
            try:
                row = runner.run_one(self._item(), 0)
            finally:
                runner.close()
            self.assertEqual(row["status"], "api_error")
            self.assertEqual(row["error_code"], "MODEL_IDENTITY_MISMATCH")

    def test_three_infrastructure_retries_use_frozen_backoff(self) -> None:
        item = self._item()
        success = RUNNER.SSEOutcome(
            200, {}, {"model": RUNNER.MODEL, "choices": [{"message": {"content": '{"ranked_candidate_ids":["c1"]}'}, "finish_reason": "stop"}], "usage": None},
            0, 1, 1.0, 1.0, True, True, "stop",
        )
        failures = [RUNNER.SSEOutcome(524, {}, None, 0, 0, None, None, False, False, None, "HTTP_524", "HTTP error", True) for _ in range(3)]
        outcomes = iter(failures + [success])
        sleeps: list[int] = []
        with tempfile.TemporaryDirectory() as temporary:
            runner = RUNNER.FormalSSERunner("https://example.test/v1", ["secret"], Path(temporary), 1, 3, {})
            try:
                with patch.object(runner, "send_stream", side_effect=lambda *_: next(outcomes)), patch.object(RUNNER.time, "sleep", side_effect=sleeps.append):
                    row = runner.run_one(item, 0)
            finally:
                runner.close()
        self.assertEqual(sleeps, [15, 30, 60])
        self.assertEqual(row["attempt_count"], 4)
        self.assertEqual(row["status"], "succeeded")

    def test_expired_total_deadline_blocks_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = RUNNER.FormalSSERunner("https://example.test/v1", ["secret"], Path(temporary), 1, 0, {})
            try:
                outcome = runner.send_stream(runner.slots[0], self._item(), 1, Path(temporary) / "raw.jsonl", time.perf_counter() - 1)
            finally:
                runner.close()
        self.assertEqual(outcome.error_code, "MAX_WALL_TIME_EXCEEDED")

    def test_governance_key_names_are_exact(self) -> None:
        self.assertEqual(RUNNER.KEY_ENV_NAMES, [f"SDB_QWEN_API_KEY_{index:02d}" for index in range(1, 5)])

    def _stream_outcome(self, body: bytes, key: str = "secret", raw_path: Path | None = None):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(body))

        temporary_context = tempfile.TemporaryDirectory() if raw_path is None else None
        directory = Path(temporary_context.name) if temporary_context is not None else raw_path.parent
        actual_raw = raw_path if raw_path is not None else directory / "raw.jsonl"
        runner = RUNNER.FormalSSERunner("https://example.test/v1", [key], directory, 1, 0, {})
        runner.slots[0].client.close()
        runner.slots[0].client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            return runner.send_stream(runner.slots[0], self._item(), 1, actual_raw, time.perf_counter() + 60)
        finally:
            runner.close()
            if temporary_context is not None:
                temporary_context.cleanup()

    @staticmethod
    def _item():
        return RUNNER.FROZEN.RequestItem(
            request_id="unit-helper", track="native", task_type="single_service_recommendation",
            prediction_target="service", candidate_ids=["c1"], require_selected=False,
            payload={"model": RUNNER.MODEL, "messages": [], "stream": True, "max_tokens": 32},
            source_row_sha256="a" * 64, candidate_order_sha256="b" * 64,
        )


if __name__ == "__main__":
    unittest.main()
