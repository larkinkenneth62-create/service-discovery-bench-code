from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CODE = ROOT / "experiments" / "llm_v0_2_qwen38_native_single_api_correction_v1_10" / "code"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    contracts = load("sdb_contracts_v1_10_public", CODE / "output_contracts_v1_10.py")
    runner = load("sdb_runner_v1_10_public", CODE / "run_qwen38_native_single_api_correction_v1_10.py")
    candidate_ids = [f"api-{index}" for index in range(6)]
    documents = [{"candidate_id": item, "document": f"Synthetic API {item}"} for item in candidate_ids]
    payload = runner.build_payload(
        query="Use all necessary synthetic APIs.", task_type="single_api_recommendation",
        prediction_target="api", candidate_documents=documents, candidate_ids=candidate_ids,
        contract=contracts.RANKING_AND_SELECTED_SET_V1_10, max_tokens=1024,
    )
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["ranked_candidate_ids", "selected_candidate_ids"]
    assert schema["properties"]["selected_candidate_ids"]["maxItems"] == 6
    assert "gold" not in json.dumps(payload, sort_keys=True).lower()
    answer = {"ranked_candidate_ids": candidate_ids[:5], "selected_candidate_ids": candidate_ids}
    response = {"choices": [{"message": {"content": json.dumps(answer, separators=(",", ":"))}}]}
    parsed = contracts.parse_ranking_and_selected_set_response(response, candidate_ids)
    assert parsed.valid and len(parsed.data["selected_candidate_ids"]) == 6
    assert contracts.contract_for("native", "single_service_discovery") == contracts.TOP5_RANKING_V1
    print(json.dumps({"status": "PASS", "synthetic_rows": 1, "selected_count": 6}, indent=2))


if __name__ == "__main__":
    main()
