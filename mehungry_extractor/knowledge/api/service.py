"""HTTP-agnostic orchestration for batch PMID analysis.

``analyze_batch`` is the whole feature; :mod:`.app` is a thin FastAPI shell over it, and the
unit tests drive this function directly (offline, with a pre-seeded corpus). It does not import
FastAPI, so it works with only the core dependencies installed.

``analyze_batch`` is just an orchestrator: it wires together three phases, each its own
function so the "get the data" work is cleanly separable from the "process it" work. One paper
failing NEVER aborts the batch (mirrors ``stagecli._stage_extract``):

1. :func:`_acquire_documents` — DOWNLOAD: ingest each PMID if requested and not already cached
   (network → immutable corpus), so its canonical text is in place. No extraction here.
2. :func:`_extract` — EXTRACT: run the full deterministic extraction pass on each acquired
   paper (:func:`pipeline.extract_document`), persisting entities/claims/… to the DB.
3. :func:`_synthesize` — SYNTHESIZE: collect each paper's normalized concept set, score the
   batch for topic cohesion (:mod:`.cohesion`), exclude outliers, synthesize the remaining
   papers' claims (:mod:`.synthesis`), and assemble the response.

The phases hand state to each other through the :class:`Acquisition` dataclass (plus the
``ok_pmids`` list returned by :func:`_extract`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Engine

from .. import (
    EXTRACTOR_VERSION,
    PIPELINE_VERSION,
    RULESET_VERSION,
    SCHEMA_VERSION,
    SYNTHESIS_VERSION,
)
from .. import cohesion as _cohesion
from .. import entities as _entities
from .. import valence as _valence
from ..corpus import CorpusStore
from ..db import get_engine, init_db, session_scope
from ..db.schema import (
    AssessmentRow,
    ClaimRow,
    DocumentRow,
    EntityRow,
    FundingRelationshipRow,
    StudyCharacteristicRow,
)
from ..ids import document_id, normalize_pmid
from ..query import (
    list_entities_for_document,
    list_observations_for_document,
    list_open_observations_for_document,
)
from ..run import build_run
from ..vocab import (
    COUNTRIES_VERSION,
    FOOD_SOURCES_VERSION,
    FUNDERS_VERSION,
    INSTITUTIONS_VERSION,
    VOCAB_VERSION,
)
from .models import (
    AnalyzeOptions,
    AnalyzeResponse,
    CoreConcept,
    ObservationDetail,
    ObservationEvidenceModel,
    OpenRelationDetail,
    OpenRelationEntityModel,
    OpenRelationEvidenceModel,
    OpenRelationResponse,
    OutlierReport,
    PaperObservations,
    PaperOpenRelations,
    QualifierModel,
    PaperReport,
    RunInfo,
    TopicInfo,
)

_PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def _paper_url(pmid: str) -> str:
    """The public PubMed URL for a (normalized, bare-numeric) PMID."""
    return _PUBMED_URL.format(pmid=pmid)

_COHESION_METHOD = "normalized_concept_overlap"


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _concept_set(engine: Engine, doc_id: str) -> "set[str]":
    """Normalized concept ids mentioned in a document (unmatched mentions carry no concept)."""
    return {
        m.concept_id
        for m in list_entities_for_document(engine, doc_id)
        if m.status == "normalized" and m.concept_id
    }


def _paper_facts(engine: Engine, doc_ids: list[str]) -> dict[str, dict]:
    """Per-document facts for the paper report, read in one pass. Keyed by document_id."""
    facts: dict[str, dict] = {
        d: {
            "title": None,
            "source_type": None,
            "publication_year": None,
            "study_design": None,
            "sample_size": None,
            "funder_types": set(),
            "funding_independence": None,
            "n_claims": 0,
        }
        for d in doc_ids
    }
    if not doc_ids:
        return facts
    with session_scope(engine) as session:
        for d in session.query(DocumentRow).filter(DocumentRow.document_id.in_(doc_ids)).all():
            facts[d.document_id]["title"] = d.title
            facts[d.document_id]["source_type"] = d.source_type
        for sc in (
            session.query(StudyCharacteristicRow)
            .filter(StudyCharacteristicRow.document_id.in_(doc_ids))
            .all()
        ):
            if sc.field in ("study_design", "publication_year", "sample_size"):
                facts[sc.document_id][sc.field] = sc.value
        for fr in (
            session.query(FundingRelationshipRow)
            .filter(FundingRelationshipRow.document_id.in_(doc_ids))
            .all()
        ):
            facts[fr.document_id]["funder_types"].add(fr.funder_type)
        for a in (
            session.query(AssessmentRow)
            .filter(
                AssessmentRow.document_id.in_(doc_ids),
                AssessmentRow.criterion == "funding_independence",
            )
            .all()
        ):
            facts[a.document_id]["funding_independence"] = a.value
        for row in (
            session.query(ClaimRow.document_id, ClaimRow.claim_id)
            .filter(ClaimRow.document_id.in_(doc_ids))
            .all()
        ):
            facts[row[0]]["n_claims"] += 1
    # normalize types
    for d, f in facts.items():
        f["publication_year"] = _int(f["publication_year"])
        f["sample_size"] = _int(f["sample_size"])
        f["funder_types"] = sorted(f["funder_types"])
    return facts


def _concept_names(engine: Engine, concept_ids: list[str]) -> dict[str, str]:
    if not concept_ids:
        return {}
    with session_scope(engine) as session:
        rows = session.query(EntityRow).filter(EntityRow.concept_id.in_(concept_ids)).all()
        return {r.concept_id: r.canonical_name for r in rows}


def _run_info(use_model: bool, *, extra_ontology: Optional[dict] = None) -> RunInfo:
    run = build_run([])  # captures git commit + python version deterministically
    ontology = {
        "mehungry_curated": VOCAB_VERSION,
        "mehungry_funders": FUNDERS_VERSION,
        "mehungry_institutions": INSTITUTIONS_VERSION,
        "mehungry_countries": COUNTRIES_VERSION,
        "mehungry_food_sources": FOOD_SOURCES_VERSION,
    }
    model_available = _entities.model_available()
    if use_model and model_available:
        ontology[_entities._SCISPACY_MODEL] = _entities._model_version()
    if extra_ontology:
        ontology.update(extra_ontology)  # e.g. the open-relation detector name → version (Phase 11)
    return RunInfo(
        pipeline_version=PIPELINE_VERSION,
        ruleset_version=RULESET_VERSION,
        extractor_version=EXTRACTOR_VERSION,
        schema_version=SCHEMA_VERSION,
        synthesis_version=SYNTHESIS_VERSION,
        clinical_valence_version=_valence.CLINICAL_VALENCE_VERSION,
        git_commit=run.git_commit,
        python_version=run.python_version,
        model_available=model_available,
        ontology_versions=ontology,
    )


# =====================================================================================
# analyze_batch: the whole feature. Hand it PMIDs, get back one AnalyzeResponse.
#
# Big picture (a "pipeline"): (1) fetch each paper so its text is on disk, (2) extract each
# paper into the DB, (3) decide which papers are actually on-topic, merge the on-topic papers'
# findings into conclusions, and pack it all into one response. One paper failing NEVER kills
# the batch. Each numbered step is its own function below — _acquire_documents (download),
# _extract (extraction), and _synthesize (cohesion + synthesis + response) — so the "get the
# data in place" work stays cleanly separable from the "process it" work; ``analyze_batch``
# just wires the three together.
# =====================================================================================


@dataclass
class Acquisition:
    """What Part 1 (``_acquire_documents``) hands to the later phases (``_extract`` /
    ``_synthesize``).

    It carries the bookkeeping the phases share: the normalized request list, the
    pmid -> document_id map, which pmids now have their canonical text in place (``ready``),
    and the running ``warnings``/``errors`` accumulators (``_extract`` and ``_synthesize`` keep
    appending to them).
    """

    requested: list[str]  # normalized pmids, in the caller's original order.
    doc_of_pmid: dict[str, str]  # pmid -> internal document_id, for every requested pmid.
    ready: list[str]  # pmids whose text is in the corpus/DB and can be extracted, in request order.
    warnings: list[str]  # human-readable notes returned to the caller.
    errors: dict[str, str]  # pmid -> error message for papers that couldn't be acquired.


def _acquire_documents(
    pmids: list[str],
    *,
    corpus: CorpusStore,
    engine: Engine,
    ingest: bool,
) -> Acquisition:
    """Part 1 — DOWNLOAD / make sure every PMID's canonical text is in place. No extraction.

    For each pmid: if it isn't already cached, fetch it (when ``ingest`` is on) or record a
    per-paper error (when ``ingest`` is off). Papers whose text ends up available are collected
    in ``ready`` for Part 2; failures go into ``errors``/``warnings`` and are simply skipped.
    """
    # LOCAL (deferred) IMPORT. Normally imports live at the top of a file. Doing it inside the
    # function delays loading this heavy module until the function actually runs, and sidesteps
    # circular-import problems.
    from ..ingest import ingest_pmid  # fetches one paper from PubMed/PMC.

    # LIST COMPREHENSION: "for each p in pmids, call normalize_pmid(p), collect into a new list".
    # normalize_pmid cleans each ID so "12345 " and "12345" become identical.
    requested = [normalize_pmid(p) for p in pmids]
    # Accumulators we fill as we go:
    warnings: list[str] = []  # human-readable notes returned to the caller.
    errors: dict[str, str] = {}  # a DICT (hash map) mapping pmid -> error message. `{}` = empty dict.
    doc_of_pmid: dict[str, str] = {}  # maps each pmid to its internal document_id.
    ready: list[str] = []  # pmids whose text is in place, kept IN REQUEST ORDER (matters later).

    # ---- PER-PAPER LOOP: ingest (if needed) so the paper's text is on disk --------------
    for pmid in requested:
        doc_id = document_id(pmid)  # derive the internal document id from the pmid.
        doc_of_pmid[pmid] = doc_id  # remember the mapping so we can look it up later.
        # TRY BLOCK: code that might raise an error. If anything below fails, control jumps to
        # the `except` at the bottom instead of crashing the whole batch.
        try:
            # `corpus.has_canonical(pmid)` = "is this paper already cached on disk?".
            # `not ...` -> "if we do NOT have it yet".
            if not corpus.has_canonical(pmid):
                if ingest:
                    # Allowed to use the network: fetch it and save it (persist=True writes to DB + corpus).
                    ingest_pmid(pmid, corpus=corpus, engine=engine, persist=True)
                else:
                    # Network is off-limits, so we `raise` an error. The f"..." is an F-STRING
                    # (formatted string): {doc_id}/{pmid} get substituted with their values.
                    # This error is caught just below and turned into a per-paper failure.
                    raise FileNotFoundError(
                        f"{doc_id} not ingested and ingest is disabled; run "
                        f"`mehungry ingest --pmid {pmid}` first"
                    )
            ready.append(pmid)  # text is in place -> Part 2 can extract this paper.
        # EXCEPTION HANDLING: `exc` is the caught error object. type(exc).__name__ is its class
        # name (e.g. "FileNotFoundError"); {exc} is its message. We record it and keep going —
        # this is the "one failure never aborts the batch" guarantee. (The wording says
        # "extraction failed" to stay identical to the pre-split message text.)
        except Exception as exc:  # noqa: BLE001 — one failure must not abort the batch
            errors[pmid] = f"{type(exc).__name__}: {exc}"
            warnings.append(f"{doc_id}: extraction failed ({errors[pmid]}); excluded.")

    return Acquisition(requested, doc_of_pmid, ready, warnings, errors)


def analyze_batch(
    # --- The signature (how you call this function) ---
    # `pmids: list[str]` is a TYPE HINT: "expect a list of strings". Python does NOT enforce
    # hints at runtime; they document intent for you and for tools/IDEs.
    pmids: list[str],
    # `Optional[X]` means "an X, or None". `= None` makes the argument optional.
    options: Optional[AnalyzeOptions] = None,
    # The bare `*,` is a real Python feature: EVERYTHING AFTER IT must be passed by keyword,
    # e.g. `engine=...`, never by position. This stops callers from accidentally swapping args.
    *,
    # `corpus`/`engine` are INJECTABLE (dependency injection): tests pass their own so the
    # function runs fully offline instead of hitting the real DB / network.
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    ingest: bool = True,
) -> AnalyzeResponse:  # `-> AnalyzeResponse` documents the return type.
    """Ingest + extract + topic-filter + synthesize a batch of PMIDs. Deterministic.

    This is a thin orchestrator over three phases: :func:`_acquire_documents` (Part 1 —
    download / make sure the data is in place), :func:`_extract` (Part 2 — run the deterministic
    extraction pass on each paper), and :func:`_synthesize` (Part 3 — cohesion + synthesis +
    response assembly).

    ``ingest`` gates the network entirely (the FastAPI layer ANDs it with the request option);
    when false, PMIDs absent from the corpus/DB are reported as per-paper errors instead of being
    fetched. ``corpus``/``engine`` are injectable so tests can run fully offline.
    """
    # `A or B` returns A if A is "truthy", else B. Since None is falsy, this reads as:
    # "use the caller's options if given, otherwise build a default AnalyzeOptions()".
    # AnalyzeOptions (models.py) holds the tunables (core_fraction, outlier_threshold, ...).
    options = options or AnalyzeOptions()
    # Same idiom: reuse the caller's corpus, or open the default on-disk cache of fetched papers.
    corpus = corpus or CorpusStore()
    # Here we test `is None` explicitly (not `or`): an Engine could be falsy/expensive to eval,
    # so `is None` is the precise check.
    if engine is None:
        engine = get_engine()  # SQLAlchemy handle to the SQLite database.
        init_db(engine)  # create tables if they don't exist yet.

    # PART 1: download / make sure every paper's text is in place.
    acquired = _acquire_documents(pmids, corpus=corpus, engine=engine, ingest=ingest)
    # PART 2: extract each ready paper into the DB.
    ok_pmids = _extract(acquired, options, corpus=corpus, engine=engine)
    # PART 3: score cohesion, synthesize the on-topic papers, and assemble the response.
    return _synthesize(acquired, ok_pmids, options, engine=engine)


def _extract(
    acquired: Acquisition,
    options: AnalyzeOptions,
    *,
    corpus: CorpusStore,
    engine: Engine,
) -> list[str]:
    """Part 2 — EXTRACT each ready paper into the DB.

    Returns the pmids that extracted successfully, IN REQUEST ORDER (that order matters to the
    synthesis phase). Per-paper failures are recorded in the shared ``acquired.warnings`` /
    ``acquired.errors`` and simply skipped — one failure never aborts the batch.
    """
    # LOCAL (deferred) IMPORT — see the note in Part 1 for why this lives inside the function.
    from ..pipeline import extract_document  # runs the deterministic extraction pass.

    # Unpack the handover from Part 1. Mutating these lists/dicts mutates the shared Acquisition.
    doc_of_pmid = acquired.doc_of_pmid
    warnings = acquired.warnings
    errors = acquired.errors
    ok_pmids: list[str] = []  # pmids that extracted successfully, kept IN REQUEST ORDER (matters later).

    # ---- PER-PAPER LOOP: extract each paper whose text Part 1 put in place --------------
    for pmid in acquired.ready:
        doc_id = doc_of_pmid[pmid]  # look up the internal document id remembered by Part 1.
        # TRY BLOCK: code that might raise an error. If anything below fails, control jumps to
        # the `except` at the bottom instead of crashing the whole batch.
        try:
            # The heart of extraction: parses the paper and persists entities, claims, study
            # characteristics, funding, assessments, ... into the DB. use_model optionally
            # turns on the scispaCy NER model.
            extract_document(
                pmid, corpus=corpus, engine=engine, persist=True, use_model=options.use_model
            )
            ok_pmids.append(pmid)  # got here without raising -> this paper succeeded.
        # EXCEPTION HANDLING: `exc` is the caught error object. type(exc).__name__ is its class
        # name (e.g. "FileNotFoundError"); {exc} is its message. We record it and keep going —
        # this is the "one failure never aborts the batch" guarantee.
        except Exception as exc:  # noqa: BLE001 — one failure must not abort the batch
            errors[pmid] = f"{type(exc).__name__}: {exc}"
            warnings.append(f"{doc_id}: extraction failed ({errors[pmid]}); excluded.")

    return ok_pmids


def _synthesize(
    acquired: Acquisition,
    ok_pmids: list[str],
    options: AnalyzeOptions,
    *,
    engine: Engine,
) -> AnalyzeResponse:
    """Part 3 — score topic cohesion over the extracted papers, exclude outliers, synthesize
    the on-topic papers' claims, and assemble the final :class:`AnalyzeResponse`.

    ``ok_pmids`` is the successful-extraction list from :func:`_extract`; the rest of the batch
    bookkeeping (request order, doc ids, warnings, errors) comes from ``acquired``. Keeps
    appending to ``acquired.warnings`` as cohesion/synthesis produce their own notes.
    """
    # Unpack the shared handover. These names match the rest of the function below.
    requested = acquired.requested
    doc_of_pmid = acquired.doc_of_pmid
    warnings = acquired.warnings
    errors = acquired.errors

    # ---- TOPIC COHESION over successfully-extracted papers -----------------------------
    # DICT COMPREHENSION (like a list comprehension but builds a dict): for each successful
    # pmid, map document_id -> the paper's SET of normalized concept ids. A `set` is an
    # unordered collection of unique items, ideal for the set arithmetic cohesion does.
    # _concept_set (defined above) reads the DB via list_entities_for_document.
    concept_sets = {doc_of_pmid[p]: _concept_set(engine, doc_of_pmid[p]) for p in ok_pmids}
    # cohesion.detect() = the deterministic "which papers are on-topic?" logic. It builds the
    # TOPIC CORE (concepts shared by enough papers), scores each paper's coverage of that core,
    # and flags low-coverage papers as OUTLIERS. Returns a CohesionResult dataclass.
    result = _cohesion.detect(
        concept_sets,
        core_fraction=options.core_fraction,
        outlier_threshold=options.outlier_threshold,
    )
    # `.extend()` appends ALL items from another list (vs `.append()` which adds one item).
    warnings.extend(result.warnings)
    # Build a quick lookup: document_id -> its PaperCohesion (score/outlier standing).
    cohesion_by_doc = {p.document_id: p for p in result.papers}

    # --- assemble the response ------------------------------------------------------
    # Iterating a dict yields its KEYS, so sorted(concept_sets) = the doc ids in deterministic
    # order. _paper_facts reads the DB ONCE and returns document_id -> {title, source_type,
    # publication_year, study_design, sample_size, funder_types, funding_independence, n_claims}.
    all_ok_docs = sorted(concept_sets)
    facts = _paper_facts(engine, all_ok_docs)
    # If the optional NER model isn't installed, note that entity recall is dictionary-only.
    if not _entities.model_available():
        warnings.append(
            "scispaCy model not installed: entity recall is dictionary-only (deterministic but "
            "narrower). Install the optional [ner] extra + model to broaden coverage."
        )

    # ---- BUILD PER-PAPER REPORTS -------------------------------------------------------
    papers: list[PaperReport] = []
    included_pmids: list[str] = []
    outliers: list[OutlierReport] = []
    # Loop over ALL requested pmids IN ORIGINAL ORDER (not just successes) so the report also
    # includes failures and outliers.
    for pmid in requested:
        doc_id = doc_of_pmid[pmid]
        # If this pmid failed extraction earlier, emit an error report and move on.
        if pmid in errors:
            papers.append(
                PaperReport(pmid=pmid, document_id=doc_id, status="error", error=errors[pmid])
            )
            # `continue` SKIPS to the next loop iteration — don't build facts for a failed paper.
            continue
        # `.get(key)` returns the value or None if missing (unlike `[key]`, which would raise).
        pc = cohesion_by_doc.get(doc_id)  # PaperCohesion or None.
        f = facts.get(doc_id, {})  # the 2nd arg is the DEFAULT: an empty dict if not found.
        # TERNARY (conditional) EXPRESSION: "X if cond else Y". Safely handles pc being None.
        is_outlier = pc.is_outlier if pc else False
        # Construct the PaperReport (a Pydantic model, models.py). Facts come from the `f` dict
        # via `.get()` (safe defaults); cohesion fields come from `pc` with the same `if pc else`
        # guard so a missing pc never crashes us.
        report = PaperReport(
            pmid=pmid,
            document_id=doc_id,
            status="outlier" if is_outlier else "included",
            title=f.get("title"),
            publication_year=f.get("publication_year"),
            source_type=f.get("source_type"),
            study_design=f.get("study_design"),
            sample_size=f.get("sample_size"),
            funder_types=f.get("funder_types", []),
            funding_independence=f.get("funding_independence"),
            n_claims=f.get("n_claims", 0),
            concept_count=pc.concept_count if pc else 0,
            cohesion_score=pc.score if pc else None,
            outlier_reason=pc.reason if pc else None,
            missing_core_concepts=(pc.missing_core_concepts if pc else []),
        )
        papers.append(report)
        # Outlier -> also add a dedicated OutlierReport (with reason + which core concepts it
        # lacks). `pc.reason or "off_topic"` supplies a fallback if pc.reason is None.
        if is_outlier:
            outliers.append(
                OutlierReport(
                    pmid=pmid,
                    document_id=doc_id,
                    reason=pc.reason or "off_topic",
                    cohesion_score=pc.score,
                    missing_core_concepts=pc.missing_core_concepts,
                )
            )
        else:
            included_pmids.append(pmid)  # non-outlier -> record as an included pmid.

        # `x in ("abstract", "none")` tests membership in a TUPLE of allowed values. For included
        # papers with only abstract/no text, warn that claim recall is limited.
        if f.get("source_type") in ("abstract", "none") and not is_outlier:
            warnings.append(
                f"{doc_id}: only {f.get('source_type')} text available (no open-access full "
                "text); claim recall is limited to the abstract."
            )

    # ---- NAME THE TOPIC CORE -----------------------------------------------------------
    # Look up human-readable names for the core concept ids: concept_id -> canonical name.
    names = _concept_names(engine, result.core_concepts)
    # Recompute DOCUMENT FREQUENCY (how many papers mention each concept) for display.
    # concept_sets.values() iterates the SETS (not the keys). `df.get(c, 0) + 1` is the standard
    # counting idiom: current count (or 0 if unseen) plus one.
    df: dict[str, int] = {}
    for cset in concept_sets.values():
        for c in cset:
            df[c] = df.get(c, 0) + 1
    # Build the TopicInfo model. The core_concepts field uses a nested list comprehension turning
    # each core concept id into a CoreConcept with its name + paper count.
    topic = TopicInfo(
        core_concepts=[
            CoreConcept(concept_id=c, name=names.get(c), paper_count=df.get(c, 0))
            for c in result.core_concepts
        ],
        method=_COHESION_METHOD,
        core_fraction=result.core_fraction,
        outlier_threshold=result.outlier_threshold,
        min_core_papers=result.min_core_papers,
    )

    # ---- PER-PAPER OBSERVATIONS --------------------------------------------------------
    # The raw, un-synthesized layer: for each successfully-extracted paper (in request order),
    # every rule-detected observation with all of its detail. Read the observation rows once per
    # paper, then resolve the endpoint concept ids to canonical names in ONE batched lookup so we
    # never issue an N+1 over the concept table.
    obs_by_pmid: dict[str, list[dict]] = {}
    obs_concept_ids: set[str] = set()
    for pmid in requested:
        if pmid in errors:
            continue  # a failed paper has no observations.
        obs = list_observations_for_document(engine, doc_of_pmid[pmid])
        obs_by_pmid[pmid] = obs
        for o in obs:
            if o["subject_concept_id"]:
                obs_concept_ids.add(o["subject_concept_id"])
            if o["object_concept_id"]:
                obs_concept_ids.add(o["object_concept_id"])
    obs_names = _concept_names(engine, sorted(obs_concept_ids))

    paper_observations: list[PaperObservations] = []
    for pmid in requested:
        if pmid in errors:
            continue
        doc_id = doc_of_pmid[pmid]
        details = [
            ObservationDetail(
                observation_id=o["observation_id"],
                subject_text=o["subject_text"],
                subject_concept_id=o["subject_concept_id"],
                subject_name=obs_names.get(o["subject_concept_id"]) if o["subject_concept_id"] else None,
                predicate=o["predicate"],
                object_text=o["object_text"],
                object_concept_id=o["object_concept_id"],
                object_name=obs_names.get(o["object_concept_id"]) if o["object_concept_id"] else None,
                polarity=o["polarity"],
                certainty=o["certainty"],
                context=o["context"],
                sentence_id=o["sentence_id"],
                rule_id=o["rule_id"],
                rule_version=o["rule_version"],
                qualifiers=[
                    QualifierModel(
                        qualifier_type=q["qualifier_type"],
                        value_concept_id=q.get("value_concept_id"),
                        value_text=q.get("value_text"),
                    )
                    for q in o["qualifiers"]
                ],
                evidence=[
                    ObservationEvidenceModel(
                        document_id=e.get("document_id", doc_id),
                        section_id=e.get("section_id"),
                        paragraph_id=e.get("paragraph_id"),
                        sentence_id=e.get("sentence_id"),
                        start_char=e.get("start_char"),
                        end_char=e.get("end_char"),
                        quoted_text=e.get("quoted_text"),
                        precision=e.get("precision", "SENTENCE"),
                        extraction_rule=e.get("extraction_rule", o["rule_id"]),
                        extraction_rule_version=e.get("extraction_rule_version", o["rule_version"]),
                    )
                    for e in o["evidence_refs"]
                ],
            )
            for o in obs_by_pmid.get(pmid, [])
        ]
        paper_observations.append(
            PaperObservations(
                pmid=pmid,
                document_id=doc_id,
                paper_title=facts.get(doc_id, {}).get("title"),
                paper_url=_paper_url(pmid),
                observations_list=details,
            )
        )

    # ---- DE-DUPLICATE warnings while PRESERVING ORDER ----------------------------------
    # (A plain set(warnings) would dedupe but scramble order.) The trick per warning w:
    #   * `w in seen` True  -> already kept -> `not(...)` is False -> DROP it.
    #   * `w in seen` False -> new -> Python evaluates the right side of `or`: seen.add(w).
    #     set.add() RETURNS None (falsy) and records w as a side effect, so `False or None`
    #     -> None -> `not None` -> True -> KEEP it.
    # This exploits SHORT-CIRCUIT EVALUATION (`or` only evaluates its right side if the left is
    # falsy) to test-and-record in one expression. Clever but sneaky — hence the comment.
    seen: set[str] = set()
    warnings = [w for w in warnings if not (w in seen or seen.add(w))]

    # ---- RETURN the assembled response -------------------------------------------------
    # Bundle everything into AnalyzeResponse (models.py). FastAPI (app.py) uses this as its
    # response_model, so returning it also validates the shape + generates the JSON schema.
    # _run_info() captures reproducibility metadata (version strings, git commit, python version,
    # ontology versions, whether the NER model is available).
    return AnalyzeResponse(
        run=_run_info(options.use_model),
        requested_pmids=requested,  # everything asked for.
        included_pmids=included_pmids,  # the on-topic survivors.
        papers=papers,  # per-paper reports (includes errors + outliers).
        topic=topic,  # the detected topic core, named.
        outliers=outliers,  # off-topic papers with reasons.
        paper_observations=paper_observations,  # per-paper raw observations + provenance.
        warnings=warnings,  # de-duplicated notes.
    )


# =====================================================================================
# Open relation discovery (Phase 11) — served off the trusted /analyze path.
#
# A separate, clearly-labelled surface: it flags the sentences/clauses a model believes assert a
# relationship and returns them verbatim for a human to read, most-confident first. It never enters
# claims/synthesis. POST /discover/relations runs discovery + persists ("run + review"); GET
# /discover/relations/{pmid} returns the already-persisted spans without recomputing.
# =====================================================================================


def _doc_title(engine: Engine, doc_id: str) -> Optional[str]:
    """The paper's title from the persisted document row, or ``None`` if not stored yet."""
    with session_scope(engine) as session:
        row = session.get(DocumentRow, doc_id)
        return row.title if row else None


