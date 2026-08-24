from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import CALIBRATION_180, DOC_DIR, REGRESSION_DIR, append_csv_row, ensure_dir, now_text, open_csv_writer, table_lines, write_json, write_md
from semcap_v1_2_tightening_utils import run_semcap_v12


def norm_label(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"ok", "semantic_alignment_ok"}:
        return "ok"
    if v in {"mismatch", "semantic_mismatch", "semantic_mismatch_uncertain"}:
        return "mismatch"
    if v in {"coverage_ok", "coverage_mismatch", "coverage_uncertain"}:
        return v
    if v == "uncertain":
        return "uncertain"
    return v


def ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate SemCap v1.2 on combined calibration 180.")
    parser.add_argument("--input", type=Path, default=CALIBRATION_180)
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing calibration input: {args.input}")
    ensure_dir(REGRESSION_DIR)
    rows = []
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        out_fields = fieldnames + [field for field in run_semcap_v12({}).keys() if field.startswith("v12_")]
        out_path = REGRESSION_DIR / "semcap_v1_2_calibration_eval_trace.csv"
        out_f, writer = open_csv_writer(out_path, out_fields)
        try:
            for row in reader:
                out = run_semcap_v12(row)
                append_csv_row(writer, out, out_fields)
                rows.append(out)
        finally:
            out_f.close()
    total = len(rows)
    dangerous_false_keep = 0
    mismatch_total = 0
    mismatch_captured = 0
    high_ok_total = 0
    high_ok_human_ok = 0
    human_ok_total = 0
    human_ok_pred_ok = 0
    sem_match = 0
    cap_match = 0
    for row in rows:
        human_cap = norm_label(row.get("capability_coverage_check", ""))
        human_sem = norm_label(row.get("semantic_alignment_check", ""))
        pred_cap = norm_label(row.get("v12_capability_coverage_pred", ""))
        pred_sem = norm_label(row.get("v12_semantic_alignment_pred", ""))
        pred_conf = row.get("v12_capability_coverage_confidence", "")
        if human_cap == "coverage_mismatch":
            mismatch_total += 1
            if pred_cap in {"coverage_mismatch", "coverage_uncertain"}:
                mismatch_captured += 1
            if pred_cap == "coverage_ok" and pred_conf == "high":
                dangerous_false_keep += 1
        if pred_cap == "coverage_ok" and pred_conf == "high":
            high_ok_total += 1
            if human_cap == "coverage_ok":
                high_ok_human_ok += 1
        if human_cap == "coverage_ok":
            human_ok_total += 1
            if pred_cap == "coverage_ok":
                human_ok_pred_ok += 1
        if human_sem in {"ok", "uncertain", "mismatch"} and pred_sem == human_sem:
            sem_match += 1
        if human_cap in {"coverage_ok", "coverage_uncertain", "coverage_mismatch"} and pred_cap == human_cap:
            cap_match += 1
    summary = {
        "generated_time": now_text(),
        "row_count": total,
        "dangerous_false_keep": dangerous_false_keep,
        "coverage_mismatch_capture": ratio(mismatch_captured, mismatch_total),
        "high_confidence_coverage_ok_precision_like": ratio(high_ok_human_ok, high_ok_total),
        "coverage_ok_recall": ratio(human_ok_pred_ok, human_ok_total),
        "coverage_ok_over_conservative_rate": ratio(human_ok_total - human_ok_pred_ok, human_ok_total),
        "capability_agreement": ratio(cap_match, total),
        "semantic_agreement": ratio(sem_match, total),
        "v12_capability_distribution": dict(Counter(row.get("v12_capability_coverage_pred", "") for row in rows)),
        "v12_semantic_distribution": dict(Counter(row.get("v12_semantic_alignment_pred", "") for row in rows)),
        "passes_minimum_thresholds": dangerous_false_keep == 0 and ratio(mismatch_captured, mismatch_total) >= 0.9 and ratio(high_ok_human_ok, high_ok_total) >= 0.85,
    }
    write_json(REGRESSION_DIR / "semcap_v1_2_calibration_eval_summary.json", summary)
    lines = [
        "# SemCap v1.2 Calibration Eval Report v1.4b",
        "",
        f"Generated time: {now_text()}",
        f"Input: `{args.input}`",
        f"Rows: {total}",
        "",
        f"- dangerous_false_keep: {dangerous_false_keep}",
        f"- coverage_mismatch_capture: {summary['coverage_mismatch_capture']}",
        f"- high_confidence_coverage_ok_precision_like: {summary['high_confidence_coverage_ok_precision_like']}",
        f"- coverage_ok_recall: {summary['coverage_ok_recall']}",
        f"- coverage_ok_over_conservative_rate: {summary['coverage_ok_over_conservative_rate']}",
        f"- capability_agreement: {summary['capability_agreement']}",
        f"- semantic_agreement: {summary['semantic_agreement']}",
        f"- passes_minimum_thresholds: {summary['passes_minimum_thresholds']}",
        "",
        "## v12 Capability Distribution",
        "",
        *table_lines(summary["v12_capability_distribution"]),
    ]
    write_md(DOC_DIR / "semcap_v1_2_calibration_eval_report_v1_4b.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
