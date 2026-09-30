"""SQLAlchemy ORM tables + persist/load for canonical documents.

The tables mirror the canonical model 1:1 so the corpus and the DB stay in lock-step, and
every row carries ``run_id`` for reproducibility. Writes are idempotent: a re-ingest with
the same run replaces the document's structural rows rather than duplicating them.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from typing import TYPE_CHECKING

from ..canonical import (
    Document,
    DocumentMetadata,
    Paragraph,
    Section,
    Sentence,
)
from ..run import ExtractionRun

if TYPE_CHECKING:
    from ..affiliations import AffiliationExtraction
    from ..assessment import Assessment
    from ..claims import Claim
    from ..entities import EntityMention
    from ..funding import FundingRelationship
    from ..observations import Observation
    from ..openobs import OpenObservation
    from ..study import StudyCharacteristic


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


class EntityRow(Base):
    """One row per distinct normalized concept (Phase 2). Shared across documents."""

    __tablename__ = "entities"

    concept_id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    vocabulary: Mapped[str] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String, index=True)


class EntityMentionRow(Base):
    """One row per entity mention located in a document's canonical text (Phase 2).

    ``concept_id`` is nullable: an ``ambiguous``/``unmatched`` mention is kept with its exact
    span and no concept, so provenance is never dropped for a normalization miss.
    """

    __tablename__ = "entity_mentions"

    mention_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("documents.document_id"), index=True
    )
    # Indexed but *not* a hard FK: sentence rows are delete-then-inserted on every
    # ingest/normalize, while mentions are regenerated per analyze. sentence_id is a stable,
    # position-derived identifier (see ``ids.py``), so integrity holds by construction without
    # coupling the two lifecycles.
    sentence_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    concept_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("entities.concept_id"), nullable=True, index=True
    )
    surface_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str] = mapped_column(String)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    normalization_source: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class EntityModifierRow(Base):
    """A restrictive modifier of an entity mention (Phase 12): a text-derived concept→concept edge.

    Evidence is stored inline as JSON (mirrors :class:`ObservationQualifierRow`). ``value_concept_id``
    is nullable — an unmatched object is kept with its ``value_text`` surface and no concept, so a
    normalization miss never drops the edge.
    """

    __tablename__ = "entity_modifiers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    mention_id: Mapped[str] = mapped_column(
        String, ForeignKey("entity_mentions.mention_id"), index=True
    )
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    relation: Mapped[str] = mapped_column(String)
    preposition: Mapped[str] = mapped_column(String)
    value_concept_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    value_text: Mapped[str] = mapped_column(Text)
    value_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    rule_version: Mapped[str] = mapped_column(String)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


Index(
    "ix_entity_modifiers_lookup",
    EntityModifierRow.document_id,
    EntityModifierRow.relation,
    EntityModifierRow.value_concept_id,
)


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


def persist_entities(
    session: Session,
    document: Document,
    mentions: "list[EntityMention]",
    run: ExtractionRun,
) -> None:
    """Idempotently write a document's entity mentions and their concepts (Phase 2).

    Delete-then-insert per document (mirrors :func:`persist_document`): re-running ``analyze``
    replaces a document's mentions rather than duplicating them. Parents before children —
    the run and the shared concept rows are upserted (and flushed) before the mentions that
    FK to them.
    """
    from ..normalize import concept_by_id

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

    # Upsert the concepts referenced by this document's normalized mentions.
    for concept_id in sorted({m.concept_id for m in mentions if m.concept_id}):
        concept = concept_by_id(concept_id)
        if concept is None:
            continue
        session.merge(
            EntityRow(
                concept_id=concept.concept_id,
                canonical_name=concept.canonical_name,
                vocabulary=concept.vocabulary,
                entity_type=concept.entity_type,
            )
        )
    session.flush()

    # Register the Phase-12 modifier detector rules so every edge traces back to its rule.
    from ..modifiers import registry as modifier_registry

    for meta_rule in modifier_registry():
        session.merge(
            ExtractionRuleRow(
                rule_id=meta_rule["rule_id"],
                version=meta_rule["version"],
                predicate=meta_rule["predicate"],
                description=meta_rule["description"],
            )
        )

    # Replace this document's mentions (and their modifiers) so a re-analyze never leaves stale
    # spans behind. Delete children before parents so the mention FK is satisfied.
    session.execute(
        delete(EntityModifierRow).where(EntityModifierRow.document_id == document.document_id)
    )
    session.execute(
        delete(EntityMentionRow).where(EntityMentionRow.document_id == document.document_id)
    )
    session.flush()

    session.add_all(
        [
            EntityMentionRow(
                mention_id=m.mention_id,
                document_id=m.document_id,
                sentence_id=m.sentence_id,
                concept_id=m.concept_id,
                surface_text=m.surface_text,
                normalized_text=m.normalized_text,
                entity_type=m.entity_type,
                start_char=m.start_char,
                end_char=m.end_char,
                normalization_source=m.normalization_source,
                status=m.status,
                run_id=run.run_id,
            )
            for m in mentions
        ]
    )
    session.flush()  # mentions before the modifier rows that FK to them

    # Phase 12 — modifier rows (deterministic id: unique per mention + relation + value).
    session.add_all(
        [
            EntityModifierRow(
                id=f"{m.mention_id}:{mod.relation}:{mod.value_concept_id or mod.value_text}",
                mention_id=m.mention_id,
                document_id=m.document_id,
                relation=mod.relation,
                preposition=mod.preposition,
                value_concept_id=mod.value_concept_id,
                value_text=mod.value_text,
                value_type=mod.value_type,
                status=mod.status,
                rule_id=mod.rule_id,
                rule_version=mod.rule_version,
                evidence_refs=[_evidence_json(r) for r in mod.evidence_refs],
                run_id=run.run_id,
            )
            for m in mentions
            for mod in m.modifiers
        ]
    )


# =====================================================================================
# Phase 3 — relations → observations → claims
# =====================================================================================


class ExtractionRuleRow(Base):
    """Registry of the relation rules that fired (spec §7): id, version, description."""

    __tablename__ = "extraction_rules"

    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    predicate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text)


class ObservationRow(Base):
    """A low-level relation observation (the audit layer). Evidence is stored inline as JSON;
    an observation is always single-sentence so one sentence-level ref suffices."""

    __tablename__ = "observations"

    observation_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    sentence_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    subject_mention_id: Mapped[str] = mapped_column(String)
    subject_text: Mapped[str] = mapped_column(Text)
    subject_concept_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    predicate: Mapped[str] = mapped_column(String, index=True)
    object_mention_id: Mapped[str] = mapped_column(String)
    object_text: Mapped[str] = mapped_column(Text)
    object_concept_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    polarity: Mapped[str] = mapped_column(String)
    certainty: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    rule_version: Mapped[str] = mapped_column(String)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class ClaimRow(Base):
    """The normalized, concept-level claim (spec §7). Evidence lives in ``claim_evidence``."""

    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    subject_concept_id: Mapped[str] = mapped_column(String, index=True)
    subject_name: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(String, index=True)
    object_concept_id: Mapped[str] = mapped_column(String, index=True)
    object_name: Mapped[str] = mapped_column(Text)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    polarity: Mapped[str] = mapped_column(String)
    certainty: Mapped[str] = mapped_column(String)
    observation_ids: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class ClaimEvidenceRow(Base):
    """M:N claim ↔ evidence (spec §7): the union of a claim's observations' evidence refs."""

    __tablename__ = "claim_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_id: Mapped[str] = mapped_column(String, ForeignKey("claims.claim_id"), index=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    section_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    paragraph_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sentence_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    start_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quoted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    precision: Mapped[str] = mapped_column(String)
    extraction_rule: Mapped[str] = mapped_column(String)
    extraction_rule_version: Mapped[str] = mapped_column(String)
    extractor_version: Mapped[str] = mapped_column(String)


# =====================================================================================
# Phase 6 — typed qualifiers (the context model)
# =====================================================================================
#
# One row per qualifier on an observation/claim. Evidence is stored inline as JSON — consistent
# with ``ObservationRow`` (which also inlines its evidence), and sufficient because a qualifier's
# provenance is always one-or-more cue spans that we never join to in SQL. The composite index on
# ``(document_id, qualifier_type, value_concept_id)`` backs the ``disease_state`` claim filter.


class ObservationQualifierRow(Base):
    """A typed condition attached to an observation (Phase 6). Evidence inline as JSON."""

    __tablename__ = "observation_qualifiers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    observation_id: Mapped[str] = mapped_column(
        String, ForeignKey("observations.observation_id"), index=True
    )
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    qualifier_type: Mapped[str] = mapped_column(String)
    value_concept_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    value_text: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(String)
    rule_version: Mapped[str] = mapped_column(String)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class ClaimQualifierRow(Base):
    """A typed condition attached to a claim (Phase 6): the deduped union of its observations'."""

    __tablename__ = "claim_qualifiers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_id: Mapped[str] = mapped_column(String, ForeignKey("claims.claim_id"), index=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    qualifier_type: Mapped[str] = mapped_column(String)
    value_concept_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    value_text: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(String)
    rule_version: Mapped[str] = mapped_column(String)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class ClaimModifierRow(Base):
    """A restrictive endpoint modifier attached to a claim (Phase 12 / A2): the deduped union of its
    observations' subject/object modifiers. ``endpoint`` is ``subject`` or ``object``. Evidence inline
    as JSON, mirroring :class:`ClaimQualifierRow`. A *key-bearing* modifier is part of the claim key,
    so every row here shares its claim's forked ``claim_id``.
    """

    __tablename__ = "claim_modifiers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_id: Mapped[str] = mapped_column(String, ForeignKey("claims.claim_id"), index=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    endpoint: Mapped[str] = mapped_column(String)  # "subject" | "object"
    relation: Mapped[str] = mapped_column(String)
    preposition: Mapped[str] = mapped_column(String)
    value_concept_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    value_text: Mapped[str] = mapped_column(Text)
    value_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    rule_version: Mapped[str] = mapped_column(String)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


