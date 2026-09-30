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
)
