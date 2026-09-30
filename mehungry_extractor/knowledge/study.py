"""Deterministic study-metadata extraction: design, year, sample size, follow-up, country.

Facts, not judgements (spec §9). Authoritative structured metadata is preferred over text
mining and the origin is always recorded in ``classification_source`` so it is auditable:

* **study_design** — mapped from PubMed ``publication_types`` first (``pubmed_publication_type``,
  ``METADATA`` precision); only if that yields nothing do deterministic text rules run
  (``text_rule``, ``EXACT_SPAN``); otherwise ``unknown`` — never a guess.
* **publication_year** — parsed from the metadata publication date (``metadata`` precision).
* **sample_size / follow_up / country** — deterministic regex/dictionary rules over the
  canonical text, each with an ``EXACT_SPAN``/``SENTENCE`` evidence ref.

Absent information stays absent (or ``unknown``); it is never inferred.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from . import RULESET_VERSION
from .provenance import EvidenceRef, ProvenancePrecision
from .vocab import COUNTRIES_VERSION, load_countries

if TYPE_CHECKING:
    from .canonical import Document

RULE_VERSION = RULESET_VERSION

# spec §9 design categories.
DESIGN_UNKNOWN = "unknown"

# PubMed publication type (casefolded) → design category, most authoritative first when several
# apply (the ranking below decides ties).
_PUBTYPE_DESIGN: dict[str, str] = {
    "systematic review": "systematic_review",
    "meta-analysis": "meta_analysis",
    "randomized controlled trial": "randomized_controlled_trial",
    "controlled clinical trial": "controlled_trial",
    "clinical trial": "controlled_trial",
    "observational study": "cohort",
    "case reports": "case_report",
    "case report": "case_report",
    "comparative study": "controlled_trial",
    "practice guideline": "guideline",
    "guideline": "guideline",
    "review": "review",
    "editorial": "editorial",
    "letter": "letter",
}

# Higher rank wins when a paper carries several mappable publication types.
_DESIGN_RANK: dict[str, int] = {
    "systematic_review": 100,
    "meta_analysis": 95,
    "randomized_controlled_trial": 90,
    "controlled_trial": 70,
    "cohort": 60,
    "case_control": 55,
    "cross_sectional": 50,
    "case_series": 40,
    "case_report": 35,
    "guideline": 30,
    "review": 20,
    "editorial": 10,
    "letter": 5,
    "animal": 15,
    "in_vitro": 15,
}

# Deterministic text-rule design cues (fallback only).
_TEXT_DESIGN: tuple[tuple[str, str], ...] = (
    (r"\bsystematic review\b", "systematic_review"),
    (r"\bmeta-?analys[ie]s\b", "meta_analysis"),
    (r"\brandomi[sz]ed controlled trial\b|\brct\b", "randomized_controlled_trial"),
    (r"\bcase-control study\b", "case_control"),
    (r"\bcross-sectional\b", "cross_sectional"),
    (r"\bcohort study\b|\bprospective cohort\b|\bretrospective cohort\b", "cohort"),
    (r"\bcase series\b", "case_series"),
    (r"\bcase report\b", "case_report"),
    (r"\bin vitro\b", "in_vitro"),
    (r"\banimal (?:study|model)\b|\bin vivo\b|\bmurine\b|\bmouse model\b", "animal"),
)
_TEXT_DESIGN_RE = [(re.compile(p, re.IGNORECASE), d) for p, d in _TEXT_DESIGN]

_SAMPLE_SIZE_RE = re.compile(
    r"\b(?:n\s*=\s*|enrolled|recruited|included|randomi[sz]ed)\s*([0-9][0-9,]{1,6})\b"
    r"|\b([0-9][0-9,]{2,6})\s+(?:participants|patients|subjects|individuals|women|men|adults|children)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_RE = re.compile(
    r"\bfollow(?:ed)?[-\s]?up\b[^.]{0,40}?"
    r"\b((?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:years?|months?|weeks?|days?))\b",
    re.IGNORECASE,
)


class StudyCharacteristic(BaseModel):
    """One extracted study fact with its origin and provenance."""

    document_id: str
    field: str  # study_design | publication_year | sample_size | follow_up | country
    value: str
    classification_source: str  # pubmed_publication_type | text_rule | metadata
    rule_id: str
    evidence_ref: EvidenceRef


def _design_from_pubtypes(pub_types: list[str]) -> Optional[tuple[str, str]]:
    """Return ``(design, matched_publication_type)`` for the highest-ranked mappable type."""
    best: Optional[tuple[str, str]] = None
    best_rank = -1
    for pt in pub_types:
        design = _PUBTYPE_DESIGN.get(pt.strip().casefold())
        if design is None:
            continue
        rank = _DESIGN_RANK.get(design, 0)
        if rank > best_rank:
            best_rank = rank
            best = (design, pt)
    return best


def _design_from_text(document: "Document") -> Optional[tuple[str, int, int]]:
    """Return ``(design, start_char, end_char)`` for the first text design cue found."""
    text = document.text
    for regex, design in _TEXT_DESIGN_RE:
        m = regex.search(text)
        if m:
            return design, m.start(), m.end()
    return None


def _match_span(document: "Document", regex: re.Pattern) -> Optional[tuple[str, int, int]]:
    m = regex.search(document.text)
    if not m:
        return None
    # Prefer the captured group (the value); fall back to the whole match.
    for gi in range(1, (m.lastindex or 0) + 1):
        if m.group(gi):
            return m.group(gi), m.start(gi), m.end(gi)
    return m.group(0), m.start(), m.end()


def _country(document: "Document", affiliations: list[str]) -> Optional[tuple[str, str, Optional[tuple[int, int]]]]:
    """Find a country from the canonical text (with offsets) or, failing that, affiliations."""
    haystacks: list[tuple[str, bool]] = [(document.text, True)]
    haystacks.extend((a, False) for a in affiliations)
    for country in load_countries():
        for surface in country["surface_forms"]:
            pat = re.compile(rf"(?<!\w){re.escape(surface)}(?!\w)", re.IGNORECASE)
            for text, in_canonical in haystacks:
                m = pat.search(text)
                if m:
                    span = (m.start(), m.end()) if in_canonical else None
                    return country["canonical_name"], surface, span
    return None


def classify(document: "Document", *, affiliations: Optional[list[str]] = None) -> list[StudyCharacteristic]:
    """Extract study characteristics for a canonical document. Deterministic, offline."""
    affiliations = affiliations or []
    out: list[StudyCharacteristic] = []
    doc_id = document.document_id
    meta = document.metadata

    # --- study_design: publication types first (authoritative), then text, else unknown ---
    pub_match = _design_from_pubtypes(meta.publication_types or [])
    if pub_match is not None:
        design, matched_pt = pub_match
        out.append(
            StudyCharacteristic(
                document_id=doc_id,
                field="study_design",
                value=design,
                classification_source="pubmed_publication_type",
                rule_id="study_design_pubtype",
                evidence_ref=EvidenceRef(
                    document_id=doc_id,
                    quoted_text=matched_pt,
                    precision=ProvenancePrecision.METADATA,
                    extraction_rule="study_design_pubtype",
                    extraction_rule_version=RULE_VERSION,
                ),
            )
        )
    else:
        text_match = _design_from_text(document)
        if text_match is not None:
            design, start, end = text_match
            out.append(
                StudyCharacteristic(
                    document_id=doc_id,
                    field="study_design",
                    value=design,
                    classification_source="text_rule",
                    rule_id="study_design_text",
                    evidence_ref=EvidenceRef.for_span(
                        document, start, end,
                        extraction_rule="study_design_text",
                        extraction_rule_version=RULE_VERSION,
                    ),
                )
            )
        else:
            out.append(
                StudyCharacteristic(
                    document_id=doc_id,
                    field="study_design",
                    value=DESIGN_UNKNOWN,
                    classification_source="text_rule",
                    rule_id="study_design_text",
                    evidence_ref=EvidenceRef(
                        document_id=doc_id,
                        precision=ProvenancePrecision.DOCUMENT,
                        extraction_rule="study_design_text",
                        extraction_rule_version=RULE_VERSION,
                    ),
                )
            )

    # --- publication_year from metadata date ---
    if meta.publication_date:
        ym = re.match(r"\s*(\d{4})", meta.publication_date)
        if ym:
            out.append(
                StudyCharacteristic(
                    document_id=doc_id,
                    field="publication_year",
                    value=ym.group(1),
                    classification_source="metadata",
                    rule_id="publication_year_metadata",
                    evidence_ref=EvidenceRef(
                        document_id=doc_id,
                        quoted_text=meta.publication_date,
                        precision=ProvenancePrecision.METADATA,
                        extraction_rule="publication_year_metadata",
                        extraction_rule_version=RULE_VERSION,
                    ),
                )
            )

    # --- sample_size (text) ---
    ss = _match_span(document, _SAMPLE_SIZE_RE)
    if ss is not None:
        value, start, end = ss
        out.append(
            StudyCharacteristic(
                document_id=doc_id,
                field="sample_size",
                value=value.replace(",", ""),
                classification_source="text_rule",
                rule_id="sample_size_text",
                evidence_ref=EvidenceRef.for_span(
                    document, start, end,
                    extraction_rule="sample_size_text",
                    extraction_rule_version=RULE_VERSION,
                ),
            )
        )

    # --- follow_up (text) ---
    fu = _match_span(document, _FOLLOW_UP_RE)
    if fu is not None:
        value, start, end = fu
        out.append(
            StudyCharacteristic(
                document_id=doc_id,
                field="follow_up",
                value=value,
                classification_source="text_rule",
                rule_id="follow_up_text",
                evidence_ref=EvidenceRef.for_span(
                    document, start, end,
                    extraction_rule="follow_up_text",
                    extraction_rule_version=RULE_VERSION,
                ),
            )
        )

    # --- country (text or affiliations) ---
    country = _country(document, affiliations)
    if country is not None:
        canonical, surface, span = country
        if span is not None:
            ref = EvidenceRef.for_span(
                document, span[0], span[1],
                extraction_rule="country_dictionary",
                extraction_rule_version=COUNTRIES_VERSION,
            )
        else:
            ref = EvidenceRef(
                document_id=doc_id,
                quoted_text=surface,
                precision=ProvenancePrecision.METADATA,
                extraction_rule="country_dictionary",
                extraction_rule_version=COUNTRIES_VERSION,
            )
        out.append(
            StudyCharacteristic(
                document_id=doc_id,
                field="country",
                value=canonical,
                classification_source="text_rule" if span is not None else "metadata",
                rule_id="country_dictionary",
                evidence_ref=ref,
            )
        )

    return out
