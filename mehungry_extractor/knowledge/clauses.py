"""Deterministic clause / contrast segmentation of a sentence (Phase 7).

A single sentence can assert *opposite valence under different conditions* —
*"fiber is beneficial during remission **but** may aggravate symptoms in active flare"*. Relation
matching over the whole sentence collapses or mis-binds those two scoped relations, and a cue or
negation from one clause leaks into the other. :func:`segment` breaks a sentence into ordered
:class:`Clause` sub-spans on coordinating/contrastive markers (``but``, ``whereas``, ``however``,
``while``, ``although``, ``;``) so :mod:`.observations` can match relations **within a clause**,
bounding both the connecting-text window and the negation region to the clause.

Pure, versioned, cue-based — no parser model. Each clause records the marker that opened it (for
audit) and whether it is *contrastive* to the previous clause. The offset contract holds: every
clause is a sub-span of its sentence, whose span is a sub-span of the document ``text``. A sentence
with no marker is a single clause (nothing is dropped for failing to segment).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from . import RULESET_VERSION

if TYPE_CHECKING:
    from .canonical import Document, Sentence

CLAUSE_RULESET = "clause_segmentation"

# Marker → whether a clause it opens is *contrastive* to the preceding clause. The word markers
# are matched word-bounded; ``;`` is a bare coordinating punctuation split (not contrastive).
# ``however``/``whereas``/``although``/``but`` reverse the assertion's direction; ``while`` is
# treated as contrastive because in this corpus it overwhelmingly introduces a contrast
# ("beneficial while in remission, harmful while flaring"); ``;`` merely coordinates.
_CONTRASTIVE: dict[str, bool] = {
    "but": True,
    "whereas": True,
    "however": True,
    "although": True,
    "while": True,
    ";": False,
}

# One ordered scan for every marker occurrence. Word markers need word boundaries so "buttress"
# or "erstwhile" never match; ``;`` is literal punctuation.
_MARKER_RE = re.compile(
    r"\b(?:but|whereas|however|although|while)\b|;",
    re.IGNORECASE,
)


class Clause(BaseModel):
    """One ordered sub-span of a sentence, split on a coordinating/contrastive marker.

    ``start_char``/``end_char`` are absolute offsets into the document ``text`` and obey the
    offset contract (``document.text[start_char:end_char] == text``). ``marker`` is the lexical
    cue that opened this clause (``None`` for the sentence's leading clause); ``contrastive`` is
    ``True`` only when that marker reverses the assertion relative to the previous clause.
    """

    clause_id: str
    sentence_id: str
    index: int
    start_char: int
    end_char: int
    text: str
    marker: Optional[str] = None
    contrastive: bool = False


def _clause_id(sentence_id: str, index: int) -> str:
    return f"{sentence_id}_c{index:02d}"


def segment(document: "Document", sentence: "Sentence") -> list[Clause]:
    """Split ``sentence`` into ordered :class:`Clause` sub-spans. Deterministic.

    Markers are located in sentence order; the text between consecutive markers is one clause
    (the marker itself is excluded from the clause span, so cue matching never sees it). Spans are
    trimmed of surrounding whitespace so provenance is tight; a clause that trims to empty (two
    adjacent markers, or a leading marker) is skipped — its marker simply does not open a clause,
    and the next real clause keeps its own preceding marker. A sentence with no marker yields a
    single clause spanning the whole sentence.
    """
    base = sentence.start_char
    text = sentence.text

    # (rel_start, rel_end, marker_that_precedes_this_segment)
    segments: list[tuple[int, int, Optional[str]]] = []
    cursor = 0
    prev_marker: Optional[str] = None
    for m in _MARKER_RE.finditer(text):
        segments.append((cursor, m.start(), prev_marker))
        prev_marker = m.group().lower()
        cursor = m.end()
    segments.append((cursor, len(text), prev_marker))

    clauses: list[Clause] = []
    for rel_start, rel_end, marker in segments:
        # Trim surrounding whitespace / stray punctuation-adjacent spaces to a tight span.
        start, end = rel_start, rel_end
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start:
            continue  # empty segment — carries no content; its marker opens nothing

        index = len(clauses)
        contrastive = bool(clauses) and marker is not None and _CONTRASTIVE.get(marker, False)
        clauses.append(
            Clause(
                clause_id=_clause_id(sentence.sentence_id, index),
                sentence_id=sentence.sentence_id,
                index=index,
                start_char=base + start,
                end_char=base + end,
                text=text[start:end],
                marker=marker,
                contrastive=contrastive,
            )
        )

    return clauses


def registry() -> list[dict]:
    """The clause-segmentation ruleset, for the ``extraction_rules`` registry (audit)."""
    return [
        {
            "rule_id": CLAUSE_RULESET,
            "version": RULESET_VERSION,
            "predicate": None,
            "description": "clause/contrast segmentation on coordinating markers (but/whereas/however/while/although/;)",
        }
    ]
