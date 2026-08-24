from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

csv.field_size_limit(2_147_483_647)


def text(value: object) -> str:
    return str(value or "").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def iter_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(fieldnames or (list(rows[0]) if rows else []))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def append_once(path: Path, marker: str, content: str) -> bool:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in original:
        return False
    separator = "" if not original or original.endswith("\n") else "\n"
    path.write_text(original + separator + content.rstrip() + "\n", encoding="utf-8")
    return True


def json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    parsed = json.loads(text(value) or "[]")
    if not isinstance(parsed, list):
        raise ValueError("expected JSON list")
    return [str(item) for item in parsed]


def inventory(root: Path, *, exclude_relative: set[str] | None = None) -> list[dict[str, object]]:
    excluded = {value.replace("\\", "/") for value in (exclude_relative or set())}
    rows: list[dict[str, object]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        rows.append({"relative_path": relative, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def tree_hash(root: Path, *, exclude_relative: set[str] | None = None) -> str:
    digest = hashlib.sha256()
    for row in inventory(root, exclude_relative=exclude_relative):
        digest.update(f"{row['relative_path']}\0{row['size_bytes']}\0{row['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()
