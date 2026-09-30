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
from .db.schema import (
    AssessmentRow,
    ClaimEvidenceRow,
    ClaimModifierRow,
    ClaimQualifierRow,
    ClaimRow,
    EntityMentionRow,
    EntityModifierRow,
    ExtractionRunRow,
    FundingRelationshipRow,
    ObservationQualifierRow,
    ObservationRow,
    OpenObservationRow,
    SentenceRow,
    StudyCharacteristicRow,
)
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


def list_entities_for_document(engine: Engine, doc_or_pmid: str) -> list[EntityMentionRow]:
    """All entity mentions in a document, ordered by span (Phase 2).

    Includes unmatched/ambiguous mentions — provenance is never hidden by a normalization
    miss. Ordering is by ``(start_char, end_char, entity_type)`` for deterministic output.
    """
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        return (
            session.query(EntityMentionRow)
            .filter(EntityMentionRow.document_id == doc_id)
            .order_by(
                EntityMentionRow.start_char,
                EntityMentionRow.end_char,
                EntityMentionRow.entity_type,
            )
            .all()
        )


def find_mentions(engine: Engine, *, concept_id: str) -> list[EntityMentionRow]:
    """All mentions of a given normalized concept, across documents (Phase 2).

    Ordered by ``(document_id, start_char)`` so the same concept's evidence is grouped by
    paper and reads in document order.
    """
    with session_scope(engine) as session:
        return (
            session.query(EntityMentionRow)
            .filter(EntityMentionRow.concept_id == concept_id)
            .order_by(EntityMentionRow.document_id, EntityMentionRow.start_char)
            .all()
        )


# --- Phase 3/4/5 filtering + provenance API (spec §14–§15) ---
#
# All filters are exact and deterministic (no fuzzy/semantic search). String endpoint filters
# (``subject``/``condition``) match either a concept id or a canonical name, case-insensitively,
# so a caller can pass ``"DIS:crohn_disease"`` or ``"Crohn disease"`` interchangeably.


def _matches_endpoint(concept_id: str, name: str, value: str) -> bool:
    v = value.casefold()
    return concept_id.casefold() == v or name.casefold() == v


def _funding_index(session) -> dict[str, set[str]]:
    """document_id → set of funder_types recorded for it (empty set means no funding info)."""
    index: dict[str, set[str]] = {}
    for row in session.query(FundingRelationshipRow).all():
        index.setdefault(row.document_id, set()).add(row.funder_type)
    return index


def _is_independent(funder_types: "set[str] | None") -> bool:
    """A paper is independent iff it has funding info and none of it is industry (spec §14)."""
    from .assessment import INDUSTRY_FUNDER_TYPES

    if not funder_types:  # None or empty → no funding info → not independent
        return False
    return not (funder_types & INDUSTRY_FUNDER_TYPES)


def _qualifier_dict(r: ClaimQualifierRow) -> dict:
    return {
        "qualifier_type": r.qualifier_type,
        "value_concept_id": r.value_concept_id,
        "value_text": r.value_text,
        "rule_id": r.rule_id,
        "rule_version": r.rule_version,
        "evidence_refs": list(r.evidence_refs or []),
    }


def _claim_qualifiers_index(session, claim_ids: "list[str]") -> dict[str, list[dict]]:
    """claim_id → its qualifier dicts (Phase 6). Batched to avoid an N+1 over the claim set."""
    index: dict[str, list[dict]] = {}
    if not claim_ids:
        return index
    rows = (
        session.query(ClaimQualifierRow)
        .filter(ClaimQualifierRow.claim_id.in_(list(claim_ids)))
        .order_by(ClaimQualifierRow.id)
        .all()
    )
    for r in rows:
        index.setdefault(r.claim_id, []).append(_qualifier_dict(r))
    return index


def _claim_modifier_dict(r: ClaimModifierRow) -> dict:
    return {
        "endpoint": r.endpoint,
        "relation": r.relation,
        "preposition": r.preposition,
        "value_concept_id": r.value_concept_id,
        "value_text": r.value_text,
        "value_type": r.value_type,
        "status": r.status,
    }


