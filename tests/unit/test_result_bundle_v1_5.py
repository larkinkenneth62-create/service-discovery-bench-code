from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts" / "release" / "build_llm_native_machine_bundle_v1_5.py"
SPEC = importlib.util.spec_from_file_location("bundle_v1_5_tested", PATH)
assert SPEC and SPEC.loader
B = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = B
SPEC.loader.exec_module(B)


def make_run(path: Path, count: int, status: str = "COMPLETE_ALL_PARSED"):
    path.mkdir()
    rows = []
    for index in range(count):
        rows.append({
            "experiment_revision": B.REVISION,
            "request_id": f"r-{index}",
            "status": "succeeded",
            "parse_status": "valid",
            "output_contract": "TOP5_RANKING_V1",
            "candidate_count": 5,
        })
    (path / "REQUEST_STATUS.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (path / "RUN_SUMMARY.json").write_text(json.dumps({"status": status, "requested_rows": count, "terminal_rows": count}), encoding="utf-8")


def test_validate_track_checks_exact_counts_and_blockers(tmp_path):
    run = tmp_path / "run"
    make_run(run, 3)
    summary, rows = B.validate_track(run, "synthetic", 3, formal=True)
    assert len(rows) == 3
    with pytest.raises(ValueError, match="row mismatch"):
        B.validate_track(run, "synthetic", 4, formal=True)
    rows[0]["status"] = "infra_error"
    (run / "REQUEST_STATUS.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="blocking"):
        B.validate_track(run, "synthetic", 3, formal=True)


def test_percentiles_are_deterministic():
    values = [1.0, 2.0, 3.0, 4.0]
    assert B.percentile(values, 0.5) == 2.5
    assert B.percentile(values, 0.95) == pytest.approx(3.85)
