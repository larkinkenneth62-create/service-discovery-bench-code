from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Any

from huggingface_hub import snapshot_download
from transformers import AutoTokenizer


RUNNER_PATH = Path(__file__).resolve().parent / "run_qwen_sse_formal_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_qwen_sse_budget_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)

TOKENIZER_REPO = "Qwen/Qwen3.6-35B-A3B"
TOKENIZER_REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"
TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "chat_template.json",
    "vocab.json",
    "merges.txt",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))
    return ordered[index]


def legal_output(item: Any) -> str:
    result: dict[str, Any] = {"ranked_candidate_ids": list(item.candidate_ids)}
    if item.require_selected:
        result["selected_candidate_ids"] = list(item.candidate_ids)
    return RUNNER.stable_json(result)


def input_tokens(tokenizer: Any, item: Any) -> int:
    messages = item.payload.get("messages", [])
    try:
        encoded = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        return len(encoded)
    except Exception:
        return len(tokenizer.encode(RUNNER.stable_json(messages), add_special_tokens=False))


def audit_track(tokenizer: Any, name: str, items: list[Any], source: Path) -> dict[str, Any]:
    input_counts: list[int] = []
    output_counts: list[int] = []
    worst_input: tuple[int, str] = (-1, "")
    worst_output: tuple[int, str, str] = (-1, "", "")
    for item in items:
        in_count = input_tokens(tokenizer, item)
        output = legal_output(item)
        out_count = len(tokenizer.encode(output, add_special_tokens=False))
        input_counts.append(in_count)
        output_counts.append(out_count)
        if in_count > worst_input[0]:
            worst_input = (in_count, item.request_id)
        if out_count > worst_output[0]:
            worst_output = (out_count, item.request_id, RUNNER.sha256_text(output))
    frozen_max_tokens = math.ceil(worst_output[0] * 1.10)
    return {
        "track": name,
        "source_path": str(source.resolve()),
        "source_sha256": sha256_file(source),
        "rows": len(items),
        "input_tokens": {
            "min": min(input_counts),
            "median": int(median(input_counts)),
            "p95": percentile(input_counts, 0.95),
            "max": worst_input[0],
            "max_task_id": worst_input[1],
        },
        "worst_legal_output": {
            "token_count": worst_output[0],
            "task_id": worst_output[1],
            "canonical_json_sha256": worst_output[2],
            "construction": "full candidate permutation; selected set equals full pool when the frozen schema requires it",
            "serialization": "UTF-8 canonical compact JSON with sorted keys",
        },
        "margin_fraction": 0.10,
        "frozen_max_tokens": frozen_max_tokens,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--machine", type=Path, required=True)
    parser.add_argument("--unified", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()

    snapshot = Path(snapshot_download(
        repo_id=TOKENIZER_REPO,
        revision=TOKENIZER_REVISION,
        cache_dir=args.cache_dir,
        allow_patterns=list(TOKENIZER_FILES),
    ))
    tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True, trust_remote_code=False)
    native_items = list(RUNNER.FROZEN.iter_formal(args.native, "native", RUNNER.MODEL))
    machine_items = list(RUNNER.FROZEN.iter_formal(args.machine, "machine", RUNNER.MODEL))
    unified_items = list(RUNNER.FROZEN.iter_formal(args.unified, "unified", RUNNER.MODEL))
    files = []
    for name in TOKENIZER_FILES:
        path = snapshot / name
        if path.is_file():
            files.append({"name": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    report = {
        "schema_version": 1,
        "status": "PASS",
        "model": RUNNER.MODEL,
        "tokenizer_binding": {
            "repo_id": TOKENIZER_REPO,
            "revision": TOKENIZER_REVISION,
            "tokenizer_class": type(tokenizer).__name__,
            "files": files,
            "binding_basis": "official base model named by the served GGUF filename; quantization/finetune suffix does not alter token IDs",
        },
        "budget_rule": "ceil(actual_tokenizer_worst_legal_canonical_json_tokens * 1.10)",
        "frozen_adapter_sha256": RUNNER.FROZEN_ADAPTER_SHA256,
        "tracks": {
            "native": audit_track(tokenizer, "native", native_items, args.native),
            "machine": audit_track(tokenizer, "machine", machine_items, args.machine),
            "unified": audit_track(tokenizer, "unified", unified_items, args.unified),
        },
        "formal_parameters": {"temperature": 0, "top_p": 1, "n": 1, "seed": 0, "stream": True},
    }
    RUNNER.atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
