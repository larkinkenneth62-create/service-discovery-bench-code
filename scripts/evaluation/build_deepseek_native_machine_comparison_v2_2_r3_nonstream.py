from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R3_NONSTREAM_GATEWAY"
TRANSPORT_PROTOCOL = "openai_chat_completions_json_nonstream"
INFERENCE_PUBLIC_COMMIT = "3657a53b3ac3c98adc66ee3475111ba2115b83a3"


def _load() -> Any:
    path = Path(__file__).with_name("build_deepseek_native_machine_comparison_v2_2.py")
    spec = importlib.util.spec_from_file_location("sdb_deepseek_r2_pairing_for_r3", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def validate_binding(path: Path) -> dict[str, Any]:
    value = read_json(path)
    expected = {
        "status": "PASS",
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "inference_public_commit": INFERENCE_PUBLIC_COMMIT,
        "source_snapshot_match": True,
    }
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        raise ValueError("BLOCKED_R3_PROVENANCE_BINDING")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an explicit R3 Native/Machine comparison")
    parser.add_argument("--native-scores", type=Path, required=True)
    parser.add_argument("--machine-scores", type=Path, required=True)
    parser.add_argument("--pairing-manifest", type=Path)
    parser.add_argument("--provenance-binding", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    binding = validate_binding(args.provenance_binding)
    native_rows = BASE.read_rows(args.native_scores)
    machine_rows = BASE.read_rows(args.machine_scores)
    pairing_rows = BASE.read_rows(args.pairing_manifest) if args.pairing_manifest is not None else None
    rows, validation = BASE.build_comparison(native_rows, machine_rows, pairing_rows)
    validation.update({
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "inference_public_commit": INFERENCE_PUBLIC_COMMIT,
        "provenance_binding_sha256": sha256_file(args.provenance_binding),
        "native_scores_sha256": sha256_file(args.native_scores),
        "machine_scores_sha256": sha256_file(args.machine_scores),
        "pairing_manifest_sha256": sha256_file(args.pairing_manifest) if args.pairing_manifest is not None else None,
        "inference_rerun": False,
        "paid_api_calls": 0,
    })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    BASE.write_csv(args.output_dir / "NATIVE_MACHINE_MATCHED_DELTA.csv", rows)
    (args.output_dir / "PAIRING_VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
