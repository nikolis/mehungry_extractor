"""Relation-layer quality metrics from self-referential relations (a label-free precision probe).

A *self-loop* is a relation whose subject and object resolve to the same concept
(``Inflammatory bowel disease — associated_with — Inflammatory bowel disease``). Such a claim is
wrong by construction — a concept cannot stand in a directional relation to itself — so the rate
at which the deterministic relation layer (:mod:`.relations` → :mod:`.observations` →
:mod:`.claims`) produces them is a **directly observable, zero-label lower bound on its
false-positive rate**.

Why a *floor* and not the whole story: a self-loop is only the visible fraction of the spurious
population — the pairs whose two endpoints happened to collapse onto one concept. The same
mechanisms that create self-loops (broad lexical cues fired between two mentions the cue does not
actually govern; adjacency pairing across clause/sentence boundaries; coordinate lists; compound
head-noun matches) also relate two *distinct* concepts, and those look plausible. To turn the
floor into an estimate we correct by the *detectability* of a spurious pair — the background
probability that two adjacent, both-normalized mentions in a sentence already share a concept
(:func:`aliasing_probability`, ``p_alias``). Under the stated assumption that spurious relations
alias at the same rate as the background pair population::

    self_loop_rate    = self_loops / total_claims              # observed FP floor (hard)
    estimated_fp_rate ≈ self_loop_rate / p_alias  (clamped ≤1) # model-based (soft)

Everything here is a **pure function of already-extracted rows** — no model, no network, no new
evidence — mirroring :func:`mehungry_extractor.knowledge.claims.normalize`. The query/audit
surface (:func:`mehungry_extractor.knowledge.query.relation_quality_report`,
:func:`mehungry_extractor.knowledge.audit.render_relation_quality`) assembles the inputs from the
store and calls in here; the computation itself is offline and byte-reproducible.

The ``mechanism`` histogram buckets each self-loop by the cheapest signal that explains it, so the
metric is *actionable* — it names which shared layer to fix, not merely that precision is low:

* ``sub_phrase``  — the two mentions have different surface text but one concept
  (``dietary fiber`` / ``fiber``): a compound/head-noun vocabulary-granularity problem.
* ``long_window`` — the connecting text between the mentions is long: the pair was formed across
  a clause or (on a segmentation error) sentence boundary; the real endpoints are elsewhere.
* ``list_like``   — the connecting text is only separators/conjunctions: a coordinate
  enumeration of endpoints mistaken for a predicate.
* ``topic_repeat``— the remainder: same surface, short window; the concept is the document's
  topic, repeated as context on both sides of a cue that actually relates two other entities
  (this bucket also absorbs nominalizations such as ``prevention``/``association``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

# A connecting window wider than this many characters is treated as a cross-clause / segmentation
# artifact rather than a local relation. ~80 chars is roughly one clause of scientific prose; the
# median sentence in the corpus is ~150 chars, so this catches pairs that span most of a sentence.
LONG_WINDOW_CHARS = 80

# Connecting text that is *only* separators and coordinating conjunctions — an enumeration, not a
# predicate ("clinical remission, endoscopic improvement and remission").
_LIST_LIKE_RE = re.compile(r"^[\s,;/&()\[\]-]*(?:\b(?:and|or|as well as|versus|vs)\b[\s,;/&()\[\]-]*)*$", re.IGNORECASE)

MECHANISMS = ("sub_phrase", "long_window", "list_like", "topic_repeat")


class _ClaimLike(Protocol):
    subject_concept_id: str
    object_concept_id: str
    predicate: str


class _ObsLike(Protocol):
    observation_id: str
    subject_concept_id: Optional[str]
    object_concept_id: Optional[str]
    predicate: str
    rule_id: str
    subject_text: str
    object_text: str


class _MentionLike(Protocol):
    sentence_id: Optional[str]
    concept_id: Optional[str]
    start_char: int
    end_char: int


@dataclass(frozen=True)
class ObsGeometry:
    """Per-observation geometry the store supplies for mechanism bucketing (both spans + gap text)."""

    distance: int  # object.start_char - subject.end_char (chars of connecting text)
    connecting_text: str


@dataclass
class RelationQuality:
    """The relation-layer precision probe for one extraction run. All counts are exact.

    ``self_loop_rate`` is the observed false-positive floor; ``estimated_fp_rate`` is the
    floor divided by ``p_alias`` (detectability), clamped to ``[0, 1]`` — a model-based
    estimate, not a measurement. ``by_predicate`` / ``by_rule_id`` map to ``(self_loops, total)``
    so a spike is traceable to the exact cue in the ``extraction_rules`` registry.
    """

    run_id: Optional[str]
    total_claims: int
    self_loops: int
    by_predicate: dict[str, tuple[int, int]] = field(default_factory=dict)
    by_rule_id: dict[str, tuple[int, int]] = field(default_factory=dict)
    by_document: dict[str, tuple[int, int]] = field(default_factory=dict)
    mechanism: dict[str, int] = field(default_factory=dict)
    p_alias: Optional[float] = None

    @property
    def self_loop_rate(self) -> float:
        return self.self_loops / self.total_claims if self.total_claims else 0.0

    @property
    def estimated_fp_rate(self) -> Optional[float]:
        if not self.p_alias:  # None or 0 → cannot divide; the floor is all we can honestly report
            return None
        return min(1.0, self.self_loop_rate / self.p_alias)

    def to_dict(self) -> dict:
        """JSON-friendly view (tuples → lists), matching the query layer's dict convention."""
        return {
            "run_id": self.run_id,
            "total_claims": self.total_claims,
            "self_loops": self.self_loops,
            "self_loop_rate": self.self_loop_rate,
            "p_alias": self.p_alias,
            "estimated_fp_rate": self.estimated_fp_rate,
            "by_predicate": {k: list(v) for k, v in self.by_predicate.items()},
            "by_rule_id": {k: list(v) for k, v in self.by_rule_id.items()},
            "by_document": {k: list(v) for k, v in self.by_document.items()},
            "mechanism": dict(self.mechanism),
        }