Index(
    "ix_obs_qualifiers_lookup",
    ObservationQualifierRow.document_id,
    ObservationQualifierRow.qualifier_type,
    ObservationQualifierRow.value_concept_id,
)
Index(
    "ix_claim_qualifiers_lookup",
    ClaimQualifierRow.document_id,
    ClaimQualifierRow.qualifier_type,
    ClaimQualifierRow.value_concept_id,
)
Index(
    "ix_claim_modifiers_lookup",
    ClaimModifierRow.document_id,
    ClaimModifierRow.relation,
    ClaimModifierRow.value_concept_id,
)


# =====================================================================================
# Phase 11 — open relation discovery (relation-bearing spans, kept out of claims)
# =====================================================================================
#
# A parallel, clearly-labelled record: a span a *model* flagged as asserting *some* relationship,
# recorded verbatim with an EXACT_SPAN ref (see ``openobs.py``). It has **no** foreign key into
# ``claims``/``claim_evidence`` and is **never** read by ``claims.py``/``synthesis.py`` — it cannot
# leak into the trusted pipeline. Evidence is stored inline as JSON (consistent with
# ``observations``). Tied to an ``ExtractionRun`` whose fingerprint includes the detector name +
# version, so re-running the same detector replaces a document's open observations and switching
# detectors forks the run.


