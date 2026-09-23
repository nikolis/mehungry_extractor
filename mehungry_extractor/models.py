"""Pydantic schemas for the extraction output — the contract with the server.

A ``Finding`` mirrors one row the server upserts into
``condition_recommendation_candidates``. The LLM is asked to emit a list of these;
pydantic validates the shape before we post.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TargetKind = Literal["compound", "nutrient", "food_pattern"]
Direction = Literal["avoid", "limit", "caution", "encourage", "monitor", "neutral"]
Severity = Literal["low", "moderate", "high", "severe"]


class Finding(BaseModel):
    """One phase-aware dietary recommendation extracted from a study."""

    raw_term: str = Field(..., description="The food/compound/nutrient exactly as the paper names it.")
    target_kind: TargetKind = Field(
        ...,
        description=(
            "compound = a specific bioactive chemical; nutrient = a macro/micro nutrient "
            "(fiber, omega-3, sodium…); food_pattern = a dietary pattern or food class "
            "(low-residue, low-FODMAP, raw vegetables…)."
        ),
    )
    direction: Direction = Field(..., description="What the paper advises for this target in this phase.")
    condition_state_slug: str = Field(
        "general",
        description=(
            "The disease phase this advice applies to — one of the provided state slugs, "
            "or 'general' if the paper does not distinguish a phase."
        ),
    )
    severity: Optional[Severity] = None
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="Extractor confidence in this finding.")
    evidence_snippet: Optional[str] = Field(
        None, description="A short verbatim quote from the text supporting this finding."
    )


class Findings(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