def _claim_modifiers_index(session, claim_ids: "list[str]") -> dict[str, dict[str, list[dict]]]:
    """claim_id → {"subject": [...], "object": [...]} modifier dicts (Phase 12 / A2). Batched."""
    index: dict[str, dict[str, list[dict]]] = {}
    if not claim_ids:
        return index
    rows = (
        session.query(ClaimModifierRow)
        .filter(ClaimModifierRow.claim_id.in_(list(claim_ids)))
        .order_by(ClaimModifierRow.id)
        .all()
    )
    for r in rows:
        bucket = index.setdefault(r.claim_id, {"subject": [], "object": []})
        bucket.setdefault(r.endpoint, []).append(_claim_modifier_dict(r))
    return index


def _claim_dict(
    c: ClaimRow,
    qualifiers: "Optional[list[dict]]" = None,
    modifiers: "Optional[dict[str, list[dict]]]" = None,
) -> dict:
    modifiers = modifiers or {}
    return {
        "claim_id": c.claim_id,
        "document_id": c.document_id,
        "subject_concept_id": c.subject_concept_id,
        "subject_name": c.subject_name,
        "predicate": c.predicate,
        "object_concept_id": c.object_concept_id,
        "object_name": c.object_name,
        "polarity": c.polarity,
        "certainty": c.certainty,
        "context": c.context,
        "qualifiers": qualifiers or [],
        # Phase 12 (A2) — restrictive modifiers per endpoint; empty lists when the claim has none.
        "subject_modifiers": modifiers.get("subject", []),
        "object_modifiers": modifiers.get("object", []),
    }


def _matches_disease_state(qualifiers: "list[dict]", value: str) -> bool:
    """True iff a claim carries a disease_state qualifier matching ``value`` (concept id or surface)."""
    v = value.casefold()
    for q in qualifiers:
        if q["qualifier_type"] != "disease_state":
            continue
        cid = (q.get("value_concept_id") or "").casefold()
        text = (q.get("value_text") or "").casefold()
        if v in (cid, text):
            return True
    return False


def find_claims(
    engine: Engine,
    *,
    subject: Optional[str] = None,
    condition: Optional[str] = None,
    predicate: Optional[str] = None,
    polarity: Optional[str] = None,
    disease_state: Optional[str] = None,
    funder_type: Optional[str] = None,
    require_independent_funding: bool = False,
) -> list[dict]:
    """Deterministically filtered claims (spec §14). Returns claim dicts, ordered by claim_id.

    ``subject`` matches the subject endpoint; ``condition`` matches *either* endpoint (a condition
    can be subject or object). ``disease_state`` (Phase 6) keeps only claims carrying a
    ``disease_state`` qualifier whose concept id or surface matches the value (case-insensitive).
    ``funder_type``/``require_independent_funding`` filter on the claim's paper's Phase-4 funding
    facts; ``unknown``/absent funding is never treated as independent.
    """
    with session_scope(engine) as session:
        q = session.query(ClaimRow)
        if predicate is not None:
            q = q.filter(ClaimRow.predicate == predicate)
        if polarity is not None:
            q = q.filter(ClaimRow.polarity == polarity)
        rows = q.order_by(ClaimRow.claim_id).all()

        funding = _funding_index(session) if (funder_type or require_independent_funding) else {}
        qualifiers = _claim_qualifiers_index(session, [c.claim_id for c in rows])
        modifiers = _claim_modifiers_index(session, [c.claim_id for c in rows])

        out: list[dict] = []
        for c in rows:
            if subject is not None and not _matches_endpoint(c.subject_concept_id, c.subject_name, subject):
                continue
            if condition is not None and not (
                _matches_endpoint(c.subject_concept_id, c.subject_name, condition)
                or _matches_endpoint(c.object_concept_id, c.object_name, condition)
            ):
                continue
            claim_quals = qualifiers.get(c.claim_id, [])
            if disease_state is not None and not _matches_disease_state(claim_quals, disease_state):
                continue
            if funder_type is not None and funder_type not in funding.get(c.document_id, set()):
                continue
            if require_independent_funding and not _is_independent(funding.get(c.document_id)):
                continue
            out.append(_claim_dict(c, claim_quals, modifiers.get(c.claim_id)))
        return out


