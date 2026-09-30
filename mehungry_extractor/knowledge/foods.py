"""Compound/nutrient → food composition edges (5a — the traceability linking layer).

Most papers study *compounds* (curcumin, omega-3, sulforaphane), but the product question is
"which *foods* help?". Bridging the two needs a **composition graph**: ``curcumin —found_in→
turmeric``, ``omega-3 —found_in→ fish``. This module materializes that graph from the checked-in,
versioned ontology (:func:`..vocab.load_food_sources`) — a pure lookup keyed to the entity
vocabulary's concept ids, **no inference and no new evidence** (the same discipline as the
funder/institution ontologies).

It is deliberately just the *linking layer* (5a). Turning a compound-level finding into a
food-level conclusion ("food containing it helps") is the separate, clearly-labeled *derived*
layer (5b): that leap is inference and must be attributed to (the compound claim's evidence) +
(the ``found_in`` edge + version). This module supplies the edge + version that such a derivation
would cite; it does not itself assert any food↔disease relation.
"""

from __future__ import annotations

import functools
from typing import Optional

from pydantic import BaseModel

from .normalize import concept_by_id
from .vocab import FOOD_SOURCES_VERSION, load_food_sources

FOUND_IN = "found_in"


class CompositionEdge(BaseModel):
    """One ``source —found_in→ food`` edge, resolved to canonical names + types.

    ``ontology``/``ontology_version`` are the edge's provenance: unlike a text-derived fact it has
    no source span — it is asserted by the curated composition ontology, and any derived food-level
    conclusion (5b) must cite exactly this edge + version for the compound→food leap.
    """

    source_concept_id: str
    source_name: str
    source_type: str
    relation: str = FOUND_IN
    food_concept_id: str
    food_name: str
    ontology: str = "mehungry_food_sources"
    ontology_version: str = FOOD_SOURCES_VERSION


@functools.lru_cache(maxsize=1)
def load_edges() -> list[CompositionEdge]:
    """Resolve the ontology into typed :class:`CompositionEdge`s. Deterministic, name-ordered.

    Every endpoint must resolve against the entity vocabulary; an edge with an unknown endpoint is
    a checked-in data error and raises (the shipped ontology is validated so this never fires at
    runtime — it guards against a bad future edit).
    """
    edges: list[CompositionEdge] = []
    for rec in load_food_sources():
        source = concept_by_id(rec["source_concept_id"])
        food = concept_by_id(rec["food_concept_id"])
        if source is None or food is None:
            missing = rec["source_concept_id"] if source is None else rec["food_concept_id"]
            raise ValueError(f"food_sources edge references unknown concept: {missing}")
        edges.append(
            CompositionEdge(
                source_concept_id=source.concept_id,
                source_name=source.canonical_name,
                source_type=source.entity_type,
                food_concept_id=food.concept_id,
                food_name=food.canonical_name,
            )
        )
    edges.sort(key=lambda e: (e.source_concept_id, e.food_concept_id))
    return edges


@functools.lru_cache(maxsize=1)
def _by_source() -> dict[str, list[CompositionEdge]]:
    out: dict[str, list[CompositionEdge]] = {}
    for e in load_edges():
        out.setdefault(e.source_concept_id, []).append(e)
    return out


@functools.lru_cache(maxsize=1)
def _by_food() -> dict[str, list[CompositionEdge]]:
    out: dict[str, list[CompositionEdge]] = {}
    for e in load_edges():
        out.setdefault(e.food_concept_id, []).append(e)
    return out


def food_sources_for(source_concept_id: str) -> list[CompositionEdge]:
    """Foods that a compound/nutrient is found in (empty if none). Deterministic order."""
    return list(_by_source().get(source_concept_id, []))


def compounds_in(food_concept_id: str) -> list[CompositionEdge]:
    """Compounds/nutrients found in a food (empty if none). Deterministic order."""
    return list(_by_food().get(food_concept_id, []))


def registry() -> dict:
    """Ontology identity for the run's ``ontology_versions`` (mirrors the other vocab loaders)."""
    return {"mehungry_food_sources": FOOD_SOURCES_VERSION}
