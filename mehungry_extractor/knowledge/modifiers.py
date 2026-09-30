"""Restrictive entity modifiers — the entity-level analogue of the Phase-6 qualifier layer.

A mention of *"dysbiosis of the gut microbiome"* resolves its head to the bare concept
``DIS:dysbiosis``; on its own that loses the restriction the sentence placed on it. This module
recovers the restrictive prepositional phrase and attaches it to the mention as a typed
:class:`EntityModifier` whose value is *itself a resolved concept* — a **text-derived
concept→concept edge**::

    dysbiosis  —localized_in→  gut microbiome   (BIOM:gut_microbiota)

It is the read-from-text sibling of the curated ``found_in`` edges in :mod:`.foods`: same
"edge between two concepts" shape, but its provenance is a source *span* (an ``EXACT_SPAN``
:class:`~.provenance.EvidenceRef`), not an ontology version. Like a :class:`~.qualifiers.Qualifier`
it is a *condition on meaning, not an assertion* — it has **no polarity**, and an object that fails
to normalize is kept ``unmatched`` with its surface, never dropped.

Two detectors, chosen by ``use_model`` (mirroring :mod:`.observations`):

* **Parse (model path).** For each mention's anchor token, walk its attributive prep-phrase
  children (:meth:`.parse.SentenceParse.modifier_phrases`) and resolve each object — preferring an
  already-extracted entity mention under it, else normalizing the phrase.
* **Deterministic floor (``use_model=False``).** No parser: two consecutive mentions joined by
  exactly a modifier preposition (``m1 of the m2``) form the edge, reusing the existing spans.

**Never guess the relation from the preposition** — ``of`` is ambiguous ("dysbiosis *of* the gut
microbiome" vs "risk *of* cancer" vs "reduction *of* inflammation"). The relation is decided by the
resolved object against a curated cue table (:func:`..vocab.load_modifier_relations`): an object in
the ``site_concepts`` allowlist yields ``localized_in``; any other attributive ``of``/``within``
phrase falls back to the generic, non-discriminating ``qualified_by``; a condition preposition like
``in remission`` is left to the qualifier layer and produces no modifier.

Scope (Phase 12, A1): representation, provenance, and display. This module does **not** touch the
claim key — :func:`signature` is provided for the follow-on discrimination work but is unused here,
so every existing claim id is byte-identical.
"""

from __future__ import annotations

import functools
import re
from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from .normalize import normalize
from .provenance import EvidenceRef
from .vocab import MODIFIER_RULES_VERSION, load_modifier_relations

if TYPE_CHECKING:  # avoid import cycles / heavy imports at module load
    from .canonical import Document, Sentence
    from .entities import EntityMention


_PARSE_RULE = "entity_modifier:parse"
_FLOOR_RULE = "entity_modifier:floor"

# The model-free floor's connecting text between two mentions: exactly a modifier preposition
# (optionally with a determiner), and nothing else — so only true "<head> of the <object>"
# adjacencies form an edge.
_FLOOR_GAP_RE = re.compile(r"^\s+(of|in|within)(?:\s+(?:the|a|an))?\s+$", re.IGNORECASE)

# Leading preposition + optional article to strip before normalizing a fallback object phrase.
_LEAD_RE = re.compile(r"^\s*(?:of|in|within|on|for|with)\s+(?:the|a|an)\s+", re.IGNORECASE)


class ModifierRelation(str, Enum):
    """Controlled entity-modifier relations. Grows one per need; ``localized_in`` is first.

    ``qualified_by`` is the generic, deliberately *non-discriminating* fallback for an attributive
    phrase whose object isn't a recognised site (recorded for audit, but not key-bearing).
    """

    LOCALIZED_IN = "localized_in"   # dysbiosis OF the gut microbiome; inflammation IN the colon
    QUALIFIED_BY = "qualified_by"   # generic attributive fallback ("… of X")
    PART_OF = "part_of"             # reserved extension point
    DERIVED_FROM = "derived_from"   # reserved extension point


# Only these relations widen a claim key (A2): a discriminating modifier like ``localized_in`` splits
# "gut dysbiosis" from "oral dysbiosis", while the generic ``qualified_by`` never does, so noise can't
# split claims. Used by :func:`signature` / :func:`signature_from_dicts`.
KEY_BEARING_RELATIONS: frozenset[str] = frozenset({ModifierRelation.LOCALIZED_IN.value})
_KEY_BEARING = KEY_BEARING_RELATIONS  # internal alias, kept for existing references


class EntityModifier(BaseModel):
    """One restrictive modifier of an entity head, resolved to a concept where possible.

    Like a :class:`~.qualifiers.Qualifier` it is a *condition on meaning, not an assertion* — no
    polarity. ``value_concept_id``/``value_type`` are populated only when ``status == "normalized"``;
    an unresolved object keeps its ``value_text`` surface and ``status == "unmatched"``, never
    dropped. ``evidence_refs`` point at the connecting prep-phrase that justified the edge.
    """

    relation: str
    preposition: str
    value_concept_id: Optional[str] = None
    value_text: str
    value_type: Optional[str] = None
    status: str
    evidence_refs: list[EvidenceRef]
    rule_id: str
    rule_version: str