def list_claims_for_document(engine: Engine, doc_or_pmid: str) -> list[dict]:
    """All claims for one document, ordered by claim_id."""
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        rows = (
            session.query(ClaimRow)
            .filter(ClaimRow.document_id == doc_id)
            .order_by(ClaimRow.claim_id)
            .all()
        )
        qualifiers = _claim_qualifiers_index(session, [c.claim_id for c in rows])
        modifiers = _claim_modifiers_index(session, [c.claim_id for c in rows])
        return [
            _claim_dict(c, qualifiers.get(c.claim_id, []), modifiers.get(c.claim_id))
            for c in rows
        ]


def _observation_qualifier_dict(r: ObservationQualifierRow) -> dict:
    return {
        "qualifier_type": r.qualifier_type,
        "value_concept_id": r.value_concept_id,
        "value_text": r.value_text,
    }


def _observation_qualifiers_index(session, observation_ids: "list[str]") -> dict[str, list[dict]]:
    """observation_id → its qualifier dicts (Phase 6). Batched to avoid an N+1 over observations."""
    index: dict[str, list[dict]] = {}
    if not observation_ids:
        return index
    rows = (
        session.query(ObservationQualifierRow)
        .filter(ObservationQualifierRow.observation_id.in_(list(observation_ids)))
        .order_by(ObservationQualifierRow.id)
        .all()
    )
    for r in rows:
        index.setdefault(r.observation_id, []).append(_observation_qualifier_dict(r))
    return index


def _modifier_dict(r: EntityModifierRow) -> dict:
    """One entity modifier as a display/API dict (Phase 12)."""
    return {
        "relation": r.relation,
        "preposition": r.preposition,
        "value_concept_id": r.value_concept_id,
        "value_text": r.value_text,
        "value_type": r.value_type,
        "status": r.status,
    }


def _modifiers_index(session, mention_ids: "list[str]") -> dict[str, list[dict]]:
    """mention_id → its restrictive-modifier dicts (Phase 12). Batched to avoid an N+1."""
    index: dict[str, list[dict]] = {}
    ids = [mid for mid in mention_ids if mid]
    if not ids:
        return index
    rows = (
        session.query(EntityModifierRow)
        .filter(EntityModifierRow.mention_id.in_(ids))
        .order_by(EntityModifierRow.id)
        .all()
    )
    for r in rows:
        index.setdefault(r.mention_id, []).append(_modifier_dict(r))
    return index


def list_modifiers_for_document(engine: Engine, doc_or_pmid: str) -> dict[str, list[dict]]:
    """mention_id → restrictive modifiers, for every modified mention in a document (Phase 12)."""
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        rows = (
            session.query(EntityModifierRow)
            .filter(EntityModifierRow.document_id == doc_id)
            .order_by(EntityModifierRow.id)
            .all()
        )
        index: dict[str, list[dict]] = {}
        for r in rows:
            index.setdefault(r.mention_id, []).append(_modifier_dict(r))
        return index


def list_observations_for_document(engine: Engine, doc_or_pmid: str) -> list[dict]:
    """All low-level observations for one document, ordered by observation_id.

    Each dict carries the full observation record — endpoints (surface text + concept id),
    predicate, modality (polarity/certainty), the clause-scoped ``qualifiers``, the rule that
    fired, and the inline ``evidence_refs`` (sentence-level source spans). Observations whose
    endpoints did not normalize are included (``*_concept_id`` is ``None``) — the audit layer is
    never pruned for a normalization miss.
    """
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        rows = (
            session.query(ObservationRow)
            .filter(ObservationRow.document_id == doc_id)
            .order_by(ObservationRow.observation_id)
            .all()
        )
        quals = _observation_qualifiers_index(session, [o.observation_id for o in rows])
        mods = _modifiers_index(
            session,
            [o.subject_mention_id for o in rows] + [o.object_mention_id for o in rows],
        )
        return [
            {
                "observation_id": o.observation_id,
                "document_id": o.document_id,
                "sentence_id": o.sentence_id,
                "subject_mention_id": o.subject_mention_id,
                "subject_text": o.subject_text,
                "subject_concept_id": o.subject_concept_id,
                # Phase 12 — restrictive modifiers on each endpoint (e.g. Dysbiosis
                # [localized_in: gut microbiome]); empty list when the head has none.
                "subject_modifiers": mods.get(o.subject_mention_id, []),
                "predicate": o.predicate,
                "object_mention_id": o.object_mention_id,
                "object_text": o.object_text,
                "object_concept_id": o.object_concept_id,
                "object_modifiers": mods.get(o.object_mention_id, []),
                "context": o.context,
                "polarity": o.polarity,
                "certainty": o.certainty,
                "rule_id": o.rule_id,
                "rule_version": o.rule_version,
                "evidence_refs": list(o.evidence_refs or []),
                "qualifiers": quals.get(o.observation_id, []),
            }
            for o in rows
        ]


