"""Fetch study text from NCBI by PMID.

Prefers PubMed Central open-access full text (richer — the methods/results discuss
flare vs remission); falls back to the PubMed abstract. Pure stdlib + requests; no
domain knowledge. Modeled on ``MehungryLocalAi.PMC`` but self-contained here.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_API_KEY = os.environ.get("NCBI_API_KEY")


@dataclass
class Document:
    pmid: str
    text: str
    source: str  # "open_access" | "abstract" | "none"


def _params(extra: dict) -> dict:
    p = dict(extra)
    if _API_KEY:
        p["api_key"] = _API_KEY
    return p


def fetch(pmid: str, timeout: int = 30) -> Document:
    """Full-text (PMC OA) if available, else abstract, else empty."""
    pmcid = _pmid_to_pmcid(pmid, timeout)
    if pmcid:
        body = _pmc_full_text(pmcid, timeout)
        if body:
            return Document(pmid=str(pmid), text=body, source="open_access")

    abstract = _pubmed_abstract(pmid, timeout)
    if abstract:
        return Document(pmid=str(pmid), text=abstract, source="abstract")

    return Document(pmid=str(pmid), text="", source="none")


def _pmid_to_pmcid(pmid: str, timeout: int) -> str | None:
    resp = requests.get(
        f"{EUTILS}/elink.fcgi",
        params=_params({"dbfrom": "pubmed", "db": "pmc", "id": pmid, "retmode": "json"}),
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        linksets = resp.json()["linksets"]
        for ls in linksets:
            for db in ls.get("linksetdbs", []):
                if db.get("dbto") == "pmc" and db.get("links"):
                    return f"PMC{db['links'][0]}"
    except (KeyError, IndexError, ValueError):
        pass
    return None


def _pmc_full_text(pmcid: str, timeout: int) -> str | None:
    resp = requests.get(
        f"{EUTILS}/efetch.fcgi",
        params=_params({"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"}),
        timeout=timeout,
    )
    resp.raise_for_status()
    return _jats_to_text(resp.text)


def _pubmed_abstract(pmid: str, timeout: int) -> str | None:
    resp = requests.get(
        f"{EUTILS}/efetch.fcgi",
        params=_params({"db": "pubmed", "id": pmid, "retmode": "xml"}),
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None

    parts = []
    title = root.findtext(".//ArticleTitle")
    if title:
        parts.append(title)
    for node in root.findall(".//Abstract/AbstractText"):
        parts.append("".join(node.itertext()))
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    return text or None


def _jats_to_text(xml: str) -> str | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    body = root.find(".//body")
    node = body if body is not None else root
    text = " ".join(node.itertext())
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
