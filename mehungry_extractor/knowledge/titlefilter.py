"""Title-based input filter — the topical gate at the mouth of the pipeline.

Papers are screened by their **title** before anything is archived or built: only a paper
whose title names a nutrition/diet topic is allowed to proceed. This is applied at the single
network entry point (``ingest.ingest_pmid``), so an off-topic paper never enters the immutable
corpus and therefore never reaches any downstream stage.

Matching is **case-insensitive** and **whole-word, plus regular plurals**: a keyword must
appear as its own token (bounded by word boundaries), optionally with a trailing ``s``/``es``.
So ``Plant`` matches "plant" / "plants" / "plant-based" but not "transplantation" or "plantar",
and ``Diet`` matches "diet" / "diets" while "dietary" is caught by its own listed keyword.
"""

from __future__ import annotations

import re
from typing import Optional

# Whole-word, case-insensitive keywords a title must contain for the paper to be processed.
# Case/variant duplicates from the request collapse here (matching is case-insensitive).
TITLE_KEYWORDS: tuple[str, ...] = (
    "nutritional",
    "nutrition",
    "dietary",
    "diet",
    "food-based",
    "food",
    "meal",
    "body mass index",
    "metabolic",
    "lifestyle",
    "plant-based",
    "plant",
    "protein",
    "vegetarian",
)

# One alternation of the keywords, anchored by word boundaries and allowing a regular English
# plural (``s``/``es``) so "diets"/"foods"/"meals" match. Order is irrelevant to the boolean
# result, but longer phrases precede the shorter tokens they contain for readability.
_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in TITLE_KEYWORDS) + r")(?:es|s)?\b",
    re.IGNORECASE,
)


def title_matches(title: Optional[str]) -> bool:
    """True if *title* contains at least one topic keyword (case-insensitive, whole word)."""
    return bool(title and _PATTERN.search(title))


class TitleFiltered(Exception):
    """A paper was skipped because its title matched none of :data:`TITLE_KEYWORDS`.

    Carries the ``pmid`` and the offending ``title`` so callers can report the skip honestly —
    it is a deliberate topical exclusion, not an acquisition or extraction failure.
    """

    def __init__(self, pmid: str, title: Optional[str]):
        self.pmid = pmid
        self.title = title
        super().__init__(f"title does not match the topic filter: {title!r}")
