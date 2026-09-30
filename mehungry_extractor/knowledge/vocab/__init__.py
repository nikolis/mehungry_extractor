"""Bundled deterministic controlled vocabulary — where Phase 2 determinism lives.

The curated dictionaries under this package (checked in, versioned) are the backbone of
entity normalization: surface forms → controlled concepts, resolved with no model, no
network, and no randomness. scispaCy (when installed) is only a *candidate generator* whose
surfaces are normalized against this same vocabulary, so the reproducible core never depends
on it.

``VOCAB_VERSION`` is recorded on every :class:`~..run.ExtractionRun` (``ontology_versions``)
and flows into each mention's ``normalization_source`` so outputs from different vocabulary
revisions are distinguishable. Bump it whenever ``dictionaries.json`` changes.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

_DIR = Path(__file__).parent
_DATA = _DIR / "dictionaries.json"


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    with (_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def _raw() -> dict:
    return _load("dictionaries.json")


def load_concepts() -> list[dict]:
    """Return the curated concept records (deterministic order, as authored)."""
    return list(_raw()["concepts"])


def load_funders() -> list[dict]:
    """Curated funder → funder_type records (Phase 4)."""
    return list(_load("funders.json")["funders"])


def load_institutions() -> list[dict]:
    """Curated institution → institution_type records (Phase 4)."""
    return list(_load("institutions.json")["institutions"])


def load_countries() -> list[dict]:
    """Curated country surface forms (Phase 4)."""
    return list(_load("countries.json")["countries"])


def load_disease_states() -> list[dict]:
    """Curated disease-state concept records (Phase 6).

    A *separate* auxiliary vocabulary (like funders/institutions/countries), kept out of the
    entity ``concepts`` index on purpose: disease-state cues qualify a relation's *condition*;
    they must never be extracted as relation endpoints (:mod:`..entities`) nor collide with
    outcome concepts like ``OUT:remission`` during entity normalization.
    """
    return list(_load("disease_states.json")["disease_states"])


def load_food_sources() -> list[dict]:
    """Curated compound/nutrient → food ``found_in`` composition edges (5a).

    A checked-in, versioned ontology (like funders/institutions/countries): each edge links a
    ``source_concept_id`` (a ``CHEM:``/``NUTR:`` concept) to a ``food_concept_id`` it is found in.
    Both endpoints are ids from the entity ``concepts`` vocabulary, so the edges are pure lookups —
    no inference. Consumed by :mod:`..foods`.
    """
    return list(_load("food_sources.json")["edges"])


def load_modifier_relations() -> dict:
    """Curated cue table for typing an entity's restrictive prep-phrase modifier (Phase 12).

    A checked-in, versioned reference table (like the other auxiliary vocabularies): it maps a
    modifier's *resolved object* — not the ambiguous preposition — to a :class:`..modifiers.
    ModifierRelation`. ``site_concepts`` are the concept ids that read as a locus/site, so a
    prep-phrase resolving to one is a ``localized_in`` edge; any other attributive ``of``/``within``
    phrase falls back to the generic ``qualified_by``. Consumed by :mod:`..modifiers`.
    """
    return dict(_load("modifier_relations.json"))


VOCAB_VERSION: str = _raw()["version"]
VOCABULARY_NAME: str = _raw()["vocabulary"]
FUNDERS_VERSION: str = _load("funders.json")["version"]
INSTITUTIONS_VERSION: str = _load("institutions.json")["version"]
COUNTRIES_VERSION: str = _load("countries.json")["version"]
DISEASE_STATES_VERSION: str = _load("disease_states.json")["version"]
FOOD_SOURCES_VERSION: str = _load("food_sources.json")["version"]
MODIFIER_RULES_VERSION: str = _load("modifier_relations.json")["version"]

__all__ = [
    "VOCAB_VERSION",
    "VOCABULARY_NAME",
    "FUNDERS_VERSION",
    "INSTITUTIONS_VERSION",
    "COUNTRIES_VERSION",
    "DISEASE_STATES_VERSION",
    "FOOD_SOURCES_VERSION",
    "MODIFIER_RULES_VERSION",
    "load_concepts",
    "load_funders",
    "load_institutions",
    "load_countries",
    "load_disease_states",
    "load_food_sources",
    "load_modifier_relations",
]
