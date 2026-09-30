"""Deterministic cross-paper synthesis of persisted claims + facts (read-side only).

Given a set of documents that have already been extracted (Phases 2-5) and judged on-topic by
:mod:`.cohesion`, this module aggregates their claims into organized, evidence-backed
conclusions. It **adds no new evidence and performs no inference** — it only reads what the
deterministic pipeline already persisted (:mod:`.query`) and groups it, so the same documents
always yield the same synthesis.

A conclusion is one canonical relation ``(subject_concept, predicate, object_concept)`` observed
across the batch, annotated with which papers support/contradict/are-neutral on it, the spread of
assertion certainty, the study-design strength of the papers involved, and a provenance-carrying
source quote. Conflicting evidence is surfaced explicitly, never averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Engine

from . import foods as _foods
from . import modifiers as _modifiers
from . import valence as _valence
from .db import session_scope
from .db.schema import AssessmentRow, DocumentRow, FundingRelationshipRow, StudyCharacteristicRow
from .enums import Polarity
from .query import find_evidence, list_claims_for_document

_STRENGTH_CRITERION = "study_design_strength"
_FUNDING_CRITERION = "funding_independence"

# Conclusion direction across the batch (derived purely from per-paper claim polarity).
SUPPORTED = "supported"
REFUTED = "refuted"
CONFLICTING = "conflicting"
INCONCLUSIVE = "inconclusive"


@dataclass
class EvidenceQuote:
    """One provenance-carrying source quote backing a conclusion direction."""

    document_id: str
    claim_id: str
    quoted_text: str
    section_id: Optional[str]
    paragraph_id: Optional[str]
    sentence_id: Optional[str]
    precision: str
    extraction_rule: str
    extraction_rule_version: str


@dataclass
class Conclusion:
    """One canonical relation aggregated across the batch.

    Phase 6: the grouping key includes the claims' qualifier signature, so the *same* entity pair
    under different disease states yields *separate* conclusions (a conditional conclusion set),
    each carrying the ``qualifiers`` that define its condition.
    """

    subject_concept_id: str
    subject_name: str
    predicate: str
    object_concept_id: str
    object_name: str
    qualifiers: list[dict]
    # Phase 12 (A2) — the restrictive endpoint modifiers; a key-bearing one (e.g. localized_in) makes
    # a localized entity pair its own conclusion rather than merging back into the bare pair.
    subject_modifiers: list[dict]
    object_modifiers: list[dict]
    direction: str
    # Valence A2 — the clinical benefit/harm reading of the relation (beneficial/harmful/neutral/caution).
    # Polarity-independent: whether the papers *agree* is ``direction``; this is what the relation
    # *means* clinically when asserted. Together they read as e.g. "beneficial, supported by 3".
    clinical_direction: str
    paper_count: int
    supporting_papers: list[str]
    contradicting_papers: list[str]
    neutral_papers: list[str]
    certainty_counts: dict[str, int]
    design_strength_counts: dict[str, int]
    evidence: list[EvidenceQuote]


@dataclass
class CompositionVia:
    """The composition edge a derived food conclusion was bridged through (its provenance)."""

    source_concept_id: str   # the compound/nutrient the finding is actually about
    source_name: str
    relation: str            # found_in
    ontology: str
    ontology_version: str


@dataclass
class DerivedConclusion:
    """A food-level conclusion **derived** from a compound-level one via the composition ontology.

    This is the one place the engine crosses from *observed* to *inferred*: a paper found that a
    *compound* helps, and the composition ontology says a *food* contains that compound, so the food
    *may* carry the effect. That leap is made explicit and auditable — ``derived_via`` names the
    mechanism, ``via`` carries the exact ontology edge(s) + version, and ``evidence`` is the original
    **compound** claim's source spans (not new evidence). Kept in its own section so it is never
    mistaken for a directly-observed food↔outcome finding.
    """

    food_concept_id: str
    food_name: str
    predicate: str
    object_concept_id: str
    object_name: str
    qualifiers: list[dict]
    # Phase 12 (A2) — the object endpoint's modifiers carry over from the compound conclusion (the
    # object is unchanged); the subject is now the food, so it has none of its own.
    object_modifiers: list[dict]
    direction: str
    clinical_direction: str
    paper_count: int
    supporting_papers: list[str]
    contradicting_papers: list[str]
    neutral_papers: list[str]
    via: list[CompositionVia]
    evidence: list[EvidenceQuote]
    derived_via: str = "composition_ontology"


@dataclass
class BatchFacts:
    """Batch-level rollup of the papers' study/funding characteristics."""

    paper_count: int
    study_designs: dict[str, int]
    publication_year_min: Optional[int]
    publication_year_max: Optional[int]
    funding_independence: dict[str, int]
    source_types: dict[str, int]


