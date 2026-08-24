#!/usr/bin/env python
"""Run v0.4.1 and v0.4.2 validator tests and write test_summary.json."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run source-QA validator v0.4.2 regression suite.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-dir", default="outputs/validator_patch_v0_4_2")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    output_dir = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests/validation"),
        pattern="test_source_qa_review_validator_v0_4*.py",
    )
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    failing = [test.id() for test, _ in result.failures]
    failing.extend(test.id() for test, _ in result.errors)
    summary = {
        "generated_at": now_iso(),
        "test_scope": "v0.4.1 regression plus v0.4.2 precedence/target/provenance/pack tests",
        "tests_run": result.testsRun,
        "tests_passed": result.testsRun - len(result.failures) - len(result.errors),
        "tests_failed": len(result.failures) + len(result.errors),
        "failing_test_names": failing,
        "successful": result.wasSuccessful(),
    }
    (output_dir / "test_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "test_output.txt").write_text(stream.getvalue(), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
