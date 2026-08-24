import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "10_build_release_docs.py"
SPEC = importlib.util.spec_from_file_location("build_release_docs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ReleasePackagingTests(unittest.TestCase):
    def test_public_logical_path_removes_external_absolute_prefix(self):
        value = str(Path(tempfile.gettempdir()) / "private" / "source.csv")
        self.assertEqual(MODULE.public_logical_path(value), "external_input/source.csv")

    def test_catalog_sanitizer_rewrites_top_level_and_nested_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog_dir = Path(temporary)
            absolute = str(Path(temporary) / "private" / "source.csv")
            row = {
                "source_path": absolute,
                "metadata_json": json.dumps({"catalog_source_path": absolute, "url": "/lookup"}),
            }
            for name in ("service_catalog.jsonl", "api_catalog.jsonl"):
                (catalog_dir / name).write_text(json.dumps(row) + "\n", encoding="utf-8")

            MODULE.sanitize_public_catalogs(catalog_dir)

            sanitized = json.loads((catalog_dir / "service_catalog.jsonl").read_text(encoding="utf-8"))
            metadata = json.loads(sanitized["metadata_json"])
            self.assertEqual(sanitized["source_path"], "external_input/source.csv")
            self.assertEqual(metadata["catalog_source_path"], "external_input/source.csv")
            self.assertEqual(metadata["url"], "/lookup")

    def test_private_path_scrubber_handles_raw_and_json_escaped_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            raw = str(MODULE.ROOT / "private" / "source.json")
            escaped = json.dumps(raw, ensure_ascii=False)[1:-1]
            artifact = release / "artifact.json"
            artifact.write_text(f'{{"raw":"{escaped}","note":"{raw}"}}', encoding="utf-8")

            MODULE.scrub_private_paths(release)

            text = artifact.read_text(encoding="utf-8")
            self.assertNotIn(str(MODULE.ROOT), text)
            self.assertNotIn(Path.home().name, text)
            self.assertIn("PROJECT_ROOT", text)


if __name__ == "__main__":
    unittest.main()
