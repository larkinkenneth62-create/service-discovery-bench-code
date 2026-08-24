#!/usr/bin/env python
"""Run v0.4.1 validator regression tests and emit a machine-readable summary."""

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
    parser = argparse.ArgumentParser(description="Run source-QA validator v0.4.1 tests.")
    parser.add_argument(
        "--output-dir",
        default="outputs/validator_patch_v0_4_1",
        help="Directory for test summary and runner output.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    test_dir = root / "tests" / "validation"
    output_dir = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    suite = unittest.defaultTestLoader.discover(
        str(test_dir), pattern="test_source_qa_review_validator_v0_4_1.py"
    )
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    failing = [test.id() for test, _ in result.failures]
    failing.extend(test.id() for test, _ in result.errors)
    summary = {
        "generated_at": now_iso(),
        "test_directory": str(test_dir),
        "tests_run": result.testsRun,
        "tests_passed": result.testsRun - len(result.failures) - len(result.errors),
        "tests_failed": len(result.failures) + len(result.errors),
        "failing_test_names": failing,
        "successful": result.wasSuccessful(),
    }
    (output_dir / "validator_regression_test_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "validator_regression_test_output.txt").write_text(
        stream.getvalue(), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
