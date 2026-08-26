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
        values = direct
    else:
        visible = row.get("model_visible_input", row)
        documents = visible.get("candidate_documents") if isinstance(visible, dict) else None
        if isinstance(documents, list):
            values = [item.get("candidate_id") for item in documents]
        else:
            messages = row.get("messages")
            if not isinstance(messages, list):
                raise ValueError("cannot resolve candidate IDs")
            user_messages = [item.get("content") for item in messages if item.get("role") == "user"]
            if len(user_messages) != 1 or not isinstance(user_messages[0], str) or "INPUT_JSON=" not in user_messages[0]:
                raise ValueError("cannot resolve candidate IDs from messages")
            parsed = json.loads(user_messages[0].split("INPUT_JSON=", 1)[1].strip())
            values = [item.get("candidate_id") for item in parsed.get("candidate_documents", [])]
    if not values or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("candidate IDs must be non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError("candidate IDs must be unique")
    return list(values)


def task_type(row: dict[str, Any]) -> str:
    value = row.get("task_type")
    if not isinstance(value, str) or not value:
        raise ValueError("missing task_type")
    return value


def is_selected_set_task(row: dict[str, Any]) -> bool:
    return task_type(row).startswith(("multi_", "composable_"))


def json_array_upper_bound_tokens(field: str, ids: list[str], token_count: Callable[[str], int]) -> int:
    # Tokenize the immutable JSON wrapper, every quoted ID independently, and every
    # separator independently.  The sum is a conservative upper bound for any ordering
    # of the same IDs because tokenizer merges across piece boundaries can only reduce
    # the number of tokens.
    prefix = stable_json({field: []})[:-2]  # remove the final []}
    suffix = "]}"
    separator = ","
    return (
        token_count(prefix)
        + token_count(suffix)
        + sum(token_count(stable_json(value)) for value in ids)
        + max(0, len(ids) - 1) * max(1, token_count(separator))
    )


def top5_json_tokens(ids: list[str], token_count: Callable[[str], int]) -> int:
    longest = sorted(ids, key=lambda value: (-token_count(stable_json(value)), value))[: min(5, len(ids))]
    return json_array_upper_bound_tokens("ranked_candidate_ids", longest, token_count)


def selected_set_json_tokens(ids: list[str], token_count: Callable[[str], int]) -> int:
    # The output contract allows any duplicate-free subset of the candidate pool. The
    # only safe deterministic content bound is therefore the complete in-pool set. This
    # does not disclose or use Gold cardinality and lets over-selection be measured by
    # the scorer instead of being converted into truncation/parse failure.
    return json_array_upper_bound_tokens("selected_candidate_ids", ids, token_count)


def freeze(
    *, native_path: Path, machine_path: Path, smoke_path: Path,
    token_count: Callable[[str], int],
) -> dict[str, Any]:
    native_rows = rows(native_path)
    machine_rows = rows(machine_path)
    smoke_rows = rows(smoke_path)

    native_top5 = [top5_json_tokens(candidate_ids(row), token_count) for row in native_rows + smoke_rows if not is_selected_set_task(row)]
    native_selected = [selected_set_json_tokens(candidate_ids(row), token_count) for row in native_rows + smoke_rows if is_selected_set_task(row)]
    machine_top5 = [top5_json_tokens(candidate_ids(row), token_count) for row in machine_rows]

    if not native_top5:
        raise ValueError("no Native/Smoke Top-5 rows were found")
    if not native_selected:
        raise ValueError("no Native/Smoke selected-set rows were found")
    if not machine_top5:
        raise ValueError("no Machine rows were found")

    native_top5_max = max(native_top5)
    native_selected_max = max(native_selected)
    machine_top5_max = max(machine_top5)
    native_frozen = max(native_top5_max, native_selected_max) + SAFETY_MARGIN_TOKENS
    machine_frozen = machine_top5_max + SAFETY_MARGIN_TOKENS

    return {
        "schema_version": 2,
        "status": "PASS",
        "experiment_revision": "QWEN_SSE_SELECTION_V1_5_R2",
        "model": MODEL,
        "tokenizer_repo_id": TOKENIZER_REPO_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "safety_margin_tokens": SAFETY_MARGIN_TOKENS,
        "budget_principle": "FULL_LEGAL_OUTPUT_SPACE_NO_GOLD_CARDINALITY",
        "statistics": {
            "native_top5_json_tokens_max": native_top5_max,
            "native_selected_set_full_pool_json_tokens_max": native_selected_max,
            "machine_top5_json_tokens_max": machine_top5_max,
            "native_candidate_count_max": max(len(candidate_ids(row)) for row in native_rows),
            "smoke_candidate_count_max": max(len(candidate_ids(row)) for row in smoke_rows),
            "machine_candidate_count_max": max(len(candidate_ids(row)) for row in machine_rows),
        },
        "tracks": {
            "native": {
                "frozen_max_tokens": native_frozen,
                "allowed_source_manifest_sha256": [sha256_file(native_path), sha256_file(smoke_path)],
                "selected_set_upper_bound": "ALL_CANDIDATE_IDS_IN_LARGEST_NATIVE_OR_SMOKE_SELECTED_SET_ROW",
            },
            "machine": {
                "frozen_max_tokens": machine_frozen,
                "allowed_source_manifest_sha256": [sha256_file(machine_path)],
                "output_upper_bound": "TOP5_LONGEST_IDS_PER_ROW",
            },
        },
        "dev_gold_read": False,
        "test_gold_read": False,
        "model_results_read": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Qwen Selection V1.5 R2 output token budgets")
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--machine", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--dev-truth", type=Path, help="Deprecated compatibility argument; never read")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer-path", type=Path)
    args = parser.parse_args()

    from transformers import AutoTokenizer

    source = str(args.tokenizer_path) if args.tokenizer_path else TOKENIZER_REPO_ID
    tokenizer = AutoTokenizer.from_pretrained(
        source,
        revision=None if args.tokenizer_path else TOKENIZER_REVISION,
        local_files_only=args.tokenizer_path is not None,
        trust_remote_code=False,
    )
    result = freeze(
        native_path=args.native,
        machine_path=args.machine,
        smoke_path=args.smoke,
        token_count=lambda value: len(tokenizer.encode(value, add_special_tokens=False)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
