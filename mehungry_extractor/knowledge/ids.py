"""Deterministic, position-based identifiers for canonical text objects.

IDs are stable functions of (pmid, structural position) only — no randomness, no
timestamps, no content hashing — so re-ingesting the same document yields byte-identical
IDs. They are also hierarchical, so a sentence ID names its paragraph, section, and
document, giving cheap bidirectional traceability without a lookup table.

Layout::

    pmid_12345678
    pmid_12345678_sec003
    pmid_12345678_sec003_p007
    pmid_12345678_sec003_p007_s02
"""

from __future__ import annotations

import re

_PMID_RE = re.compile(r"^\d+$")


def normalize_pmid(pmid: str | int) -> str:
    """Return the bare numeric PMID as a string, or raise ``ValueError``.

    Accepts ``"12345678"``, ``12345678``, or ``"pmid:12345678"``.
    """
    s = str(pmid).strip()
    if s.lower().startswith("pmid:"):
        s = s[len("pmid:") :].strip()
    if not _PMID_RE.match(s):
        raise ValueError(f"not a valid PMID: {pmid!r}")
    return s


def document_id(pmid: str | int) -> str:
    return f"pmid_{normalize_pmid(pmid)}"


def section_id(doc_id: str, index: int) -> str:
    return f"{doc_id}_sec{index:03d}"


def paragraph_id(sec_id: str, index: int) -> str:
    return f"{sec_id}_p{index:03d}"


def sentence_id(par_id: str, index: int) -> str:
    return f"{par_id}_s{index:02d}"
