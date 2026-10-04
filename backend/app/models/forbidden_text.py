"""Whole-word matching for forbidden claim patterns (Section 3B).

Every contract model that refuses claim-shaped text ("rating", "price",
"reservation", "book now", ...) keeps its own pattern list. This module is
the one way those lists are MATCHED: as a whole word or phrase (optionally
plural), never as a raw substring.

A raw substring match treats ordinary words -- and real place names -- as
factual claims: "reservation" inside "Preservation Hall", "rating" inside
"celebrating", "price" inside "priceless", "cheap" inside "Cheapside". The
claims themselves ("reservation", "reservations", "Reservation Center",
"highly rated", ...) are blocked exactly as before.

A boundary is only required on a side of the pattern that is a letter or a
digit, so a symbol pattern such as "$" still matches inside "$20".
"""

from __future__ import annotations

import re
from typing import Iterable

CompiledForbiddenPatterns = tuple[tuple[str, re.Pattern[str]], ...]


def compile_forbidden_patterns(patterns: Iterable[str]) -> CompiledForbiddenPatterns:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        lowered = pattern.lower()
        before = r"(?<![a-z0-9])" if lowered[:1].isalnum() else ""
        after = r"(?:s|es)?(?![a-z0-9])" if lowered[-1:].isalnum() else ""
        compiled.append((pattern, re.compile(before + re.escape(lowered) + after)))
    return tuple(compiled)


def find_forbidden_pattern(text: str, compiled: CompiledForbiddenPatterns) -> str | None:
    """The first forbidden pattern present in `text` as a whole word or
    phrase (case-insensitive), or None."""
    lowered = text.lower()
    for pattern, regex in compiled:
        if regex.search(lowered):
            return pattern
    return None