class OpenObservationRow(Base):
    """A relation-bearing span flagged by a detector (Phase 11). Verbatim; no subject/predicate/object."""

    __tablename__ = "open_observations"

    open_observation_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    sentence_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    clause_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    detector_name: Mapped[str] = mapped_column(String, index=True)
    detector_version: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float, index=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


# =====================================================================================
# Phase 4 — study metadata, funding, affiliations
# =====================================================================================


class StudyCharacteristicRow(Base):
    __tablename__ = "study_characteristics"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # f"{document_id}:{field}"
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    field: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[str] = mapped_column(String, index=True)
    classification_source: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    evidence_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class FunderRow(Base):
    """One row per distinct funder (shared across documents), like ``entities``."""

    __tablename__ = "funders"

    funder_id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    funder_type: Mapped[str] = mapped_column(String, index=True)


class FundingRelationshipRow(Base):
    __tablename__ = "funding_relationships"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    funder: Mapped[str] = mapped_column(Text)
    funder_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("funders.funder_id"), nullable=True)
    funder_type: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str] = mapped_column(String)
    evidence_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class InstitutionRow(Base):
    __tablename__ = "institutions"

    institution_id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    institution_type: Mapped[str] = mapped_column(String, index=True)


class AuthorRow(Base):
    __tablename__ = "authors"

    author_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class AffiliationRow(Base):
    __tablename__ = "affiliations"

    affiliation_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    institution_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("institutions.institution_id"), nullable=True, index=True
    )
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


class AuthorAffiliationRow(Base):
    __tablename__ = "author_affiliations"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # author_id:affiliation_id
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    author_id: Mapped[str] = mapped_column(String, index=True)
    affiliation_id: Mapped[str] = mapped_column(String, index=True)


# =====================================================================================
# Phase 5 — assessment frameworks (kept separate from evidence)
# =====================================================================================


