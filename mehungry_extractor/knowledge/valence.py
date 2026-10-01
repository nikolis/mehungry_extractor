"""Clinical benefit/harm axis over relations (A2 — the "helps / caution" reading).

The relation layer speaks in *lexical* predicates (``improves``, ``reduces_risk``,
``associated_with_adverse_event``, …). A reader wants the *clinical* reading: does this compound
**help**, **harm**, or is it a **caution** factor? That reading is not the predicate alone — it
depends on whether the relation's **object is something we want more of or less of**. Reducing an
undesirable target (``decreases CRP``) is *beneficial*; reducing a desirable one
(``decreases remission``) is *harmful*. This module computes that mapping.

It is a pure, versioned function — no evidence is created or mutated (it mirrors the separation of
:mod:`.assessment`): given a relation's ``predicate`` and its ``object`` concept it returns a
:data:`ClinicalDirection`. The valence is **polarity-independent** on purpose: a negated claim
("fiber does *not* improve remission") is *no benefit shown*, not *harm* — that agreement/refutation
axis is already carried by the synthesis ``direction`` (supported/refuted/conflicting). So a
conclusion pairs a stable clinical **direction** (what the relation *means* when asserted) with the
synthesis direction (whether the papers *agree*).

Everything here is a small checked-in table, versioned by :data:`CLINICAL_VALENCE_VERSION`, so the
reading can evolve without silently changing older outputs.
"""

from __future__ import annotations

from typing import Optional

# Bump when the tables/logic below change the produced clinical reading.
CLINICAL_VALENCE_VERSION = "0.2.0"

# --- the axis -------------------------------------------------------------------------
BENEFICIAL = "beneficial"
HARMFUL = "harmful"
NEUTRAL = "neutral"
CAUTION = "caution"  # a safety signal (adverse event / contraindication), independent of direction

# --- object desirability --------------------------------------------------------------
DESIRABLE = "desirable"       # a target we want more of / to reach (remission, HDL, quality of life)
UNDESIRABLE = "undesirable"   # a target we want less of / to avoid (disease, symptom, CRP, LDL)
NEUTRAL_TARGET = "neutral"    # no inherent health direction (a nutrient/food as object, microbiota)

# Per-concept overrides where the entity-type default is wrong. Kept explicit and auditable.
_DESIRABLE_CONCEPTS: frozenset[str] = frozenset({
    # desirable biomarkers (want higher / adequate)
    "BIOM:hdl", "BIOM:albumin", "BIOM:hemoglobin", "BIOM:ferritin", "BIOM:vitamin_d_level",
})
_UNDESIRABLE_CONCEPTS: frozenset[str] = frozenset({
    # outcomes that are bad to reach
    "OUT:hospitalization", "OUT:surgery",
    # biomarkers we want to lower
    "BIOM:crp", "BIOM:esr", "BIOM:fecal_calprotectin", "BIOM:hba1c", "BIOM:ldl",
    "BIOM:triglycerides", "BIOM:fasting_glucose", "BIOM:insulin", "BIOM:homa_ir",
    "BIOM:blood_pressure", "BIOM:bmi", "BIOM:tnf_alpha", "BIOM:il6", "BIOM:waist_circumference",
    # chemicals framed as bad when elevated
    "CHEM:cholesterol", "CHEM:glucose", "CHEM:tmao", "CHEM:lps",
})

# Predicate groupings by how they act on the object.
_CAUTION_PREDICATES = frozenset({"associated_with_adverse_event", "contraindicated"})
_NULL_PREDICATES = frozenset({"no_effect", "no_association"})
# A directional association reads on the benefit/harm axis exactly like the causal predicate of the
# same direction — "associated with reduced CRP" leans beneficial just as "decreases CRP" does; its
# weaker (non-causal) epistemic standing is carried by `certainty`, not by the valence (see the module
# docstring). So the directional-association predicates join the lowering/raising groups.
_LOWERING_PREDICATES = frozenset({
    "decreases", "reduces_risk", "prevents", "associated_with_reduced",
})
# "achieves" promotes/attains its object (an outcome) — beneficial when that outcome is desirable.
_RAISING_PREDICATES = frozenset({
    "increases", "increases_risk", "causes", "achieves", "associated_with_increased",
})


def object_desirability(object_concept_id: str, object_entity_type: Optional[str] = None) -> str:
    """Is the relation's object something we want more of, less of, or neither?

    Uses explicit per-concept overrides first, then an entity-type default. ``object_entity_type``
    may be passed directly; when omitted it is resolved from the curated vocabulary via the concept
    id, falling back to the id's type prefix so this never needs a DB.
    """
    if object_concept_id in _DESIRABLE_CONCEPTS:
        return DESIRABLE
    if object_concept_id in _UNDESIRABLE_CONCEPTS:
        return UNDESIRABLE

    etype = object_entity_type or _entity_type_of(object_concept_id)
    if etype in ("disease", "symptom"):
        return UNDESIRABLE
    if etype == "outcome":
        return DESIRABLE
    return NEUTRAL_TARGET


def clinical_direction(
    predicate: str,
    object_concept_id: str,
    object_entity_type: Optional[str] = None,
) -> str:
    """The benefit/harm reading of a positively-asserted relation on this object.

    Polarity-independent (see the module docstring): negation/refutation is the synthesis
    ``direction`` axis, not this one. Returns one of :data:`BENEFICIAL`, :data:`HARMFUL`,
    :data:`NEUTRAL`, :data:`CAUTION`.
    """
    if predicate in _CAUTION_PREDICATES:
        return CAUTION
    if predicate in _NULL_PREDICATES:
        return NEUTRAL

    desirability = object_desirability(object_concept_id, object_entity_type)

    # Lexically-valenced predicates carry their reading regardless of the object.
    if predicate == "improves":
        return BENEFICIAL
    if predicate == "worsens":
        return HARMFUL

    if predicate in _LOWERING_PREDICATES:
        if desirability == UNDESIRABLE:
            return BENEFICIAL
        if desirability == DESIRABLE:
            return HARMFUL
        return NEUTRAL
    if predicate in _RAISING_PREDICATES:
        if desirability == UNDESIRABLE:
            return HARMFUL
        if desirability == DESIRABLE:
            return BENEFICIAL
        return NEUTRAL

    # Bare association is non-causal: it leans by the object's desirability but stays a weak signal
    # (the reader still has certainty + agreement to qualify it).
    if predicate == "associated_with":
        if desirability == DESIRABLE:
            return BENEFICIAL
        if desirability == UNDESIRABLE:
            return HARMFUL
        return NEUTRAL

    return NEUTRAL


def _entity_type_of(concept_id: str) -> Optional[str]:
    """Resolve a concept's entity type from the curated vocabulary, else infer from the id prefix."""
    from .normalize import concept_by_id

    concept = concept_by_id(concept_id)
    if concept is not None:
        return concept.entity_type
    return _PREFIX_TYPE.get(concept_id.split(":", 1)[0])


_PREFIX_TYPE = {
    "DIS": "disease",
    "SYMP": "symptom",
    "OUT": "outcome",
    "BIOM": "biomarker",
    "CHEM": "chemical",
    "NUTR": "nutrient",
    "FOOD": "food",
    "INT": "intervention",
}