def list_open_observations_for_document(engine: Engine, doc_or_pmid: str) -> list[dict]:
    """All open (relation-bearing) observations for one document, most-confident first (Phase 11).

    Ordered by descending ``score`` then start offset — the review order (a human reads the spans
    the detector is most confident assert a relationship, in the paper's own words). Each dict is the
    full record: the verbatim ``text``, its exact span, the detector name/version + score, and the
    single ``EXACT_SPAN`` evidence ref. This is the read side of the Phase-11 sandbox; it never
    touches claims/synthesis.
    """
    doc_id = doc_or_pmid if doc_or_pmid.startswith("pmid_") else document_id(doc_or_pmid)
    with session_scope(engine) as session:
        rows = (
            session.query(OpenObservationRow)
            .filter(OpenObservationRow.document_id == doc_id)
            .order_by(OpenObservationRow.score.desc(), OpenObservationRow.start_char)
            .all()
        )
        return [
            {
                "open_observation_id": o.open_observation_id,
                "document_id": o.document_id,
                "sentence_id": o.sentence_id,
                "clause_index": o.clause_index,
                "start_char": o.start_char,
                "end_char": o.end_char,
                "text": o.text,
                "detector_name": o.detector_name,
                "detector_version": o.detector_version,
                "score": o.score,
                "evidence_refs": list(o.evidence_refs or []),
            }
            for o in rows
        ]


def find_evidence(engine: Engine, claim_id: str) -> list[dict]:
    """Evidence refs backing a claim (spec §15), ordered by span. Verifies each reconstructs."""
    with session_scope(engine) as session:
        rows = (
            session.query(ClaimEvidenceRow)
            .filter(ClaimEvidenceRow.claim_id == claim_id)
            .order_by(ClaimEvidenceRow.start_char, ClaimEvidenceRow.sentence_id, ClaimEvidenceRow.extraction_rule)
            .all()
        )
        doc_text: dict[str, str] = {}
        out: list[dict] = []
        for r in rows:
            reconstructed = None
            if r.start_char is not None and r.end_char is not None:
                if r.document_id not in doc_text:
                    drow = session.get(DocumentRow, r.document_id)
                    doc_text[r.document_id] = drow.canonical_text if drow else ""
                reconstructed = doc_text[r.document_id][r.start_char : r.end_char]
            out.append(
                {
                    "claim_id": r.claim_id,
                    "document_id": r.document_id,
                    "section_id": r.section_id,
                    "paragraph_id": r.paragraph_id,
                    "sentence_id": r.sentence_id,
                    "start_char": r.start_char,
                    "end_char": r.end_char,
                    "quoted_text": r.quoted_text,
                    "reconstructed_text": reconstructed,
                    "precision": r.precision,
                    "extraction_rule": r.extraction_rule,
                    "extraction_rule_version": r.extraction_rule_version,
                    "extractor_version": r.extractor_version,
                }
            )
        return out


