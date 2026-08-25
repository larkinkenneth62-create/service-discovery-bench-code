from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


MODEL = "Qwen3.6-35B-A3B-APEX-I-Compact.gguf"
TOKENIZER_REPO_ID = "Qwen/Qwen3.6-35B-A3B"
TOKENIZER_REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"
SAFETY_MARGIN_TOKENS = 64


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def candidate_ids(row: dict[str, Any]) -> list[str]:
    direct = row.get("candidate_ids")
    if isinstance(direct, list):
        return direct
    visible = row.get("model_visible_input", row)
    documents = visible.get("candidate_documents") if isinstance(visible, dict) else None
    if isinstance(documents, list):
        return [item["candidate_id"] for item in documents]
    messages = row.get("messages")
    if isinstance(messages, list):
        content = next(item["content"] for item in messages if item.get("role") == "user")
        visible = json.loads(content.split("INPUT_JSON=", 1)[1])
        return [item["candidate_id"] for item in visible["candidate_documents"]]
    raise ValueError("cannot resolve candidate IDs")


def task_type(row: dict[str, Any]) -> str:
    value = row.get("task_type")
    if not isinstance(value, str):
        raise ValueError("missing task_type")
    return value


def _flatten_gold_sets(value: Any) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        return []
    if all(isinstance(item, str) for item in value):
        return [value]
    result: list[list[str]] = []
    for item in value:
        result.extend(_flatten_gold_sets(item))
    return result


def dev_selected_cardinality(row: dict[str, Any], dev_truth: dict[str, dict[str, Any]]) -> int:
    for field in ("acceptable_gold_sets", "gold_candidate_ids", "gold_ids", "gold"):
        sets = _flatten_gold_sets(row.get(field))
        if sets:
            return max(len(item) for item in sets)
    request_id = row.get("benchmark_task_id", row.get("request_id", ""))
    benchmark_task_id = request_id[request_id.index("sdb-") :] if isinstance(request_id, str) and "sdb-" in request_id else request_id
    truth = dev_truth.get(benchmark_task_id, {})
    for field in ("acceptable_gold_sets", "reference_gold_ids"):
        sets = _flatten_gold_sets(truth.get(field))
        if sets:
            return max(len(item) for item in sets)
    count = truth.get("gold_count")
    return int(count) if isinstance(count, int) and count >= 0 else 0


def freeze(
    *, native_path: Path, machine_path: Path, smoke_path: Path, dev_truth_path: Path,
    token_count: Callable[[str], int],
) -> dict[str, Any]:
    native_rows = rows(native_path)
    machine_rows = rows(machine_path)
    smoke_rows = rows(smoke_path)
    dev_truth = {row["benchmark_task_id"]: row for row in rows(dev_truth_path)}
    all_rows = native_rows + machine_rows + smoke_rows
    distinct_ids = sorted(
        {item for row in all_rows for item in candidate_ids(row)},
        key=lambda value: (-token_count(stable_json(value)), value),
    )
    max_id = distinct_ids[0]
    max_id_tokens = token_count(stable_json(max_id))
    top5_example = {"ranked_candidate_ids": distinct_ids[:5]}
    max_dev_selected = max((dev_selected_cardinality(row, dev_truth) for row in smoke_rows if task_type(row).startswith(("multi_", "composable_"))), default=0)
    if max_dev_selected < 1:
        raise ValueError("Dev selected-set cardinality could not be resolved from the frozen Dev truth")
    selected_example = {"selected_candidate_ids": distinct_ids[:max_dev_selected]}
    top5_tokens = token_count(stable_json(top5_example))
    selected_tokens = token_count(stable_json(selected_example))
    frozen = max(top5_tokens, selected_tokens) + SAFETY_MARGIN_TOKENS
    return {
        "schema_version": 1,
        "status": "PASS",
        "experiment_revision": "QWEN_SSE_SELECTION_V1_5",
        "model": MODEL,
        "tokenizer_repo_id": TOKENIZER_REPO_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "safety_margin_tokens": SAFETY_MARGIN_TOKENS,
        "statistics": {
            "max_candidate_id_tokens": max_id_tokens,
            "top5_json_tokens": top5_tokens,
            "dev_max_legal_selected_cardinality": max_dev_selected,
            "selected_set_json_tokens": selected_tokens,
        },
        "tracks": {
            "native": {
                "frozen_max_tokens": frozen,
                "allowed_source_manifest_sha256": [sha256_file(native_path), sha256_file(smoke_path)],
            },
            "machine": {
                "frozen_max_tokens": top5_tokens + SAFETY_MARGIN_TOKENS,
                "allowed_source_manifest_sha256": [sha256_file(machine_path)],
            },
        },
        "test_gold_read": False,
        "model_results_read": False,
        "dev_truth_sha256": sha256_file(dev_truth_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Qwen Selection V1.5 output token budgets")
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--machine", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--dev-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer-path", type=Path)
    args = parser.parse_args()
    from transformers import AutoTokenizer

    source = str(args.tokenizer_path) if args.tokenizer_path else TOKENIZER_REPO_ID
    tokenizer = AutoTokenizer.from_pretrained(
        source,
        revision=None if args.tokenizer_path else TOKENIZER_REVISION,
        local_files_only=args.tokenizer_path is None,
        trust_remote_code=False,
    )
    result = freeze(
        native_path=args.native,
        machine_path=args.machine,
        smoke_path=args.smoke,
        dev_truth_path=args.dev_truth,
        token_count=lambda value: len(tokenizer.encode(value, add_special_tokens=False)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
