from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "experiments" / "llm_v0_2_qwen38_sse_structured_selection_v1_9"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load("synthetic_qwen38_structured_runner_v1_9", EXPERIMENT / "code" / "run_qwen38_sse_structured_selection_v1_9.py")
SCORER = load("synthetic_scorer_v1_5", ROOT / "scripts" / "evaluation" / "score_native_machine_selection_v1_5.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    fixture = ROOT / "tests" / "fixtures" / "synthetic_native_machine" / "manifest.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))
    runtime_tmp = ROOT / "runtime_tmp"
    runtime_tmp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=runtime_tmp) as temporary:
        work = Path(temporary)
        status_rows = []
        manifest_rows = []
        for index, row in enumerate(rows):
            contract = RUNNER.CONTRACTS.contract_for("native", row["task_type"])
            candidate_ids = [item["candidate_id"] for item in row["candidate_documents"]]
            payload = RUNNER.build_payload(
                query=row["query"], task_type=row["task_type"], prediction_target=row["prediction_target"],
                candidate_documents=row["candidate_documents"], candidate_ids=candidate_ids,
                contract=contract, max_tokens=256,
            )
            assert payload["stream"] is True and "gold" not in payload["messages"][1]["content"].lower()
            assert payload["model"] == "qwen3.8-27b-fp8"
            assert payload["chat_template_kwargs"] == {
                "enable_thinking": True,
                "preserve_thinking": True,
            }
            assert payload["response_format"]["type"] == "json_schema"
            assert payload["response_format"]["json_schema"]["strict"] is True
            schema = payload["response_format"]["json_schema"]["schema"]
            field = "ranked_candidate_ids" if contract == RUNNER.CONTRACTS.TOP5_RANKING_V1 else "selected_candidate_ids"
            assert schema["properties"][field]["items"]["enum"] == candidate_ids
            gold_options = row.get("acceptable_gold_sets") or []
            if not gold_options or not isinstance(gold_options[0], list):
                raise AssertionError("synthetic fixture must provide at least one acceptable Gold set")
            gold = list(gold_options[0])
            if contract == RUNNER.CONTRACTS.TOP5_RANKING_V1:
                # Put a known acceptable Gold item first, then fill the remaining
                # deterministic Top-K positions from the candidate pool.
                ordered = list(dict.fromkeys([*gold, *candidate_ids]))
                answer = {"ranked_candidate_ids": ordered[: min(5, len(candidate_ids))]}
            else:
                # Use one complete acceptable set so the synthetic end-to-end path
                # validates a successful set-selection score without unioning alternatives.
                answer = {"selected_candidate_ids": gold}
            state = {"content": [], "reasoning": [], "heartbeats": 0, "events": 0, "terminal": False, "done": False, "finish_reason": None, "response_model": None, "usage": None}
            RUNNER._consume_frame("{}", "heartbeat", state)
            delta = {"content": json.dumps(answer)}
            if index % 2 == 0:
                delta["reasoning_content"] = "synthetic reasoning"
            event = {"model": RUNNER.MODEL, "choices": [{"delta": delta, "finish_reason": "stop"}]}
            RUNNER._consume_frame(json.dumps(event), None, state)
            RUNNER._consume_frame("[DONE]", None, state)
            response = {"choices": [{"message": {"content": "".join(state["content"]), "reasoning_content": "".join(state["reasoning"])}}]}
            parsed = (
                RUNNER.CONTRACTS.parse_topk_response(response, candidate_ids, min(5, len(candidate_ids)))
                if contract == RUNNER.CONTRACTS.TOP5_RANKING_V1
                else RUNNER.CONTRACTS.parse_selected_set_response(response, candidate_ids)
            )
            assert parsed.valid
            prediction = work / "artifacts" / f"row-{index}" / "parsed_prediction.json"
            write_json(prediction, parsed.data)
            status_rows.append({
                "experiment_revision": RUNNER.REVISION,
                "request_id": row["request_id"], "status": "succeeded", "parse_status": "valid",
                "task_type": row["task_type"], "prediction_target": row["prediction_target"],
                "output_contract": contract, "candidate_count": len(candidate_ids),
                "parsed_prediction_path": prediction.relative_to(work).as_posix(),
                "heartbeat_count": state["heartbeats"], "retry_count": 0, "end_to_end_latency_ms": 1.0,
                "attempt_count": 1,
                "response_format_mode": RUNNER.RESPONSE_FORMAT_MODE,
                "response_format_type": "json_schema", "reasoning_channel_status": "present" if state["reasoning"] else "absent",
                "response_schema_strict": True,
            })
            manifest_rows.append({"benchmark_task_id": row["request_id"], **row})
        scored = SCORER.score_rows(manifest_rows, status_rows, work)
        assert len(scored) == len(rows)
        assert all(item["metrics"]["parse_failure"] == 0 for item in scored)
        assert all(item["metrics"]["task_success"] == 1.0 for item in scored)
        tables = SCORER.build_tables(scored)
        assert tables["macro_6"][0]["task_success"] == 1.0
        assert tables["macro_6"][0]["task_count"] == len({row["task_type"] for row in rows})

        run_dirs = []
        for label in ("smoke", "machine", "native"):
            directory = work / label
            directory.mkdir()
            (directory / "REQUEST_STATUS.jsonl").write_text("".join(json.dumps(row) + "\n" for row in status_rows), encoding="utf-8")
            ledger_rows = []
            for row in status_rows:
                artifact = directory / "artifacts" / hashlib.sha256(row["request_id"].encode()).hexdigest()[:24]
                artifact.mkdir(parents=True, exist_ok=True)
                raw = artifact / "raw_sse_events_attempt_1.jsonl"
                raw.write_text("", encoding="utf-8")
                relative = raw.relative_to(directory).as_posix()
                common = {
                    "schema_version": 1,
                    "experiment_revision": RUNNER.REVISION,
                    "request_id": row["request_id"],
                    "request_sha256": hashlib.sha256(row["request_id"].encode()).hexdigest(),
                    "attempt": 1,
                }
                ledger_rows.extend([
                    {**common, "event": "attempt_started", "raw_sse_events_path": relative},
                    {**common, "event": "attempt_finished", "raw_sse_events_path": relative, "raw_sse_events_sha256": sha(raw)},
                ])
            (directory / "ATTEMPT_LEDGER.jsonl").write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in ledger_rows), encoding="utf-8"
            )
            write_json(directory / "RUN_SUMMARY.json", {
                "status": "COMPLETE_ALL_PARSED", "requested_rows": len(status_rows), "terminal_rows": len(status_rows), "label": label,
            })
            for row in status_rows:
                source = work / row["parsed_prediction_path"]
                destination = directory / row["parsed_prediction_path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            run_dirs.append(directory)
        scores = work / "scores"
        scores.mkdir()
        write_json(scores / "SCORE_SUMMARY.json", {"status": "PASS", "rows": len(status_rows) * 2})
        (scores / "synthetic_scores.csv").write_text("metric,value\ntask_success,1\n", encoding="utf-8")
        budget = work / "budget.json"
        write_json(budget, {"status": "PASS", "model": RUNNER.MODEL, "tokenizer_revision": RUNNER.TOKENIZER_REVISION})
        bundle_dir = work / "bundle"
        bundle_zip = work / "bundle.zip"
        command = [
            sys.executable, str(ROOT / "scripts" / "release" / "build_qwen38_structured_native_machine_bundle_v1_9.py"),
            "--mode", "synthetic",
            "--smoke-dir", str(run_dirs[0]), "--machine-dir", str(run_dirs[1]), "--native-dir", str(run_dirs[2]),
            "--scores-dir", str(scores), "--prompt-contract", str(EXPERIMENT / "prompts" / "SELECTION_PROMPT_CONTRACT_V1_5.md"),
            "--output-contract-registry", str(EXPERIMENT / "schemas" / "OUTPUT_CONTRACT_REGISTRY_V1_5.json"),
            "--token-budget-freeze", str(budget), "--output-dir", str(bundle_dir), "--zip", str(bundle_zip),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        assert bundle_zip.is_file() and bundle_zip.with_suffix(".zip.sha256").is_file()
        assert (bundle_dir / "VALIDATION_SUMMARY.json").is_file()
        print(json.dumps({"status": "PASS", "synthetic_rows": len(rows), "bundle_bytes": bundle_zip.stat().st_size, "bundle_sha256": sha(bundle_zip)}, indent=2))


if __name__ == "__main__":
    main()