def _openobs_to_dict(o) -> dict:
    """Normalize an :class:`~.openobs.OpenObservation` to the same dict shape the query layer emits,
    so the freshly-discovered (POST) and already-persisted (GET) paths build responses identically."""
    return {
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
        "evidence_refs": [r.model_dump(mode="json") for r in o.evidence_refs],
    }


def _entities_in_span(mentions: list, start_char: int, end_char: int) -> list[OpenRelationEntityModel]:
    """Known entity mentions overlapping ``[start_char, end_char)`` — OPTIONAL context only.

    Reuses whatever Concept-6 mentions are already persisted for the document (from a prior
    ``/analyze`` or extraction run); it imposes no relation structure and is simply empty when the
    paper's entities have not been extracted.
    """
    out: list[OpenRelationEntityModel] = []
    for m in mentions:
        if m.start_char < end_char and start_char < m.end_char:
            out.append(
                OpenRelationEntityModel(
                    surface_text=m.surface_text,
                    entity_type=m.entity_type,
                    concept_id=m.concept_id,
                    start_char=m.start_char,
                    end_char=m.end_char,
                )
            )
    return out


def _build_open_relations(engine: Engine, doc_id: str, obs_dicts: list[dict]) -> list[OpenRelationDetail]:
    """Assemble the review-friendly ``OpenRelationDetail`` list for one paper.

    ``obs_dicts`` are already ordered most-confident first (query layer / detector output). The
    optional ``entities`` array is filled from the document's persisted mentions (read once)."""
    mentions = list_entities_for_document(engine, doc_id) if obs_dicts else []
    details: list[OpenRelationDetail] = []
    for o in obs_dicts:
        details.append(
            OpenRelationDetail(
                open_observation_id=o["open_observation_id"],
                text=o["text"],
                sentence_id=o["sentence_id"],
                clause_index=o.get("clause_index"),
                start_char=o["start_char"],
                end_char=o["end_char"],
                score=o["score"],
                detector_name=o["detector_name"],
                detector_version=o["detector_version"],
                entities=_entities_in_span(mentions, o["start_char"], o["end_char"]),
                evidence=[
                    OpenRelationEvidenceModel(
                        document_id=e.get("document_id", doc_id),
                        sentence_id=e.get("sentence_id"),
                        start_char=e.get("start_char"),
                        end_char=e.get("end_char"),
                        quoted_text=e.get("quoted_text"),
                        precision=e.get("precision", "EXACT_SPAN"),
                    )
                    for e in o["evidence_refs"]
                ],
            )
        )
    return details


