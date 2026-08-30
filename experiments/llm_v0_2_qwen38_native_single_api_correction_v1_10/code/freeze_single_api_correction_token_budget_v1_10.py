from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

OFFICIAL_MODEL_ID = "Qwen/Qwen3.8-27B-FP8"
MODEL = "qwen3.8-27b-fp8"
TOKENIZER_REPO_ID = "Qwen/Qwen3.8-27B-FP8"
TOKENIZER_REVISION = "RUNTIME_REPORTED_OR_UNAVAILABLE"
TOKEN_COUNTER_REVISION = "UTF8_BYTE_UPPER_BOUND_PLUS_REASONING_4096_V1"
SAFETY_MARGIN_TOKENS = 64
THINKING_ALLOWANCE_TOKENS = 4096


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


def combined_json_tokens(ids: list[str], token_count: Callable[[str], int]) -> int:
    # Summing the two complete one-field JSON objects conservatively over-counts the
    # braces needed by the actual two-field object. It covers Top-5 plus the legal
    # worst case in which every candidate is selected, without consulting Gold.
    return top5_json_tokens(ids, token_count) + selected_set_json_tokens(ids, token_count)


def freeze(
    *, native_path: Path, smoke_path: Path,
    token_count: Callable[[str], int],
) -> dict[str, Any]:
    native_rows = rows(native_path)
    smoke_rows = rows(smoke_path)

    if len(native_rows) != 3043 or len(smoke_rows) != 10:
        raise ValueError("V1.10 token freeze requires exactly 3,043 formal and 10 frozen smoke rows")
    if any(task_type(row) != "single_api_recommendation" for row in native_rows + smoke_rows):
        raise ValueError("V1.10 token freeze accepts only Single API rows")
    combined = [combined_json_tokens(candidate_ids(row), token_count) for row in native_rows + smoke_rows]
    combined_max = max(combined)
    native_answer_bound = combined_max + SAFETY_MARGIN_TOKENS
    native_frozen = native_answer_bound + THINKING_ALLOWANCE_TOKENS

    return {
        "schema_version": 1,
        "status": "PASS",
        "experiment_revision": "QWEN38_NATIVE_SINGLE_API_RANKING_AND_SET_CORRECTION_V1_10",
        "model": MODEL,
        "official_model_id": OFFICIAL_MODEL_ID,
        "tokenizer_repo_id": TOKENIZER_REPO_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "token_counter_revision": TOKEN_COUNTER_REVISION,
        "token_counter_method": "UTF8_BYTES_AS_CONSERVATIVE_TOKEN_UPPER_BOUND",
        "safety_margin_tokens": SAFETY_MARGIN_TOKENS,
        "thinking_allowance_tokens": THINKING_ALLOWANCE_TOKENS,
        "budget_principle": "FIXED_REASONING_ALLOWANCE_PLUS_FULL_LEGAL_ANSWER_SPACE_NO_GOLD_CARDINALITY",
        "statistics": {
            "combined_top5_plus_full_pool_json_tokens_max": combined_max,
            "native_answer_upper_bound_with_margin": native_answer_bound,
            "native_candidate_count_max": max(len(candidate_ids(row)) for row in native_rows),
            "smoke_candidate_count_max": max(len(candidate_ids(row)) for row in smoke_rows),
        },
        "tracks": {
            "native": {
                "frozen_max_tokens": native_frozen,
                "allowed_source_manifest_sha256": [sha256_file(native_path), sha256_file(smoke_path)],
                "selected_set_upper_bound": "ALL_CANDIDATE_IDS_IN_LARGEST_NATIVE_OR_SMOKE_SINGLE_API_ROW",
                "reasoning_allowance_tokens": THINKING_ALLOWANCE_TOKENS,
            },
        },
        "dev_gold_read": False,
        "test_gold_read": False,
        "model_results_read": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Qwen3.8 Single API Correction V1.10 output token budget")
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--dev-truth", type=Path, help="Deprecated compatibility argument; never read")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer-path", type=Path, help="Forbidden compatibility argument")
    args = parser.parse_args()
    if args.tokenizer_path is not None:
        raise SystemExit("Single API Correction V1.10 uses the frozen byte upper bound plus reasoning allowance; --tokenizer-path is forbidden")
    result = freeze(
        native_path=args.native,
        smoke_path=args.smoke,
        token_count=lambda value: len(value.encode("utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
