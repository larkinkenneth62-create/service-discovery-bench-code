#!/usr/bin/env python3
"""Flag likely truncated or identifier-damaging Chinese reviewer aids."""

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "ServiceDiscoveryBench-v0.1-candidate" / "qa"
translations = json.loads((QA / "query_translations_zh.json").read_text(encoding="utf-8"))
rows = []
for path in sorted((QA / "blind_packs").rglob("*_blind_pack.csv")):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows.extend(csv.DictReader(handle))

seen = set()
flagged = []
for row in rows:
    source = row["query_text"].strip()
    target = translations[row["benchmark_task_id"]].strip()
    if source in seen:
        continue
    seen.add(source)
    tokens = re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9._:/-]{5,}", source)
    tokens = [token.rstrip(".") for token in tokens]
    tokens = [
        token
        for token in tokens
        if any(char.isdigit() for char in token) or "." in token
    ]
    tokens = [
        token for token in tokens
        if not re.fullmatch(r"\d+-(?:day|hour|mile|meter|kilometer|character)", token, re.I)
    ]
    missing = [token for token in tokens if token not in target]
    reasons = []
    ratio = len(target) / max(1, len(source))
    if ratio < 0.12:
        reasons.append(f"length_ratio={ratio:.2f}")
    if target.rstrip().endswith((",", "，", ":", "：")):
        reasons.append("trailing_delimiter")
    if missing:
        reasons.append("missing_identifiers=" + "|".join(missing))
    if reasons:
        flagged.append({
            "query_text": source,
            "query_translation_zh": target,
            "reasons": ";".join(reasons),
        })

report = QA / "reports" / "query_translation_zh_quality_flags.csv"
with report.open("w", encoding="utf-8-sig", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["query_text", "query_translation_zh", "reasons"])
    writer.writeheader()
    writer.writerows(flagged)
print(json.dumps({"unique_queries": len(seen), "flagged": len(flagged), "report": str(report)}, ensure_ascii=False))
