"""The :class:`OpenObservation` — a relation-bearing span, deliberately *without* structure (Phase 11).

Despite the name, an ``OpenObservation`` holds **no** subject, predicate, or object — that absence
*is* the design. It is a stretch of the canonical text a model flagged as *asserting some
relationship*, recorded **verbatim and whole**, anchored to its exact source offsets, and nothing
more. It is the deliberate opposite of a ``(subject, predicate, object)`` triple: a triple bakes in
binarity, a single connecting predicate, and an argument/predicate segmentation — all *schema
decided in advance*. Dietary/biomedical relationships are routinely n-ary and conditional (dose,
population, disease state, duration), so committing to a triple would throw exactly that richness
away. This record commits to nothing; it just surfaces the natural-language relationship for a human
to read, most-confident first.

Because the unit of record *is* a sentence/clause of the canonical text, provenance is the simplest
it can be: ``text == document.text[start_char:end_char]``, so the single :class:`~.provenance.EvidenceRef`
is ``EXACT_SPAN`` **by construction** (the offset contract of Concept 2 holds for free — no argument
re-anchoring, no generative-rewrite problem). The detector is still a **model**, hence
non-deterministic, so the record stores *how it was produced* — the detector name + version and the
model's confidence score — auditable though not reproducible.

These records live in their own table and are served over their own endpoints; they are **never**
read by :mod:`.claims`/:mod:`.synthesis`. A trusted :class:`~.observations.Observation` (Concept 9)
requires a controlled ``predicate``, a ``rule_id``/``rule_version`` and a ``polarity``/``certainty``
— everything a claim key and synthesis grouping depend on. An open observation has none of those, by
design, so it cannot leak into the trusted pipeline.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from pydantic import BaseModel

from .provenance import EvidenceRef


def open_observation_id(document_id: str, start_char: int, end_char: int, detector_name: str) -> str:
    """Deterministic id for one flagged span: a hash of ``(document, span, detector)``.

    The detector name is part of the identity so the id is attributable to the exact detector that
    produced it (the *same* span flagged by a different detector is a distinct id), while re-running
    the same detector over the same span reproduces the id — keeping persistence idempotent.
    """
    key = f"{document_id}|{start_char}|{end_char}|{detector_name}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"openobs_{digest}"


class OpenObservation(BaseModel):
    """One relation-bearing span flagged by a detector, recorded verbatim with exact provenance.

    ``text`` is the exact source slice (``document.text[start_char:end_char]``); ``sentence_id`` is
    the owning sentence and ``clause_index`` is set only when the span was localized to a clause
    (Concept 15) rather than the whole sentence. There is intentionally **no** subject/predicate/
    object field: none has been decided.
    """

    open_observation_id: str
    document_id: str
    sentence_id: str
    clause_index: Optional[int] = None
    start_char: int
    end_char: int
    text: str
    detector_name: str
    detector_version: str
    score: float
    evidence_refs: list[EvidenceRef]
