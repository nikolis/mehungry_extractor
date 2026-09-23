"""First-class evidence/provenance types.

The fundamental design requirement of this engine (spec §4):

    No normalized knowledge object may exist without provenance identifying the source
    document and, where possible, the exact source text span from which it was derived.

An :class:`EvidenceRef` is that provenance. In Phase 1 no knowledge objects are produced
yet, so ``EvidenceRef`` exists, is fully tested for source-text reconstruction, and is the
type later phases (observations, claims, entity mentions, funding, study characteristics)
will structurally require. When an exact character span is unavailable, the ref records the
highest-precision level that *is* available and marks it explicitly, so uncertainty in
provenance is never silently upgraded.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from . import EXTRACTOR_VERSION

if TYPE_CHECKING:  # avoid an import cycle; only needed for type hints
    from .canonical import Document, Sentence


class ProvenancePrecision(str, Enum):
    """How precisely a piece of evidence is located, best → coarsest (spec §4)."""

    EXACT_SPAN = "EXACT_SPAN"
    SENTENCE = "SENTENCE"
    PARAGRAPH = "PARAGRAPH"
    SECTION = "SECTION"
    DOCUMENT = "DOCUMENT"
    METADATA = "METADATA"


class EvidenceRef(BaseModel):
    """A pointer from a knowledge object back to its exact source location.

    ``start_char``/``end_char`` are absolute offsets into the owning document's canonical
    text (see :mod:`mehungry_extractor.knowledge.canonical` for the offset contract), so
    ``document.text[start_char:end_char] == quoted_text`` whenever the precision is
    ``EXACT_SPAN`` or ``SENTENCE``.
    """

    document_id: str
    section_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    quoted_text: Optional[str] = None
    precision: ProvenancePrecision = ProvenancePrecision.SENTENCE
    extraction_rule: str = Field(
        "canonical_ingest",
        description="Identifier of the rule/step that produced this evidence.",
    )
    extraction_rule_version: str = Field(
        "0.1.0", description="Version of the specific extraction rule."
    )
    extractor_version: str = Field(
        default=EXTRACTOR_VERSION, description="Version of the extractor code."
    )

    @classmethod
    def for_sentence(
        cls,
        document: "Document",
        sentence: "Sentence",
        *,
        precision: ProvenancePrecision = ProvenancePrecision.SENTENCE,
        extraction_rule: str = "canonical_ingest",
        extraction_rule_version: str = "0.1.0",
    ) -> "EvidenceRef":
        """Build a sentence-level evidence ref straight from a canonical sentence.

        Slices the document's canonical text at the sentence offsets rather than trusting
        the sentence's own ``text``, so the ref is self-consistent with the offsets by
        construction.
        """
        return cls(
            document_id=document.document_id,
            section_id=sentence.section_id,
            paragraph_id=sentence.paragraph_id,
            sentence_id=sentence.sentence_id,
            start_char=sentence.start_char,
            end_char=sentence.end_char,
            quoted_text=document.text[sentence.start_char : sentence.end_char],
            precision=precision,
            extraction_rule=extraction_rule,
            extraction_rule_version=extraction_rule_version,
        )
