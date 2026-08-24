import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.leakage import find_exact_surface, is_generic_common_surface


class LeakageTest(unittest.TestCase):
    def test_exact_surface_has_offsets(self):
        hits = find_exact_surface("Use Weather Tool today", "weather tool")
        self.assertEqual((hits[0]["start_offset"], hits[0]["end_offset"]), (4, 16))

    def test_token_boundary_blocks_substring(self):
        self.assertEqual(find_exact_surface("researching", "search"), [])

    def test_endpoint_surface(self):
        self.assertTrue(find_exact_surface("Call /v1/weather now", "/v1/weather"))

    def test_generic_common_surface_is_routed_to_human(self):
        self.assertTrue(is_generic_common_surface("weather"))
        self.assertFalse(is_generic_common_surface("getInventory"))


if __name__ == "__main__":
    unittest.main()
