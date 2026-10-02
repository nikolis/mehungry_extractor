"""Surface form → controlled concept (deterministic vocabulary linking, spec §6).

Normalization is a pure lookup against the bundled curated vocabulary
(:mod:`.vocab`): no model, no network, no fuzzy matching. A surface is casefolded and its
internal whitespace collapsed, then resolved against the concept index:

* exactly one matching concept → ``normalized``
* several matching concepts (genuine lexical ambiguity) → ``ambiguous`` (no concept chosen —
  we never invent a resolution)
* no match → ``unmatched``

A mention is **never dropped** for failing to normalize; the caller keeps it with the
returned ``status`` and its surface span intact.
"""

from __future__ import annotations

import functools
import re
from typing import Optional

from pydantic import BaseModel

from .vocab import VOCAB_VERSION, VOCABULARY_NAME, load_concepts

# Normalization outcomes (spec §6 ``status``).
STATUS_NORMALIZED = "normalized"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNMATCHED = "unmatched"

# Identifies which vocabulary + revision performed (or attempted) the linking.
NORMALIZATION_SOURCE = f"{VOCABULARY_NAME}:{VOCAB_VERSION}"

_WS_RE = re.compile(r"\s+")


class EntityConcept(BaseModel):
    """A normalized controlled-vocabulary target. Immutable identity is ``concept_id``."""

    concept_id: str
    canonical_name: str
    vocabulary: str
    entity_type: str


class Normalization(BaseModel):
    """Result of resolving one surface form."""

    concept: Optional[EntityConcept] = None
    status: str = STATUS_UNMATCHED
    normalization_source: str = NORMALIZATION_SOURCE


def _key(surface: str) -> str:
    """Canonical lookup key: casefolded, whitespace-collapsed, stripped."""
    return _WS_RE.sub(" ", surface).strip().casefold()


@functools.lru_cache(maxsize=1)
def _index() -> dict[str, list[EntityConcept]]:
    """Surface-key → concepts. Built once from the checked-in vocabulary."""
    index: dict[str, list[EntityConcept]] = {}
    for rec in load_concepts():
        concept = EntityConcept(
            concept_id=rec["concept_id"],
            canonical_name=rec["canonical_name"],
            vocabulary=rec.get("vocabulary", VOCABULARY_NAME),
            entity_type=rec["entity_type"],
        )
        for surface in rec["surface_forms"]:
            index.setdefault(_key(surface), []).append(concept)
    return index


def invalidate_caches() -> None:
    """Drop the surface-form index / id map so they rebuild from the current vocabulary.

    Called after the writable vocabulary overlay changes (see :func:`vocab.save_overlay`), so edits
    take effect for subsequent normalization without restarting the process.
    """
    _index.cache_clear()
    _by_id.cache_clear()
    surface_forms.cache_clear()


@functools.lru_cache(maxsize=1)
def _by_id() -> dict[str, EntityConcept]:
    """concept_id → concept, for resolving a mention's link back to full concept attributes."""
    out: dict[str, EntityConcept] = {}
    for concepts in _index().values():
        for c in concepts:
            out[c.concept_id] = c
    return out


def concept_by_id(concept_id: str) -> Optional[EntityConcept]:
    """Look up a controlled concept by id (``None`` if unknown)."""
    return _by_id().get(concept_id)


@functools.lru_cache(maxsize=1)
def surface_forms() -> list[str]:
    """All known surface keys, longest first — the dictionary matcher's search terms.

    Longest-first ordering lets a regex alternation prefer the longest phrase at a given
    position (e.g. ``dietary fiber`` over ``fiber``), which keeps span selection
    deterministic. Ties broken alphabetically so the order is fully stable.
    """
    return sorted(_index(), key=lambda s: (-len(s), s))


def concepts_for(surface: str) -> list[EntityConcept]:
    """All concepts a surface form maps to (empty if unknown). Read-only view of the index."""
    return list(_index().get(_key(surface), []))


def normalize(surface: str, entity_type: Optional[str] = None) -> Normalization:
    """Resolve a surface form to a concept.

    ``entity_type`` (when known, e.g. a scispaCy label) narrows the candidate set to that
    type before deciding normalized/ambiguous, so a surface that is unambiguous *within its
    type* resolves cleanly.
    """
    candidates = _index().get(_key(surface), [])
    if entity_type is not None:
        candidates = [c for c in candidates if c.entity_type == entity_type]

    if not candidates:
        return Normalization(concept=None, status=STATUS_UNMATCHED)
    if len(candidates) > 1:
        # Distinct concepts share this surface — do not guess which one is meant.
        distinct = {c.concept_id for c in candidates}
        if len(distinct) > 1:
            return Normalization(concept=None, status=STATUS_AMBIGUOUS)
    return Normalization(concept=candidates[0], status=STATUS_NORMALIZED)
