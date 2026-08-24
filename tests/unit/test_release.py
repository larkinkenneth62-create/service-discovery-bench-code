import unittest

from servicediscoverybench.release import (
    SOURCE_TERMS_CLEARED,
    source_terms_are_cleared,
    source_terms_status,
)


class SourceTermsReleaseGateTests(unittest.TestCase):
    def test_review_required_does_not_clear(self):
        text = "# Evidence\n\nrelease_terms_status: REVIEW_REQUIRED\n"
        self.assertEqual(source_terms_status(text), "REVIEW_REQUIRED")
        self.assertFalse(source_terms_are_cleared(text))

    def test_exact_standalone_clearance_clears(self):
        text = f"# Signed decision\n\nrelease_terms_status: {SOURCE_TERMS_CLEARED}\n"
        self.assertTrue(source_terms_are_cleared(text))

    def test_prose_mention_does_not_clear(self):
        text = "Do not write `release_terms_status: CLEARED_FOR_BENCHMARK_RELEASE` before approval."
        self.assertIsNone(source_terms_status(text))
        self.assertFalse(source_terms_are_cleared(text))

    def test_conflicting_markers_fail_closed(self):
        text = (
            "release_terms_status: REVIEW_REQUIRED\n"
            "release_terms_status: CLEARED_FOR_BENCHMARK_RELEASE\n"
        )
        self.assertIsNone(source_terms_status(text))
        self.assertFalse(source_terms_are_cleared(text))


if __name__ == "__main__":
    unittest.main()
