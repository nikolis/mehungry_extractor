"""Observation surface: inspect the output of *every* pipeline stage for a PMID.

This module is the read/observe counterpart to :mod:`.service` (which only exposes the final
``/analyze`` result). It lets a caller run each stage — acquisition/canonical, entities,
observations/claims/facts/assessments — live for one paper, or read back what any stage already
persisted, plus run the batch cohesion + synthesis stage over several papers.

By design it is **purely observational**: it never changes extraction behavior. Every function
here either calls an existing stage entry point (``ingest_pmid`` / ``analyze_document`` /
``extract_document`` / ``analyze_batch`` / ``synthesis.synthesize``) or reads via the existing
:mod:`..query` functions. It is HTTP-agnostic (no FastAPI import) so it stays unit-testable
offline, exactly like :mod:`.service`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from sqlalchemy import Engine

from ..corpus import CorpusStore
from ..db import get_engine, init_db, session_scope
from ..db.schema import (
    AffiliationRow,
    AssessmentRow,
    AuthorAffiliationRow,
    AuthorRow,
    FundingRelationshipRow,
    InstitutionRow,
    StudyCharacteristicRow,
)
from ..ids import document_id, normalize_pmid
from ..titlefilter import TitleFiltered
from .. import query as _query

_PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def _ensure_engine(engine: Optional[Engine]) -> Engine:
    if engine is None:
        engine = get_engine()
        init_db(engine)
    return engine


def _doc_id(pmid: str) -> str:
    return document_id(normalize_pmid(pmid))


# --- read persisted stage output ---------------------------------------------------------


def list_cached_documents(engine: Optional[Engine] = None) -> list[dict]:
    """Every ingested paper (for the paper picker). Ordered by document_id."""
    engine = _ensure_engine(engine)
    return _query.list_documents(engine)


def get_canonical(
    pmid: str, *, corpus: Optional[CorpusStore] = None, engine: Optional[Engine] = None
) -> dict:
    """Stage: canonical document — the single offset-addressable text + structure.

    Corpus-first then DB (mirrors the extraction entry points). Raises ``FileNotFoundError`` when
    the paper has never been ingested.
    """
    corpus = corpus or CorpusStore()
    pmid = normalize_pmid(pmid)
    if corpus.has_canonical(pmid):
        document = corpus.read_canonical(pmid)
    else:
        engine = _ensure_engine(engine)
        document = _query.get_document(engine, pmid)
    if document is None:
        raise FileNotFoundError(
            f"no canonical document for {_doc_id(pmid)}; run ingest first"
        )
    data = document.model_dump(mode="json")
    data["summary"] = {
        "sections": len(document.sections),
        "sentences": sum(1 for _ in document.iter_sentences()),
        "chars": len(document.text),
        "source_type": document.source_type,
    }
    data["paper_url"] = _PUBMED_URL.format(pmid=normalize_pmid(pmid))
    return data


def _mention_dict(row, modifiers: dict[str, list[dict]]) -> dict:
    return {
        "mention_id": row.mention_id,
        "document_id": row.document_id,
        "sentence_id": row.sentence_id,
        "surface_text": row.surface_text,
        "normalized_text": row.normalized_text,
        "concept_id": row.concept_id,
        "entity_type": row.entity_type,
        "start_char": row.start_char,
        "end_char": row.end_char,
        "status": row.status,
        "normalization_source": row.normalization_source,
        "modifiers": modifiers.get(row.mention_id, []),
    }


def get_entities(pmid: str, *, engine: Optional[Engine] = None) -> list[dict]:
    """Stage: entity mentions — each with its span, concept, status, and restrictive modifiers."""
    engine = _ensure_engine(engine)
    doc_id = _doc_id(pmid)
    rows = _query.list_entities_for_document(engine, doc_id)
    modifiers = _query.list_modifiers_for_document(engine, doc_id)
    return [_mention_dict(r, modifiers) for r in rows]


def get_observations(pmid: str, *, engine: Optional[Engine] = None) -> list[dict]:
    """Stage: observations — the audit layer (rule-based relations + modality + qualifiers)."""
    engine = _ensure_engine(engine)
    return _query.list_observations_for_document(engine, _doc_id(pmid))


def get_claims(pmid: str, *, engine: Optional[Engine] = None) -> list[dict]:
    """Stage: claims — the concept layer (observations folded by canonical relation key)."""
    engine = _ensure_engine(engine)
    return _query.list_claims_for_document(engine, _doc_id(pmid))


def get_open_relations(pmid: str, *, engine: Optional[Engine] = None) -> list[dict]:
    """Opt-in sandbox: relation-bearing spans (Phase 11), most-confident first. Empty if none."""
    engine = _ensure_engine(engine)
    return _query.list_open_observations_for_document(engine, _doc_id(pmid))


def get_facts(pmid: str, *, engine: Optional[Engine] = None) -> dict:
    """Stage: paper facts — study characteristics, funding, affiliations, and assessments.

    Read here (inside the api layer) rather than via a new query helper so no code lands outside
    ``api/`` — same pattern as :func:`service._paper_facts`.
    """
    engine = _ensure_engine(engine)
    doc_id = _doc_id(pmid)
    with session_scope(engine) as session:
        study = [
            {
                "field": sc.field,
                "value": sc.value,
                "classification_source": sc.classification_source,
                "rule_id": sc.rule_id,
                "evidence_ref": sc.evidence_ref,
            }
            for sc in session.query(StudyCharacteristicRow)
            .filter(StudyCharacteristicRow.document_id == doc_id)
            .order_by(StudyCharacteristicRow.field)
            .all()
        ]
        funding = [
            {
                "funder": f.funder,
                "funder_type": f.funder_type,
                "source": f.source,
                "rule_id": f.rule_id,
                "evidence_ref": f.evidence_ref,
            }
            for f in session.query(FundingRelationshipRow)
            .filter(FundingRelationshipRow.document_id == doc_id)
            .order_by(FundingRelationshipRow.id)
            .all()
        ]
        assessments = [
            {
                "framework_id": a.framework_id,
                "framework_version": a.framework_version,
                "criterion": a.criterion,
                "value": a.value,
                "rationale": a.rationale,
                "evidence_refs": a.evidence_refs,
            }
            for a in session.query(AssessmentRow)
            .filter(AssessmentRow.document_id == doc_id)
            .order_by(AssessmentRow.criterion)
            .all()
        ]
        institutions = {
            i.institution_id: {
                "canonical_name": i.canonical_name,
                "institution_type": i.institution_type,
            }
            for i in session.query(InstitutionRow).all()
        }
        aff_rows = {
            a.affiliation_id: a
            for a in session.query(AffiliationRow)
            .filter(AffiliationRow.document_id == doc_id)
            .all()
        }
        authors_by_id: dict[str, dict] = {}
        for au in (
            session.query(AuthorRow)
            .filter(AuthorRow.document_id == doc_id)
            .order_by(AuthorRow.position)
            .all()
        ):
            authors_by_id[au.author_id] = {
                "name": au.name,
                "position": au.position,
                "affiliations": [],
            }
        for link in (
            session.query(AuthorAffiliationRow)
            .filter(AuthorAffiliationRow.document_id == doc_id)
            .all()
        ):
            author = authors_by_id.get(link.author_id)
            aff = aff_rows.get(link.affiliation_id)
            if author is None or aff is None:
                continue
            inst = institutions.get(aff.institution_id) if aff.institution_id else None
            author["affiliations"].append(
                {
                    "raw_text": aff.raw_text,
                    "institution_id": aff.institution_id,
                    "institution_name": inst["canonical_name"] if inst else None,
                    "institution_type": inst["institution_type"] if inst else None,
                }
            )
        authors = sorted(authors_by_id.values(), key=lambda a: a["position"])
    return {
        "study_characteristics": study,
        "funding": funding,
        "affiliations": authors,
        "assessments": assessments,
    }


def explain_claim(claim_id: str, *, engine: Optional[Engine] = None) -> Optional[dict]:
    """Full provenance for a claim: the ``explain_claim`` block + the self-verifying evidence refs.

    Returns ``None`` when the claim id is unknown so the HTTP layer can map it to a 404.
    """
    engine = _ensure_engine(engine)
    block = _query.explain_claim(engine, claim_id)
    if block is None:
        return None
    block["evidence"] = _query.find_evidence(engine, claim_id)
    return block


# --- run a stage live --------------------------------------------------------------------


def run_ingest(
    pmid: str,
    *,
    force: bool = False,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    ingest: bool = True,
) -> dict:
    """Run the acquisition → canonical stage for one PMID (network, once).

    A title-filtered paper is reported as a deliberate ``excluded`` outcome, not an error
    (mirrors ``stagecli._stage_ingest``). ``ingest`` gates the network: when false, a paper not
    already cached is reported rather than fetched.
    """
    from ..ingest import ingest_pmid

    corpus = corpus or CorpusStore()
    engine = _ensure_engine(engine)
    pmid = normalize_pmid(pmid)

    if not ingest and not corpus.has_canonical(pmid):
        return {
            "pmid": pmid,
            "document_id": _doc_id(pmid),
            "status": "not_ingested",
            "detail": "paper not cached and ingest (network) is disabled",
        }
    try:
        document = ingest_pmid(pmid, force=force, corpus=corpus, engine=engine, persist=True)
    except TitleFiltered as skip:
        return {
            "pmid": pmid,
            "document_id": _doc_id(pmid),
            "status": "excluded",
            "detail": f"excluded by title filter: {skip.title!r}",
        }
    return {
        "pmid": pmid,
        "document_id": document.document_id,
        "status": "ok",
        "source_type": document.source_type,
        "title": document.metadata.title,
        "sections": len(document.sections),
        "sentences": sum(1 for _ in document.iter_sentences()),
        "chars": len(document.text),
    }


def run_analyze(
    pmid: str,
    *,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    use_model: bool = True,
) -> dict:
    """Run the entity stage live, then return the persisted mentions (same shape as ``get_entities``)."""
    from ..entities import analyze_document

    corpus = corpus or CorpusStore()
    engine = _ensure_engine(engine)
    _doc, mentions = analyze_document(
        pmid, corpus=corpus, engine=engine, persist=True, use_model=use_model
    )
    normalized = sum(1 for m in mentions if m.status == "normalized")
    return {
        "pmid": normalize_pmid(pmid),
        "document_id": _doc_id(pmid),
        "status": "ok",
        "mentions": len(mentions),
        "normalized": normalized,
        "entities": get_entities(pmid, engine=engine),
    }


def run_extract(
    pmid: str,
    *,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    use_model: bool = True,
) -> dict:
    """Run the full knowledge pass live (observations/claims/facts/assessments) and return its counts."""
    from ..pipeline import extract_document

    corpus = corpus or CorpusStore()
    engine = _ensure_engine(engine)
    summary = extract_document(
        pmid, corpus=corpus, engine=engine, persist=True, use_model=use_model
    )
    out = summary.model_dump()
    out["status"] = "ok"
    return out


# --- sentence deconstruction (entities → observations, the sub-pipeline) ----------------
#
# ``observations.extract`` turns entity mentions into observations through a per-sentence
# sub-pipeline that is otherwise invisible: clause segmentation (the flat floor), a dependency
# parse, predicate-head discovery, predicate selection (verb→rule map, with risk/direction
# promotions), argument binding, and modality/qualifier extraction. These functions expose that
# deconstruction for inspection by *mirroring* the real binder (:func:`observations._parse_bind_sentence`)
# over a fresh parse of the same sentence — never forking its decision logic into persisted data.


def _mentions_from_rows(rows, document) -> list:
    """Rebuild :class:`entities.EntityMention` objects from persisted rows (no NER re-run).

    The parse binder only reads a mention's span/concept/status, so a minimal ``EvidenceRef`` and an
    empty ``modifiers`` list are sufficient to reproduce predicate/argument binding faithfully.
    """
    from ..entities import EntityMention
    from ..provenance import EvidenceRef, ProvenancePrecision

    out = []
    for r in rows:
        out.append(
            EntityMention(
                mention_id=r.mention_id,
                document_id=r.document_id,
                sentence_id=r.sentence_id,
                surface_text=r.surface_text,
                normalized_text=r.normalized_text,
                concept_id=r.concept_id,
                entity_type=r.entity_type,
                start_char=r.start_char,
                end_char=r.end_char,
                normalization_source=r.normalization_source,
                status=r.status,
                evidence_ref=EvidenceRef(
                    document_id=r.document_id,
                    start_char=r.start_char,
                    end_char=r.end_char,
                    quoted_text=document.text[r.start_char : r.end_char],
                    precision=ProvenancePrecision.EXACT_SPAN,
                ),
                modifiers=[],
            )
        )
    return out


def _mention_brief(m) -> dict:
    return {
        "surface": m.surface_text,
        "concept_id": m.concept_id,
        "status": m.status,
        "entity_type": m.entity_type,
        "start_char": m.start_char,
        "end_char": m.end_char,
    }


def _resolved_brief(m) -> "dict | None":
    if m is None:
        return None
    return {"surface": m.surface_text, "concept_id": m.concept_id, "status": m.status}


def _predicate_trace(sp, document, sentence) -> list[dict]:
    """Per predicate-head trace that mirrors :func:`observations._parse_bind_sentence`.

    For each discovered predicate (ROOT + conj + subordinate verbs) it records the verb, the
    risk/direction promotions, the selected :class:`~relations.RelationRule` (or why none), the
    resolved subject(s)/object(s), the condition phrases that become qualifiers, negation, and a
    plain-language ``note`` explaining the binding outcome.
    """
    from .. import parse as _parse
    from .. import relations as _relations

    traces: list[dict] = []
    for root in sp.predicate_heads():
        root_subjects = sp.subject_tokens(root)
        is_participial = root.dep_.split(":")[0] in ("acl", "relcl")
        for pred in [root] + sp.conj_verbs(root):
            subj_tokens = sp.subject_tokens(pred) if pred.i != root.i else root_subjects
            if pred.i != root.i and not subj_tokens:
                subj_tokens = root_subjects

            expanded_subj: list = []
            for st in subj_tokens:
                if st.lemma_.lower() in _parse.MEASURE_NOUNS:
                    unwrapped = sp.measure_objects(st)
                    expanded_subj.extend(unwrapped if unwrapped else [st])
                else:
                    expanded_subj.append(st)
            subj_tokens = expanded_subj

            object_is_risk = False
            object_direction = None
            obj_tokens: list = []
            for ot in sp.object_tokens(pred):
                lemma = ot.lemma_.lower()
                if lemma == "risk":
                    object_is_risk = True
                    object_direction = object_direction or sp.direction_of(ot)
                    obj_tokens.extend(sp.measure_objects(ot))
                elif lemma in _parse.MEASURE_NOUNS:
                    object_direction = object_direction or sp.direction_of(ot)
                    unwrapped = sp.measure_objects(ot)
                    obj_tokens.extend(unwrapped if unwrapped else [ot])
                else:
                    object_direction = object_direction or sp.direction_of(ot)
                    obj_tokens.append(ot)

            rule = _relations.rule_for_verb(
                pred.lemma_, object_is_risk=object_is_risk, object_direction=object_direction
            )
            subjects = [sp.subject_mention_for_token(t) for t in subj_tokens]
            objects = [sp.mention_for_token(t) for t in obj_tokens]
            subj_resolved = [s for s in subjects if s is not None]
            obj_resolved = [o for o in objects if o is not None]
            conditions = [document.text[slice(*sp.token_span(c))] for c in sp.condition_tokens(pred)]

            if rule is None:
                note = f"verb '{pred.lemma_}' is not in the predicate map → left to the flat fallback"
            elif not subj_resolved and any(t.pos_ in ("NOUN", "PROPN") for t in subj_tokens):
                note = "subject is a non-entity (did not normalize) → relation dropped" + (
                    "" if is_participial else "; flat fallback suppressed"
                )
            elif not subj_resolved:
                note = "no subject resolved → deferred to the flat fallback"
            elif not obj_resolved:
                note = "no object resolved → relation dropped"
            else:
                note = f"bound {len(subj_resolved)}×{len(obj_resolved)} (subject × object)"

            traces.append(
                {
                    "verb": pred.text,
                    "lemma": pred.lemma_,
                    "dep": pred.dep_,
                    "is_participial": is_participial,
                    "negated": sp.has_neg(pred),
                    "object_is_risk": object_is_risk,
                    "object_direction": object_direction,
                    "rule": None
                    if rule is None
                    else {"predicate": rule.predicate, "rule_id": rule.rule_id, "version": rule.version},
                    "subjects": [_resolved_brief(s) for s in subjects],
                    "objects": [_resolved_brief(o) for o in objects],
                    "conditions": conditions,
                    "note": note,
                }
            )
    return traces


def _deconstruct_sentence(document, sentence, mentions, observations, parse_on) -> dict:
    from .. import clauses as _clauses

    clause_list = [
        {
            "text": c.text,
            "marker": c.marker,
            "contrastive": c.contrastive,
            "start_char": c.start_char,
            "end_char": c.end_char,
        }
        for c in _clauses.segment(document, sentence)
    ]

    parse_block = None
    if parse_on and mentions:
        from .. import parse as _parse

        sp = _parse.SentenceParse(sentence, mentions)
        base = sentence.start_char
        tokens = [
            {
                "i": t.i,
                "text": t.text,
                "lemma": t.lemma_,
                "pos": t.pos_,
                "dep": t.dep_,
                "head": t.head.i,
                "start_char": base + t.idx,
            }
            for t in sp.doc
        ]
        parse_block = {
            "tokens": tokens,
            "predicate_heads": _predicate_trace(sp, document, sentence),
            "clausal_subjects": [m.surface_text for m in sp.clausal_subject_mentions()],
        }

    return {
        "sentence_id": sentence.sentence_id,
        "text": sentence.text,
        "start_char": sentence.start_char,
        "end_char": sentence.end_char,
        "mentions": [_mention_brief(m) for m in mentions],
        "clauses": clause_list,
        "parse": parse_block,
        "observations": [
            {
                "subject_text": o["subject_text"],
                "subject_concept_id": o["subject_concept_id"],
                "predicate": o["predicate"],
                "object_text": o["object_text"],
                "object_concept_id": o["object_concept_id"],
                "polarity": o["polarity"],
                "certainty": o["certainty"],
                "context": o["context"],
                "rule_id": o["rule_id"],
            }
            for o in observations
        ],
    }


def deconstruct(pmid: str, *, corpus=None, engine=None) -> dict:
    """The entities → observations sub-pipeline, per sentence, for inspection.

    Rebuilds mentions from the persisted entity rows (so no model re-run is needed for NER) and, for
    each sentence that has at least two mentions or produced an observation, exposes: the flat
    binder's clause segmentation, the dependency parse, the discovered predicate heads with their
    selected rule (and risk/direction promotions), the resolved subject/object bindings, condition
    phrases, negation, and the observations actually emitted for that sentence.
    """
    from .. import parse as _parse

    corpus = corpus or CorpusStore()
    engine = _ensure_engine(engine)
    pmid = normalize_pmid(pmid)
    doc_id = _doc_id(pmid)

    if corpus.has_canonical(pmid):
        document = corpus.read_canonical(pmid)
    else:
        document = _query.get_document(engine, pmid)
    if document is None:
        raise FileNotFoundError(f"no canonical document for {doc_id}; run ingest first")

    rows = _query.list_entities_for_document(engine, doc_id)
    mentions = _mentions_from_rows(rows, document)
    by_sentence: dict[str, list] = {}
    for m in mentions:
        if m.sentence_id:
            by_sentence.setdefault(m.sentence_id, []).append(m)

    obs_by_sentence: dict[str, list] = {}
    for o in _query.list_observations_for_document(engine, doc_id):
        if o["sentence_id"]:
            obs_by_sentence.setdefault(o["sentence_id"], []).append(o)

    parse_on = _parse.available()
    sentences = list(document.iter_sentences())
    out_sentences = []
    for s in sentences:
        sent_mentions = sorted(
            by_sentence.get(s.sentence_id, []), key=lambda m: (m.start_char, m.end_char)
        )
        sent_obs = obs_by_sentence.get(s.sentence_id, [])
        # Candidate sentences: where relation binding is even possible (≥2 mentions) or that already
        # produced an observation. Others are skipped so the view stays focused and fast.
        if len(sent_mentions) < 2 and not sent_obs:
            continue
        out_sentences.append(
            _deconstruct_sentence(document, s, sent_mentions, sent_obs, parse_on)
        )

    return {
        "document_id": doc_id,
        "pmid": pmid,
        "parse_available": parse_on,
        "sentence_count": len(sentences),
        "deconstructed_count": len(out_sentences),
        "entities_present": bool(rows),
        "observations_present": bool(obs_by_sentence),
        "sentences": out_sentences,
    }


# --- batch stage: cohesion + synthesis ---------------------------------------------------


def synthesize(
    pmids: list[str],
    *,
    options: Any = None,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    ingest: bool = True,
) -> dict:
    """Run the batch stage: ingest + extract + cohesion (via ``analyze_batch``), then synthesis.

    ``analyze_batch`` already does acquisition, per-paper extraction, and cohesion/outlier
    detection, so we reuse it for the per-paper report and topic core. We then additionally run
    :func:`synthesis.synthesize` over the on-topic (included) papers to surface the cross-paper
    conclusions that ``/analyze`` does not currently return.
    """
    from .. import synthesis as _synthesis
    from . import service as _service

    corpus = corpus or CorpusStore()
    engine = _ensure_engine(engine)

    report = _service.analyze_batch(
        pmids, options, corpus=corpus, engine=engine, ingest=ingest
    )
    included_doc_ids = [document_id(p) for p in report.included_pmids]
    synth = _synthesis.synthesize(engine, included_doc_ids)
    return {
        "report": report.model_dump(mode="json"),
        "synthesis": asdict(synth),
    }