# --- cue table (curated, versioned) ---------------------------------------------------


@functools.lru_cache(maxsize=1)
def _cue_table() -> dict:
    raw = load_modifier_relations()
    return {
        "site_concepts": frozenset(raw.get("site_concepts", [])),
        "localized_in_prepositions": frozenset(
            p.lower() for p in raw.get("localized_in_prepositions", [])
        ),
        "attributive_prepositions": frozenset(
            p.lower() for p in raw.get("attributive_prepositions", [])
        ),
    }


def _relation_for(preposition: str, concept_id: Optional[str], status: str) -> Optional[str]:
    """Type a modifier from its *resolved object* + preposition, or ``None`` to skip.

    ``localized_in`` when the object is a curated site concept under a localizing preposition;
    otherwise the generic ``qualified_by`` for an attributive ``of``/``within`` phrase (including an
    unmatched object); ``None`` for anything else (e.g. a condition ``in`` phrase — a qualifier, not
    a modifier).
    """
    table = _cue_table()
    prep = preposition.lower()
    if (
        status == "normalized"
        and concept_id in table["site_concepts"]
        and prep in table["localized_in_prepositions"]
    ):
        return ModifierRelation.LOCALIZED_IN.value
    if prep in table["attributive_prepositions"]:
        return ModifierRelation.QUALIFIED_BY.value
    return None


# --- extraction -----------------------------------------------------------------------


def extract(
    document: "Document",
    mentions: "list[EntityMention]",
    *,
    use_model: bool = True,
) -> "dict[str, list[EntityModifier]]":
    """Restrictive modifiers per mention id (empty dict when none found).

    ``use_model=True`` uses the parse detector (falling back to the floor per sentence the parser
    can't handle); ``use_model=False`` runs the deterministic, model-free floor throughout. The
    result maps ``mention_id -> [EntityModifier, …]``; a mention with no modifier is simply absent,
    so a caller can assign ``mention.modifiers = result.get(mention.mention_id, [])``.
    """
    by_sentence: dict[str, list["EntityMention"]] = {}
    for m in mentions:
        if m.sentence_id is not None:
            by_sentence.setdefault(m.sentence_id, []).append(m)

    sentences = {s.sentence_id: s for s in document.iter_sentences()}
    parse_on = use_model and _parse_available()

    result: dict[str, list[EntityModifier]] = {}
    for sentence_id, sent_mentions in by_sentence.items():
        sentence = sentences.get(sentence_id)
        if sentence is None:
            continue
        ordered = sorted(sent_mentions, key=lambda m: (m.start_char, m.end_char))

        found: dict[str, list[EntityModifier]] = {}
        if parse_on:
            found = _parse_sentence(document, sentence, ordered)
        if not found:  # floor, or fallback for a sentence the parse yielded nothing for
            found = _floor_sentence(document, sentence, ordered)

        for mention_id, mods in found.items():
            result.setdefault(mention_id, []).extend(mods)

    return result


def annotate(
    document: "Document", mentions: "list[EntityMention]", *, use_model: bool = True
) -> "list[EntityMention]":
    """Attach restrictive modifiers onto ``mentions`` in place (post-pass), returning them.

    The single entry point the pipeline calls after entity extraction: it runs :func:`extract` and
    assigns each mention's ``modifiers`` list (empty when none), so persistence and query read them
    off the mention directly."""
    mods = extract(document, mentions, use_model=use_model)
    for m in mentions:
        m.modifiers = mods.get(m.mention_id, [])
    return mentions


def _parse_available() -> bool:
    from . import parse as _parse

    return _parse.available()


def _parse_sentence(
    document: "Document", sentence: "Sentence", ordered: "list[EntityMention]"
) -> "dict[str, list[EntityModifier]]":
    """Attach modifiers from the dependency parse (model path)."""
    from . import parse as _parse

    sp = _parse.SentenceParse(sentence, ordered)
    out: dict[str, list[EntityModifier]] = {}
    for mention, anchor in sp.anchored_mentions():
        for preposition, obj_head in sp.modifier_phrases(anchor):
            obj_mention = sp.mention_for_token(obj_head)
            if obj_mention is not None and obj_mention.mention_id == mention.mention_id:
                continue  # a token can't modify itself
            concept_id, value_type, value_text, status = _resolve_object(
                document, sp, obj_head, obj_mention
            )
            relation = _relation_for(preposition, concept_id, status)
            if relation is None:
                continue
            start, end = sp.token_span(obj_head)  # "of the gut microbiome"
            out.setdefault(mention.mention_id, []).append(
                _build(
                    document, sentence, start, end, relation, preposition,
                    concept_id, value_text, value_type, status, _PARSE_RULE,
                )
            )
    return out


