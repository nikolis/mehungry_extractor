"""Normalize observations into canonical *claims* (spec §5, §7).

A :class:`Claim` is the concept-level form of one-or-more :class:`~.observations.Observation`s
that agree on ``(subject_concept, predicate, object_concept, polarity, certainty)``. Because a
claim is keyed on :class:`~.normalize.EntityConcept` ids, it can only form when **both** endpoint
mentions normalized (``concept_id`` set). An observation whose subject or object is
``unmatched``/``ambiguous`` is retained at the observation layer but produces no claim; the count
of such drops is returned in :class:`NormalizeResult`, never silently swallowed.

Normalization is a **pure function of the observations** — no new evidence is introduced here. A
claim's evidence is exactly the union of its observations' evidence refs (deduplicated, ordered),
which is what the ``claim_evidence`` table stores.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from . import modifiers as _modifiers
from . import qualifiers as _qualifiers
from .modifiers import EntityModifier
from .provenance import EvidenceRef
from .qualifiers import Qualifier

if TYPE_CHECKING:
    from .observations import Observation


class Claim(BaseModel):
    """The normalized, concept-level assertion aggregated from one-or-more observations."""

    claim_id: str
    document_id: str

    # Phase 13 (hierarchical relations) — the claim this one is nested beneath, lifted from its
    # observations' ``parent_observation_id`` (the parent observation's owning claim). Set only when
    # that parent claim is **unambiguous** across all of this claim's observations; left ``None``
    # otherwise — and necessarily ``None`` when the parent relation formed no claim (e.g. its subject
    # did not normalize), since claim-level nesting cannot point at a non-claim (Concept 3: never
    # guess). The observation layer still records the finer-grained link regardless.
    parent_claim_id: Optional[str] = None

    subject_concept_id: str
    subject_name: str
    predicate: str
    object_concept_id: str
    object_name: str

    context: Optional[str] = None
    polarity: str
    certainty: str

    # Phase 6 — the typed conditions this claim holds under (the deduped union of its
    # observations' qualifiers). Claims with differing qualifier signatures never merge.
    qualifiers: list[Qualifier] = []

    # Phase 12 (A2) — the restrictive modifiers on each endpoint (deduped union of the
    # observations'). A *key-bearing* modifier signature widens the claim key, so "gut dysbiosis"
    # and "oral dysbiosis" form separate claims even though their bare concept ids match.
    subject_modifiers: list[EntityModifier] = []
    object_modifiers: list[EntityModifier] = []

    evidence_refs: list[EvidenceRef]
    observation_ids: list[str]


@dataclass
class NormalizeResult:
    claims: list[Claim]
    dropped: int  # observations that could not form a claim (an endpoint didn't normalize)


def _claim_id(
    document_id: str,
    key: tuple[str, str, str, str, str],
    qualifier_sig: str = "",
    subject_modifier_sig: str = "",
    object_modifier_sig: str = "",
) -> str:
    """Stable id: a hash of the canonical claim key, so re-runs reproduce it exactly.

    Each optional signature is appended **only when non-empty**, in a fixed order (qualifier, then
    the subject and object modifier signatures). So a claim with no qualifiers *and* no key-bearing
    modifier hashes the exact same string it did before — its id is byte-identical (backward
    compatibility with Phases 5/6); a non-empty signature forks the id, keeping conditions and
    localized endpoints distinct.
    """
    parts = list(key)
    if qualifier_sig:
        parts.append(qualifier_sig)
    if subject_modifier_sig:
        parts.append(f"subj_mod={subject_modifier_sig}")
    if object_modifier_sig:
        parts.append(f"obj_mod={object_modifier_sig}")
    digest = hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{document_id}_claim_{digest}"


def _dedupe_qualifiers(quals: "list[Qualifier]") -> list[Qualifier]:
    """Union of qualifiers over a claim's observations: merge on identity, union evidence.

    Identity is ``(qualifier_type, value_concept_id or normalized value_text)`` — the same key the
    signature uses. Mirrors :func:`_dedupe_evidence` so a claim's qualifiers are deterministic.
    """
    merged: dict[tuple[str, str], Qualifier] = {}
    order: list[tuple[str, str]] = []
    for q in quals:
        identity = (q.qualifier_type, q.value_concept_id or _qualifiers._key(q.value_text))
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = q.model_copy(deep=True)
            order.append(identity)
        else:
            existing.evidence_refs = _dedupe_evidence(existing.evidence_refs + q.evidence_refs)
    out = [merged[k] for k in order]
    out.sort(key=lambda q: (q.qualifier_type, q.value_concept_id or "", q.value_text.casefold()))
    return out


def _dedupe_modifiers(mods: "list[EntityModifier]") -> "list[EntityModifier]":
    """Union of an endpoint's modifiers over a claim's observations: merge on identity, union evidence.

    Identity is ``(relation, value_concept_id or normalized value_text)`` — the same shape the
    signature keys on. Mirrors :func:`_dedupe_qualifiers` so a claim's modifiers are deterministic.
    """
    merged: dict[tuple[str, str], EntityModifier] = {}
    order: list[tuple[str, str]] = []
    for m in mods:
        identity = (m.relation, m.value_concept_id or _modifiers._key(m.value_text))
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = m.model_copy(deep=True)
            order.append(identity)
        else:
            existing.evidence_refs = _dedupe_evidence(existing.evidence_refs + m.evidence_refs)
    out = [merged[k] for k in order]
    out.sort(key=lambda m: (m.relation, m.value_concept_id or "", m.value_text.casefold()))
    return out


def _dedupe_evidence(refs: list[EvidenceRef]) -> list[EvidenceRef]:
    """Union of evidence refs, deduped on their locating fields, in a deterministic order."""
    seen: set[tuple] = set()
    out: list[EvidenceRef] = []
    for ref in refs:
        k = (ref.sentence_id, ref.start_char, ref.end_char, ref.extraction_rule)
        if k in seen:
            continue
        seen.add(k)
        out.append(ref)
    out.sort(key=lambda r: (r.start_char if r.start_char is not None else -1, r.sentence_id or "", r.extraction_rule))
    return out


def normalize(observations: "list[Observation]", *, concept_name=None) -> NormalizeResult:
    """Fold observations into claims.

    ``concept_name(concept_id) -> str | None`` resolves a concept's canonical name; defaults to
    :func:`mehungry_extractor.knowledge.normalize.concept_by_id`. The subject/object surface
    text is used as a fallback name if the concept can't be resolved (it always can for a
    normalized mention, but the fallback keeps the claim well-formed regardless).
    """
    if concept_name is None:
        from .normalize import concept_by_id

        def concept_name(cid: str):  # type: ignore[misc]
            c = concept_by_id(cid)
            return c.canonical_name if c else None

    # key = (subject, predicate, object, polarity, certainty, qualifier_signature). Folding the
    # signature in is the crux of Phase 6: two observations merge into one claim only when their
    # conditions match, so a remission claim and a flare claim on identical entities stay distinct.
    groups: dict[tuple, list["Observation"]] = {}
    dropped = 0
    for obs in observations:
        if not obs.subject_concept_id or not obs.object_concept_id:
            dropped += 1
            continue
        key = (
            obs.subject_concept_id,
            obs.predicate,
            obs.object_concept_id,
            obs.polarity,
            obs.certainty,
            _qualifiers.signature(obs.qualifiers),
            # A2: a key-bearing endpoint modifier (e.g. localized_in gut microbiome) forks the key,
            # so localized endpoints stay distinct even when their bare concept ids match. Empty for
            # an unmodified endpoint ⇒ the claim groups (and hashes) exactly as before.
            _modifiers.signature(obs.subject_modifiers),
            _modifiers.signature(obs.object_modifiers),
        )
        groups.setdefault(key, []).append(obs)

    claims: list[Claim] = []
    for key, obs_group in groups.items():
        (subject_cid, predicate, object_cid, polarity, certainty,
         qualifier_sig, subj_mod_sig, obj_mod_sig) = key
        evidence = _dedupe_evidence([r for o in obs_group for r in o.evidence_refs])
        qualifiers = _dedupe_qualifiers([q for o in obs_group for q in o.qualifiers])
        subject_modifiers = _dedupe_modifiers([m for o in obs_group for m in o.subject_modifiers])
        object_modifiers = _dedupe_modifiers([m for o in obs_group for m in o.object_modifiers])
        contexts = sorted({o.context for o in obs_group if o.context})
        subj_fallback = obs_group[0].subject_text
        obj_fallback = obs_group[0].object_text
        claims.append(
            Claim(
                claim_id=_claim_id(
                    obs_group[0].document_id, key[:5], qualifier_sig, subj_mod_sig, obj_mod_sig
                ),
                document_id=obs_group[0].document_id,
                subject_concept_id=subject_cid,
                subject_name=concept_name(subject_cid) or subj_fallback,
                predicate=predicate,
                object_concept_id=object_cid,
                object_name=concept_name(object_cid) or obj_fallback,
                context="; ".join(contexts) if contexts else None,
                polarity=polarity,
                certainty=certainty,
                qualifiers=qualifiers,
                subject_modifiers=subject_modifiers,
                object_modifiers=object_modifiers,
                evidence_refs=evidence,
                observation_ids=sorted(o.observation_id for o in obs_group),
            )
        )

    _link_parent_claims(claims, observations)

    claims.sort(
        key=lambda c: (
            c.subject_concept_id, c.predicate, c.object_concept_id, c.polarity, c.certainty,
            _qualifiers.signature(c.qualifiers),
            _modifiers.signature(c.subject_modifiers),
            _modifiers.signature(c.object_modifiers),
        )
    )
    return NormalizeResult(claims=claims, dropped=dropped)


def _link_parent_claims(
    claims: "list[Claim]", observations: "list[Observation]"
) -> None:
    """Lift the observation-level parent link (Phase 13) up to the claim layer.

    A claim's parent is the claim its observations' ``parent_observation_id``s resolve to. Because a
    claim folds several observations, the parent is set **only when it is unambiguous** — every
    parent observation that formed a claim points at the *same* claim — and never to the claim
    itself. When the observations disagree, or the parent relation formed no claim (its subject did
    not normalize, so its observation was dropped), the link is left ``None`` rather than guessed."""
    obs_by_id = {o.observation_id: o for o in observations}
    claim_of_obs: dict[str, str] = {}
    for c in claims:
        for oid in c.observation_ids:
            claim_of_obs[oid] = c.claim_id
    for c in claims:
        parents: set[str] = set()
        for oid in c.observation_ids:
            obs = obs_by_id.get(oid)
            if obs is None or not obs.parent_observation_id:
                continue
            parent_claim = claim_of_obs.get(obs.parent_observation_id)
            if parent_claim and parent_claim != c.claim_id:
                parents.add(parent_claim)
        if len(parents) == 1:
            c.parent_claim_id = next(iter(parents))