@dataclass
class Synthesis:
    conclusions: list[Conclusion]
    facts: BatchFacts
    # 5b — food-level conclusions bridged from the compound-level ones via the composition
    # ontology. A *separate* section on purpose: derived, clearly-labeled, never mixed into the
    # directly-observed ``conclusions``.
    derived_conclusions: list[DerivedConclusion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strength_by_doc(engine: Engine, doc_ids: list[str]) -> dict[str, str]:
    """document_id -> study_design_strength assessment value (``high``/``moderate``/...)."""
    if not doc_ids:
        return {}
    with session_scope(engine) as session:
        rows = (
            session.query(AssessmentRow)
            .filter(
                AssessmentRow.document_id.in_(doc_ids),
                AssessmentRow.criterion == _STRENGTH_CRITERION,
            )
            .all()
        )
        return {r.document_id: r.value for r in rows}


def _first_quote(engine: Engine, claim_id: str) -> Optional[EvidenceQuote]:
    """The first reconstructable evidence span for a claim (deterministic ordering)."""
    for e in find_evidence(engine, claim_id):
        text = e.get("reconstructed_text") or e.get("quoted_text")
        if text:
            return EvidenceQuote(
                document_id=e["document_id"],
                claim_id=claim_id,
                quoted_text=text,
                section_id=e.get("section_id"),
                paragraph_id=e.get("paragraph_id"),
                sentence_id=e.get("sentence_id"),
                precision=e["precision"],
                extraction_rule=e["extraction_rule"],
                extraction_rule_version=e["extraction_rule_version"],
            )
    return None


def _batch_facts(engine: Engine, doc_ids: list[str]) -> BatchFacts:
    designs: dict[str, int] = {}
    years: list[int] = []
    funding: dict[str, int] = {}
    source_types: dict[str, int] = {}
    if doc_ids:
        with session_scope(engine) as session:
            for sc in (
                session.query(StudyCharacteristicRow)
                .filter(StudyCharacteristicRow.document_id.in_(doc_ids))
                .all()
            ):
                if sc.field == "study_design":
                    designs[sc.value] = designs.get(sc.value, 0) + 1
                elif sc.field == "publication_year":
                    try:
                        years.append(int(sc.value))
                    except (TypeError, ValueError):
                        pass
            for a in (
                session.query(AssessmentRow)
                .filter(
                    AssessmentRow.document_id.in_(doc_ids),
                    AssessmentRow.criterion == _FUNDING_CRITERION,
                )
                .all()
            ):
                funding[a.value] = funding.get(a.value, 0) + 1
            for d in (
                session.query(DocumentRow).filter(DocumentRow.document_id.in_(doc_ids)).all()
            ):
                source_types[d.source_type] = source_types.get(d.source_type, 0) + 1
    return BatchFacts(
        paper_count=len(doc_ids),
        study_designs=dict(sorted(designs.items())),
        publication_year_min=min(years) if years else None,
        publication_year_max=max(years) if years else None,
        funding_independence=dict(sorted(funding.items())),
        source_types=dict(sorted(source_types.items())),
    )


def _qualifier_signature(qualifiers: list[dict]) -> str:
    """Canonical condition signature from a claim's qualifier dicts (matches qualifiers.signature).

    Empty when there are no qualifiers, so unqualified relations group exactly as before Phase 6.
    """
    parts = sorted(
        (
            q["qualifier_type"],
            q.get("value_concept_id") or " ".join((q.get("value_text") or "").split()).casefold(),
        )
        for q in qualifiers
    )
    return "|".join(f"{qtype}={value}" for qtype, value in parts)


def _endpoint_modifier_signature(subject_modifiers: list[dict], object_modifiers: list[dict]) -> str:
    """Combined key-bearing modifier signature for a claim's two endpoints (Phase 12 / A2).

    Delegates to :func:`modifiers.signature_from_dicts` so the read side keys identically to the
    write side. Empty when neither endpoint carries a key-bearing modifier, so unmodified relations
    group exactly as before — a localized pair (e.g. dysbiosis of the gut microbiome) is the only
    thing that forks into its own conclusion.
    """
    subj = _modifiers.signature_from_dicts(subject_modifiers)
    obj = _modifiers.signature_from_dicts(object_modifiers)
    if not subj and not obj:
        return ""
    return f"subj[{subj}]|obj[{obj}]"


def _derive_food_conclusions(conclusions: list[Conclusion]) -> list[DerivedConclusion]:
    """Bridge compound-level conclusions to food-level ones via the composition ontology (5b).

    For each conclusion whose subject is a compound/nutrient found in some food(s), emit a food-level
    conclusion carrying the *same* relation/qualifiers/valence but with the food as subject. Adds no
    new evidence: it reuses the compound conclusion's evidence and papers, and records the exact
    ``found_in`` edge(s) as the provenance for the leap. Conclusions sharing a food + relation +
    condition are merged, listing every compound (``via``) that supports the bridge.
    """
    groups: dict[tuple, dict] = {}
    for c in conclusions:
        edges = _foods.food_sources_for(c.subject_concept_id)
        if not edges:
            continue
        for e in edges:
            if e.food_concept_id == c.object_concept_id:
                continue  # a food that *is* the object — not a meaningful bridge
            key = (
                e.food_concept_id, c.predicate, c.object_concept_id,
                _qualifier_signature(c.qualifiers),
                _modifiers.signature_from_dicts(c.object_modifiers),
            )
            g = groups.setdefault(
                key,
                {
                    "food_name": e.food_name,
                    "object_name": c.object_name,
                    "qualifiers": c.qualifiers,
                    "object_modifiers": c.object_modifiers,
                    "clinical_direction": c.clinical_direction,
                    "pos": set(), "neg": set(), "neu": set(),
                    "via": {},        # source_concept_id -> CompositionVia
                    "evidence": [],   # EvidenceQuote list (deduped below)
                },
            )
            g["pos"].update(c.supporting_papers)
            g["neg"].update(c.contradicting_papers)
            g["neu"].update(c.neutral_papers)
            g["via"].setdefault(
                e.source_concept_id,
                CompositionVia(
                    source_concept_id=e.source_concept_id,
                    source_name=e.source_name,
                    relation=e.relation,
                    ontology=e.ontology,
                    ontology_version=e.ontology_version,
                ),
            )
            g["evidence"].extend(c.evidence)

    derived: list[DerivedConclusion] = []
    for (food_cid, predicate, object_cid, _qsig, _msig), g in groups.items():
        pos, neg, neu = g["pos"], g["neg"], g["neu"]
        if pos and neg:
            direction = CONFLICTING
        elif pos:
            direction = SUPPORTED
        elif neg:
            direction = REFUTED
        else:
            direction = INCONCLUSIVE
        # dedupe evidence on its locating identity, deterministic order
        seen: set[tuple] = set()
        evidence: list[EvidenceQuote] = []
        for q in sorted(g["evidence"], key=lambda e: (e.document_id, e.claim_id, e.quoted_text)):
            k = (q.document_id, q.claim_id)
            if k not in seen:
                seen.add(k)
                evidence.append(q)
        derived.append(
            DerivedConclusion(
                food_concept_id=food_cid,
                food_name=g["food_name"],
                predicate=predicate,
                object_concept_id=object_cid,
                object_name=g["object_name"],
                qualifiers=g["qualifiers"],
                object_modifiers=list(g["object_modifiers"]),
                direction=direction,
                clinical_direction=g["clinical_direction"],
                paper_count=len(pos | neg | neu),
                supporting_papers=sorted(pos),
                contradicting_papers=sorted(neg),
                neutral_papers=sorted(neu),
                via=[g["via"][k] for k in sorted(g["via"])],
                evidence=evidence,
            )
        )

    derived.sort(
        key=lambda d: (
            -d.paper_count, d.food_name.casefold(), d.predicate, d.object_name.casefold(),
            d.food_concept_id, d.object_concept_id, _qualifier_signature(d.qualifiers),
            _modifiers.signature_from_dicts(d.object_modifiers),
        )
    )
    return derived


def synthesize(engine: Engine, document_ids: list[str]) -> Synthesis:
    """Aggregate the given documents' persisted claims into ranked conclusions. Deterministic."""
    doc_ids = sorted(set(document_ids))
    warnings: list[str] = []
    strength = _strength_by_doc(engine, doc_ids)

    # key -> accumulator across papers. The qualifier signature is part of the key (Phase 6), so a
    # remission claim and a flare claim on the same entity pair accumulate into distinct groups.
    groups: dict[tuple[str, str, str, str, str], dict] = {}
    for doc_id in doc_ids:
        for c in list_claims_for_document(engine, doc_id):
            qualifiers = c.get("qualifiers") or []
            subject_modifiers = c.get("subject_modifiers") or []
            object_modifiers = c.get("object_modifiers") or []
            key = (
                c["subject_concept_id"],
                c["predicate"],
                c["object_concept_id"],
                _qualifier_signature(qualifiers),
                # A2: a localized endpoint pair is its own conclusion, not merged into the bare pair.
                _endpoint_modifier_signature(subject_modifiers, object_modifiers),
            )
            g = groups.setdefault(
                key,
                {
                    "subject_name": c["subject_name"],
                    "object_name": c["object_name"],
                    "qualifiers": qualifiers,
                    "subject_modifiers": subject_modifiers,
                    "object_modifiers": object_modifiers,
                    "pos": set(),
                    "neg": set(),
                    "neu": set(),
                    "papers": set(),
                    "certainty": {},
                    "pos_claims": [],
                    "neg_claims": [],
                },
            )
            g["papers"].add(doc_id)
            g["certainty"][c["certainty"]] = g["certainty"].get(c["certainty"], 0) + 1
            if c["polarity"] == Polarity.POSITIVE.value:
                g["pos"].add(doc_id)
                g["pos_claims"].append(c["claim_id"])
            elif c["polarity"] == Polarity.NEGATIVE.value:
                g["neg"].add(doc_id)
                g["neg_claims"].append(c["claim_id"])
            else:
                g["neu"].add(doc_id)

    conclusions: list[Conclusion] = []
    for (subject_cid, predicate, object_cid, _qsig, _msig), g in groups.items():
        pos, neg, neu, papers = g["pos"], g["neg"], g["neu"], g["papers"]
        if pos and neg:
            direction = CONFLICTING
        elif pos:
            direction = SUPPORTED
        elif neg:
            direction = REFUTED
        else:
            direction = INCONCLUSIVE

        strength_counts: dict[str, int] = {}
        for d in papers:
            value = strength.get(d, "unknown")
            strength_counts[value] = strength_counts.get(value, 0) + 1

        evidence: list[EvidenceQuote] = []
        for claim_id in (
            (sorted(set(g["pos_claims"]))[:1]) + (sorted(set(g["neg_claims"]))[:1])
        ):
            quote = _first_quote(engine, claim_id)
            if quote is not None:
                evidence.append(quote)

        conclusions.append(
            Conclusion(
                subject_concept_id=subject_cid,
                subject_name=g["subject_name"],
                predicate=predicate,
                object_concept_id=object_cid,
                object_name=g["object_name"],
                qualifiers=[
                    {
                        "qualifier_type": q["qualifier_type"],
                        "value_concept_id": q.get("value_concept_id"),
                        "value_text": q.get("value_text"),
                    }
                    for q in g["qualifiers"]
                ],
                subject_modifiers=list(g["subject_modifiers"]),
                object_modifiers=list(g["object_modifiers"]),
                direction=direction,
                clinical_direction=_valence.clinical_direction(predicate, object_cid),
                paper_count=len(papers),
                supporting_papers=sorted(pos),
                contradicting_papers=sorted(neg),
                neutral_papers=sorted(neu),
                certainty_counts=dict(sorted(g["certainty"].items())),
                design_strength_counts=dict(sorted(strength_counts.items())),
                evidence=evidence,
            )
        )

    # Rank: most-corroborated first, then a fully deterministic tie-break.
    conclusions.sort(
        key=lambda c: (
            -c.paper_count,
            c.subject_name.casefold(),
            c.predicate,
            c.object_name.casefold(),
            c.subject_concept_id,
            c.object_concept_id,
            _qualifier_signature(c.qualifiers),
            _endpoint_modifier_signature(c.subject_modifiers, c.object_modifiers),
        )
    )

    if doc_ids and not conclusions:
        warnings.append(
            "no normalized claims were extracted from the included papers, so no cross-paper "
            "conclusions could be synthesized."
        )

    return Synthesis(
        conclusions=conclusions,
        facts=_batch_facts(engine, doc_ids),
        derived_conclusions=_derive_food_conclusions(conclusions),
        warnings=warnings,
    )