def _is_self_loop_claim(c: _ClaimLike) -> bool:
    # A claim always has both concept ids set (that is what makes it a claim); the guard keeps the
    # function total if called on partially-built rows.
    return bool(c.subject_concept_id) and c.subject_concept_id == c.object_concept_id


def _classify_mechanism(obs: _ObsLike, geom: Optional[ObsGeometry]) -> str:
    """Bucket one self-loop observation by the cheapest signal that explains it (priority order)."""
    if obs.subject_text.strip().casefold() != obs.object_text.strip().casefold():
        return "sub_phrase"
    if geom is not None:
        if _LIST_LIKE_RE.match(geom.connecting_text):
            return "list_like"
        if geom.distance > LONG_WINDOW_CHARS:
            return "long_window"
    return "topic_repeat"


def aliasing_probability(mentions: Iterable[_MentionLike]) -> Optional[float]:
    """``p_alias``: P(two adjacent, both-normalized mentions in a sentence share a concept).

    This mirrors the consecutive-pair scan the flat floor binder uses
    (:func:`mehungry_extractor.knowledge.observations._flat_bind_sentence`; the Phase-10 parse
    binder binds by dependency structure instead): within each sentence the normalized mentions
    are ordered by span and every non-overlapping neighbour pair is a candidate. It is the
    *detectability* of a spurious relation — the chance one would surface as
    a self-loop rather than as a plausible cross-concept relation — and is computed independently
    of which relations actually fired, so it is not circular. ``None`` if there are no candidate
    pairs (division undefined).
    """
    by_sentence: dict[str, list[_MentionLike]] = {}
    for m in mentions:
        if m.sentence_id is None or not m.concept_id:
            continue
        by_sentence.setdefault(m.sentence_id, []).append(m)

    pairs = 0
    aliased = 0
    for sent_mentions in by_sentence.values():
        ordered = sorted(sent_mentions, key=lambda m: (m.start_char, m.end_char))
        for subj, obj in zip(ordered, ordered[1:]):
            if obj.start_char < subj.end_char:  # overlapping spans — not a candidate pair
                continue
            pairs += 1
            if subj.concept_id == obj.concept_id:
                aliased += 1
    return aliased / pairs if pairs else None


def _bump(table: dict[str, tuple[int, int]], key: str, *, loop: bool) -> None:
    loops, total = table.get(key, (0, 0))
    table[key] = (loops + (1 if loop else 0), total + 1)


def relation_quality(
    claims: Iterable[_ClaimLike],
    observations: Iterable[_ObsLike],
    *,
    run_id: Optional[str] = None,
    geometry: Optional[dict[str, ObsGeometry]] = None,
    p_alias: Optional[float] = None,
) -> RelationQuality:
    """Fold claims + observations into the relation-quality probe. Pure and deterministic.

    ``geometry`` maps ``observation_id → ObsGeometry`` for the ``long_window``/``list_like``
    mechanism buckets; when absent those self-loops fall back to ``topic_repeat`` (the metric
    still reports correct rates, only the mechanism split is coarser). ``p_alias`` is the
    detectability from :func:`aliasing_probability`; when absent, ``estimated_fp_rate`` is
    ``None`` and only the observed floor is reported.
    """
    geometry = geometry or {}

    by_predicate: dict[str, tuple[int, int]] = {}
    by_document: dict[str, tuple[int, int]] = {}
    total = 0
    self_loops = 0
    for c in claims:
        total += 1
        loop = _is_self_loop_claim(c)
        if loop:
            self_loops += 1
        _bump(by_predicate, c.predicate, loop=loop)
        _bump(by_document, getattr(c, "document_id", "?"), loop=loop)

    # Rule attribution + mechanism buckets are read off the observation layer, where the firing
    # rule_id and both surface strings live. A claim aggregates observations that agree on the
    # concept-level key, so counting self-loop observations per rule is the faithful attribution.
    by_rule_id: dict[str, tuple[int, int]] = {}
    mechanism: dict[str, int] = {m: 0 for m in MECHANISMS}
    for o in observations:
        loop = bool(o.subject_concept_id) and o.subject_concept_id == o.object_concept_id
        _bump(by_rule_id, o.rule_id, loop=loop)
        if loop:
            mechanism[_classify_mechanism(o, geometry.get(o.observation_id))] += 1

    return RelationQuality(
        run_id=run_id,
        total_claims=total,
        self_loops=self_loops,
        by_predicate=dict(sorted(by_predicate.items())),
        by_rule_id=dict(sorted(by_rule_id.items())),
        by_document=dict(sorted(by_document.items())),
        mechanism=mechanism,
        p_alias=p_alias,
    )
