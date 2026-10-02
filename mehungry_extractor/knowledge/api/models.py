"""Pydantic request/response schemas for the batch analysis API.

Only depends on pydantic (a core dependency), so :mod:`.service` can build these without the
optional FastAPI extra installed. FastAPI (in :mod:`.app`) uses ``AnalyzeResponse`` as its
``response_model`` for automatic validation + OpenAPI docs.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..cohesion import DEFAULT_CORE_FRACTION, DEFAULT_OUTLIER_THRESHOLD

MAX_PMIDS = 250


# --- request ------------------------------------------------------------------------


class AnalyzeOptions(BaseModel):
    """Tunables for one analysis request (all have conservative deterministic defaults)."""

    core_fraction: float = Field(
        DEFAULT_CORE_FRACTION,
        gt=0.0,
        le=1.0,
        description="A concept joins the topic core if it appears in at least this fraction of "
        "the papers.",
    )
    outlier_threshold: float = Field(
        DEFAULT_OUTLIER_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="A paper covering less than this fraction of the topic core is an outlier.",
    )
    ingest: bool = Field(
        True,
        description="Fetch PMIDs not already cached from PubMed/PMC (network). If false, "
        "un-ingested PMIDs are reported as errors.",
    )
    use_model: bool = Field(
        True, description="Use the optional scispaCy model as an entity candidate generator when installed."
    )


class AnalyzeRequest(BaseModel):
    pmids: list[str] = Field(..., min_length=1, max_length=MAX_PMIDS)
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)

    @field_validator("pmids")
    @classmethod
    def _clean_pmids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value:
            pmid = (raw or "").strip()
            if pmid and pmid not in seen:
                seen.add(pmid)
                out.append(pmid)
        if not out:
            raise ValueError("no non-empty PMIDs provided")
        return out


# --- observe surface requests (stage-by-stage inspection) ---------------------------


class ObserveIngestRequest(BaseModel):
    """Run the acquisition → canonical stage for one PMID."""

    pmid: str = Field(..., min_length=1)
    force: bool = Field(False, description="Re-download cached raw sources before rebuilding.")
    ingest: bool = Field(True, description="Allow the (single) network fetch. If false, an "
                         "un-cached paper is reported rather than fetched.")


class ObserveStageRequest(BaseModel):
    """Run an offline per-paper stage (entities, or the full extraction pass) for one PMID."""

    pmid: str = Field(..., min_length=1)
    use_model: bool = Field(True, description="Use the scispaCy model as a candidate generator "
                            "when installed.")


class ObserveSynthesizeRequest(BaseModel):
    """Run the batch stage (cohesion + synthesis) over several PMIDs."""

    pmids: list[str] = Field(..., min_length=1, max_length=MAX_PMIDS)
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)

    @field_validator("pmids")
    @classmethod
    def _clean_pmids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value:
            pmid = (raw or "").strip()
            if pmid and pmid not in seen:
                seen.add(pmid)
                out.append(pmid)
        if not out:
            raise ValueError("no non-empty PMIDs provided")
        return out


# --- response -----------------------------------------------------------------------


class RunInfo(BaseModel):
    """The versions that produced this response (reproducibility)."""

    pipeline_version: str
    ruleset_version: str
    extractor_version: str
    schema_version: str
    synthesis_version: str
    clinical_valence_version: str
    git_commit: Optional[str] = None
    python_version: str
    model_available: bool
    ontology_versions: dict[str, str] = Field(default_factory=dict)


class PaperReport(BaseModel):
    """Per-paper outcome: facts + its topic-cohesion standing."""

    pmid: str
    document_id: str
    status: str  # included | outlier | error
    title: Optional[str] = None
    publication_year: Optional[int] = None
    source_type: Optional[str] = None
    study_design: Optional[str] = None
    sample_size: Optional[int] = None
    funder_types: list[str] = Field(default_factory=list)
    funding_independence: Optional[str] = None
    n_claims: int = 0
    concept_count: int = 0
    cohesion_score: Optional[float] = None
    outlier_reason: Optional[str] = None
    missing_core_concepts: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class CoreConcept(BaseModel):
    concept_id: str
    name: Optional[str] = None
    paper_count: int


class TopicInfo(BaseModel):
    core_concepts: list[CoreConcept]
    method: str
    core_fraction: float
    outlier_threshold: float
    min_core_papers: int


class OutlierReport(BaseModel):
    pmid: str
    document_id: str
    reason: str
    cohesion_score: Optional[float] = None
    missing_core_concepts: list[str] = Field(default_factory=list)


class EvidenceQuoteModel(BaseModel):
    document_id: str
    claim_id: str
    quoted_text: str
    section_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    precision: str
    extraction_rule: str
    extraction_rule_version: str


class QualifierModel(BaseModel):
    qualifier_type: str  # disease_state (Phase 6)
    value_concept_id: Optional[str] = None
    value_text: Optional[str] = None


class EntityModifierModel(BaseModel):
    """A restrictive modifier on an endpoint head (Concept 19), serialized for display.

    Recovers the target the head loses on its own: *"dysbiosis"* → ``localized_in`` *gut microbiome*.
    It is a condition on the endpoint's *meaning*, not an assertion, so it carries no polarity. An
    unresolved object is kept (``status == "unmatched"``) with its surface, never dropped.
    """

    relation: str  # localized_in | qualified_by | … (Concept 19)
    preposition: str  # of | in | within — the phrase that introduced the modifier
    value_concept_id: Optional[str] = None
    value_text: str
    value_type: Optional[str] = None
    status: str  # normalized | unmatched


class ConclusionModel(BaseModel):
    subject_concept_id: str
    subject_name: str
    predicate: str
    object_concept_id: str
    object_name: str
    qualifiers: list[QualifierModel] = Field(default_factory=list)
    direction: str  # supported | refuted | conflicting | inconclusive (agreement across papers)
    clinical_direction: str  # beneficial | harmful | neutral | caution (the "helps/caution" reading)
    paper_count: int
    supporting_papers: list[str] = Field(default_factory=list)
    contradicting_papers: list[str] = Field(default_factory=list)
    neutral_papers: list[str] = Field(default_factory=list)
    certainty_counts: dict[str, int] = Field(default_factory=dict)
    design_strength_counts: dict[str, int] = Field(default_factory=dict)
    evidence: list[EvidenceQuoteModel] = Field(default_factory=list)


class CompositionViaModel(BaseModel):
    """The composition edge a derived food conclusion was bridged through (its provenance)."""

    source_concept_id: str
    source_name: str
    relation: str = "found_in"
    ontology: str
    ontology_version: str


class DerivedConclusionModel(BaseModel):
    """A food-level conclusion *derived* from a compound-level one via the composition ontology.

    Kept in its own response section (never mixed into ``conclusions``): ``derived_via`` names the
    mechanism, ``via`` carries the exact ontology edge(s) + version behind the leap, and
    ``evidence`` is the underlying **compound** claim's spans (no new evidence is created).
    """

    food_concept_id: str
    food_name: str
    predicate: str
    object_concept_id: str
    object_name: str
    qualifiers: list[QualifierModel] = Field(default_factory=list)
    direction: str
    clinical_direction: str
    paper_count: int
    supporting_papers: list[str] = Field(default_factory=list)
    contradicting_papers: list[str] = Field(default_factory=list)
    neutral_papers: list[str] = Field(default_factory=list)
    derived_via: str = "composition_ontology"
    via: list[CompositionViaModel] = Field(default_factory=list)
    evidence: list[EvidenceQuoteModel] = Field(default_factory=list)


class BatchFactsModel(BaseModel):
    paper_count: int
    study_designs: dict[str, int] = Field(default_factory=dict)
    publication_year_min: Optional[int] = None
    publication_year_max: Optional[int] = None
    funding_independence: dict[str, int] = Field(default_factory=dict)
    source_types: dict[str, int] = Field(default_factory=dict)


# --- per-paper claims (the normalized concept layer) ---------------------------------


class ClaimEvidenceModel(BaseModel):
    """A source span backing one claim (its provenance; spec §15).

    A claim's evidence is the deduplicated union of its observations' refs. Each span is re-sliced
    from the canonical text as ``reconstructed_text`` so the chain is self-verifying — it should
    equal ``quoted_text`` verbatim.
    """

    document_id: str
    claim_id: str
    section_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    sentence_id: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    quoted_text: Optional[str] = None
    reconstructed_text: Optional[str] = None
    precision: str
    extraction_rule: str
    extraction_rule_version: str
    extractor_version: Optional[str] = None


class ClaimDetail(BaseModel):
    """One normalized, concept-level claim with every detail around it (spec §7, Concept 9).

    A claim folds the observations that agree on ``(subject_concept, predicate, object_concept,
    polarity, certainty)`` into one concept-keyed assertion — the comparable-across-papers layer.
    Both endpoints are resolved concepts (a claim only forms when both normalized), it carries the
    clause-scoped conditions it holds under (``qualifiers``), and its evidence is the deduplicated
    union of its observations' source spans.
    """

    claim_id: str
    # Phase 13 (hierarchical relations) — the claim this one is nested beneath (its subject is that
    # one's object), or null for a top-level claim. Set only when unambiguous across observations.
    parent_claim_id: Optional[str] = None
    subject_concept_id: str
    subject_name: Optional[str] = None
    # Concept 19 — restrictive modifiers on each endpoint head, and the endpoint rendered *with*
    # them folded in (``object_label`` turns a bare "Dysbiosis" into "Dysbiosis of the gut
    # microbiome"), so a reader sees the target the head resolves away on its own.
    subject_modifiers: list[EntityModifierModel] = Field(default_factory=list)
    subject_label: str = ""
    predicate: str
    object_concept_id: str
    object_name: Optional[str] = None
    object_modifiers: list[EntityModifierModel] = Field(default_factory=list)
    object_label: str = ""
    polarity: str  # positive | negative (negation flips the rule's base polarity)
    certainty: str  # asserted | hedged
    context: Optional[str] = None  # negation/uncertainty cue + clause marker, for audit
    qualifiers: list[QualifierModel] = Field(default_factory=list)
    evidence: list[ClaimEvidenceModel] = Field(default_factory=list)


class PaperClaims(BaseModel):
    """A single paper and every claim normalized from it."""

    pmid: str
    document_id: str
    paper_title: Optional[str] = None
    paper_url: Optional[str] = None
    source_type: Optional[str] = None  # open_access | abstract | none — what text the claims came from
    claims_list: list[ClaimDetail] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    run: RunInfo
    requested_pmids: list[str]
    included_pmids: list[str]
    papers: list[PaperReport]
    topic: TopicInfo
    outliers: list[OutlierReport]
    paper_claims: list[PaperClaims] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- open relation discovery (Phase 11 — relation-bearing spans, for human review) ---------
#
# Deliberately unstructured: a discovered "relation" is a stretch of the paper's own text a model
# flagged as asserting *some* relationship. There is no subject/predicate/object field and no
# predicate highlighting, because none has been decided — the reviewable payload is just `text` +
# `score`. These records never enter claims/synthesis (see ``openobs.py``).


class DiscoverRequest(BaseModel):
    pmids: list[str] = Field(..., min_length=1, max_length=MAX_PMIDS)
    extract: bool = Field(
        True,
        description="Run discovery + persist for each paper ('run + review'). If false, only the "
        "already-persisted spans are returned (no recomputation).",
    )

    @field_validator("pmids")
    @classmethod
    def _clean_pmids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value:
            pmid = (raw or "").strip()
            if pmid and pmid not in seen:
                seen.add(pmid)
                out.append(pmid)
        if not out:
            raise ValueError("no non-empty PMIDs provided")
        return out


class OpenRelationEntityModel(BaseModel):
    """A known entity that happens to appear inside a flagged span — OPTIONAL context only.

    Reuses the existing Concept-6 mention detection to *lightly* mark which known entities fall in
    the span. It imposes no relation structure: there is no subject/object role here, just "this
    concept is mentioned somewhere in the sentence the model flagged".
    """

    surface_text: str
    entity_type: str
    concept_id: Optional[str] = None
    start_char: int
    end_char: int


class OpenRelationEvidenceModel(BaseModel):
    """The single ``EXACT_SPAN`` source span behind a discovered relation (its provenance)."""

    document_id: str
    sentence_id: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    quoted_text: Optional[str] = None
    precision: str


class OpenRelationDetail(BaseModel):
    """One relation-bearing span, recorded verbatim. The thing a human reads.

    ``text`` is the relation-bearing sentence/clause in the paper's own words; ``score`` is the
    detector's confidence. There is intentionally no subject/predicate/object — the point of this
    stage is to commit to nothing and surface the natural-language relationship for review.
    """

    open_observation_id: str
    text: str
    sentence_id: Optional[str] = None
    clause_index: Optional[int] = None
    start_char: int
    end_char: int
    score: float
    detector_name: str
    detector_version: str
    entities: list[OpenRelationEntityModel] = Field(default_factory=list)
    evidence: list[OpenRelationEvidenceModel] = Field(default_factory=list)


class PaperOpenRelations(BaseModel):
    """A single paper and every relation-bearing span discovered in it, most-confident first."""

    pmid: str
    document_id: str
    paper_title: Optional[str] = None
    paper_url: Optional[str] = None
    relations: list[OpenRelationDetail] = Field(default_factory=list)


class OpenRelationResponse(BaseModel):
    run: RunInfo  # includes the detector name + version under ``ontology_versions``
    papers: list[PaperOpenRelations] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- entity vocabulary management (the recognisable-entity list + its edits) ----------
#
# The ``/vocab`` surface lists every concept the entity recognizer *can* match and lets a user add,
# edit, or remove them. Edits are stored in a writable overlay on top of the checked-in vocabulary
# (see ``vocab.save_overlay``); each takes effect for subsequent extractions in the same process.

_CONCEPT_ID_RE = r"^[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9_]+$"


class ConceptModel(BaseModel):
    """One entity concept: its id, display name, type, and the surface forms that match it."""

    concept_id: str = Field(..., pattern=_CONCEPT_ID_RE, description="PREFIX:slug, e.g. NUTR:zinc.")
    canonical_name: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1, description="e.g. nutrient / food / outcome / disease.")
    surface_forms: list[str] = Field(..., min_length=1, description="Match strings (case-insensitive).")

    @field_validator("surface_forms")
    @classmethod
    def _clean_surfaces(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value:
            s = (raw or "").strip()
            key = s.casefold()
            if s and key not in seen:
                seen.add(key)
                out.append(s)
        if not out:
            raise ValueError("at least one non-empty surface form is required")
        return out


class ConceptRecord(ConceptModel):
    """A concept as returned by the list endpoint, tagged with where it came from.

    ``origin`` is ``builtin`` (checked-in, unedited), ``overridden`` (checked-in but edited by the
    overlay), or ``custom`` (added by the user, not in the checked-in vocabulary).
    """

    origin: str


class VocabResponse(BaseModel):
    """The effective entity vocabulary: every concept the recognizer can match right now."""

    vocabulary: str
    version: str  # the checked-in VOCAB_VERSION
    overlay_digest: Optional[str] = None  # content hash of the overlay, null when pristine
    entity_types: list[str] = Field(default_factory=list)
    concepts: list[ConceptRecord] = Field(default_factory=list)
