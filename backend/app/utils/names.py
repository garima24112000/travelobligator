"""Script-independent name comparison (Section 203C.2B).

`comparable_name` reduces a place name to a form that can be compared for
equality in ANY script: NFKD, combining accents removed (Córdoba ==
Cordoba), casefolded, every non-letter/non-digit run collapsed to one
space. Letters of non-Latin scripts are kept, so two different names in
the same script never collapse to the same (empty) string.

It is for comparison only -- never used to change a stored or displayed
name -- and it does no transliteration or fuzzy matching: two names are
"the same" only when they are equal after this normalisation. An empty
result means "no comparable name" and must never be treated as a match.
"""

from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")


def _keep(ch: str) -> bool:
    # Letters and digits of any script, plus the dependent vowel/tone marks
    # that are part of a word in scripts that use them.
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def comparable_name(name: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    kept = "".join(ch if _keep(ch) else " " for ch in stripped.casefold())
    return _SPACES.sub(" ", kept).strip()


def same_name(a: str | None, b: str | None) -> bool:
    """Equal comparable names; False when either has no comparable name."""
    left = comparable_name(a)
    return bool(left) and left == comparable_name(b)
