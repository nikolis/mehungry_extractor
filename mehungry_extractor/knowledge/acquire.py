"""Raw source acquisition from NCBI E-utilities.

Fetches the *raw* XML artifacts (for durable archival) rather than the flattened text the
legacy path uses — the immutable corpus keeps these bytes forever (spec §2). This module
makes no parsing decisions beyond resolving a PMCID; structure/metadata parsing lives in
``jats``/``pubmed``. It reuses the E-utilities plumbing from the legacy ``pmc`` module so
there is a single source of truth for the endpoint and API-key handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

from .. import pmc  # reuse EUTILS, _params, _pmid_to_pmcid — no behavior change to pmc.py
from .ids import normalize_pmid


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
    return f"{pmc.EUTILS}/efetch.fcgi?db={db}&id={uid}&retmode=xml"


def fetch(pmid: str | int, timeout: int = 30) -> RawSources:
    """Fetch PubMed XML and (if linked) PMC full-text XML for ``pmid``."""
    pmid = normalize_pmid(pmid)
    urls: dict[str, str] = {}

    pubmed_resp = requests.get(
        f"{pmc.EUTILS}/efetch.fcgi",
        params=pmc._params({"db": "pubmed", "id": pmid, "retmode": "xml"}),
        timeout=timeout,
    )
    pubmed_resp.raise_for_status()
    pubmed_xml = pubmed_resp.content
    urls["pubmed"] = _efetch_url("pubmed", pmid)

    pmcid = pmc._pmid_to_pmcid(pmid, timeout)
    pmc_xml: Optional[bytes] = None
    if pmcid:
        pmc_resp = requests.get(
            f"{pmc.EUTILS}/efetch.fcgi",
            params=pmc._params({"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"}),
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
