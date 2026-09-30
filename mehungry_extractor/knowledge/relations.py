"""Deterministic, rule-based relation detection between entity mentions (spec §7).

Rules only — lexical/regex cue patterns applied to the text *connecting* two entity mentions
in the same sentence. No statistical relation classifier, no LLM, and (deliberately) no parser
model, so relation extraction is fully offline and byte-reproducible. Each rule carries a
stable ``rule_id`` and ``version`` and is recorded in the ``extraction_rules`` registry, so
every observation the rules produce can be traced back to the exact rule that fired.

The registry is **ordered by priority**: :func:`match` returns the first rule whose cue matches,
so more specific cues (``no association``, ``reduces the risk``) must precede the generic ones
(``associated with``, ``reduces``) they would otherwise be swallowed by. Each rule also declares
its ``base_polarity`` and whether a negation cue in the sentence should flip that polarity
(:mod:`.negation` applies the flip) — a lexically-negative predicate like ``no_association``
sets polarity itself and must not be re-flipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from . import RULESET_VERSION
from .enums import Polarity


@dataclass(frozen=True)
class RelationRule:
    """One lexical relation rule. ``pattern`` is searched against the connecting text."""

    rule_id: str
    version: str
    predicate: str
    pattern: re.Pattern
    base_polarity: str
    flip_on_negation: bool
    description: str


def _rule(
    rule_id: str,
    predicate: str,
    pattern: str,
    *,
    polarity: str = Polarity.POSITIVE.value,
    flip: bool = True,
    description: str,
) -> RelationRule:
    return RelationRule(
        rule_id=rule_id,
        version=RULESET_VERSION,
        predicate=predicate,
        pattern=re.compile(pattern, re.IGNORECASE),
        base_polarity=polarity,
        flip_on_negation=flip,
        description=description,
    )


# Ordered by priority: the first matching rule wins (see module docstring). Lexically negative
# predicates (no_association/no_effect) come first and carry their own polarity (flip=False), so
# the negation pass does not double-negate them.
RULES: tuple[RelationRule, ...] = (
    _rule(
        "rel_no_association",
        "no_association",
        r"\bno (?:significant )?association\b|\bnot associated\b|\bno (?:significant )?correlation\b",
        polarity=Polarity.NEGATIVE.value,
        flip=False,
        description="explicit absence of association between subject and object",
    ),
    _rule(
        "rel_no_effect",
        "no_effect",
        r"\bno (?:significant )?effect\b|\bno (?:significant )?impact\b|\bhad no effect\b",
        polarity=Polarity.NEUTRAL.value,
        flip=False,
        description="explicit statement of no effect",
    ),
    _rule(
        "rel_contraindicated",
        "contraindicated",
        r"\bcontraindicat\w*\b",
        description="subject is contraindicated with/for object",
    ),
    _rule(
        "rel_adverse_event",
        "associated_with_adverse_event",
        r"\badverse (?:event|effect|reaction|outcome)s?\b",
        description="subject associated with an adverse event",
    ),
    _rule(
        "rel_reduces_risk",
        "reduces_risk",
        r"\b(?:reduc\w+|lower\w+|decreas\w+) (?:the |a )?risk\b",
        description="subject reduces the risk of object",
    ),
    _rule(
        "rel_increases_risk",
        "increases_risk",
        r"\b(?:increas\w+|rais\w+|elevat\w+|higher) (?:the |a )?risk\b",
        description="subject increases the risk of object",
    ),
    # "associated with" is the canonical, explicit relation connective — it outranks the generic
    # directional verbs below so that e.g. "associated with reduced disease activity during
    # remission" is read as an association, not as "decreases remission" (the "reduced" there
    # modifies a non-endpoint noun). Directional verbs still win when no association cue is present.
    _rule(
        "rel_associated_with",
        "associated_with",
        r"\bassociat\w+ with\b|\bassociation between\b|\bcorrelat\w+ with\b|\blinked to\b|\brelated to\b",
        description="subject is associated with object",
    ),
    _rule(
        "rel_prevents",
        "prevents",
        r"\bprevent\w*\b|\bprotect\w* against\b",
        description="subject prevents/protects against object",
    ),
    _rule(
        "rel_causes",
        "causes",
        r"\bcaus\w+\b|\binduc\w+\b|\bleads? to\b|\bled to\b|\bresult\w* in\b",
        description="subject causes/induces object",
    ),
    # Outcome attainment/maintenance (A4): an intervention *reaches* or *sustains* an outcome
    # ("achieved remission", "maintained mucosal healing"). A distinct, outcome-oriented predicate
    # so the role reads as attainment rather than generic causation. "induced" stays with causes.
    _rule(
        "rel_achieves",
        "achieves",
        r"\bachiev\w+\b|\battain\w+\b|\bmaintain\w+\b|\bsustain\w+\b",
        description="subject achieves/maintains an outcome",
    ),
    _rule(
        "rel_improves",
        "improves",
        r"\bimprov\w+\b|\benhanc\w+\b|\bameliorat\w+\b",
        description="subject improves object",
    ),
    _rule(
        "rel_worsens",
        "worsens",
        r"\bworsen\w+\b|\baggravat\w+\b|\bexacerbat\w+\b",
        description="subject worsens object",
    ),
    _rule(
        "rel_increases",
        "increases",
        r"\bincreas\w+\b|\brais\w+\b|\belevat\w+\b",
        description="subject increases object",
    ),
    _rule(
        "rel_decreases",
        "decreases",
        r"\bdecreas\w+\b|\breduc\w+\b|\blower\w+\b",
        description="subject decreases/reduces object",
    ),
)


def match(connecting_text: str) -> "RelationRule | None":
    """Return the highest-priority rule whose cue appears in ``connecting_text`` (or ``None``)."""
    for rule in RULES:
        if rule.pattern.search(connecting_text):
            return rule
    return None


def registry() -> list[RelationRule]:
    """All rules, in priority order — used to populate the ``extraction_rules`` table."""
    return list(RULES)


# =====================================================================================
# Verb-lemma → predicate map (Phase 10 — the parse binder's predicate source)
# =====================================================================================
#
# The flat :data:`RULES` above match a cue in the *connecting text* between two adjacent mentions.
# The Phase-10 parse binder (:mod:`.observations` on the model path) instead locates a predicate
# *verb* by walking the dependency parse and needs to map that verb's lemma to the same controlled
# predicate. This table is the deliberate counterpart of the directional connecting-text cues: it
# covers exactly the lemmas those cues already recognise, and each entry resolves back to the
# **existing** :class:`RelationRule` (via :func:`rule_for_predicate`), so an observation the parse
# binder emits carries the identical ``rule_id``/``version``/``base_polarity``/``flip_on_negation``
# as its flat-binder twin — :mod:`.negation` behaves the same regardless of which binder fired.
#
# Lexical, non-verb predicates (``no_association``/``no_effect``/``contraindicated``/
# ``adverse_event``) are intentionally *absent*: they are not single verbs, so a sentence built on
# them yields no mapped predicate and the parse binder falls back to the flat rules for it.

VERB_PREDICATE_MAP: dict[str, str] = {
    "associate": "associated_with",
    "correlate": "associated_with",
    "link": "associated_with",
    "relate": "associated_with",
    "reduce": "decreases",
    "lower": "decreases",
    "decrease": "decreases",
    "increase": "increases",
    "raise": "increases",
    "elevate": "increases",
    "cause": "causes",
    "induce": "causes",
    "lead": "causes",
    "prevent": "prevents",
    "protect": "prevents",
    "improve": "improves",
    "enhance": "improves",
    "ameliorate": "improves",
    "worsen": "worsens",
    "aggravate": "worsens",
    "exacerbate": "worsens",
    "achieve": "achieves",
    "attain": "achieves",
    "maintain": "achieves",
    "sustain": "achieves",
}

# When the verb's grammatical object head is the noun *risk* ("reduced the **risk** of cancer"),
# the relation is about risk modulation, not a direct decrease/increase of the endpoint — so the
# predicate is promoted to the dedicated risk predicate (matching the flat ``rel_reduces_risk`` /
# ``rel_increases_risk`` cues), and the real endpoints are the nouns under risk's ``of``-phrase.
_RISK_OVERRIDE: dict[str, str] = {
    "decreases": "reduces_risk",
    "increases": "increases_risk",
}

_RULE_BY_PREDICATE: dict[str, RelationRule] = {r.predicate: r for r in RULES}


def rule_for_predicate(predicate: str) -> "RelationRule | None":
    """The :class:`RelationRule` carrying ``predicate`` (its polarity/flip/id/version)."""
    return _RULE_BY_PREDICATE.get(predicate)


def rule_for_verb(lemma: str, *, object_is_risk: bool = False) -> "RelationRule | None":
    """Map a predicate verb's ``lemma`` to its :class:`RelationRule`, or ``None`` if unmapped.

    ``object_is_risk`` promotes ``decreases``/``increases`` to the corresponding risk predicate,
    so *"reduced the risk of cancer"* is bound as ``reduces_risk`` exactly as the flat cue would.
    """
    predicate = VERB_PREDICATE_MAP.get(lemma.lower())
    if predicate is None:
        return None
    if object_is_risk:
        predicate = _RISK_OVERRIDE.get(predicate, predicate)
    return _RULE_BY_PREDICATE.get(predicate)


# =====================================================================================
# Templated, multi-slot patterns (Phase 7)
# =====================================================================================
#
# A flat :class:`RelationRule` is one flavour of relation pattern: a cue searched in the text
# *connecting* two adjacent endpoints. Phase 7's clause scoping needs a slightly richer shape — a
# qualifier concept (e.g. a ``disease_state`` mention) may sit **between** the subject and object
# without breaking the pair. A :class:`RelationTemplate` makes that slot structure explicit: an
# ordered sequence of slots matched within a clause. It is deliberately *additive* — every existing
# flat rule is exactly the template ``subject · cue · object`` (:meth:`RelationTemplate.from_rule`),
# so the flat ``RULES`` keep working unchanged and :mod:`.observations` can drive either form.


class Slot(str, Enum):
    """The ordered constituents a :class:`RelationTemplate` binds within one clause."""

    SUBJECT = "subject"
    QUALIFIER = "qualifier"  # optional — a qualifier concept allowed between subject and object
    CUE = "cue"
    OBJECT = "object"


@dataclass(frozen=True)
class RelationTemplate:
    """An ordered slot sequence whose ``cue`` slot is one :class:`RelationRule`.

    The template carries the same ``rule_id``/``version`` as its cue rule, so an observation it
    produces is traced back identically to a flat-rule observation. ``slots`` documents the
    accepted constituent order; ``QUALIFIER`` is optional (an intervening qualifier concept is
    tolerated but not required), which is what lets *"fiber reduces X in remission"* bind across
    an intervening ``disease_state`` mention.
    """

    rule: RelationRule
    slots: tuple[Slot, ...]

    @property
    def rule_id(self) -> str:
        return self.rule.rule_id

    @property
    def version(self) -> str:
        return self.rule.version

    @property
    def predicate(self) -> str:
        return self.rule.predicate

    @classmethod
    def from_rule(cls, rule: RelationRule) -> "RelationTemplate":
        """Wrap a flat rule as the equivalent ``subject · (qualifier?) · cue · object`` template."""
        return cls(rule=rule, slots=(Slot.SUBJECT, Slot.QUALIFIER, Slot.CUE, Slot.OBJECT))


# Every flat rule, exposed in priority order as a template (the equivalence that keeps the two
# frameworks coexisting). :func:`match` remains the cue-detection primitive both forms share.
TEMPLATES: tuple[RelationTemplate, ...] = tuple(RelationTemplate.from_rule(r) for r in RULES)


def match_template(connecting_text: str) -> "RelationTemplate | None":
    """Template-level counterpart of :func:`match`: the first template whose cue rule fires."""
    rule = match(connecting_text)
    return RelationTemplate.from_rule(rule) if rule is not None else None
