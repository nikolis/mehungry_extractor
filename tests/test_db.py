"""SQLite persistence round-trip + indexes + bidirectional trace (spec §13, §20)."""

from sqlalchemy import inspect

from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, load_document, session_scope
from mehungry_extractor.knowledge.db.schema import SentenceRow
from mehungry_extractor.knowledge.ingest import normalize_document


def _ingest_offline(tmp_path, pubmed_xml, pmc_xml):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": pubmed_xml, "pmc.xml": pmc_xml})
    engine = get_engine(tmp_path / "db.sqlite")
    doc = normalize_document("12345678", corpus=store, engine=engine)
    return engine, doc


def test_persist_and_load_round_trip(tmp_path, pubmed_xml, pmc_xml):
    engine, doc = _ingest_offline(tmp_path, pubmed_xml, pmc_xml)
    with session_scope(engine) as session:
        loaded = load_document(session, doc.document_id)
    assert loaded is not None
    assert loaded.model_dump() == doc.model_dump()


def test_indexes_present(tmp_path, pubmed_xml, pmc_xml):
    engine, _ = _ingest_offline(tmp_path, pubmed_xml, pmc_xml)
    insp = inspect(engine)
    doc_indexed = {c for idx in insp.get_indexes("documents") for c in idx["column_names"]}
    assert {"pmid", "pmcid", "doi"} <= doc_indexed
    sent_index_names = {idx["name"] for idx in insp.get_indexes("sentences")}
    assert "ix_sentences_doc_span" in sent_index_names


def test_bidirectional_trace(tmp_path, pubmed_xml, pmc_xml):
    """Paper → all its sentences, and each sentence → back to its document."""
    engine, doc = _ingest_offline(tmp_path, pubmed_xml, pmc_xml)
    with session_scope(engine) as session:
        rows = (
            session.query(SentenceRow)
            .filter(SentenceRow.document_id == doc.document_id)
            .order_by(SentenceRow.start_char)
            .all()
        )
        assert rows
        for r in rows:
            assert r.document_id == doc.document_id
            # each stored sentence reconstructs from the canonical text via its offsets
            assert doc.text[r.start_char : r.end_char] == r.text
