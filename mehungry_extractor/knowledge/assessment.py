"""Assessment frameworks applied *on top of* the evidence, never mutating it (spec §11).

An assessment framework is a **pure, versioned function** of the Phase-4 facts
(``study_design``, ``publication_year``, ``sample_size``, industry funding). It maps those facts
to graded values that live only in the ``assessments`` table — it never writes a score back onto
a claim or paper, and re-running it replaces only that framework's rows. Multiple frameworks can
coexist over the same untouched evidence.

The framework ``version`` participates in reproducibility: changing the mapping bumps the version
and produces new assessment rows, while every evidence/claim/fact row stays byte-identical.

``unknown`` funding is treated as *not independent* (spec §14) — the engine never assumes
independence from absence of information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel

from .provenance import EvidenceRef, ProvenancePrecision

FRAMEWORK_ID = "mehungry_evidence_v1"
FRAMEWORK_VERSION = "1.0.0"
FRAMEWORK_NAME = "Mehungry evidence framework v1"

# Funder-type categories that count as industry (non-independent) funding (spec §10/§14).
INDUSTRY_FUNDER_TYPES = frozenset(
    {"pharmaceutical", "biotechnology", "medical_device", "food_industry", "private_company"}
)

_HIGH_DESIGNS = frozenset({"systematic_review", "meta_analysis", "randomized_controlled_trial"})
_MODERATE_DESIGNS = frozenset({"controlled_trial", "cohort", "case_control"})


@dataclass
class PaperFacts:
    """The Phase-4 facts a framework reads. Assembled deterministically from the DB."""

    document_id: str
    study_design: Optional[str] = None
    publication_year: Optional[int] = None
    sample_size: Optional[int] = None
    # None → no funding information at all; True/False → industry funding present/absent.
    industry_funding: Optional[bool] = None
    funder_types: list[str] = field(default_factory=list)


class Assessment(BaseModel):
    """One framework judgement about a paper. Derived, never authoritative over evidence."""

    framework_id: str
    framework_version: str
    document_id: str
    criterion: str
    value: str
    rationale: str
    evidence_refs: list[EvidenceRef]


@dataclass(frozen=True)
class Criterion:
    criterion: str
    description: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion("study_design_strength", "Strength of the study design (high/moderate/low/unknown)."),
    Criterion("recency", "Whether the publication is recent (>= 2015)."),
    Criterion("sample_size_adequacy", "Whether the sample size is adequate (>= 100)."),
    Criterion("funding_independence", "Whether funding is independent of industry."),
)


def _ref(facts: PaperFacts, quoted: str) -> EvidenceRef:
    """A METADATA-precision ref tying an assessment back to the paper + the fact it read."""
    return EvidenceRef(
        document_id=facts.document_id,
        quoted_text=quoted,
        precision=ProvenancePrecision.METADATA,
        extraction_rule=f"{FRAMEWORK_ID}",
        extraction_rule_version=FRAMEWORK_VERSION,
    )


def _mk(facts: PaperFacts, criterion: str, value: str, rationale: str, quoted: str) -> Assessment:
    return Assessment(
        framework_id=FRAMEWORK_ID,
        framework_version=FRAMEWORK_VERSION,
        document_id=facts.document_id,
        criterion=criterion,
        value=value,
        rationale=rationale,
        evidence_refs=[_ref(facts, quoted)],
    )


def assess(facts: PaperFacts) -> list[Assessment]:
    """Apply ``mehungry_evidence_v1`` to a paper's facts. Pure + deterministic."""
    out: list[Assessment] = []

    # study_design_strength
    design = facts.study_design
    if design is None or design == "unknown":
        out.append(_mk(facts, "study_design_strength", "unknown",
                       "No study design could be determined.", str(design)))
    elif design in _HIGH_DESIGNS:
        out.append(_mk(facts, "study_design_strength", "high",
                       f"{design} is a high-strength design.", design))
    elif design in _MODERATE_DESIGNS:
        out.append(_mk(facts, "study_design_strength", "moderate",
                       f"{design} is a moderate-strength design.", design))
    else:
        out.append(_mk(facts, "study_design_strength", "low",
                       f"{design} is a lower-strength design.", design))

    # recency
    if facts.publication_year is None:
        out.append(_mk(facts, "recency", "unknown", "No publication year available.", "None"))
    elif facts.publication_year >= 2015:
        out.append(_mk(facts, "recency", "recent",
                       f"Published in {facts.publication_year} (>= 2015).", str(facts.publication_year)))
    else:
        out.append(_mk(facts, "recency", "older",
                       f"Published in {facts.publication_year} (< 2015).", str(facts.publication_year)))

    # sample_size_adequacy
    if facts.sample_size is None:
        out.append(_mk(facts, "sample_size_adequacy", "unknown", "No sample size reported.", "None"))
    elif facts.sample_size >= 100:
        out.append(_mk(facts, "sample_size_adequacy", "adequate",
                       f"Sample size {facts.sample_size} (>= 100).", str(facts.sample_size)))
    else:
        out.append(_mk(facts, "sample_size_adequacy", "small",
                       f"Sample size {facts.sample_size} (< 100).", str(facts.sample_size)))

    # funding_independence — unknown funding is NOT independent (spec §14).
    if facts.industry_funding is None:
        out.append(_mk(facts, "funding_independence", "unknown",
                       "No funding information; independence cannot be assumed.", "None"))
    elif facts.industry_funding:
        out.append(_mk(facts, "funding_independence", "industry_funded",
                       f"Industry funding present ({', '.join(sorted(set(facts.funder_types)))}).",
                       ", ".join(sorted(set(facts.funder_types)))))
    else:
        out.append(_mk(facts, "funding_independence", "independent",
                       "No industry funding among the recorded funders.",
                       ", ".join(sorted(set(facts.funder_types))) or "non-industry"))

    return out
