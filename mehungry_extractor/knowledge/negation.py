"""Deterministic negation + uncertainty detection (spec §8).

Cue-based rules over the sentence region leading up to (and including the connector between)
the two entities of a candidate relation. The result is a :class:`Modality` — a ``polarity``
and a ``certainty`` from :mod:`.enums` — plus the triggering cue text, which the caller stores
in ``Observation.context`` so the judgement is auditable.

Precedence, most decisive first:

1. **insufficient / no evidence** → ``neutral`` polarity, ``insufficient_evidence`` certainty.
   This dominates: "insufficient evidence that X is associated with Y" is neither a positive nor
   a negative assertion.
2. **hypothetical** cues (``hypothesized``, ``in theory``) → ``hypothetical`` certainty.
3. **negation** cues (``not``, ``no``, ``failed to`` …) flip a flippable rule's polarity.
4. **uncertainty** cues (``may``, ``might``, ``suggests`` …) → ``possible`` certainty.

Everything is a pinned, versioned regex over the canonical text; no model, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .enums import Certainty, Polarity

NEGATION_RULESET = "negation_cues"
NEGATION_RULESET_VERSION = "0.1.0"

_INSUFFICIENT = re.compile(
    r"\b(?:insufficient|inadequate|no|lack of|limited|weak|inconclusive) evidence\b|\binconclusive\b",
    re.IGNORECASE,
)
_HYPOTHETICAL = re.compile(
    r"\bhypothesi[sz]\w*\b|\bin theory\b|\btheoretically\b|\bspeculat\w+\b", re.IGNORECASE
)
_NEGATION = re.compile(
    r"\b(?:not|no|without|never|neither|nor|failed to|does not|do not|did not|"
    r"was not|were not|is not|are not|cannot|absence of|lack of)\b",
    re.IGNORECASE,
)
_UNCERTAINTY = re.compile(
    r"\b(?:may|might|could|suggest\w*|indicat\w*|possibl\w*|potential\w*|"
    r"appears? to|seems? to|likely|unclear|uncertain)\b",
    re.IGNORECASE,
)


@dataclass
class Modality:
    polarity: str
    certainty: str
    cue: Optional[str]


def analyze(region: str, *, base_polarity: str, flip_on_negation: bool) -> Modality:
    """Judge polarity + certainty for a relation whose evidence spans ``region``.

    ``region`` should cover the sentence text up to (and including) the connector between the
    two entities, so pre-subject cues ("insufficient evidence that …") and inter-entity cues
    ("… was not associated with …") are both seen.
    """
    m = _INSUFFICIENT.search(region)
    if m:
        return Modality(Polarity.NEUTRAL.value, Certainty.INSUFFICIENT_EVIDENCE.value, m.group())

    cues: list[str] = []
    certainty = Certainty.ASSERTED.value
    polarity = base_polarity

    mh = _HYPOTHETICAL.search(region)
    if mh:
        certainty = Certainty.HYPOTHETICAL.value
        cues.append(mh.group())

    if flip_on_negation:
        mn = _NEGATION.search(region)
        if mn:
            if base_polarity == Polarity.POSITIVE.value:
                polarity = Polarity.NEGATIVE.value
            elif base_polarity == Polarity.NEGATIVE.value:
                polarity = Polarity.POSITIVE.value
            cues.append(mn.group())

    if certainty == Certainty.ASSERTED.value:
        mu = _UNCERTAINTY.search(region)
        if mu:
            certainty = Certainty.POSSIBLE.value
            cues.append(mu.group())

    return Modality(polarity, certainty, "; ".join(cues) if cues else None)
