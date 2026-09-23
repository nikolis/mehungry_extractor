"""PubMed (efetch db=pubmed) XML parsing → bibliographic metadata + abstract structure.

Deterministic, stdlib-only. Missing fields become explicit ``None``/empty — never invented
(spec §2, §9). ``PublicationType`` and author affiliations are extracted now (stored for
Phases 4-5) even though they are not yet richly modeled. The abstract is turned into
:class:`ParsedSection` objects so an abstract-only document still has real section/
paragraph/sentence provenance.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from .canonical import ParsedSection, normalize_ws


@dataclass
class PubMedRecord:
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    journal: Optional[str] = None
    publication_date: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract_sections: list[ParsedSection] = field(default_factory=list)


def _text(el: Optional[ET.Element]) -> Optional[str]:
    if el is None:
        return None
    t = normalize_ws("".join(el.itertext()))
    return t or None


def _pub_date(article: ET.Element) -> Optional[str]:
    """Compose an ISO-ish date from PubDate (Year[-Month[-Day]]) or ArticleDate."""
    _MONTHS = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    node = article.find(".//Journal/JournalIssue/PubDate")
    if node is None:
        node = article.find(".//ArticleDate")
    if node is None:
        return None
    year = node.findtext("Year")
    if not year:
        medline = node.findtext("MedlineDate")  # e.g. "2010 Jan-Feb"
        return medline.strip() if medline else None
    month = node.findtext("Month") or ""
    day = node.findtext("Day") or ""
    month = _MONTHS.get(month.strip().lower()[:3], month.strip())
    parts = [year.strip()]
    if month:
        parts.append(month.zfill(2) if month.isdigit() else month)
    if day:
        parts.append(day.strip().zfill(2))
    return "-".join(parts)


def _authors(article: ET.Element) -> tuple[list[str], list[str]]:
    authors: list[str] = []
    affiliations: list[str] = []
    seen_aff: set[str] = set()
    for author in article.findall(".//AuthorList/Author"):
        last = author.findtext("LastName")
        if last:
            fore = author.findtext("ForeName") or author.findtext("Initials") or ""
            authors.append(normalize_ws(f"{last} {fore}").strip())
        elif author.findtext("CollectiveName"):
            authors.append(normalize_ws(author.findtext("CollectiveName")))
        for aff in author.findall(".//AffiliationInfo/Affiliation"):
            text = _text(aff)
            if text and text not in seen_aff:
                seen_aff.add(text)
                affiliations.append(text)
    return authors, affiliations


def _abstract_sections(article: ET.Element) -> list[ParsedSection]:
    nodes = article.findall(".//Abstract/AbstractText")
    if not nodes:
        return []
    # If AbstractText nodes are labeled (structured abstract), one section per label;
    # otherwise a single "Abstract" section holding all paragraphs.
    labeled = [n for n in nodes if (n.get("Label") or n.get("NlmCategory"))]
    if labeled and len(labeled) == len(nodes):
        out = []
        for n in nodes:
            label = n.get("Label") or n.get("NlmCategory")
            text = _text(n)
            if text:
                out.append(ParsedSection(title=normalize_ws(label).title(), paragraphs=[text]))
        return out
    paras = [t for t in (_text(n) for n in nodes) if t]
    return [ParsedSection(title="Abstract", paragraphs=paras)] if paras else []


def parse(xml: str | bytes) -> PubMedRecord:
    """Parse one PubMed article record. Returns an empty record if unparseable."""
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return PubMedRecord()

    article = root.find(".//PubmedArticle")
    if article is None:
        article = root  # tolerate a bare <MedlineCitation> style payload

    rec = PubMedRecord()
    rec.pmid = _text(article.find(".//MedlineCitation/PMID")) or _text(article.find(".//PMID"))
    rec.title = _text(article.find(".//Article/ArticleTitle"))
    rec.journal = _text(article.find(".//Article/Journal/Title"))

    art_el = article.find(".//Article")
    if art_el is not None:
        rec.publication_date = _pub_date(art_el)
        rec.authors, rec.affiliations = _authors(art_el)
        rec.abstract_sections = _abstract_sections(art_el)

    rec.publication_types = [
        t for t in (_text(pt) for pt in article.findall(".//PublicationTypeList/PublicationType")) if t
    ]

    for aid in article.findall(".//ArticleIdList/ArticleId"):
        id_type = (aid.get("IdType") or "").lower()
        value = _text(aid)
        if not value:
            continue
        if id_type == "doi" and rec.doi is None:
            rec.doi = value
        elif id_type == "pmc" and rec.pmcid is None:
            rec.pmcid = value if value.upper().startswith("PMC") else f"PMC{value}"

    return rec
