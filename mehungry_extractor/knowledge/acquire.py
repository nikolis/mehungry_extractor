"""Raw source acquisition from NCBI E-utilities.

Fetches the *raw* XML artifacts (for durable archival): the immutable corpus keeps these bytes
forever (spec §2). This module makes no parsing decisions beyond resolving a PMCID;
structure/metadata parsing lives in ``jats``/``pubmed``. It owns its own thin E-utilities plumbing
(endpoint + API-key handling) so the deterministic engine has no external dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

from .ids import normalize_pmid

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_API_KEY = os.environ.get("NCBI_API_KEY")


def _params(extra: dict) -> dict:
    """Merge the optional NCBI API key into an E-utilities query param dict."""
    p = dict(extra)
    if _API_KEY:
        p["api_key"] = _API_KEY
    return p


@dataclass
class RawSources:
    """Bytes + acquisition provenance for one PMID. ``pmc_xml`` is ``None`` when the
    article is not in PMC open access."""

    pmid: str
    pmcid: Optional[str]
    pubmed_xml: Optional[bytes]
    pmc_xml: Optional[bytes]
    source_urls: dict[str, str] = field(default_factory=dict)
    retrieval_timestamp: str = ""


def _efetch_url(db: str, uid: str) -> str:
    return f"{EUTILS}/efetch.fcgi?db={db}&id={uid}&retmode=xml"


def _pmid_to_pmcid(pmid: str, timeout: int) -> Optional[str]:
    """Resolve a PMID to its PMC id via elink, or ``None`` when the article is not in PMC."""
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


def fetch(pmid: str | int, timeout: int = 30) -> RawSources:
    """Fetch PubMed XML and (if linked) PMC full-text XML for ``pmid``."""
    pmid = normalize_pmid(pmid)
    urls: dict[str, str] = {}

    pubmed_resp = requests.get(
        f"{EUTILS}/efetch.fcgi",
        params=_params({"db": "pubmed", "id": pmid, "retmode": "xml"}),
        timeout=timeout,
    )
    pubmed_resp.raise_for_status()
    pubmed_xml = pubmed_resp.content
    urls["pubmed"] = _efetch_url("pubmed", pmid)

    pmcid = _pmid_to_pmcid(pmid, timeout)
    pmc_xml: Optional[bytes] = None
    if pmcid:
        pmc_resp = requests.get(
            f"{EUTILS}/efetch.fcgi",
            params=_params({"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"}),
            timeout=timeout,
        )
        pmc_resp.raise_for_status()
        pmc_xml = pmc_resp.content
        urls["pmc"] = _efetch_url("pmc", pmcid.replace("PMC", ""))

    return RawSources(
        pmid=pmid,
        pmcid=pmcid,
        pubmed_xml=pubmed_xml,
        pmc_xml=pmc_xml,
        source_urls=urls,
        retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
    )