class AssessmentFrameworkRow(Base):
    __tablename__ = "assessment_frameworks"

    framework_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class AssessmentCriterionRow(Base):
    __tablename__ = "assessment_criteria"

    framework_id: Mapped[str] = mapped_column(String, primary_key=True)
    framework_version: Mapped[str] = mapped_column(String, primary_key=True)
    criterion: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(Text)


class AssessmentRow(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    framework_id: Mapped[str] = mapped_column(String, index=True)
    framework_version: Mapped[str] = mapped_column(String)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.document_id"), index=True)
    criterion: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[str] = mapped_column(String, index=True)
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("extraction_runs.run_id"))


# --- persistence helpers -------------------------------------------------------------


def _run_row(run: ExtractionRun) -> ExtractionRunRow:
    return ExtractionRunRow(
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


def _evidence_json(ref) -> dict:
    return ref.model_dump(mode="json")


def persist_relations(
    session: Session,
    document: Document,
    observations: "list[Observation]",
    claims: "list[Claim]",
    run: ExtractionRun,
) -> None:
    """Idempotently persist observations, claims, claim_evidence, and the rule registry.

    Delete-then-insert per document (mirrors :func:`persist_document`) so re-running ``extract``
    replaces a document's relations rather than duplicating them.
    """
    from ..clauses import registry as clause_registry
    from ..qualifiers import registry as qualifier_registry
    from ..relations import registry

    session.merge(_run_row(run))

    # Registry is small and shared across documents — upsert the whole thing each run. The relation
    # rules, the Phase-6/7 qualifier cue rules, and the Phase-7 clause-segmentation ruleset all live
    # in ``extraction_rules`` so every produced fact traces back to the exact rule that fired.
    for rule in registry():
        session.merge(
            ExtractionRuleRow(
                rule_id=rule.rule_id,
                version=rule.version,
                predicate=rule.predicate,
                description=rule.description,
            )
        )
    for meta_rule in (*qualifier_registry(), *clause_registry()):
        session.merge(
            ExtractionRuleRow(
                rule_id=meta_rule["rule_id"],
                version=meta_rule["version"],
                predicate=meta_rule["predicate"],
                description=meta_rule["description"],
            )
        )
    session.flush()

    # Delete-then-insert also covers the qualifier/modifier rows so a re-run replaces them (idempotent).
    for table in (
        ClaimModifierRow,
        ClaimQualifierRow,
        ObservationQualifierRow,
        ClaimEvidenceRow,
        ClaimRow,
        ObservationRow,
    ):
        session.execute(delete(table).where(table.document_id == document.document_id))
    session.flush()

    session.add_all(
        [
            ObservationRow(
                observation_id=o.observation_id,
                document_id=o.document_id,
                sentence_id=o.sentence_id,
                subject_mention_id=o.subject_mention_id,
                subject_text=o.subject_text,
                subject_concept_id=o.subject_concept_id,
                predicate=o.predicate,
                object_mention_id=o.object_mention_id,
                object_text=o.object_text,
                object_concept_id=o.object_concept_id,
                context=o.context,
                polarity=o.polarity,
                certainty=o.certainty,
                rule_id=o.rule_id,
                rule_version=o.rule_version,
                evidence_refs=[_evidence_json(r) for r in o.evidence_refs],
                run_id=run.run_id,
            )
            for o in observations
        ]
    )
    session.add_all(
        [
            ClaimRow(
                claim_id=c.claim_id,
                document_id=c.document_id,
                subject_concept_id=c.subject_concept_id,
                subject_name=c.subject_name,
                predicate=c.predicate,
                object_concept_id=c.object_concept_id,
                object_name=c.object_name,
                context=c.context,
                polarity=c.polarity,
                certainty=c.certainty,
                observation_ids=list(c.observation_ids),
                run_id=run.run_id,
            )
            for c in claims
        ]
    )
    session.flush()

    for c in claims:
        for ref in c.evidence_refs:
            row_id = f"{c.claim_id}:{ref.sentence_id or ''}:{ref.start_char}:{ref.end_char}:{ref.extraction_rule}"
            session.merge(
                ClaimEvidenceRow(
                    id=row_id,
                    claim_id=c.claim_id,
                    document_id=c.document_id,
                    section_id=ref.section_id,
                    paragraph_id=ref.paragraph_id,
                    sentence_id=ref.sentence_id,
                    start_char=ref.start_char,
                    end_char=ref.end_char,
                    quoted_text=ref.quoted_text,
                    precision=ref.precision.value if hasattr(ref.precision, "value") else ref.precision,
                    extraction_rule=ref.extraction_rule,
                    extraction_rule_version=ref.extraction_rule_version,
                    extractor_version=ref.extractor_version,
                )
            )

    # Phase 6 — qualifier rows (deterministic ids; a qualifier is unique per owner + identity).
    session.add_all(
        [
            ObservationQualifierRow(
                id=f"{o.observation_id}:{q.qualifier_type}:{q.value_concept_id or q.value_text}",
                observation_id=o.observation_id,
                document_id=o.document_id,
                qualifier_type=q.qualifier_type,
                value_concept_id=q.value_concept_id,
                value_text=q.value_text,
                rule_id=q.rule_id,
                rule_version=q.rule_version,
                evidence_refs=[_evidence_json(r) for r in q.evidence_refs],
                run_id=run.run_id,
            )
            for o in observations
            for q in o.qualifiers
        ]
    )
    session.add_all(
        [
            ClaimQualifierRow(
                id=f"{c.claim_id}:{q.qualifier_type}:{q.value_concept_id or q.value_text}",
                claim_id=c.claim_id,
                document_id=c.document_id,
                qualifier_type=q.qualifier_type,
                value_concept_id=q.value_concept_id,
                value_text=q.value_text,
                rule_id=q.rule_id,
                rule_version=q.rule_version,
                evidence_refs=[_evidence_json(r) for r in q.evidence_refs],
                run_id=run.run_id,
            )
            for c in claims
            for q in c.qualifiers
        ]
    )

    # Phase 12 (A2) — claim endpoint modifiers (deterministic id: unique per claim + endpoint +
    # relation + value).
    session.add_all(
        [
            ClaimModifierRow(
                id=f"{c.claim_id}:{endpoint}:{mod.relation}:{mod.value_concept_id or mod.value_text}",
                claim_id=c.claim_id,
                document_id=c.document_id,
                endpoint=endpoint,
                relation=mod.relation,
                preposition=mod.preposition,
                value_concept_id=mod.value_concept_id,
                value_text=mod.value_text,
                value_type=mod.value_type,
                status=mod.status,
                rule_id=mod.rule_id,
                rule_version=mod.rule_version,
                evidence_refs=[_evidence_json(r) for r in mod.evidence_refs],
                run_id=run.run_id,
            )
            for c in claims
            for endpoint, mods in (("subject", c.subject_modifiers), ("object", c.object_modifiers))
            for mod in mods
        ]
    )


def persist_open_observations(
    session: Session,
    document: Document,
    observations: "list[OpenObservation]",
    run: ExtractionRun,
) -> None:
    """Idempotently persist a document's open (relation-bearing) observations (Phase 11).

    Delete-then-insert per document (the same pattern as :func:`persist_relations`): the latest
    discovery run replaces a document's open observations rather than duplicating them, so re-running
    the *same* detector is idempotent. Switching detectors forks the :class:`ExtractionRun` (its
    fingerprint includes the detector name + version) so the current rows are attributable to the
    exact detector that produced them. This table is deliberately isolated — no join to claims — so
    nothing here can leak into the trusted pipeline.
    """
    session.merge(_run_row(run))
    session.flush()
    session.execute(
        delete(OpenObservationRow).where(OpenObservationRow.document_id == document.document_id)
    )
    session.flush()
    session.add_all(
        [
            OpenObservationRow(
                open_observation_id=o.open_observation_id,
                document_id=o.document_id,
                sentence_id=o.sentence_id,
                clause_index=o.clause_index,
                start_char=o.start_char,
                end_char=o.end_char,
                text=o.text,
                detector_name=o.detector_name,
                detector_version=o.detector_version,
                score=o.score,
                evidence_refs=[_evidence_json(r) for r in o.evidence_refs],
                run_id=run.run_id,
            )
            for o in observations
        ]
    )


def persist_study(
    session: Session, document: Document, characteristics: "list[StudyCharacteristic]", run: ExtractionRun
) -> None:
    """Idempotently persist study characteristics (delete-then-insert per document)."""
    session.merge(_run_row(run))
    session.flush()
    session.execute(
        delete(StudyCharacteristicRow).where(StudyCharacteristicRow.document_id == document.document_id)
    )
    session.flush()
    session.add_all(
        [
            StudyCharacteristicRow(
                id=f"{sc.document_id}:{sc.field}",
                document_id=sc.document_id,
                field=sc.field,
                value=sc.value,
                classification_source=sc.classification_source,
                rule_id=sc.rule_id,
                evidence_ref=_evidence_json(sc.evidence_ref),
                run_id=run.run_id,
            )
            for sc in characteristics
        ]
    )


def persist_funding(
    session: Session, document: Document, relationships: "list[FundingRelationship]", run: ExtractionRun
) -> None:
    """Idempotently persist funding relationships + their funders (delete-then-insert)."""
    session.merge(_run_row(run))
    for rel in relationships:
        if rel.funder_id:
            session.merge(
                FunderRow(funder_id=rel.funder_id, canonical_name=rel.funder, funder_type=rel.funder_type)
            )
    session.flush()
    session.execute(
        delete(FundingRelationshipRow).where(FundingRelationshipRow.document_id == document.document_id)
    )
    session.flush()
    session.add_all(
        [
            FundingRelationshipRow(
                id=f"{rel.document_id}:{i:03d}",
                document_id=rel.document_id,
                funder=rel.funder,
                funder_id=rel.funder_id,
                funder_type=rel.funder_type,
                source=rel.source,
                rule_id=rel.rule_id,
                evidence_ref=_evidence_json(rel.evidence_ref),
                run_id=run.run_id,
            )
            for i, rel in enumerate(relationships)
        ]
    )


def persist_affiliations(
    session: Session, document: Document, extraction: "AffiliationExtraction", run: ExtractionRun
) -> None:
    """Idempotently persist authors, affiliations, institutions, and their links."""
    session.merge(_run_row(run))
    for inst in extraction.institutions:
        session.merge(
            InstitutionRow(
                institution_id=inst.institution_id,
                canonical_name=inst.canonical_name,
                institution_type=inst.institution_type,
            )
        )
    session.flush()
    for table in (AuthorAffiliationRow, AffiliationRow, AuthorRow):
        session.execute(delete(table).where(table.document_id == document.document_id))
    session.flush()
    session.add_all(
        [
            AuthorRow(
                author_id=a.author_id,
                document_id=a.document_id,
                position=a.position,
                name=a.name,
                run_id=run.run_id,
            )
            for a in extraction.authors
        ]
    )
    session.add_all(
        [
            AffiliationRow(
                affiliation_id=aff.affiliation_id,
                document_id=aff.document_id,
                raw_text=aff.raw_text,
                institution_id=aff.institution_id,
                run_id=run.run_id,
            )
            for aff in extraction.affiliations
        ]
    )
    session.flush()
    session.add_all(
        [
            AuthorAffiliationRow(
                id=f"{aa.author_id}:{aa.affiliation_id}",
                document_id=aa.document_id,
                author_id=aa.author_id,
                affiliation_id=aa.affiliation_id,
            )
            for aa in extraction.author_affiliations
        ]
    )


def persist_assessments(
    session: Session, document_id: str, assessments: "list[Assessment]", run: ExtractionRun
) -> None:
    """Persist a framework's assessments, replacing only *that framework's* rows for the paper.

    This is the separation guarantee (spec §11): re-applying a framework touches only
    ``assessments`` for its ``framework_id`` — evidence, claim, and fact rows are never rewritten.
    """
    from ..assessment import CRITERIA, FRAMEWORK_ID, FRAMEWORK_NAME, FRAMEWORK_VERSION

    session.merge(_run_row(run))
    session.merge(AssessmentFrameworkRow(framework_id=FRAMEWORK_ID, version=FRAMEWORK_VERSION, name=FRAMEWORK_NAME))
    for crit in CRITERIA:
        session.merge(
            AssessmentCriterionRow(
                framework_id=FRAMEWORK_ID,
                framework_version=FRAMEWORK_VERSION,
                criterion=crit.criterion,
                description=crit.description,
            )
        )
    session.flush()

    frameworks = {(a.framework_id, a.framework_version) for a in assessments}
    for fid, fver in frameworks:
        session.execute(
            delete(AssessmentRow).where(
                AssessmentRow.document_id == document_id,
                AssessmentRow.framework_id == fid,
                AssessmentRow.framework_version == fver,
            )
        )
    session.flush()
    session.add_all(
        [
            AssessmentRow(
                id=f"{a.document_id}:{a.framework_id}:{a.framework_version}:{a.criterion}",
                framework_id=a.framework_id,
                framework_version=a.framework_version,
                document_id=a.document_id,
                criterion=a.criterion,
                value=a.value,
                rationale=a.rationale,
                evidence_refs=[_evidence_json(r) for r in a.evidence_refs],
                run_id=run.run_id,
            )
            for a in assessments
        ]
    )


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
