from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer


RUNNER_PATH = Path(__file__).resolve().parent / "run_qwen_sse_formal_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_qwen_sse_budget_v2", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def pretty_legal_output(row: dict[str, Any], track: str) -> str:
    value: dict[str, Any] = {"ranked_candidate_ids": list(row["candidate_ids"])}
    if track != "unified" and bool(row.get("require_selected")):
        value["selected_candidate_ids"] = list(row["candidate_ids"])
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def audit_output(tokenizer: Any, track: str, source: Path, prior: dict[str, Any]) -> dict[str, Any]:
    worst = (-1, "", "")
    rows = 0
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            output = pretty_legal_output(row, track)
            count = len(tokenizer.encode(output, add_special_tokens=False))
            task_id = row.get("benchmark_task_id", row.get("request_id", ""))
            if count > worst[0]:
                worst = (count, task_id, RUNNER.sha256_text(output))
            rows += 1
    if rows != prior["rows"] or sha256_file(source) != prior["source_sha256"]:
        raise SystemExit(f"{track} source changed relative to V1 freeze")
    result = dict(prior)
    result["worst_legal_output"] = {
        "token_count": worst[0],
        "task_id": worst[1],
        "pretty_json_sha256": worst[2],
        "construction": "full candidate permutation; selected set equals full pool when required",
        "serialization": "UTF-8 JSON, sort_keys=true, indent=2",
        "registered_reason": "covers the endpoint JSON-mode formatting envelope observed in Q1 without using gold labels or accuracy",
    }
    result["margin_fraction"] = 0.10
    result["frozen_max_tokens"] = math.ceil(worst[0] * 1.10)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--machine", type=Path, required=True)
    parser.add_argument("--unified", type=Path, required=True)
    parser.add_argument("--prior-freeze", type=Path, required=True)
    parser.add_argument("--tokenizer-snapshot", type=Path, required=True)
    parser.add_argument("--q1-gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prior = json.loads(args.prior_freeze.read_text(encoding="utf-8"))
    if prior.get("status") != "PASS" or prior.get("tokenizer_binding", {}).get("revision") != RUNNER.TOKENIZER_REVISION:
        raise SystemExit("prior freeze is not the registered V1 tokenizer binding")
    q1 = json.loads(args.q1_gate.read_text(encoding="utf-8"))
    if q1.get("status") != "FAIL" or q1.get("checks", {}).get("no_output_length_finish") is not False:
        raise SystemExit("Q1 evidence does not establish output-budget truncation")
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_snapshot, local_files_only=True, trust_remote_code=False)
    report = {
        "schema_version": 2,
        "status": "PASS",
        "model": RUNNER.MODEL,
        "tokenizer_binding": prior["tokenizer_binding"],
        "frozen_adapter_sha256": RUNNER.FROZEN_ADAPTER_SHA256,
        "budget_rule": "ceil(actual_tokenizer_worst_indent2_full_legal_JSON_tokens * 1.10)",
        "output_serialization_envelope": "sort_keys=true, indent=2",
        "correction_scope": "output formatting envelope only; no prompt/schema/parser/candidate/decoding/accuracy change",
        "supersedes_budget_freeze_sha256": sha256_file(args.prior_freeze),
        "q1_failure_evidence_sha256": sha256_file(args.q1_gate),
        "tracks": {
            "native": audit_output(tokenizer, "native", args.native, prior["tracks"]["native"]),
            "machine": audit_output(tokenizer, "machine", args.machine, prior["tracks"]["machine"]),
            "unified": audit_output(tokenizer, "unified", args.unified, prior["tracks"]["unified"]),
        },
        "formal_parameters": prior["formal_parameters"],
    }
    RUNNER.atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
