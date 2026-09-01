from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "experiments" / "llm_v0_2_deepseek_v4_flash_structured_selection_v2_2" / "code"
SCHEMA = ROOT / "experiments" / "llm_v0_2_deepseek_v4_flash_structured_selection_v2_2" / "schemas"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = load("sdb_deepseek_r3_nonstream_test", CODE / "run_deepseek_v4_flash_v2_2_r3_nonstream.py")


def item():
    documents = [{"candidate_id": "c1", "document": "A candidate."}]
    payload = R.build_payload(
        query="Find a candidate.",
        task_type="single_service_discovery",
        prediction_target="service",
        candidate_documents=documents,
        candidate_ids=["c1"],
        contract=R.CONTRACTS.TOP5_RANKING_V1,
        max_tokens=32,
    )
    return R.RequestItem("r1", "smoke", "single_service_discovery", "service", ["c1"], R.CONTRACTS.TOP5_RANKING_V1, payload, "0" * 64)


def test_r3_freezes_exact_nonstream_contract():
    request = item().payload
    assert request["model"] == "DeepSeek-V4-Flash"
    assert request["stream"] is False
    assert "stream_options" not in request
    runtime = R.load_runtime_freeze(SCHEMA / "DEEPSEEK_V4_FLASH_RUNTIME_FREEZE_V2_2_R3_NONSTREAM.json")
    assert runtime["transport_protocol"] == R.TRANSPORT_PROTOCOL


def test_r3_nonstream_transport_captures_complete_json(monkeypatch, tmp_path):
    response_body = {
        "id": "resp-1",
        "created": 1,
        "model": "DeepSeek-V4-Flash",
        "system_fingerprint": "fp",
        "choices": [{"message": {"content": json.dumps({"ranked_candidate_ids": ["c1"]})}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    class Response:
        status_code = 200
        text = json.dumps(response_body)
        content = text.encode()

        def json(self):
            return response_body

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            assert kwargs["json"]["stream"] is False
            return Response()

    monkeypatch.setattr(R.httpx, "Client", Client)
    runner = R.DeepSeekRunner(base_url="https://example.invalid/v1", key="secret", output_dir=tmp_path, concurrency=1, provenance={})
    raw = tmp_path / "raw.json"
    outcome = runner.send_nonstream(item(), raw)
    assert outcome.http_status == 200
    assert outcome.error_code is None
    assert outcome.terminal is True and outcome.done is True
    assert outcome.final_response == response_body
    saved = json.loads(raw.read_text(encoding="utf-8"))
    assert saved["body"] == response_body
    assert "secret" not in raw.read_text(encoding="utf-8")