def find_papers(
    engine: Engine,
    *,
    study_design: Optional[str] = None,
    publication_year_from: Optional[int] = None,
    funder_type: Optional[str] = None,
    require_independent_funding: bool = False,
) -> list[dict]:
    """Papers matching deterministic study-design/year/funding filters (spec §14).

    Returns dicts of ``document_id`` + the paper's study characteristics, ordered by document_id.
    """
    with session_scope(engine) as session:
        chars: dict[str, dict[str, str]] = {}
        for sc in session.query(StudyCharacteristicRow).all():
            chars.setdefault(sc.document_id, {})[sc.field] = sc.value

        funding = _funding_index(session) if (funder_type or require_independent_funding) else {}

        out: list[dict] = []
        for doc_id in sorted(chars):
            fields = chars[doc_id]
            if study_design is not None and fields.get("study_design") != study_design:
                continue
            if publication_year_from is not None:
                year = fields.get("publication_year")
                try:
                    if year is None or int(year) < publication_year_from:
                        continue
                except ValueError:
                    continue
            if funder_type is not None and funder_type not in funding.get(doc_id, set()):
                continue
            if require_independent_funding and not _is_independent(funding.get(doc_id)):
                continue
            out.append({"document_id": doc_id, **fields})
        return out


def latest_run_id(engine: Engine) -> Optional[str]:
    """The most recent extraction run's id (by timestamp, id as tiebreak), or ``None`` if empty."""
    with session_scope(engine) as session:
        row = (
            session.query(ExtractionRunRow)
            .order_by(ExtractionRunRow.timestamp.desc(), ExtractionRunRow.run_id.desc())
            .first()
        )
        return row.run_id if row else None


def relation_quality_report(engine: Engine, run_id: Optional[str] = None) -> Optional[dict]:
    """Relation-layer precision probe from self-loops (see :mod:`.metrics`).

    With ``run_id`` given, scopes to that one extraction run (the per-run lens for a CI regression
    guard). With ``run_id`` omitted, aggregates the **whole store** across all runs — the corpus
    lens, which is what the current one-document-per-run ingestion makes useful. In either case it
    folds claims + observations into a :class:`~.metrics.RelationQuality`, supplying the
    per-observation connecting-text geometry (for the mechanism buckets) and the ``p_alias``
    detectability from the mentions. Returns the metric as a JSON-friendly dict, or ``None`` if
    the store (or the named run) has no data.

    Caveat: aggregating across runs mixes their ``ruleset_version``s; when comparing runs for a
    regression, pass an explicit ``run_id`` so the rate is attributable to one ruleset.
    """
    from .metrics import ObsGeometry, aliasing_probability, relation_quality

    with session_scope(engine) as session:
        claim_q = session.query(ClaimRow)
        obs_q = session.query(ObservationRow)
        mention_q = session.query(EntityMentionRow)
        if run_id is not None:
            claim_q = claim_q.filter(ClaimRow.run_id == run_id)
            obs_q = obs_q.filter(ObservationRow.run_id == run_id)
            mention_q = mention_q.filter(EntityMentionRow.run_id == run_id)
        claims = claim_q.all()
        observations = obs_q.all()
        mentions = mention_q.all()
        if not claims and not observations:
            return None

        mention_by_id = {m.mention_id: m for m in mentions}
        doc_text: dict[str, str] = {}

        def _text(document_id_: str) -> str:
            if document_id_ not in doc_text:
                drow = session.get(DocumentRow, document_id_)
                doc_text[document_id_] = drow.canonical_text if drow else ""
            return doc_text[document_id_]

        # Connecting-text geometry per observation: recover both endpoint spans via mention_id and
        # slice the canonical text between them (the same window the relation layer matched cues in).
        geometry: dict[str, ObsGeometry] = {}
        for o in observations:
            subj = mention_by_id.get(o.subject_mention_id)
            obj = mention_by_id.get(o.object_mention_id)
            if subj is None or obj is None:
                continue
            distance = obj.start_char - subj.end_char
            connecting = _text(o.document_id)[subj.end_char : obj.start_char]
            geometry[o.observation_id] = ObsGeometry(distance=distance, connecting_text=connecting)

        p_alias = aliasing_probability(mentions)

    return relation_quality(
        claims, observations, run_id=run_id, geometry=geometry, p_alias=p_alias
    ).to_dict()


