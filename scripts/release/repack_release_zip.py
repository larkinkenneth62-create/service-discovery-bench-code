"""Repack a validated ServiceDiscoveryBench release without runtime/cache files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[2]
CONTROL_FILES = {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"}
JUNK_DIRECTORY_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__MACOSX"}
JUNK_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_packaging_junk(relative: Path) -> bool:
    return (
        any(part in JUNK_DIRECTORY_NAMES for part in relative.parts)
        or relative.name in JUNK_FILE_NAMES
        or relative.name.startswith("._")
        or relative.suffix.casefold() in JUNK_SUFFIXES
    )


def manifest_bytes(rows: list[dict[str, str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=["path", "size_bytes", "sha256"], lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + text.getvalue().encode("utf-8")


def sums_bytes(rows: list[dict[str, str]]) -> bytes:
    return "".join(f"{row['sha256']}  {row['path']}\n" for row in rows).encode("utf-8")


def load_clean_manifest(package_root: Path) -> tuple[list[dict[str, str]], dict[str, Path]]:
    with (package_root / "OUTPUT_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))

    actual_files = {
        path.relative_to(package_root).as_posix(): path
        for path in package_root.rglob("*")
        if path.is_file()
        and path.name not in CONTROL_FILES
        and not is_packaging_junk(path.relative_to(package_root))
    }
    clean_rows = [
        row for row in source_rows
        if row["path"] in actual_files and not is_packaging_junk(Path(row["path"]))
    ]
    clean_rows.sort(key=lambda row: row["path"])

    listed = {row["path"] for row in clean_rows}
    if listed != set(actual_files):
        missing = sorted(set(actual_files) - listed)
        stale = sorted(listed - set(actual_files))
        raise ValueError(f"manifest/source mismatch: missing={missing[:5]}, stale={stale[:5]}")
    for row in clean_rows:
        if actual_files[row["path"]].stat().st_size != int(row["size_bytes"]):
            raise ValueError(f"size mismatch for {row['path']}")
    return clean_rows, actual_files


def repack(package_root: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.with_suffix(output.suffix + ".sha256.txt").exists() or output.with_suffix(output.suffix + ".crc.json").exists():
        raise FileExistsError(f"refusing to overwrite release output: {output}")
    if not package_root.is_dir() or not (package_root / "VALIDATION_SUMMARY.json").exists():
        raise FileNotFoundError(f"validated package root not found: {package_root}")
    validation = json.loads((package_root / "VALIDATION_SUMMARY.json").read_text(encoding="utf-8"))
    if validation.get("status") != "PASS":
        raise ValueError("source package validation is not PASS")

    rows, files = load_clean_manifest(package_root)
    manifest = manifest_bytes(rows)
    sums = sums_bytes(rows)
    archive_root = package_root.name
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for relative in sorted(files):
            archive.write(files[relative], f"{archive_root}/{relative}")
        archive.writestr(f"{archive_root}/OUTPUT_MANIFEST.csv", manifest)
        archive.writestr(f"{archive_root}/SHA256SUMS.txt", sums)

    with zipfile.ZipFile(output) as archive:
        bad_member = archive.testzip()
        names = archive.namelist()
    absolute_paths = sum(bool(name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)) for name in names)
    junk_entries = sum(is_packaging_junk(Path(name)) for name in names)
    archive_hash = sha256_file(output)
    result = {
        "zip": output.name,
        "sha256": archive_hash,
        "crc_pass": bad_member is None,
        "bad_member": bad_member,
        "absolute_member_paths": absolute_paths,
        "member_count": len(names),
        "junk_entries": junk_entries,
        "official_root": f"{archive_root}/",
        "source_validation_status": validation["status"],
    }
    output.with_suffix(output.suffix + ".sha256.txt").write_text(
        f"{archive_hash}  {output.name}\n", encoding="utf-8"
    )
    output.with_suffix(output.suffix + ".crc.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if bad_member is not None or absolute_paths or junk_entries:
        raise ValueError(f"rebuilt archive failed hygiene checks: {result}")
    return result


def parse_args() -> argparse.Namespace:
    state = json.loads((PROJECT / "CURRENT_PROJECT_STATE.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, default=PROJECT / state["dataset_root"])
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "outputs/runs/ServiceDiscoveryBench-v0.1.1-paper-dataset-clean.zip",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = repack(args.package_root.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
