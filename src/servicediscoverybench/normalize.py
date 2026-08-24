from __future__ import annotations

import re
import unicodedata


NORMALIZATION_VERSION = "nfkc_ws_v1"


def normalize_text(value: object, *, casefold: bool = False) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold() if casefold else text
