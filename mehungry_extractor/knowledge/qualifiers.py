"""Typed, evidence-bearing *qualifiers* on relations (Phase 6 — the context model).

A bare ``(subject, predicate, object, polarity, certainty)`` triple cannot tell
*"fiber is beneficial **in remission**"* apart from *"fiber may harm **in active flare**"* — the
two collapse into one claim. A :class:`Qualifier` fixes that: it attaches a **typed condition**
(first and only type this phase: :attr:`QualifierType.DISEASE_STATE`) to an
:class:`~.observations.Observation`/:class:`~.claims.Claim`, each carrying its own
:class:`~.provenance.EvidenceRef` back to the cue that justified it.

A qualifier is a *condition*, not an assertion, so it has **no polarity**. Its ``value`` is
normalized against a small, checked-in disease-state vocabulary (:func:`..vocab.load_disease_states`)
when the cue is recognized; when it is not, the qualifier is kept ``unmatched``
(``value_concept_id=None``) with its surface span intact — never dropped.

Extraction is the same rule discipline as :mod:`.relations`/:mod:`.negation`: pinned, versioned
regex cues over the observation's sentence text — no model, no network, byte-reproducible. Each
cue rule carries a ``rule_id`` + ``version`` and is recorded in the ``extraction_rules`` registry.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

from pydantic import BaseModel

from . import RULESET_VERSION
from .provenance import EvidenceRef
from .vocab import load_disease_states

if TYPE_CHECKING:
    from .canonical import Document, Sentence


class QualifierType(str, Enum):
    """Controlled qualifier vocabulary (spec Part II). Grows one type per phase.

    Phase 6 shipped :attr:`DISEASE_STATE`; Phase 7 adds the remaining clause-scoped types
    (``population``/``dose``/``duration``/``route``/``severity``).
    """

    DISEASE_STATE = "disease_state"
    POPULATION = "population"
    DOSE = "dose"
    DURATION = "duration"
    ROUTE = "route"
    SEVERITY = "severity"


class Qualifier(BaseModel):
    """One typed condition a relation holds under, with its own provenance.

    ``value_concept_id`` is populated only when the cue surface normalizes against the
    disease-state vocabulary; otherwise the qualifier is kept with ``value_text`` (the exact
    source surface) and no concept. There is deliberately **no polarity** — a qualifier states a
    condition, it does not assert anything.
    """

    qualifier_type: str
    value_concept_id: Optional[str] = None
    value_text: str
    evidence_refs: list[EvidenceRef]
    rule_id: str
    rule_version: str


# --- disease-state normalization (a dedicated, entity-index-free lookup) --------------


_WS_RE = re.compile(r"\s+")


def _key(surface: str) -> str:
    return _WS_RE.sub(" ", surface).strip().casefold()


@functools.lru_cache(maxsize=1)
def _disease_state_index() -> dict[str, list[str]]:
    """surface-key → distinct disease-state concept ids (its own index, not the entity one)."""
    index: dict[str, list[str]] = {}
    for rec in load_disease_states():
        cid = rec["concept_id"]
        for surface in rec["surface_forms"]:
            bucket = index.setdefault(_key(surface), [])
            if cid not in bucket:
                bucket.append(cid)
    return index


def _normalize_disease_state(surface: str) -> Optional[str]:
    """Resolve a cue surface to a disease-state concept id, or ``None`` when unmatched/ambiguous.

    Never guesses: a surface that maps to several distinct concepts stays unmatched (like
    :func:`..normalize.normalize`'s ambiguous path), so the qualifier keeps its surface instead.
    """
    candidates = _disease_state_index().get(_key(surface), [])
    if len(candidates) == 1:
        return candidates[0]
    return None


# --- cue rules ------------------------------------------------------------------------
#
# Pinned, ordered regex cues, each capturing the qualifier *core* surface in the ``core`` group;
# the core is what gets normalized (so an unrecognized core, e.g. "severe flare", yields an
# ``unmatched`` qualifier rather than being dropped). Every rule declares its :class:`QualifierType`
# and a ``normalize`` callable (``core -> concept_id | None``). ``rule_id`` + ``version`` are
# recorded in ``extraction_rules`` and stamped on every qualifier produced. Rules are scoped to a
# span (a clause, in Phase 7) rather than the whole sentence, so a contrastive clause's condition
# ("… but harmful in active flare") attaches only to that clause's relations.


@dataclass(frozen=True)
class _CueRule:
    rule_id: str
    qualifier_type: str
    pattern: re.Pattern
    normalize: Callable[[str], Optional[str]]
    description: str


def _unnormalized(_core: str) -> None:
    """New Phase-7 types carry no checked-in vocabulary yet — kept unmatched with their surface."""
    return None


_ADJ = r"active|acute|clinical|chronic|inactive|severe|mild|moderate|quiescent"
_HEAD = r"flare-ups?|flareups?|flares?|remission|relapse|quiescence|inflammation|disease"

DISEASE_STATE_CONTEXT_RULE = "qual_disease_state_context"
DISEASE_STATE_ADJECTIVE_RULE = "qual_disease_state_adjective"
POPULATION_RULE = "qual_population"
DOSE_RULE = "qual_dose"
DURATION_RULE = "qual_duration"
ROUTE_RULE = "qual_route"
SEVERITY_RULE = "qual_severity"

# A preposition-triggered condition: "during remission", "in active disease", "in clinical flare".
# The trigger set is deliberately narrow (``during``/``in``/``under``): ``with`` is excluded
# because it is the tail of the relation connective ("associated **with** remission"), where the
# noun is the *object*, not a condition. The pre-core determiners ("the"/"a"/"patients with"/
# "periods of") are consumed so they never leak into the captured core surface, and still let
# "in patients with active disease" fire via the ``in`` trigger.
_CONTEXT = re.compile(
    rf"\b(?:during|in|under)\s+"
    rf"(?:the\s+|a\s+|an\s+|patients?\s+with\s+|periods?\s+of\s+|phases?\s+of\s+|states?\s+of\s+)?"
    rf"(?P<core>(?:(?:{_ADJ})\s+)?(?:{_HEAD}))\b",
    re.IGNORECASE,
)

# A bare adjectival cue that needs no preposition ("… while quiescent").
_ADJECTIVE = re.compile(r"\b(?P<core>quiescent)\b", re.IGNORECASE)

# Population: a study/target group introduced by in/among/for ("in children", "among adults").
_POPULATION = re.compile(
    r"\b(?:in|among|for)\s+(?P<core>children|infants|adults|adolescents|neonates|elderly)\b",
    re.IGNORECASE,
)

# Dose: a strength descriptor ("high-dose", "low dose") or an explicit amount ("≥ 2 g", "400 mg/day").
_DOSE = re.compile(
    r"\b(?P<core>high[- ]dose|low[- ]dose|standard[- ]dose|"
    r"(?:≥\s*|>\s*)?\d+(?:\.\d+)?\s*(?:mg|g|mcg|iu|units?)(?:\s*/\s*(?:day|kg|d))?)\b",
    re.IGNORECASE,
)

# Duration: a time window ("for 12 weeks") or a horizon adjective ("long-term").
_DURATION = re.compile(
    r"\b(?P<core>for\s+\d+\s+(?:days?|weeks?|months?|years?)|long[- ]term|short[- ]term)\b",
    re.IGNORECASE,
)

# Route of administration.
_ROUTE = re.compile(
    r"\b(?P<core>orally|oral|intravenous(?:ly)?|topical(?:ly)?|"
    r"subcutaneous(?:ly)?|intramuscular(?:ly)?)\b",
    re.IGNORECASE,
)

# Severity — but *not* the severity of a disease state (that is captured by the disease-state
# rules as e.g. "severe flare"); the negative lookahead keeps those out so they are not
# double-counted across two qualifier types.
_SEVERITY = re.compile(
    r"\b(?P<core>mild|moderate|severe)\b"
    r"(?!\s+(?:flare|flares|flare-ups?|disease|remission|relapse|inflammation|colitis))",
    re.IGNORECASE,
)

# Ordered: disease-state first (backward compatibility with Phase 6), then the Phase-7 types.
_RULES: tuple[_CueRule, ...] = (
    _CueRule(DISEASE_STATE_CONTEXT_RULE, QualifierType.DISEASE_STATE.value, _CONTEXT,
             _normalize_disease_state,
             "disease-state condition introduced by a preposition (during/in remission …)"),
    _CueRule(DISEASE_STATE_ADJECTIVE_RULE, QualifierType.DISEASE_STATE.value, _ADJECTIVE,
             _normalize_disease_state,
             "disease-state condition from a bare adjectival cue (quiescent)"),
    _CueRule(POPULATION_RULE, QualifierType.POPULATION.value, _POPULATION, _unnormalized,
             "target population the relation holds for (in children/among adults …)"),
    _CueRule(DOSE_RULE, QualifierType.DOSE.value, _DOSE, _unnormalized,
             "dose/strength the relation holds at (high-dose, ≥ 2 g, 400 mg/day …)"),
    _CueRule(DURATION_RULE, QualifierType.DURATION.value, _DURATION, _unnormalized,
             "duration the relation holds over (for 12 weeks, long-term …)"),
    _CueRule(ROUTE_RULE, QualifierType.ROUTE.value, _ROUTE, _unnormalized,
             "route of administration (oral, intravenous …)"),
    _CueRule(SEVERITY_RULE, QualifierType.SEVERITY.value, _SEVERITY, _unnormalized,
             "severity qualifier not bound to a disease state (mild/moderate/severe)"),
)


def extract(
    document: "Document",
    sentence: "Sentence",
    *,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
) -> list[Qualifier]:
    """Detect typed qualifiers within a span of ``sentence``. Deterministic.

    By default the whole sentence is scanned (Phase 6 behaviour, kept for direct callers/tests).
    Pass ``start_char``/``end_char`` — absolute offsets — to scope the scan to a clause (Phase 7),
    so a contrastive clause's condition attaches only to that clause's relations. The offsets must
    lie within the sentence; ``sentence`` supplies the section/paragraph ids for provenance.

    Every cue match becomes a :class:`Qualifier` with an ``EXACT_SPAN`` evidence ref over the core
    surface, so ``document.text[start:end] == value_text`` by construction. Qualifiers are
    deduplicated on ``(qualifier_type, value_concept_id or value_text)`` — a matched cue keys on
    its concept, an unmatched one keys on its surface — and returned in a stable order.
    """
    base = sentence.start_char if start_char is None else start_char
    end = sentence.end_char if end_char is None else end_char
    text = document.text[base:end]
    # Deduplicate on identity, unioning evidence for repeated cues of the same condition.
    by_key: dict[tuple[str, str], Qualifier] = {}
    order: list[tuple[str, str]] = []

    for rule in _RULES:
        for m in rule.pattern.finditer(text):
            core = m.group("core")
            rel_start, rel_end = m.start("core"), m.end("core")
            abs_start, abs_end = base + rel_start, base + rel_end
            concept_id = rule.normalize(core)
            identity = (
                rule.qualifier_type,
                concept_id if concept_id is not None else _key(core),
            )
            evidence = EvidenceRef.for_span(
                document,
                abs_start,
                abs_end,
                sentence=sentence,
                extraction_rule=rule.rule_id,
                extraction_rule_version=RULESET_VERSION,
            )
            existing = by_key.get(identity)
            if existing is None:
                by_key[identity] = Qualifier(
                    qualifier_type=rule.qualifier_type,
                    value_concept_id=concept_id,
                    value_text=core,
                    evidence_refs=[evidence],
                    rule_id=rule.rule_id,
                    rule_version=RULESET_VERSION,
                )
                order.append(identity)
            else:
                # Same condition matched twice (e.g. "quiescent disease" via both rules) — keep
                # one qualifier, union the provenance so every justifying cue is traceable.
                if not any(
                    (r.start_char, r.end_char) == (evidence.start_char, evidence.end_char)
                    for r in existing.evidence_refs
                ):
                    existing.evidence_refs.append(evidence)

    qualifiers = [by_key[k] for k in order]
    qualifiers.sort(key=lambda q: (q.qualifier_type, q.value_concept_id or "", q.value_text.casefold()))
    return qualifiers


def signature(qualifiers: "list[Qualifier]") -> str:
    """Canonical qualifier signature: the claim-grouping key's condition component.

    Sorted ``(qualifier_type, value_concept_id or value_text)`` tuples joined into a stable
    string. **Empty when there are no qualifiers**, so a relation with no detected condition keys
    exactly as it did before Phase 6 (byte-identical claim ids — full backward compatibility).
    A matched qualifier keys on its concept id (so surface variants of the same condition merge);
    an unmatched one keys on its normalized surface (so distinct unknown conditions stay distinct).
    """
    parts = sorted(
        (q.qualifier_type, q.value_concept_id or _key(q.value_text)) for q in qualifiers
    )
    return "|".join(f"{qtype}={value}" for qtype, value in parts)


def registry() -> list[dict]:
    """The qualifier cue rules, for the ``extraction_rules`` table (mirrors :func:`..relations.registry`)."""
    cue_rules = [
        {
            "rule_id": rule.rule_id,
            "version": RULESET_VERSION,
            "predicate": None,
            "description": rule.description,
        }
        for rule in _RULES
    ]
    return cue_rules
