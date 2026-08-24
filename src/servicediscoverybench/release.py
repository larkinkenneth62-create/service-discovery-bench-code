"""Release-gate helpers."""

from __future__ import annotations

import re


SOURCE_TERMS_STATUS_PATTERN = re.compile(
    r"(?m)^\s*release_terms_status\s*:\s*([A-Z][A-Z0-9_]*)\s*$"
)
SOURCE_TERMS_CLEARED = "CLEARED_FOR_BENCHMARK_RELEASE"


def source_terms_status(text: str) -> str | None:
    """Return one unambiguous release-terms status, or ``None``.

    A document with no status or with conflicting status declarations is not
    valid clearance evidence. Mentions inside prose, examples, or code spans do
    not count because the marker must occupy its own line.
    """

    statuses = SOURCE_TERMS_STATUS_PATTERN.findall(text)
    if len(statuses) != 1:
        return None
    return statuses[0]


def source_terms_are_cleared(text: str) -> bool:
    """Return whether the source-terms evidence explicitly clears release."""

    return source_terms_status(text) == SOURCE_TERMS_CLEARED
