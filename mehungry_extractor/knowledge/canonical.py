"""Canonical document model and its assembly from parsed source (spec §3).

**The offset contract (documented here once, enforced by tests):**

Every :class:`Document` carries a single canonical ``text`` string. Every
``Section``/``Paragraph``/``Sentence`` records absolute ``start_char``/``end_char`` offsets
into that string such that::

    document.text[obj.start_char:obj.end_char] == obj.text

This reconstruction invariant is what all provenance relies on: an
:class:`~mehungry_extractor.knowledge.provenance.EvidenceRef` stores offsets, and slicing
the document text at those offsets must reproduce the quoted source exactly.

Assembly: paragraph raw text is whitespace-normalized, empty paragraphs/sections are
dropped, and the kept paragraphs are concatenated (separated by a blank line) into the
canonical text. Section spans cover their paragraphs; sentence spans come from the
deterministic segmenter, rebased to absolute offsets. The original ``raw_text`` is retained
on each paragraph — the canonical text is *added*, the source is never lost.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel

from . import PIPELINE_VERSION
from .ids import document_id, paragraph_id, section_id, sentence_id
from .segment import active_segmenter, segment

_PARAGRAPH_SEP = "\n\n"
_WS_RE = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip. Deterministic."""
    return _WS_RE.sub(" ", text).strip()


@dataclass
class ParsedSection:
    """Structural input to the canonical builder, produced by ``jats``/``pubmed``."""

    title: Optional[str]
    paragraphs: list[str] = field(default_factory=list)


# --- canonical output models -------------------------------------------------------


class Sentence(BaseModel):
    sentence_id: str
    section_id: str
    paragraph_id: str
    start_char: int
    end_char: int
    text: str


class Paragraph(BaseModel):
    paragraph_id: str
    section_id: str
    start_char: int
    end_char: int
    text: str  # normalized; equals the canonical-text slice
    raw_text: str  # original source text, never discarded
    sentences: list[Sentence]


class Section(BaseModel):
    section_id: str
    index: int
    title: Optional[str]
    start_char: int
    end_char: int
    text: str  # equals the canonical-text slice spanning this section's paragraphs
    paragraphs: list[Paragraph]


class DocumentMetadata(BaseModel):
    """Bibliographic metadata embedded in ``document.json``.

    Deliberately excludes volatile acquisition fields (retrieval timestamp, source URL):
    those live only in the corpus ``metadata.json`` so that ``document.json`` stays a pure
    function of source content + pipeline version and remains byte-reproducible.
    """

    pmid: str
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    journal: Optional[str] = None
    publication_date: Optional[str] = None
    authors: list[str] = []
    publication_types: list[str] = []
    source_type: str = "none"  # open_access | abstract | none


class Document(BaseModel):
    document_id: str
    source_type: str
    pipeline_version: str
    text: str  # the canonical text; all offsets index into this string
    sections: list[Section]
    metadata: DocumentMetadata
    checksums: dict[str, str] = {}  # raw-file name -> sha256, for the reverse trace
    # Which segmenter produced the sentence boundaries ("scispacy-parser" model path, or the
    # "pysbd" rule floor). Recorded so a model-derived segmentation is never silently taken for
    # the floor. Defaults to the rule id for backward-compatible deserialization of older docs.
    segmenter: str = "pysbd"

    @property
    def pmid(self) -> str:
        return self.metadata.pmid

    @property
    def pmcid(self) -> Optional[str]:
        return self.metadata.pmcid

    @property
    def doi(self) -> Optional[str]:
        return self.metadata.doi

    def iter_sentences(self):
        for sec in self.sections:
            for par in sec.paragraphs:
                yield from par.sentences


def build_document(
    metadata: DocumentMetadata,
    sections: list[ParsedSection],
    checksums: Optional[dict[str, str]] = None,
    *,
    use_model: bool = True,
) -> Document:
    """Assemble a canonical :class:`Document` obeying the offset contract.

    ``use_model`` selects the sentence segmenter: ``True`` (default) uses the scispaCy parser's
    boundaries (falling back to the ``pysbd`` rule floor if the model is unavailable); ``False``
    always uses the floor. The chosen segmenter is recorded on ``Document.segmenter``.
    """
    doc_id = document_id(metadata.pmid)

    parts: list[str] = []
    cursor = 0
    sections_out: list[Section] = []
    kept_sec = 0

    for psec in sections:
        kept = [(normalize_ws(raw), raw) for raw in psec.paragraphs]
        kept = [(norm, raw) for (norm, raw) in kept if norm]
        if not kept:
            continue

        sec_id = section_id(doc_id, kept_sec)
        paragraphs_out: list[Paragraph] = []
        sec_start: Optional[int] = None
        sec_end = 0

        for kept_par, (norm, raw) in enumerate(kept):
            if cursor > 0:
                parts.append(_PARAGRAPH_SEP)
                cursor += len(_PARAGRAPH_SEP)
            p_start = cursor
            parts.append(norm)
            cursor += len(norm)
            p_end = cursor
            if sec_start is None:
                sec_start = p_start
            sec_end = p_end

            par_id = paragraph_id(sec_id, kept_par)
            sentences = [
                Sentence(
                    sentence_id=sentence_id(par_id, k),
                    section_id=sec_id,
                    paragraph_id=par_id,
                    start_char=p_start + rel_start,
                    end_char=p_start + rel_end,
                    text=stext,
                )
                for k, (rel_start, rel_end, stext) in enumerate(segment(norm, use_model=use_model))
            ]
            paragraphs_out.append(
                Paragraph(
                    paragraph_id=par_id,
                    section_id=sec_id,
                    start_char=p_start,
                    end_char=p_end,
                    text=norm,
                    raw_text=raw,
                    sentences=sentences,
                )
            )

        sections_out.append(
            Section(
                section_id=sec_id,
                index=kept_sec,
                title=psec.title,
                start_char=sec_start or 0,
                end_char=sec_end,
                text="",  # filled once canonical text is known
                paragraphs=paragraphs_out,
            )
        )
        kept_sec += 1

    canonical_text = "".join(parts)
    for sec in sections_out:
        sec.text = canonical_text[sec.start_char : sec.end_char]

    return Document(
        document_id=doc_id,
        source_type=metadata.source_type,
        pipeline_version=PIPELINE_VERSION,
        text=canonical_text,
        sections=sections_out,
        metadata=metadata,
        checksums=checksums or {},
        segmenter=active_segmenter(use_model),
    )


def to_canonical_json(document: Document) -> str:
    """Deterministic JSON serialization for ``canonical/document.json``.

    Sorted keys + fixed indent + trailing newline, so identical input yields byte-identical
    output (the reproducibility guarantee, spec §12).
    """
    payload = document.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def document_from_json(data: str | dict) -> Document:
    if isinstance(data, str):
        data = json.loads(data)
    return Document.model_validate(data)
