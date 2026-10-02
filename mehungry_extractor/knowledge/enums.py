"""Controlled enums shared across the relation/claim layer (spec §8).

Kept in one module so ``relations``/``negation``/``observations``/``claims`` and the DB layer
all agree on the exact string values that get persisted. These are ``str`` enums, so pydantic
serializes them to their string value and SQLite stores plain strings.
"""

from __future__ import annotations

from enum import Enum


class Polarity(str, Enum):
    """Direction of an asserted relation (spec §8)."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class Certainty(str, Enum):
    """How strongly a relation is asserted in the source text (spec §8)."""

    ASSERTED = "asserted"
    POSSIBLE = "possible"
    UNCERTAIN = "uncertain"
    HYPOTHETICAL = "hypothetical"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# The relation predicate vocabulary (spec §7). Deliberately small; grow rule-by-rule.
PREDICATES = (
    "associated_with",
    # Directional associations — a bare association over a (non-risk) object that carries a direction
    # word ("associated with **reduced** CRP", "linked to **lower** disease activity"). They keep the
    # non-causal association framing (the epistemic weakness stays on `certainty`) while recording the
    # direction the predicate alone would otherwise lose, so the clinical valence (benefit/harm) reads
    # correctly. They are the association analogue of `decreases`/`increases`. (An association over a
    # *risk* object stays the causal `reduces_risk`/`increases_risk` — reviewer-confirmed.)
    "associated_with_reduced",
    "associated_with_increased",
    "increases",
    "decreases",
    "improves",
    "worsens",
    "reduces_risk",
    "increases_risk",
    "causes",
    "achieves",
    "prevents",
    "no_effect",
    "no_association",
    "contraindicated",
    "associated_with_adverse_event",
    # Descriptive / definitional relation (Phase 13 — hierarchical relations). "X characterized by Y"
    # does not assert benefit/harm; it elaborates what a condition *is*. It most often appears in a
    # subordinate clause modifying the parent relation's object (so it is typically nested beneath it,
    # see the hierarchical-relations concept), and is read as clinically NEUTRAL by `valence`.
    "characterized_by",
    # Descriptive *abundance-manifestation* relations (the same descriptive family as
    # ``characterized_by``). A free-adjunct participle on a *state* — "dysbiosis, **decreasing**
    # Firmicutes … and **increasing** Proteobacteria" — does not describe an agent acting, it
    # describes a compositional change that is part of the state's manifestation. So the active
    # ``decreases``/``increases`` reading is replaced, in that construction only, by a descriptive
    # predicate that records the *direction* of the abundance change without asserting benefit/harm
    # (the per-taxon valence the engine does not know). Clinically NEUTRAL, never flipped by negation.
    "has_decreased_abundance_of",
    "has_increased_abundance_of",
)
