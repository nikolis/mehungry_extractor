"""Deterministic query API (spec §14) + provenance API (spec §15).

Phase 1 exposes what the current data supports: listing documents, loading a canonical
document, listing its sentences, and resolving a source span. Claim/entity/funding filters
(``find_claims``, ``find_papers(study_design=...)``) arrive with the phases that produce
those objects; their stubs raise a clear ``NotImplementedError`` naming the phase, so the
query surface is visible and no caller is silently given empty results.

All queries impose explicit ordering for deterministic output. No fuzzy/semantic search.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Engine

from .canonical import Document
from .db import DocumentRow, load_document, session_scope
from .db.schema import SentenceRow
from .ids import document_id


def list_documents(engine: Engine) -> list[dict]:
    """All ingested documents, ordered by document_id."""
    with session_scope(engine) as session:
        rows = session.query(DocumentRow).order_by(DocumentRow.document_id).all()
        return [
            {
                "document_id": r.document_id,
                "pmid": r.pmid,
                "pmcid": r.pmcid,
                "doi": r.doi,
                "title": r.title,
                "source_type": r.source_type,
                "publication_date": r.publication_date,
            }
            for r in rows
        ]


def get_document(engine: Engine, doc_or_pmid: str) -> Optional[Document]:
    """Load a canonical document by document_id or ``pmid:NNNN``/bare PMID."""
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        return load_document(session, doc_id)


def list_sentences_for_document(engine: Engine, doc_or_pmid: str) -> list[SentenceRow]:
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        return (
            session.query(SentenceRow)
            .filter(SentenceRow.document_id == doc_id)
            .order_by(SentenceRow.start_char)
            .all()
        )


def get_source_span(engine: Engine, document_id_: str, start_char: int, end_char: int) -> Optional[str]:
    """Return the canonical text slice for an offset span (spec §15 ``get_source_span``)."""
    with session_scope(engine) as session:
        row = session.get(DocumentRow, document_id_)
        if row is None:
            return None
        return row.canonical_text[start_char:end_char]


# --- later-phase surface (explicit stubs so the API shape is discoverable) ---


def find_claims(**_filters) -> list:
    raise NotImplementedError("find_claims arrives in Phase 3 (relation/claim extraction)")


def find_papers(**_filters) -> list:
    raise NotImplementedError("study-design/funding filters arrive in Phase 4")


def explain_claim(_claim_id: str) -> dict:
    raise NotImplementedError("explain_claim arrives in Phase 5 (assessment/audit)")
