"""Shared helpers for ServiceDiscoveryBench Round2 v0.5 validation.

The helpers are intentionally conservative:
- Source CSV files are read-only.
- Generated normalized files go under outputs/main_four_tasks_round2_rule_validation_v0_5.
- Assistant draft rows are used as human final only because the user explicitly
  declared the correction overlay to be the Round2 human-final signal; rows not
  present in the overlay are marked with provenance.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


MANUAL40_PATH = Path(
    "outputs/main_four_tasks_manual_check_v0_2/"
    "main_four_tasks_manual_decisions_40_user_approved_round1.csv"
)
ROUND2_DRAFT_PATH = Path(
    "outputs/main_four_tasks_round2_small_dryrun_v0_4/"
    "round2_assistant_draft_decisions_80.csv"
)
ROUND2_EXPECTED_HUMAN_PATH = Path(
    "outputs/main_four_tasks_round2_small_dryrun_v0_4/"
    "round2_manual_decisions_80_user_approved.csv"
)
ROUND2_OVERLAY_PATH = Path(
    "outputs/main_four_tasks_round2_small_dryrun_v0_4/"
    "round2_user_feedback_correction_overlay.csv"
)
ROUND2_DIR = Path("outputs/main_four_tasks_round2_small_dryrun_v0_4")
OUTPUT_DIR = Path("outputs/main_four_tasks_round2_rule_validation_v0_5")
NORMALIZED_ROUND2_HUMAN_PATH = OUTPUT_DIR / (
    "round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv"
)

DOCS_DIR = Path("docs/phase1")


STANDARD_COLUMNS = {
    "sample_id": [
        "sample_id",
        "round2_review_id",
        "review_id",
        "row_id",
        "task_id",
        "original_task_id",
    ],
    "task_id": ["task_id", "original_task_id", "source_task_id"],
    "task_type": ["task_type", "final_task_bucket", "suggested_final_task_bucket"],
    "review_bucket": ["review_bucket", "mechanical_screening_bucket"],
    "source_group": ["source_group", "group"],
    "manual_final_decision": [
        "manual_final_decision",
        "final_decision",
        "final_cleaning_status",
        "human_final_decision",
    ],
    "semantic_alignment_check": [
        "semantic_alignment_check",
        "manual_semantic_alignment",
        "semantic_alignment_status",
        "semantic_alignment_manual_check",
    ],
    "leakage_check": [
        "leakage_check",
        "manual_leak_check",
        "leakage_manual_check",
        "leak_status",
    ],
    "candidate_validity_check": [
        "candidate_validity_check",
        "manual_candidate_gold_validity",
        "candidate_gold_validity",
    ],
    "task_type_check": [
        "task_type_check",
        "manual_task_type_check",
        "final_task_eligibility",
    ],
    "candidate_service_count": ["candidate_service_count"],
    "gold_service_count": ["gold_service_count"],
    "candidate_api_count": ["candidate_api_count"],
    "gold_api_count": ["gold_api_count"],
    "query_text": ["query_text", "query"],
    "human_notes": [
        "human_notes",
        "manual_notes",
        "manual_decision_reason",
        "final_decision_reason",
        "cross_check_notes",
    ],
}


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def write_csv(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def column_mapping(columns: Iterable[str]) -> Dict[str, Optional[str]]:
    actual = list(columns)
    lower_to_actual = {col.lower(): col for col in actual}
    mapping: Dict[str, Optional[str]] = {}
    for standard, candidates in STANDARD_COLUMNS.items():
        found = None
        for candidate in candidates:
            if candidate.lower() in lower_to_actual:
                found = lower_to_actual[candidate.lower()]
                break
        mapping[standard] = found
    return mapping


def value(row: Dict[str, str], mapping: Dict[str, Optional[str]], standard: str) -> str:
    actual = mapping.get(standard)
    if not actual:
        return ""
    return (row.get(actual) or "").strip()


def sample_id_for(row: Dict[str, str], mapping: Dict[str, Optional[str]]) -> str:
    sample = value(row, mapping, "sample_id")
    if sample:
        return sample
    task_id = value(row, mapping, "task_id")
    if task_id:
        return task_id
    raise ValueError("Could not resolve sample_id/task_id for row")


def null_summary(columns: Sequence[str], rows: Sequence[Dict[str, str]]) -> Dict[str, int]:
    return {
        col: sum(1 for row in rows if (row.get(col) or "").strip() == "")
        for col in columns
    }


def distribution(rows: Sequence[Dict[str, str]], column: str) -> Dict[str, int]:
    counts = Counter((row.get(column) or "").strip() or "<EMPTY>" for row in rows)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def normalize_final_decision(raw: str) -> str:
    text = (raw or "").strip()
    low = text.lower()
    if low in {"keep_for_cleaning_candidate", "keep", "clean_candidate", "clean_ready_candidate"}:
        return "keep_for_cleaning_candidate"
    if low in {"remove", "removed", "remove_api_leak", "invalid_candidate_or_gold"}:
        return "remove"
    if low in {"uncertain", "api_leak_uncertain", "service_leak_only", "composable_needs_review"}:
        return "uncertain"
    return text


def semantic_bucket(raw: str) -> str:
    low = (raw or "").strip().lower()
    if not low:
        return "other"
    if "mismatch" in low:
        return "mismatch"
    if "uncertain" in low or "ambiguous" in low:
        return "uncertain"
    if low in {"ok", "semantic_alignment_ok", "semantic_ok", "aligned"} or "alignment_ok" in low:
        return "ok"
    return "other"


def leakage_bucket(raw: str) -> str:
    low = (raw or "").strip().lower()
    if not low:
        return "other"
    if "api_leak_blocking" in low or low == "api_leak" or "strong_api_leak" in low:
        return "api_leak_blocking"
    if "service_leak_only" in low:
        return "service_leak_only"
    if "ambiguous" in low or "uncertain" in low:
        return "ambiguous"
    if low in {"no_blocking", "no_blocking_leak", "no_obvious_leak", "none"}:
        return "no_blocking"
    return "other"


def candidate_validity_bucket(raw: str) -> str:
    low = (raw or "").strip().lower()
    if not low:
        return "other"
    if low == "valid" or low.endswith("_valid"):
        return "valid"
    if "insufficient" in low or "choice_space" in low:
        return "insufficient_choice_space"
    if "invalid" in low:
        return "invalid"
    if "uncertain" in low or "ambiguous" in low:
        return "uncertain"
    return "other"


def task_type_check_bucket(raw: str) -> str:
    low = (raw or "").strip().lower()
    if not low:
        return "other"
    if "valid_multi_service" in low:
        return "valid_multi_service"
    if "valid_multi_api" in low:
        return "valid_multi_api"
    if "invalid" in low:
        return "invalid"
    if "uncertain" in low or "ambiguous" in low:
        return "uncertain"
    if low.startswith("valid"):
        return "valid"
    return "other"


def review_bucket(raw: str) -> str:
    low = (raw or "").strip()
    return low or "<EMPTY>"


def task_family(raw: str) -> str:
    low = (raw or "").strip().lower()
    if "multi_service" in low:
        return "multi_service"
    if "multi_api" in low:
        return "multi_api"
    if "single_service" in low:
        return "single_service"
    if "single_api" in low:
        return "single_api"
    return raw or "other"


def parse_count(row: Dict[str, str], direct: str, json_col: str) -> Optional[int]:
    raw = (row.get(direct) or "").strip()
    if raw:
        try:
            return int(float(raw))
        except ValueError:
            pass
    text = row.get(json_col) or ""
    if not text.strip():
        return None
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return len(data)
    except Exception:
        return None
    return None


def row_to_standard(row: Dict[str, str], mapping: Dict[str, Optional[str]]) -> Dict[str, str]:
    standard = {key: value(row, mapping, key) for key in STANDARD_COLUMNS.keys()}
    standard["sample_id"] = sample_id_for(row, mapping)
    standard["manual_final_decision_norm"] = normalize_final_decision(
        standard.get("manual_final_decision", "")
    )
    standard["semantic_alignment_bucket"] = semantic_bucket(
        standard.get("semantic_alignment_check", "")
    )
    standard["leakage_bucket"] = leakage_bucket(standard.get("leakage_check", ""))
    standard["candidate_validity_bucket"] = candidate_validity_bucket(
        standard.get("candidate_validity_check", "")
    )
    standard["task_type_check_bucket"] = task_type_check_bucket(
        standard.get("task_type_check", "")
    )
    standard["task_family"] = task_family(standard.get("task_type", ""))
    standard["review_bucket_norm"] = review_bucket(standard.get("review_bucket", ""))
    for count_col in [
        "candidate_service_count",
        "gold_service_count",
        "candidate_api_count",
        "gold_api_count",
    ]:
        if not standard.get(count_col):
            standard[count_col] = ""
    return standard


def load_standardized(path: Path) -> Tuple[List[str], List[Dict[str, str]], Dict[str, Optional[str]]]:
    columns, rows = read_csv(path)
    mapping = column_mapping(columns)
    standardized: List[Dict[str, str]] = []
    for row in rows:
        std = row_to_standard(row, mapping)
        std["_raw"] = row  # type: ignore[assignment]
        standardized.append(std)
    return columns, standardized, mapping


def build_round2_human_final_from_overlay() -> Tuple[Path, Dict[str, object]]:
    """Create a normalized 80-row human final file from draft + user overlay.

    This is only allowed because the user explicitly stated that the earlier
    correction overlay is the human final. It preserves original draft fields
    and marks provenance for every row.
    """
    if not ROUND2_DRAFT_PATH.exists():
        raise FileNotFoundError(f"Round2 assistant draft not found: {ROUND2_DRAFT_PATH}")
    if not ROUND2_OVERLAY_PATH.exists():
        raise FileNotFoundError(f"Round2 user feedback overlay not found: {ROUND2_OVERLAY_PATH}")

    draft_cols, draft_rows = read_csv(ROUND2_DRAFT_PATH)
    overlay_cols, overlay_rows = read_csv(ROUND2_OVERLAY_PATH)
    overlay_by_id = {
        (row.get("round2_review_id") or "").strip(): row
        for row in overlay_rows
        if (row.get("round2_review_id") or "").strip()
    }

    extra_cols = [
        "human_final_source",
        "human_final_overlay_applied",
        "user_feedback_category",
        "user_feedback_summary",
        "calibration_reason",
        "assistant_draft_manual_semantic_alignment",
        "assistant_draft_manual_leak_check",
        "assistant_draft_manual_candidate_gold_validity",
        "assistant_draft_manual_task_type_check",
        "assistant_draft_manual_final_decision",
        "human_final_generation_note",
    ]
    fieldnames = list(draft_cols)
    for col in extra_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    final_rows: List[Dict[str, str]] = []
    applied = 0
    for draft in draft_rows:
        row = dict(draft)
        review_id = (row.get("round2_review_id") or "").strip()
        overlay = overlay_by_id.get(review_id)
        row["assistant_draft_manual_semantic_alignment"] = row.get(
            "manual_semantic_alignment", ""
        )
        row["assistant_draft_manual_leak_check"] = row.get("manual_leak_check", "")
        row["assistant_draft_manual_candidate_gold_validity"] = row.get(
            "manual_candidate_gold_validity", ""
        )
        row["assistant_draft_manual_task_type_check"] = row.get("manual_task_type_check", "")
        row["assistant_draft_manual_final_decision"] = row.get("manual_final_decision", "")

        if overlay:
            applied += 1
            row["manual_semantic_alignment"] = overlay.get(
                "suggested_manual_semantic_alignment", ""
            )
            row["manual_leak_check"] = overlay.get("suggested_manual_leak_check", "")
            row["manual_candidate_gold_validity"] = overlay.get(
                "suggested_manual_candidate_gold_validity", ""
            )
            row["manual_task_type_check"] = overlay.get("suggested_manual_task_type_check", "")
            row["manual_final_decision"] = overlay.get("suggested_manual_final_decision", "")
            row["manual_decision_reason"] = overlay.get("calibration_reason", "")
            row["final_decision_reason"] = overlay.get("calibration_reason", "")
            row["user_feedback_category"] = overlay.get("user_feedback_category", "")
            row["user_feedback_summary"] = overlay.get("user_feedback_summary", "")
            row["calibration_reason"] = overlay.get("calibration_reason", "")
            row["human_final_source"] = "user_feedback_overlay"
            row["human_final_overlay_applied"] = "yes"
            row["human_final_generation_note"] = (
                "User explicitly stated the correction overlay is Round2 human final."
            )
        else:
            row["user_feedback_category"] = ""
            row["user_feedback_summary"] = ""
            row["calibration_reason"] = ""
            row["human_final_source"] = "assistant_draft_user_accepted_by_instruction"
            row["human_final_overlay_applied"] = "no"
            row["human_final_generation_note"] = (
                "No user correction overlay for this row; retained draft value under "
                "the user's explicit declaration that the overlay-based correction set "
                "is the human-final signal."
            )
        final_rows.append(row)

    ensure_dirs()
    write_csv(NORMALIZED_ROUND2_HUMAN_PATH, final_rows, fieldnames)
    summary = {
        "normalized_path": str(NORMALIZED_ROUND2_HUMAN_PATH),
        "draft_rows": len(draft_rows),
        "overlay_rows": len(overlay_rows),
        "overlay_applied_rows": applied,
        "retained_draft_rows": len(draft_rows) - applied,
        "user_declared_overlay_as_human_final": True,
    }
    return NORMALIZED_ROUND2_HUMAN_PATH, summary


def find_round2_human_final(allow_overlay: bool = True) -> Tuple[Optional[Path], Dict[str, object]]:
    if ROUND2_EXPECTED_HUMAN_PATH.exists():
        return ROUND2_EXPECTED_HUMAN_PATH, {
            "source": "expected_round2_human_final_file",
            "path": str(ROUND2_EXPECTED_HUMAN_PATH),
        }

    if NORMALIZED_ROUND2_HUMAN_PATH.exists():
        return NORMALIZED_ROUND2_HUMAN_PATH, {
            "source": "existing_normalized_from_user_overlay",
            "path": str(NORMALIZED_ROUND2_HUMAN_PATH),
        }

    keywords = ["round2", "manual", "decision", "80", "user", "approved", "final"]
    if ROUND2_DIR.exists():
        candidates = []
        for path in ROUND2_DIR.glob("*.csv"):
            name = path.name.lower()
            score = sum(1 for kw in keywords if kw in name)
            if score >= 5:
                candidates.append((score, path.stat().st_mtime, path))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][2], {
                "source": "fallback_filename_search",
                "path": str(candidates[0][2]),
                "score": candidates[0][0],
            }

    if allow_overlay and ROUND2_OVERLAY_PATH.exists():
        path, summary = build_round2_human_final_from_overlay()
        return path, {"source": "normalized_from_user_overlay", **summary}

    return None, {"source": "missing"}


def duplicate_ids(rows: Sequence[Dict[str, str]]) -> Dict[str, int]:
    counts = Counter(row.get("sample_id", "") for row in rows)
    return {key: count for key, count in counts.items() if key and count > 1}


def crosstab(
    rows: Sequence[Dict[str, str]],
    row_key: str,
    col_key: str,
    normalize_final: bool = False,
) -> Dict[str, Dict[str, int]]:
    table: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        r = row.get(row_key, "") or "<EMPTY>"
        c = row.get(col_key, "") or "<EMPTY>"
        if normalize_final and col_key == "manual_final_decision":
            c = normalize_final_decision(c)
        table[r][c] += 1
    return {r: dict(cols) for r, cols in sorted(table.items())}


def rate(numer: int, denom: int) -> float:
    if denom == 0:
        return 0.0
    return numer / denom


def pct(numer: int, denom: int) -> str:
    return f"{rate(numer, denom) * 100:.1f}%"


def rows_to_markdown_table(
    rows: Sequence[Dict[str, object]],
    columns: Sequence[str],
    max_rows: int = 10,
) -> List[str]:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in rows[:max_rows]:
        vals = []
        for col in columns:
            text = str(row.get(col, "")).replace("\n", " ").replace("|", "/")
            if len(text) > 160:
                text = text[:157] + "..."
            vals.append(text)
        lines.append("| " + " | ".join(vals) + " |")
    if not rows:
        lines.append("| " + " | ".join(["-" for _ in columns]) + " |")
    return lines


def fieldnames_union(rows: Sequence[Dict[str, object]]) -> List[str]:
    seen = []
    for row in rows:
        for key in row.keys():
            if key not in seen and key != "_raw":
                seen.append(key)
    return seen