def _dedup_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def discover_relations_batch(
    pmids: list[str],
    *,
    corpus: Optional[CorpusStore] = None,
    engine: Optional[Engine] = None,
    ingest: bool = True,
    extract: bool = True,
    detector=None,
) -> OpenRelationResponse:
    """Discover relation-bearing spans for a batch of PMIDs (Phase 11). Off the trusted path.

    For each PMID: ensure the paper is in the corpus (the same acquisition gate as ``/analyze``);
    when ``extract`` is true, run discovery and persist ("run + review"), otherwise return the
    already-persisted spans. One paper failing never aborts the batch. ``corpus``/``engine`` are
    injectable so tests run offline; ``detector`` overrides the default (a test fake, or an
    alternative model).
    """
    from ..ingest import ingest_pmid
    from ..openrel import DetectorUnavailableError
    from ..pipeline import discover_open_relations

    corpus = corpus or CorpusStore()
    if engine is None:
        engine = get_engine()
        init_db(engine)

    requested = [normalize_pmid(p) for p in pmids]
    warnings: list[str] = []
    detector_versions: dict[str, str] = {}
    papers: list[PaperOpenRelations] = []

    for pmid in requested:
        doc_id = document_id(pmid)
        try:
            if not corpus.has_canonical(pmid):
                if ingest:
                    ingest_pmid(pmid, corpus=corpus, engine=engine, persist=True)
                else:
                    raise FileNotFoundError(
                        f"{doc_id} not ingested and ingest is disabled; run "
                        f"`mehungry ingest --pmid {pmid}` first"
                    )
            if extract:
                document, observations = discover_open_relations(
                    pmid, corpus=corpus, engine=engine, persist=True, detector=detector
                )
                obs_dicts = [_openobs_to_dict(o) for o in observations]
                title = document.metadata.title
            else:
                obs_dicts = list_open_observations_for_document(engine, doc_id)
                title = _doc_title(engine, doc_id)
            for o in obs_dicts:
                detector_versions[o["detector_name"]] = o["detector_version"]
            papers.append(
                PaperOpenRelations(
                    pmid=pmid,
                    document_id=doc_id,
                    paper_title=title,
                    paper_url=_paper_url(pmid),
                    relations=_build_open_relations(engine, doc_id, obs_dicts),
                )
            )
        except DetectorUnavailableError as exc:
            warnings.append(
                f"{doc_id}: open-relation detector unavailable ({exc}); install the optional "
                "[openrel] extra or pass a detector. Skipped."
            )
        except Exception as exc:  # noqa: BLE001 — one failure must not abort the batch
            warnings.append(f"{doc_id}: discovery failed ({type(exc).__name__}: {exc}); excluded.")

    return OpenRelationResponse(
        run=_run_info(True, extra_ontology=detector_versions),
        papers=papers,
        warnings=_dedup_preserving_order(warnings),
    )


def get_open_relations(pmid: str, *, engine: Optional[Engine] = None) -> PaperOpenRelations:
    """Return the already-persisted relation-bearing spans for one paper (no re-extraction).

    A paper with no persisted spans (never discovered, or the detector flagged none) comes back with
    an empty ``relations`` list — never a 404 — so a reviewer can always open a paper's results.
    """
    if engine is None:
        engine = get_engine()
        init_db(engine)
    doc_id = document_id(normalize_pmid(pmid))
    obs_dicts = list_open_observations_for_document(engine, doc_id)
    return PaperOpenRelations(
        pmid=normalize_pmid(pmid),
        document_id=doc_id,
        paper_title=_doc_title(engine, doc_id),
        paper_url=_paper_url(normalize_pmid(pmid)),
        relations=_build_open_relations(engine, doc_id, obs_dicts),
    )
