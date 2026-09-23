"""SQLAlchemy ORM tables + persist/load for canonical documents.

The tables mirror the canonical model 1:1 so the corpus and the DB stay in lock-step, and
every row carries ``run_id`` for reproducibility. Writes are idempotent: a re-ingest with
the same run replaces the document's structural rows rather than duplicating them.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ..canonical import (
    Document,
    DocumentMetadata,
    Paragraph,
    Section,
    Sentence,
)
from ..run import ExtractionRun


class Base(DeclarativeBase):
    pass


class ExtractionRunRow(Base):
    __tablename__ = "extraction_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    timestamp: Mapped[str] = mapped_column(String)
    git_commit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String)
    ruleset_version: Mapped[str] = mapped_column(String)
    extractor_version: Mapped[str] = mapped_column(String)
    python_version: Mapped[str] = mapped_column(String)
    spacy_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scispacy_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ontology_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    document_ids: Mapped[list] = mapped_column(JSON, default=list)


class DocumentRow(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    pmid: Mapped[str] = mapped_column(String, index=True)
    pmcid: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    doi: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    journal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    publication_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    publication_types: Mapped[list] = mapped_column(JSON, default=list)
    source_type: Mapped[str] = mapped_column(String)
    pipeline_version: Mapped[str] = mapped_column(String)
    canonical_text: Mapped[str] = mapped_column(Text)
    checksums: Mapped[dict] = mapped_column(JSON, default=dict)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # f"{document_id}:{run_id}"
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))
    pipeline_version: Mapped[str] = mapped_column(String)
    checksums: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String)


class SectionRow(Base):
    __tablename__ = "sections"

    section_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class ParagraphRow(Base):
    __tablename__ = "paragraphs"

    paragraph_id: Mapped[str] = mapped_column(String, primary_key=True)
    section_id: Mapped[str] = mapped_column(String, ForeignKey("sections.section_id"), index=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)


class SentenceRow(Base):
    __tablename__ = "sentences"

    sentence_id: Mapped[str] = mapped_column(String, primary_key=True)
    paragraph_id: Mapped[str] = mapped_column(String, ForeignKey("paragraphs.paragraph_id"), index=True)
    section_id: Mapped[str] = mapped_column(String, ForeignKey("sections.section_id"), index=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


# Composite index that the provenance/query layer leans on for span lookups.
Index("ix_sentences_doc_span", SentenceRow.document_id, SentenceRow.start_char)


def persist_document(session: Session, document: Document, run: ExtractionRun) -> None:
    """Idempotently write a canonical document and its run. Replaces prior structure rows."""
    session.merge(
        ExtractionRunRow(
            run_id=run.run_id,
            timestamp=run.timestamp,
            git_commit=run.git_commit,
            pipeline_version=run.pipeline_version,
            ruleset_version=run.ruleset_version,
            extractor_version=run.extractor_version,
            python_version=run.python_version,
            spacy_version=run.spacy_version,
            scispacy_version=run.scispacy_version,
            ontology_versions=run.ontology_versions,
            document_ids=run.document_ids,
        )
    )

    meta = document.metadata
    session.merge(
        DocumentRow(
            document_id=document.document_id,
            pmid=meta.pmid,
            pmcid=meta.pmcid,
            doi=meta.doi,
            title=meta.title,
            journal=meta.journal,
            publication_date=meta.publication_date,
            authors=meta.authors,
            publication_types=meta.publication_types,
            source_type=document.source_type,
            pipeline_version=document.pipeline_version,
            canonical_text=document.text,
            checksums=document.checksums,
            run_id=run.run_id,
        )
    )
    session.merge(
        DocumentVersionRow(
            id=f"{document.document_id}:{run.run_id}",
            document_id=document.document_id,
            run_id=run.run_id,
            pipeline_version=document.pipeline_version,
            checksums=document.checksums,
            created_at=run.timestamp,
        )
    )
    # Ensure run + document rows exist before their FK children are inserted (the ORM rows
    # use plain FK columns, not relationships, so the unit-of-work can't order them for us).
    session.flush()

    # Replace structural rows so a re-ingest never leaves stale spans behind.
    for table in (SentenceRow, ParagraphRow, SectionRow):
        session.execute(delete(table).where(table.document_id == document.document_id))
    session.flush()

    sections = [
        SectionRow(
            section_id=sec.section_id,
            document_id=document.document_id,
            idx=sec.index,
            title=sec.title,
            start_char=sec.start_char,
            end_char=sec.end_char,
            text=sec.text,
        )
        for sec in document.sections
    ]
    paragraphs = [
        ParagraphRow(
            paragraph_id=par.paragraph_id,
            section_id=sec.section_id,
            document_id=document.document_id,
            start_char=par.start_char,
            end_char=par.end_char,
            text=par.text,
            raw_text=par.raw_text,
        )
        for sec in document.sections
        for par in sec.paragraphs
    ]
    sentences = [
        SentenceRow(
            sentence_id=sent.sentence_id,
            paragraph_id=par.paragraph_id,
            section_id=sec.section_id,
            document_id=document.document_id,
            start_char=sent.start_char,
            end_char=sent.end_char,
            text=sent.text,
        )
        for sec in document.sections
        for par in sec.paragraphs
        for sent in par.sentences
    ]
    # Insert parents before children (sections → paragraphs → sentences).
    session.add_all(sections)
    session.flush()
    session.add_all(paragraphs)
    session.flush()
    session.add_all(sentences)


def load_document(session: Session, document_id: str) -> Optional[Document]:
    """Reconstruct a canonical :class:`Document` from persisted rows (reverse trace)."""
    row = session.get(DocumentRow, document_id)
    if row is None:
        return None

    sec_rows = (
        session.query(SectionRow)
        .filter(SectionRow.document_id == document_id)
        .order_by(SectionRow.idx)
        .all()
    )
    sections = []
    for sr in sec_rows:
        par_rows = (
            session.query(ParagraphRow)
            .filter(ParagraphRow.section_id == sr.section_id)
            .order_by(ParagraphRow.start_char)
            .all()
        )
        paragraphs = []
        for pr in par_rows:
            sent_rows = (
                session.query(SentenceRow)
                .filter(SentenceRow.paragraph_id == pr.paragraph_id)
                .order_by(SentenceRow.start_char)
                .all()
            )
            paragraphs.append(
                Paragraph(
                    paragraph_id=pr.paragraph_id,
                    section_id=pr.section_id,
                    start_char=pr.start_char,
                    end_char=pr.end_char,
                    text=pr.text,
                    raw_text=pr.raw_text,
                    sentences=[
                        Sentence(
                            sentence_id=s.sentence_id,
                            section_id=s.section_id,
                            paragraph_id=s.paragraph_id,
                            start_char=s.start_char,
                            end_char=s.end_char,
                            text=s.text,
                        )
                        for s in sent_rows
                    ],
                )
            )
        sections.append(
            Section(
                section_id=sr.section_id,
                index=sr.idx,
                title=sr.title,
                start_char=sr.start_char,
                end_char=sr.end_char,
                text=sr.text,
                paragraphs=paragraphs,
            )
        )

    return Document(
        document_id=row.document_id,
        source_type=row.source_type,
        pipeline_version=row.pipeline_version,
        text=row.canonical_text,
        sections=sections,
        metadata=DocumentMetadata(
            pmid=row.pmid,
            pmcid=row.pmcid,
            doi=row.doi,
            title=row.title,
            journal=row.journal,
            publication_date=row.publication_date,
            authors=list(row.authors or []),
            publication_types=list(row.publication_types or []),
            source_type=row.source_type,
        ),
        checksums=dict(row.checksums or {}),
    )
