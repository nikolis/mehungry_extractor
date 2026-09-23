"""Structured JATS (PMC full text) parsing.

Unlike ``pmc._jats_to_text`` (which flattens the whole body to one string via
``itertext()``), this preserves the section → paragraph hierarchy that provenance depends
on. It walks ``<body>`` in document order, turning each ``<sec>`` into a
:class:`~mehungry_extractor.knowledge.canonical.ParsedSection` with its ``<title>`` and
direct ``<p>`` paragraphs; nested ``<sec>`` become subsequent flat sections (document
order preserved). Deterministic, stdlib-only, no network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
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
