"""Structured JATS (PMC full text) parsing.

Rather than flattening the whole body to one string via ``itertext()``, this preserves the
section → paragraph hierarchy that provenance depends on. It walks ``<body>`` in document
order, turning each ``<sec>`` into a
:class:`~mehungry_extractor.knowledge.canonical.ParsedSection` with its ``<title>`` and
direct ``<p>`` paragraphs; nested ``<sec>`` become subsequent flat sections (document
order preserved). Deterministic, stdlib-only, no network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from .canonical import ParsedSection, normalize_ws


def _local(tag: str) -> str:
    """Strip any XML namespace, returning the local element name."""
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element) -> str:
    return "".join(el.itertext())


def _clean_title(title: Optional[str]) -> Optional[str]:
    if title is None:
        return None
    t = normalize_ws(title)
    return t or None


def _walk_sec(sec: ET.Element, out: list[ParsedSection]) -> None:
    title: Optional[str] = None
    paragraphs: list[str] = []
    subsecs: list[ET.Element] = []
    for child in sec:
        tag = _local(child.tag)
        if tag == "title" and title is None:
            title = _text(child)
        elif tag == "p":
            paragraphs.append(_text(child))
        elif tag == "sec":
            subsecs.append(child)
    out.append(ParsedSection(title=_clean_title(title), paragraphs=paragraphs))
    for sub in subsecs:
        _walk_sec(sub, out)


def parse(xml: str | bytes) -> list[ParsedSection]:
    """Return the body's sections in document order, or ``[]`` if unparseable/empty."""
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    body = None
    for el in root.iter():
        if _local(el.tag) == "body":
            body = el
            break
    if body is None:
        return []

    out: list[ParsedSection] = []
    loose: list[str] = []

    def flush_loose() -> None:
        if loose:
            out.append(ParsedSection(title=None, paragraphs=list(loose)))
            loose.clear()

    for child in body:
        tag = _local(child.tag)
        if tag == "sec":
            flush_loose()
            _walk_sec(child, out)
        elif tag == "p":
            loose.append(_text(child))
    flush_loose()

    return out


# --- front/back matter: funding, conflicts, acknowledgements (Phase 4) --------------
#
# ``parse`` above deals only with the article ``<body>``. Funding, conflict-of-interest, and
# acknowledgement statements live in JATS front/back matter and are structured metadata, not
# canonical-text spans — so they are returned as :class:`FundingStatement`s carrying their own
# text and a ``source`` label, and downstream evidence uses ``METADATA`` precision.


@dataclass
class FundingStatement:
    """A funding/COI/acknowledgement statement lifted from JATS front/back matter.

    ``funder`` is populated only when the source structure names one explicitly (an
    ``<award-group>``'s ``<funding-source>``); free-text statements leave it ``None`` and let the
    curated funder dictionary scan the ``text``. ``source`` records provenance origin, one of:
    ``pmc_award_group | pmc_funding_statement | coi_statement | acknowledgements``.
    """

    funder: Optional[str]
    text: str
    source: str


def _first_child_text(el: ET.Element, local_name: str) -> Optional[str]:
    for child in el.iter():
        if _local(child.tag) == local_name:
            t = normalize_ws(_text(child))
            return t or None
    return None


def parse_funding(xml: str | bytes) -> list[FundingStatement]:
    """Structured funding statements from ``<funding-group>`` (award groups + free statements).

    Deterministic, document order. Returns ``[]`` when there is no funding-group — *absence is
    not evidence* (spec §10): the caller records ``unknown``/omits a relationship, never
    "independent".
    """
    root = _root(xml)
    if root is None:
        return []
    out: list[FundingStatement] = []
    for fg in root.iter():
        if _local(fg.tag) != "funding-group":
            continue
        for child in fg:
            tag = _local(child.tag)
            if tag == "award-group":
                funder = _first_child_text(child, "funding-source") or _first_child_text(
                    child, "institution"
                )
                text = normalize_ws(_text(child))
                if funder or text:
                    out.append(
                        FundingStatement(funder=funder, text=text or (funder or ""), source="pmc_award_group")
                    )
            elif tag in ("funding-statement", "funding-source"):
                text = normalize_ws(_text(child))
                if text:
                    out.append(FundingStatement(funder=None, text=text, source="pmc_funding_statement"))
    return out


def parse_back_matter(xml: str | bytes) -> list[FundingStatement]:
    """Conflict-of-interest and acknowledgement statements from front/back matter.

    ``<fn fn-type="conflict"|"COI-statement">`` → ``coi_statement``; ``<ack>`` → ``acknowledgements``.
    Both are free text with ``funder=None`` (the funder dictionary scans the text).
    """
    root = _root(xml)
    if root is None:
        return []
    out: list[FundingStatement] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "fn":
            fn_type = (el.get("fn-type") or "").lower()
            if fn_type in ("conflict", "coi-statement", "coi"):
                text = normalize_ws(_text(el))
                if text:
                    out.append(FundingStatement(funder=None, text=text, source="coi_statement"))
        elif tag == "ack":
            text = normalize_ws(_text(el))
            if text:
                out.append(FundingStatement(funder=None, text=text, source="acknowledgements"))
    return out


def _root(xml: str | bytes) -> Optional[ET.Element]:
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", errors="replace")
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None
