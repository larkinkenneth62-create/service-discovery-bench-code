from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXCLUDED_PRIVATE_DATA_TESTS = {
    "test_budget_binding_for_smoke_is_exact",
    "test_pretty_safe_budget_binding_for_smoke_is_exact",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def iter_cases(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def main() -> int:
    loader = unittest.TestLoader()
    modules = [
        load_module("sdb_public_length_tests", HERE / "test_q1_length_adjudication_v1.py"),
        load_module("sdb_public_sse_tests", HERE / "test_sse_runner_contract_v1.py"),
    ]
    selected = unittest.TestSuite()
    skipped_private = []
    for module in modules:
        for case in iter_cases(loader.loadTestsFromModule(module)):
            method = case.id().rsplit(".", 1)[-1]
            if method in EXCLUDED_PRIVATE_DATA_TESTS:
                skipped_private.append(case.id())
            else:
                selected.addTest(case)
    print(f"public_code_only_tests={selected.countTestCases()}")
    print(f"excluded_private_data_integration_tests={len(skipped_private)}")
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
