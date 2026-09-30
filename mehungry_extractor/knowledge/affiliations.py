"""Author ↔ affiliation ↔ institution structuring (spec §10).

Parses per-author affiliations from the PubMed record (``pubmed.parse`` deduplicates
affiliations globally and loses the author link, so we walk the ``<AuthorList>`` directly here)
and normalizes each affiliation's institution against the curated institution dictionary. The
**raw affiliation string is always preserved**; institution normalization is deterministic and
additive — an unmatched institution still gets a stable row (keyed by a hash of its raw text,
``institution_type = "unknown"``) so "papers involving institution X" stays answerable without
inventing a category.

Deterministic, offline, stdlib-only XML.
"""

from __future__ import annotations

import functools
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Optional

from pydantic import BaseModel

from .canonical import normalize_ws
from .vocab import INSTITUTIONS_VERSION, load_institutions


class Author(BaseModel):
    author_id: str
    document_id: str
    position: int
    name: str


class Institution(BaseModel):
    institution_id: str
    canonical_name: str
    institution_type: str  # university | company | hospital | government | research_institute | unknown


class Affiliation(BaseModel):
    affiliation_id: str
    document_id: str
    raw_text: str
    institution_id: Optional[str] = None


class AuthorAffiliation(BaseModel):
    document_id: str
    author_id: str
    affiliation_id: str


class AffiliationExtraction(BaseModel):
    authors: list[Author]
    affiliations: list[Affiliation]
    institutions: list[Institution]
    author_affiliations: list[AuthorAffiliation]


@functools.lru_cache(maxsize=1)
def _institution_index() -> list[tuple[re.Pattern, str, str, str]]:
    """``(surface regex, institution_id, canonical_name, type)`` — longest surface first."""
    entries: list[tuple[str, str, str, str]] = []
    for rec in load_institutions():
        for surface in rec["surface_forms"]:
            entries.append(
                (surface, rec["institution_id"], rec["canonical_name"], rec["institution_type"])
            )
    entries.sort(key=lambda e: (-len(e[0]), e[0]))
    return [
        (re.compile(rf"(?<!\w){re.escape(surface)}(?!\w)", re.IGNORECASE), iid, name, itype)
        for surface, iid, name, itype in entries
    ]


def _normalize_institution(raw: str) -> Institution:
    for pattern, iid, canonical, itype in _institution_index():
        if pattern.search(raw):
            return Institution(institution_id=iid, canonical_name=canonical, institution_type=itype)
    # Unmatched: keep the raw affiliation as its own institution (never dropped, never guessed).
    digest = hashlib.sha256(normalize_ws(raw).casefold().encode("utf-8")).hexdigest()[:12]
    return Institution(institution_id=f"inst_{digest}", canonical_name=raw, institution_type="unknown")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _findall_local(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def extract(document_id: str, pubmed_xml: Optional[bytes]) -> AffiliationExtraction:
    """Structure authors, affiliations, and institutions from raw PubMed XML. Deterministic."""
    empty = AffiliationExtraction(authors=[], affiliations=[], institutions=[], author_affiliations=[])
    if not pubmed_xml:
        return empty
    xml = pubmed_xml.decode("utf-8", errors="replace") if isinstance(pubmed_xml, bytes) else pubmed_xml
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return empty

    authors: list[Author] = []
    affiliations: list[Affiliation] = []
    author_affiliations: list[AuthorAffiliation] = []
    institutions: dict[str, Institution] = {}
    # Deduplicate affiliations per document by raw text, so shared affiliation strings map to one
    # affiliation row that multiple authors can reference.
    aff_by_raw: dict[str, str] = {}

    author_nodes = _findall_local(root, "Author")
    for position, author in enumerate(author_nodes):
        last = author.findtext("LastName")
        if last:
            fore = author.findtext("ForeName") or author.findtext("Initials") or ""
            name = normalize_ws(f"{last} {fore}").strip()
        elif author.findtext("CollectiveName"):
            name = normalize_ws(author.findtext("CollectiveName"))
        else:
            continue
        author_id = f"{document_id}_au{position:03d}"
        authors.append(Author(author_id=author_id, document_id=document_id, position=position, name=name))

        for aff_el in _findall_local(author, "Affiliation"):
            raw = normalize_ws("".join(aff_el.itertext()))
            if not raw:
                continue
            if raw not in aff_by_raw:
                aff_id = f"{document_id}_aff{len(aff_by_raw):03d}"
                inst = _normalize_institution(raw)
                institutions.setdefault(inst.institution_id, inst)
                aff_by_raw[raw] = aff_id
                affiliations.append(
                    Affiliation(
                        affiliation_id=aff_id,
                        document_id=document_id,
                        raw_text=raw,
                        institution_id=inst.institution_id,
                    )
                )
            author_affiliations.append(
                AuthorAffiliation(
                    document_id=document_id, author_id=author_id, affiliation_id=aff_by_raw[raw]
                )
            )

    return AffiliationExtraction(
        authors=authors,
        affiliations=affiliations,
        institutions=sorted(institutions.values(), key=lambda i: i.institution_id),
        author_affiliations=author_affiliations,
    )


# Recorded on the run so different dictionary revisions are distinguishable.
AFFILIATIONS_VERSION = INSTITUTIONS_VERSION
