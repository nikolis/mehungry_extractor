"""Phase-1 ingestion orchestration: acquire → archive → canonical → persist.

``ingest_pmid`` is the network entry point; ``normalize_document`` rebuilds the canonical
representation from *cached raw only* (offline) and is byte-reproducible. Both share
``assemble_document`` so the raw→canonical transform is identical regardless of whether the
bytes came from the network or the corpus.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Engine

from . import PIPELINE_VERSION
from . import acquire as _acquire
from . import jats, pubmed
from .canonical import Document, DocumentMetadata, ParsedSection, build_document
from .corpus import CorpusStore, sha256
from .db import get_engine, init_db, persist_document, session_scope
from .ids import document_id, normalize_pmid
from .pubmed import PubMedRecord
from .run import build_run
from .titlefilter import TitleFiltered, title_matches


def assemble_document(
    pmid: str,
    pubmed_xml: Optional[bytes],
    pmc_xml: Optional[bytes],
    pmcid: Optional[str] = None,
    checksums: Optional[dict[str, str]] = None,
) -> tuple[Document, PubMedRecord]:
    """Transform raw bytes into a canonical :class:`Document`. Deterministic, no network."""
    rec = pubmed.parse(pubmed_xml) if pubmed_xml else PubMedRecord()

    body_sections = jats.parse(pmc_xml) if pmc_xml else []
    if body_sections:
        source_type = "open_access"
    elif rec.abstract_sections:
        source_type = "abstract"
        body_sections = rec.abstract_sections
    else:
        source_type = "none"
        body_sections = []

    sections: list[ParsedSection] = []
    if rec.title:
        # Give the title its own provenance-addressable section — claims sometimes live
        # in the title, and this is consistent across full-text and abstract-only docs.
        sections.append(ParsedSection(title="Title", paragraphs=[rec.title]))
    sections.extend(body_sections)

    metadata = DocumentMetadata(
        pmid=pmid,
        pmcid=pmcid or rec.pmcid,
        doi=rec.doi,
        title=rec.title,
        journal=rec.journal,
        publication_date=rec.publication_date,
        authors=rec.authors,
        publication_types=rec.publication_types,
        source_type=source_type,
    )
    document = build_document(metadata, sections, checksums or {})
    return document, rec


def _abstract_txt(rec: PubMedRecord) -> Optional[bytes]:
    paras = [p for sec in rec.abstract_sections for p in sec.paragraphs]
    text = "\n\n".join(paras).strip()
    return text.encode("utf-8") if text else None


def _metadata_dict(document: Document, rec: PubMedRecord, raw: "_acquire.RawSources | dict", checksums: dict) -> dict:
    """Assemble the corpus ``metadata.json`` (superset of embedded doc metadata)."""
    if isinstance(raw, _acquire.RawSources):
        source_urls = raw.source_urls
        retrieval_timestamp = raw.retrieval_timestamp
    else:  # rebuilt from cache: reuse stored acquisition provenance
        source_urls = raw.get("source_urls", {})
        retrieval_timestamp = raw.get("retrieval_timestamp")
    meta = document.metadata
    return {
        "pmid": meta.pmid,
        "pmcid": meta.pmcid,
        "doi": meta.doi,
        "title": meta.title,
        "journal": meta.journal,
        "publication_date": meta.publication_date,
        "authors": meta.authors,
        "publication_types": meta.publication_types,
        "affiliations": rec.affiliations,
        "source_type": document.source_type,
        "source_urls": source_urls,
        "retrieval_timestamp": retrieval_timestamp,
        "pipeline_version": PIPELINE_VERSION,
        "checksums": checksums,
    }


def _persist(document: Document, engine: Optional[Engine]) -> None:
    if engine is None:
        engine = get_engine()
    init_db(engine)
    run = build_run([document.document_id])
    with session_scope(engine) as session:
        persist_document(session, document, run)


def ingest_pmid(
    pmid: str | int,
    *,
    force: bool = False,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    persist: bool = True,
    timeout: int = 30,
) -> Document:
    """Acquire (or reuse cached) raw sources, archive them, build + persist the canonical doc."""
    pmid = normalize_pmid(pmid)
    corpus = corpus or CorpusStore()

    if corpus.has_raw(pmid) and not force:
        # Idempotent: reuse the immutable archive instead of re-fetching.
        return normalize_document(pmid, corpus=corpus, engine=engine, persist=persist)

    raw = _acquire.fetch(pmid, timeout=timeout)

    rec_preview = pubmed.parse(raw.pubmed_xml) if raw.pubmed_xml else PubMedRecord()
    # Topical gate: screen by title before archiving anything, so an off-topic paper never
    # enters the immutable corpus (and thus never reaches any downstream stage).
    if not title_matches(rec_preview.title):
        raise TitleFiltered(pmid, rec_preview.title)
    files: dict[str, bytes] = {}
    if raw.pubmed_xml:
        files["pubmed.xml"] = raw.pubmed_xml
    if raw.pmc_xml:
        files["pmc.xml"] = raw.pmc_xml
    abstract = _abstract_txt(rec_preview)
    if abstract:
        files["abstract.txt"] = abstract

    checksums = corpus.save_raw(pmid, files, force=force)

    document, rec = assemble_document(
        pmid, raw.pubmed_xml, raw.pmc_xml, pmcid=raw.pmcid, checksums=checksums
    )
    corpus.save_metadata(pmid, _metadata_dict(document, rec, raw, checksums))
    corpus.save_canonical(document)
    if persist:
        _persist(document, engine)
    return document


def normalize_document(
    pmid: str | int,
    *,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    persist: bool = True,
) -> Document:
    """Rebuild the canonical document from cached raw only — offline, byte-reproducible."""
    pmid = normalize_pmid(pmid)
    corpus = corpus or CorpusStore()
    if not corpus.has_raw(pmid):
        raise FileNotFoundError(
            f"no cached raw sources for {document_id(pmid)}; run `mehungry ingest --pmid {pmid}` first"
        )

    raw_files = corpus.read_raw(pmid)
    pubmed_xml = raw_files.get("pubmed.xml")
    pmc_xml = raw_files.get("pmc.xml")

    # Recompute checksums from the on-disk bytes so the reverse trace stays honest, but keep
    # only the archived raw inputs (not derived files) — mirrors what save_raw stored.
    checksums = corpus.read_checksums(pmid) or {
        name: sha256(data) for name, data in raw_files.items()
    }

    prior_meta = corpus.read_metadata(pmid)
    pmcid = prior_meta.get("pmcid")
    document, rec = assemble_document(
        pmid, pubmed_xml, pmc_xml, pmcid=pmcid, checksums=checksums
    )
    corpus.save_metadata(pmid, _metadata_dict(document, rec, prior_meta, checksums))
    corpus.save_canonical(document)
    if persist:
        _persist(document, engine)
    return document
