from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


FORBIDDEN_SUFFIXES = {
    ".zip", ".7z", ".rar", ".tar", ".gz", ".gguf", ".safetensors",
    ".parquet", ".pdf", ".docx", ".pptx", ".xlsx", ".jsonl", ".csv",
}
MAX_BYTES = 5 * 1024 * 1024


def tracked_files(root: Path) -> list[Path]:
    try:
        output = subprocess.check_output(["git", "-C", str(root), "ls-files"], text=True, stderr=subprocess.DEVNULL)
        return [root / line for line in output.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def audit(root: Path, *, files: list[Path] | None = None) -> dict[str, Any]:
    paths = files if files is not None else tracked_files(root)
    findings: dict[str, list[str]] = {
        "forbidden_files": [], "secret_findings": [], "absolute_private_paths": [],
        "live_tunnel_urls": [], "instantiated_benchmark_rows": [], "symlinks": [], "large_files": [],
    }
    audit_relative = "scripts/publication/audit_public_repo.py"
    for path in paths:
        relative = path.relative_to(root).as_posix()
        lower = relative.lower()
        if path.is_symlink():
            findings["symlinks"].append(relative)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            findings["large_files"].append(relative)
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or Path(lower).name in {".env", ".env.local"}:
            findings["forbidden_files"].append(relative)
        text = _text(path)
        if not text or relative == audit_relative:
            continue
        secret_patterns = (
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"(?i)authorization\s*[:=]\s*bearer\s+(?!<|\[|\$\{|example)[A-Za-z0-9._-]{16,}",
            r"(?i)(?:api[_-]?key|token)\s*[:=]\s*['\"](?!<|\[|\$\{|example)[A-Za-z0-9._-]{20,}['\"]",
        )
        if any(re.search(pattern, text) for pattern in secret_patterns):
            findings["secret_findings"].append(relative)
        if re.search(r"(?i)(?:[A-Z]:[\\/]Users[\\/][^<\s\\/]+|/home/[^<\s/]+|/Users/[^<\s/]+)", text):
            findings["absolute_private_paths"].append(relative)
        tunnel_domain = "trycloudflare" + ".com"
        if re.search(r"https?://[^\s)\]>'\"]+" + re.escape(tunnel_domain), text, re.IGNORECASE):
            findings["live_tunnel_urls"].append(relative)
        synthetic = relative.startswith("tests/fixtures/synthetic_native_machine/")
        static_prompt = "/prompts/" in relative and "<" in text
        data_like = path.suffix.lower() in {".json", ".jsonl", ".md"}
        if data_like and not synthetic and not static_prompt and re.search(r"[\"']query(?:_text)?[\"']\s*:", text) and re.search(r"[\"']candidate_id[\"']\s*:", text):
            findings["instantiated_benchmark_rows"].append(relative)
    for name in findings:
        findings[name] = sorted(set(findings[name]))
    counts = {name: len(values) for name, values in findings.items()}
    status = "PASS" if all(value == 0 for value in counts.values()) else "FAIL"
    return {"publication_audit_status": status, "counts": counts, "findings": findings}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the sanitized ServiceDiscoveryBench public repository")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = audit(args.root.resolve())
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    raise SystemExit(0 if result["publication_audit_status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
