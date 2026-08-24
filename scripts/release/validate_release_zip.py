"""Validate release ZIP structure, manifest hashes, and core task identity."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from repack_release_zip import is_packaging_junk, sha256_file


def member_sha256(archive: zipfile.ZipFile, name: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def validate(path: Path, expected_sha256: str | None = None) -> dict[str, object]:
    archive_hash = sha256_file(path)
    if expected_sha256 and archive_hash != expected_sha256.casefold():
        raise ValueError(f"archive SHA-256 mismatch: {archive_hash}")

    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1:
            raise ValueError(f"expected exactly one archive root, found {sorted(roots)}")
        root = next(iter(roots))
        absolute_paths = sum(bool(name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)) for name in names)
        junk_entries = [name for name in names if is_packaging_junk(Path(name))]
        if bad_member or absolute_paths or junk_entries:
            raise ValueError(
                f"ZIP hygiene failure: bad={bad_member}, absolute={absolute_paths}, junk={junk_entries[:5]}"
            )

        manifest_name = f"{root}/OUTPUT_MANIFEST.csv"
        sums_name = f"{root}/SHA256SUMS.txt"
        with archive.open(manifest_name) as raw:
            manifest_rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))
        manifest_path_field = "relative_path" if manifest_rows and "relative_path" in manifest_rows[0] else "path"
        listed_names = {f"{root}/{row[manifest_path_field]}" for row in manifest_rows}
        actual_payload_names = set(names) - {manifest_name, sums_name}
        release_manifest_name = f"{root}/manifests/RELEASE_FILE_MANIFEST.csv"
        # Current builders exclude the secondary manifest from OUTPUT_MANIFEST
        # to avoid a self-hash.  Historical repacks may list the frozen earlier
        # manifest as an ordinary payload file.
        if release_manifest_name not in listed_names:
            actual_payload_names.discard(release_manifest_name)
        if listed_names != actual_payload_names:
            raise ValueError("internal manifest membership does not match ZIP payload")

        hash_failures: list[str] = []
        for row in manifest_rows:
            relative = row[manifest_path_field]
            name = f"{root}/{relative}"
            size, digest = member_sha256(archive, name)
            if size != int(row["size_bytes"]) or digest != row["sha256"]:
                hash_failures.append(relative)
        if hash_failures:
            raise ValueError(f"internal manifest hash failures: {hash_failures[:5]}")

        sums: dict[str, str] = {}
        for line in archive.read(sums_name).decode("utf-8").splitlines():
            digest, separator, relative = line.partition("  ")
            if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest) or not relative:
                raise ValueError(f"malformed SHA256SUMS entry: {line!r}")
            if relative in sums:
                raise ValueError(f"duplicate SHA256SUMS entry: {relative}")
            sums[relative] = digest
        all_non_sum_names = {
            name.split("/", 1)[1]
            for name in names
            if name != sums_name
        }
        accepted_sum_memberships = (
            all_non_sum_names,
            all_non_sum_names - {"OUTPUT_MANIFEST.csv"},
        )
        if not any(set(sums) == expected for expected in accepted_sum_memberships):
            raise ValueError("SHA256SUMS membership does not match ZIP payload")
        sum_failures: list[str] = []
        for relative, expected_digest in sums.items():
            _, actual_digest = member_sha256(archive, f"{root}/{relative}")
            if actual_digest != expected_digest:
                sum_failures.append(relative)
        if sum_failures:
            raise ValueError(f"SHA256SUMS hash failures: {sum_failures[:5]}")

        if release_manifest_name in names and release_manifest_name not in listed_names:
            with archive.open(release_manifest_name) as raw:
                release_manifest_rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))
            release_path_field = (
                "relative_path"
                if release_manifest_rows and "relative_path" in release_manifest_rows[0]
                else "path"
            )
            release_manifest_failures: list[str] = []
            for row in release_manifest_rows:
                relative = row[release_path_field]
                member_name = f"{root}/{relative}"
                if member_name not in names:
                    release_manifest_failures.append(relative)
                    continue
                size, digest = member_sha256(archive, member_name)
                if size != int(row["size_bytes"]) or digest != row["sha256"]:
                    release_manifest_failures.append(relative)
            if release_manifest_failures:
                raise ValueError(f"release file manifest failures: {release_manifest_failures[:5]}")

        validation = json.loads(archive.read(f"{root}/VALIDATION_SUMMARY.json").decode("utf-8"))
        embedded_tests = validation.get("tests")
        if (
            validation.get("status") != "PASS"
            or validation.get("errors") not in (None, [])
            or (isinstance(embedded_tests, dict) and not all(embedded_tests.values()))
        ):
            raise ValueError("embedded release validation is not a complete PASS")

        task_ids: list[str] = []
        task_files = [name for name in names if name.startswith(f"{root}/tasks/") and name.endswith(".csv")]
        for name in task_files:
            with archive.open(name) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                task_ids.extend(row["benchmark_task_id"] for row in reader)
        unique_ids = set(task_ids)
        expected_task_rows = int(validation.get("row_count", validation.get("core_rows", 0)))
        if not expected_task_rows and isinstance(embedded_tests, dict):
            for test_name, passed in embedded_tests.items():
                match = re.fullmatch(r"core_rows_(\d+)", str(test_name))
                if passed and match:
                    expected_task_rows = int(match.group(1))
                    break
        if not expected_task_rows:
            raise ValueError("embedded validation does not declare the task row count")
        if (
            len(task_files) != 6
            or len(task_ids) != expected_task_rows
            or len(unique_ids) != expected_task_rows
            or "" in unique_ids
        ):
            raise ValueError("core task count or ID uniqueness check failed")

    return {
        "status": "PASS",
        "zip": path.name,
        "sha256": archive_hash,
        "crc_pass": bad_member is None,
        "member_count": len(names),
        "manifest_payload_files": len(manifest_rows),
        "checksum_entries": len(sums),
        "junk_entries": 0,
        "task_files": len(task_files),
        "task_rows": len(task_ids),
        "unique_task_ids": len(unique_ids),
        "embedded_validation": validation["status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    print(json.dumps(validate(args.zip.resolve(), args.expected_sha256), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
