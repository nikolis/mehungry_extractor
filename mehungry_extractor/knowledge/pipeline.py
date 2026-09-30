"""The full deterministic knowledge pass: entities → relations/claims → facts → assessments.

``extract_document`` is what ``mehungry extract`` runs. It loads the canonical document and its
cached raw sources (offline — never the network), then, in order:

1. Phase 2 — entity mentions (:mod:`.entities`).
2. Phase 3 — observations (:mod:`.observations`) and normalized claims (:mod:`.claims`).
3. Phase 4 — study characteristics (:mod:`.study`), funding (:mod:`.funding`), affiliations
   (:mod:`.affiliations`).
4. Phase 5 — a ``mehungry_evidence_v1`` assessment (:mod:`.assessment`) derived purely from the
   Phase-4 facts, written to the separate ``assessments`` table.

Everything is persisted under one deterministic :class:`~.run.ExtractionRun` via the
delete-then-insert helpers, so the whole pass is idempotent and independently rerunnable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from . import affiliations as _affiliations
from . import claims as _claims
from . import entities as _entities
from . import funding as _funding
from . import jats, pubmed
from . import modifiers as _modifiers
from . import observations as _observations
from . import study as _study
from .assessment import PaperFacts, assess
from .vocab import (
    COUNTRIES_VERSION,
    DISEASE_STATES_VERSION,
    FUNDERS_VERSION,
    INSTITUTIONS_VERSION,
    VOCAB_VERSION,
)

if TYPE_CHECKING:
    from .canonical import Document


class ExtractionSummary(BaseModel):
    """What ``extract`` produced for one document (counts + the drop count, printed by the CLI)."""

    document_id: str
    mentions: int
    observations: int
    claims: int
    qualifiers: int  # distinct disease-state qualifiers across the document's claims (Phase 6)
    dropped_observations: int
    study_characteristics: int
    funding_relationships: int
    authors: int
    assessments: int


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _build_facts(
    document: "Document",
    characteristics: list,
    funding_rels: list,
) -> PaperFacts:
    """Assemble the Phase-4 facts the assessment framework reads. Pure."""
    by_field = {sc.field: sc.value for sc in characteristics}
    design = by_field.get("study_design")
    if design == _study.DESIGN_UNKNOWN:
        design = None

    if funding_rels:
        industry = any(ft in _assessment_industry() for ft in (r.funder_type for r in funding_rels))
    else:
        industry = None  # no funding info at all — never assume independence

    return PaperFacts(
        document_id=document.document_id,
        study_design=design,
        publication_year=_int(by_field.get("publication_year")),
        sample_size=_int(by_field.get("sample_size")),
        industry_funding=industry,
        funder_types=sorted({r.funder_type for r in funding_rels}),
    )


def _assessment_industry():
    from .assessment import INDUSTRY_FUNDER_TYPES

    return INDUSTRY_FUNDER_TYPES


def discover_open_relations(
    pmid: str | int,
    *,
    corpus=None,
    engine=None,
    persist: bool = True,
    detector=None,
) -> "tuple[Document, list]":
    """Phase 11 — run open-relation *discovery* over one paper. Standalone, opt-in, offline.

    Assembles the canonical :class:`Document` (reusing the corpus-first-then-DB load path) and calls
    :func:`openrel.extract_open_observations`, which flags the relation-bearing spans with a
    pluggable, non-deterministic detector. This does **not** run inside :func:`extract_document`:
    discovery is orthogonal to the trusted pass and must never slow or contaminate normal
    extraction. The results are persisted to their own ``open_observations`` table under an
    :class:`ExtractionRun` whose fingerprint includes the **detector name + version** (re-running the
    same detector is idempotent; switching detectors forks the run, and the latest discovery run
    replaces the document's spans), and they are never read by claims/synthesis.

    Returns ``(document, open_observations)``. With ``detector=None`` the bundled default is used and
    :class:`~.openrel.DetectorUnavailableError` is raised if the optional ``[openrel]`` extra is
    absent — discovery is a model feature with no model-free floor.
    """
    from . import openrel as _openrel
    from .corpus import CorpusStore
    from .db import get_engine, init_db, persist_document, persist_open_observations, session_scope
    from .db.schema import DocumentRow
    from .ids import document_id, normalize_pmid
    from .run import build_run

    pmid = normalize_pmid(pmid)
    corpus = corpus or CorpusStore()

    if corpus.has_canonical(pmid):
        document = corpus.read_canonical(pmid)
    else:
        from .query import get_document

        if engine is None:
            engine = get_engine()
            init_db(engine)
        document = get_document(engine, pmid)
        if document is None:
            raise FileNotFoundError(
                f"no canonical document for {document_id(pmid)}; run "
                f"`mehungry ingest --pmid {pmid}` first"
            )

    observations = _openrel.extract_open_observations(document, detector=detector)

    if persist:
        if engine is None:
            engine = get_engine()
            init_db(engine)
        det_name = observations[0].detector_name if observations else _detector_identity(detector)[0]
        det_version = observations[0].detector_version if observations else _detector_identity(detector)[1]
        run = build_run([document.document_id], extra_fingerprint=[det_name, det_version])
        run.ontology_versions = {det_name: det_version}
        with session_scope(engine) as session:
            if session.get(DocumentRow, document.document_id) is None:
                persist_document(session, document, run)
            persist_open_observations(session, document, observations, run)

    return document, observations


def _detector_identity(detector) -> tuple[str, str]:
    """The ``(name, version)`` of the detector that will run — the passed one, else the default.

    Used to fingerprint the run even when the detector flags *no* spans (an empty result still
    belongs to a specific detector, so re-runs stay idempotent and detector switches still fork)."""
    from . import openrel as _openrel

    det = detector or _openrel._default_detector()
    return det.name, det.version


def _ontology_versions(use_model: bool) -> dict:
    versions = {
        "mehungry_curated": VOCAB_VERSION,
        "mehungry_funders": FUNDERS_VERSION,
        "mehungry_institutions": INSTITUTIONS_VERSION,
        "mehungry_countries": COUNTRIES_VERSION,
        "mehungry_disease_states": DISEASE_STATES_VERSION,
    }
    if use_model and _entities.model_available():
        versions[_entities._SCISPACY_MODEL] = _entities._model_version()
    return versions


def extract_document(
    pmid: str | int,
    *,
    corpus=None,
    engine=None,
    persist: bool = True,
    use_model: bool = True,
) -> ExtractionSummary:
    """Run the full knowledge pass for one document. Offline, deterministic, idempotent."""
    from .corpus import CorpusStore
    from .db import (
        get_engine,
        init_db,
        persist_affiliations,
        persist_assessments,
        persist_document,
        persist_entities,
        persist_funding,
        persist_relations,
        persist_study,
        session_scope,
    )
    from .db.schema import DocumentRow
    from .ids import document_id, normalize_pmid
    from .run import build_run

    pmid = normalize_pmid(pmid)
    corpus = corpus or CorpusStore()

    # 1) canonical document — corpus first, then DB (mirrors entities.analyze_document).
    if corpus.has_canonical(pmid):
        document = corpus.read_canonical(pmid)
    else:
        from .query import get_document

        if engine is None:
            engine = get_engine()
            init_db(engine)
        document = get_document(engine, pmid)
        if document is None:
            raise FileNotFoundError(
                f"no canonical document for {document_id(pmid)}; run "
                f"`mehungry ingest --pmid {pmid}` first"
            )

    # Raw sources drive funding (JATS front/back matter) + affiliations (PubMed author list).
    pubmed_xml = pmc_xml = None
    if corpus.has_raw(pmid):
        raw_files = corpus.read_raw(pmid)
        pubmed_xml = raw_files.get("pubmed.xml")
        pmc_xml = raw_files.get("pmc.xml")
    rec = pubmed.parse(pubmed_xml) if pubmed_xml else pubmed.PubMedRecord()

    # 2) entities → observations → claims
    mentions = _entities.extract(document, use_model=use_model)
    _modifiers.annotate(document, mentions, use_model=use_model)  # Phase 12 — restrictive modifiers
    observations = _observations.extract(document, mentions, use_model=use_model)
    claim_result = _claims.normalize(observations)
    claims = claim_result.claims

    # 3) study / funding / affiliations
    characteristics = _study.classify(document, affiliations=rec.affiliations)
    funding_stmts = []
    if pmc_xml:
        funding_stmts = jats.parse_funding(pmc_xml) + jats.parse_back_matter(pmc_xml)
    funding_rels = _funding.extract(document.document_id, funding_stmts)
    aff_extraction = _affiliations.extract(document.document_id, pubmed_xml)

    # 4) assessment (derived purely from the Phase-4 facts)
    facts = _build_facts(document, characteristics, funding_rels)
    assessments = assess(facts)

    if persist:
        if engine is None:
            engine = get_engine()
            init_db(engine)
        run = build_run([document.document_id])
        run.ontology_versions = _ontology_versions(use_model)
        with session_scope(engine) as session:
            if session.get(DocumentRow, document.document_id) is None:
                persist_document(session, document, run)
            persist_entities(session, document, mentions, run)
            persist_relations(session, document, observations, claims, run)
            persist_study(session, document, characteristics, run)
            persist_funding(session, document, funding_rels, run)
            persist_affiliations(session, document, aff_extraction, run)
            persist_assessments(session, document.document_id, assessments, run)

    return ExtractionSummary(
        document_id=document.document_id,
        mentions=len(mentions),
        observations=len(observations),
        claims=len(claims),
        qualifiers=sum(len(c.qualifiers) for c in claims),
        dropped_observations=claim_result.dropped,
        study_characteristics=len(characteristics),
        funding_relationships=len(funding_rels),
        authors=len(aff_extraction.authors),
        assessments=len(assessments),
    )