def _resolve_object(
    document: "Document", sp, obj_head, obj_mention
) -> "tuple[Optional[str], Optional[str], str, str]":
    """Resolve a prep-phrase object to ``(concept_id, value_type, value_text, status)``.

    Prefers an already-extracted entity mention under the object token (reusing its normalization);
    otherwise normalizes the object phrase (leading preposition/article stripped) against the entity
    vocabulary; an unresolved object is kept ``unmatched`` with its surface.
    """
    if obj_mention is not None:
        if obj_mention.concept_id is not None:
            return obj_mention.concept_id, obj_mention.entity_type, obj_mention.surface_text, "normalized"
        return None, None, obj_mention.surface_text, "unmatched"

    start, end = sp.token_span(obj_head)
    surface = document.text[start:end]
    core = _LEAD_RE.sub("", surface).strip()
    norm = normalize(core)
    if norm.concept is not None:
        return norm.concept.concept_id, norm.concept.entity_type, core, "normalized"
    return None, None, core, "unmatched"


def _floor_sentence(
    document: "Document", sentence: "Sentence", ordered: "list[EntityMention]"
) -> "dict[str, list[EntityModifier]]":
    """Deterministic, model-free floor: consecutive mentions joined by exactly a modifier
    preposition ("<head> of the <object>") form an edge, reusing both existing spans."""
    out: dict[str, list[EntityModifier]] = {}
    for head, obj in zip(ordered, ordered[1:]):
        gap = document.text[head.end_char:obj.start_char]
        match = _FLOOR_GAP_RE.match(gap)
        if match is None:
            continue
        preposition = match.group(1).lower()
        status = "normalized" if obj.concept_id is not None else "unmatched"
        relation = _relation_for(preposition, obj.concept_id, status)
        if relation is None:
            continue
        # Evidence spans the connecting phrase + object: "of the gut microbiome".
        lead_ws = len(gap) - len(gap.lstrip())
        start = head.end_char + lead_ws
        out.setdefault(head.mention_id, []).append(
            _build(
                document, sentence, start, obj.end_char, relation, preposition,
                obj.concept_id, obj.surface_text,
                obj.entity_type if obj.concept_id is not None else None,
                status, _FLOOR_RULE,
            )
        )
    return out


def _build(
    document, sentence, start, end, relation, preposition,
    concept_id, value_text, value_type, status, rule_id,
) -> EntityModifier:
    return EntityModifier(
        relation=relation,
        preposition=preposition,
        value_concept_id=concept_id,
        value_text=value_text,
        value_type=value_type,
        status=status,
        evidence_refs=[
            EvidenceRef.for_span(
                document, start, end, sentence=sentence,
                extraction_rule=rule_id, extraction_rule_version=MODIFIER_RULES_VERSION,
            )
        ],
        rule_id=rule_id,
        rule_version=MODIFIER_RULES_VERSION,
    )


# --- claim-key contribution (A2 — empty when no key-bearing modifiers) -----------------


def signature(modifiers: "list[EntityModifier]") -> str:
    """Canonical key contribution of an endpoint's *key-bearing* modifiers — **empty when none**.

    Folded into the claim key (A2, :mod:`.claims`) so "gut dysbiosis" and "oral dysbiosis" stay
    distinct as endpoints. The generic ``qualified_by`` relation never contributes, and an endpoint
    with no key-bearing modifier yields ``""`` — so a modifier-free claim's id is byte-identical to
    before. Sorted ``(relation, value_concept_id or value_text)`` tuples joined stably.
    """
    parts = sorted(
        (m.relation, m.value_concept_id or _key(m.value_text))
        for m in modifiers
        if m.relation in _KEY_BEARING
    )
    return "|".join(f"{rel}={val}" for rel, val in parts)


def signature_from_dicts(modifier_dicts: "list[dict]") -> str:
    """:func:`signature` over persisted/serialized modifier dicts (``relation``/``value_concept_id``/
    ``value_text`` keys) — so the read side (query, synthesis) computes the exact same key."""
    parts = sorted(
        (m["relation"], m.get("value_concept_id") or _key(m.get("value_text") or ""))
        for m in modifier_dicts
        if m["relation"] in _KEY_BEARING
    )
    return "|".join(f"{rel}={val}" for rel, val in parts)


_WS_RE = re.compile(r"\s+")


def _key(surface: str) -> str:
    return _WS_RE.sub(" ", surface).strip().casefold()


def registry() -> list[dict]:
    """The modifier detector rules, for the ``extraction_rules`` table (mirrors :func:`..qualifiers.registry`)."""
    return [
        {
            "rule_id": _PARSE_RULE,
            "version": MODIFIER_RULES_VERSION,
            "predicate": None,
            "description": "Restrictive entity modifier from the dependency parse (prep-phrase off an entity anchor).",
        },
        {
            "rule_id": _FLOOR_RULE,
            "version": MODIFIER_RULES_VERSION,
            "predicate": None,
            "description": "Restrictive entity modifier from the model-free floor (two mentions joined by a modifier preposition).",
        },
    ]