def gold_eval_report(engine: Engine, gold_path: Optional[str] = None) -> dict:
    """Score the pipeline's produced observations against the hand-labeled gold set (:mod:`.goldeval`).

    The labeled counterpart to :func:`relation_quality_report`: where that estimates a precision
    floor with no labels and cannot see recall, this measures recall, true precision, and a
    failure-mode histogram against ``tests/gold/observations.json`` (or ``gold_path``). Assembles
    the produced side from the store via :func:`list_observations_for_document` — regrouped by
    ``sentence_id`` and scoped to the labeled sentences — then folds it in :func:`.goldeval.score`.
    Returns the metric as a JSON-friendly dict. Deterministic and read-only.
    """
    from .goldeval import load_gold, score

    gold = load_gold(gold_path)
    produced_by_sentence: dict[str, list[dict]] = {}
    pmids = sorted({s["pmid"] for s in gold["sentences"]})
    for pmid in pmids:
        for o in list_observations_for_document(engine, pmid):
            produced_by_sentence.setdefault(o["sentence_id"], []).append(o)
    return score(gold["sentences"], produced_by_sentence).to_dict()


def explain_claim(engine: Engine, claim_id: str) -> Optional[dict]:
    """Assemble the full provenance object for a claim (spec §15).

    ``Claim → EvidenceRef[] → Document → Section/Paragraph/Sentence → exact phrase → rule →
    extractor/pipeline version → ExtractionRun``, plus the paper's study/funding facts and
    assessments. Every quoted phrase is re-sliced from the canonical text so the chain is
    self-verifying. Returns ``None`` if the claim id is unknown.
    """
    with session_scope(engine) as session:
        claim = session.get(ClaimRow, claim_id)
        if claim is None:
            return None
        claim_qualifiers = _claim_qualifiers_index(session, [claim_id]).get(claim_id, [])
        claim_modifiers = _claim_modifiers_index(session, [claim_id]).get(claim_id)
        drow = session.get(DocumentRow, claim.document_id)
        run = session.get(ExtractionRunRow, claim.run_id) if claim.run_id else None

        study = {
            sc.field: {"value": sc.value, "source": sc.classification_source, "rule_id": sc.rule_id}
            for sc in session.query(StudyCharacteristicRow)
            .filter(StudyCharacteristicRow.document_id == claim.document_id)
            .all()
        }
        funding = [
            {"funder": f.funder, "funder_type": f.funder_type, "source": f.source}
            for f in session.query(FundingRelationshipRow)
            .filter(FundingRelationshipRow.document_id == claim.document_id)
            .order_by(FundingRelationshipRow.id)
            .all()
        ]
        assessments = [
            {"framework_id": a.framework_id, "framework_version": a.framework_version,
             "criterion": a.criterion, "value": a.value, "rationale": a.rationale}
            for a in session.query(AssessmentRow)
            .filter(AssessmentRow.document_id == claim.document_id)
            .order_by(AssessmentRow.criterion)
            .all()
        ]
        observations = [
            {"observation_id": o.observation_id, "predicate": o.predicate, "polarity": o.polarity,
             "certainty": o.certainty, "context": o.context, "rule_id": o.rule_id,
             "sentence_id": o.sentence_id, "subject_text": o.subject_text, "object_text": o.object_text}
            for o in session.query(ObservationRow)
            .filter(ObservationRow.observation_id.in_(list(claim.observation_ids or [])))
            .order_by(ObservationRow.observation_id)
            .all()
        ]

        document = None
        if drow is not None:
            document = {
                "document_id": drow.document_id,
                "pmid": drow.pmid,
                "pmcid": drow.pmcid,
                "doi": drow.doi,
                "title": drow.title,
                "journal": drow.journal,
                "publication_date": drow.publication_date,
                "source_type": drow.source_type,
                "checksums": dict(drow.checksums or {}),
            }

        run_dict = None
        if run is not None:
            run_dict = {
                "run_id": run.run_id,
                "pipeline_version": run.pipeline_version,
                "ruleset_version": run.ruleset_version,
                "extractor_version": run.extractor_version,
                "git_commit": run.git_commit,
                "python_version": run.python_version,
                "ontology_versions": dict(run.ontology_versions or {}),
            }

    return {
        "claim": _claim_dict(claim, claim_qualifiers, claim_modifiers),
        "document": document,
        "evidence": find_evidence(engine, claim_id),
        "observations": observations,
        "study": study,
        "funding": funding,
        "assessments": assessments,
        "run": run_dict,
    }
