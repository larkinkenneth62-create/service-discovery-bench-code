from __future__ import annotations

import re
import unicodedata

from .normalize import normalize_text


DETECTOR_VERSION = "exact_visible_surface_v1"
GENERIC_COMMON_SURFACES = frozenset({
    "calculator", "command", "discount", "generate", "go", "hashtags", "id", "languages",
    "local", "me", "peers", "review", "revise", "translate", "translation", "trending", "view", "weather",
})


def is_generic_common_surface(surface: str) -> bool:
    """Return True for ordinary one-token overlaps that require human judgment."""
    return normalize_text(surface, casefold=True) in GENERIC_COMMON_SURFACES


def find_exact_surface(text: str, surface: str) -> list[dict]:
    text = unicodedata.normalize("NFKC", text or "")
    surface = unicodedata.normalize("NFKC", normalize_text(surface))
    if not surface:
        return []
    pieces = [re.escape(piece) for piece in surface.split()]
    expression = r"\s+".join(pieces)
    if surface[0].isalnum():
        expression = r"(?<!\w)" + expression
    if surface[-1].isalnum():
        expression += r"(?!\w)"
    return [
        {
            "matched_surface": match.group(0),
            "normalized_surface": normalize_text(surface, casefold=True),
            "start_offset": match.start(),
            "end_offset": match.end(),
            "match_type": "exact_surface",
            "detector_version": DETECTOR_VERSION,
        }
        for match in re.finditer(expression, text, flags=re.IGNORECASE)
    ]
