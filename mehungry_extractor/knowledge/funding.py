"""Deterministic funder + funder-type extraction (spec §10).

Funding is a *fact with provenance*, kept independent of any quality judgement. Two rules,
both auditable and reproducible:

* an ``<award-group>`` that names a ``<funding-source>`` yields that funder directly;
* a free-text funding / COI / acknowledgement statement is scanned for known funder surface
  forms from the curated dictionary (:mod:`.vocab`).

``funder_type`` comes **only** from the curated dictionary or authoritative structure; an
unmatched funder is recorded with ``funder_type = "unknown"`` — never inferred. Crucially,
*absence is not evidence* (spec §10): a paper with no funding statement simply yields no
:class:`FundingRelationship`; the engine never synthesizes an "independent" fact.

Evidence uses ``METADATA`` precision because funding statements live in JATS front/back matter,
not in the offset-addressable canonical body text.
"""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from .provenance import EvidenceRef, ProvenancePrecision
from .vocab import FUNDERS_VERSION, load_funders

if TYPE_CHECKING:
    from .jats import FundingStatement

FUNDER_TYPE_UNKNOWN = "unknown"


class FundingRelationship(BaseModel):
    """A paper ↔ funder fact. ``funder_type`` is ``unknown`` unless a curated rule matched."""

    document_id: str
    funder: str
    funder_id: Optional[str] = None
    funder_type: str
    source: str  # pmc_award_group | pmc_funding_statement | coi_statement | acknowledgements | pubmed_grant
    rule_id: str
    evidence_ref: EvidenceRef


@functools.lru_cache(maxsize=1)
def _funder_index() -> list[tuple[re.Pattern, str, str, str]]:
    """``(surface regex, funder_id, canonical_name, funder_type)`` — longest surface first."""
    entries: list[tuple[str, str, str, str]] = []
    for rec in load_funders():
        for surface in rec["surface_forms"]:
            entries.append((surface, rec["funder_id"], rec["canonical_name"], rec["funder_type"]))
    entries.sort(key=lambda e: (-len(e[0]), e[0]))
    return [
        (re.compile(rf"(?<!\w){re.escape(surface)}(?!\w)", re.IGNORECASE), fid, name, ftype)
        for surface, fid, name, ftype in entries
    ]


def _lookup(name: str) -> Optional[tuple[str, str, str]]:
    """Resolve a funder name against the dictionary → ``(funder_id, canonical_name, type)``."""
    for pattern, fid, canonical, ftype in _funder_index():
        if pattern.search(name):
            return fid, canonical, ftype
    return None


def _relationship(document_id: str, funder_name: str, statement: "FundingStatement") -> FundingRelationship:
    hit = _lookup(funder_name)
    funder_id = hit[0] if hit else None
    canonical = hit[1] if hit else funder_name
    ftype = hit[2] if hit else FUNDER_TYPE_UNKNOWN
    return FundingRelationship(
        document_id=document_id,
        funder=canonical,
        funder_id=funder_id,
        funder_type=ftype,
        source=statement.source,
        rule_id="funder_dictionary",
        evidence_ref=EvidenceRef(
            document_id=document_id,
            quoted_text=statement.text or funder_name,
            precision=ProvenancePrecision.METADATA,
            extraction_rule="funder_dictionary",
            extraction_rule_version=FUNDERS_VERSION,
        ),
    )


def extract(document_id: str, statements: "list[FundingStatement]") -> list[FundingRelationship]:
    """Build funding relationships from parsed statements. Deterministic; ``[]`` when none."""
    out: list[FundingRelationship] = []
    seen: set[tuple[str, str, str]] = set()

    for statement in statements:
        # An explicitly named funder (award-group) is taken as-is; a free-text statement is
        # scanned for every known funder surface it mentions.
        named: list[str] = []
        if statement.funder:
            named.append(statement.funder)
        else:
            for pattern, _fid, canonical, _ftype in _funder_index():
                if pattern.search(statement.text):
                    named.append(canonical)

        for funder_name in named:
            rel = _relationship(document_id, funder_name, statement)
            key = (rel.funder, rel.funder_type, rel.source)
            if key in seen:
                continue
            seen.add(key)
            out.append(rel)

    out.sort(key=lambda r: (r.funder, r.source))
    return out
