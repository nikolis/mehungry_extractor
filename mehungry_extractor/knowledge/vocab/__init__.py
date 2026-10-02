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
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

_DIR = Path(__file__).parent
_DATA = _DIR / "dictionaries.json"

# Fields that make up one entity concept record in ``dictionaries.json`` (and the overlay).
_CONCEPT_FIELDS = ("concept_id", "canonical_name", "entity_type", "surface_forms")


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    with (_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def _raw() -> dict:
    return _load("dictionaries.json")


# =====================================================================================
# Writable overlay — user add/remove/edit layered on top of the checked-in vocabulary.
#
# The bundled ``dictionaries.json`` stays pristine and version-controlled (the reproducible
# baseline). A separate overlay file (default ``data/vocab_overlay.json``, next to the corpus/DB;
# override with ``MEHUNGRY_VOCAB_OVERLAY``) records the user's edits and is MERGED into
# ``load_concepts()`` so they take effect for subsequent extractions. Its shape:
#
#     { "concepts": [ {concept_id, canonical_name, entity_type, surface_forms[]}, ... ],
#       "removed":  [ "NUTR:iron", ... ] }
#
# An overlay ``concepts`` entry whose id matches a built-in one *replaces* it (this is how a
# built-in's ``surface_forms`` are edited); an id not in the built-in set is a net-new concept.
# ``removed`` hides a concept (built-in or custom) entirely. The overlay is never silent: its
# content digest is surfaced through ``overlay_digest`` and recorded on every extraction run, so a
# result produced under a customized vocabulary is distinguishable from the pristine baseline.
# =====================================================================================

_EMPTY_OVERLAY: dict = {"concepts": [], "removed": []}


def overlay_path() -> Path:
    """Where the writable vocabulary overlay lives (env-overridable, defaults next to the data dir)."""
    return Path(os.environ.get("MEHUNGRY_VOCAB_OVERLAY", "data/vocab_overlay.json"))


@functools.lru_cache(maxsize=1)
def _overlay() -> dict:
    """The parsed overlay (``{concepts, removed}``), or an empty overlay when the file is absent.

    Cached like the bundled dictionaries; :func:`reload_overlay` drops the cache after a write.
    """
    path = overlay_path()
    if not path.exists():
        return {"concepts": [], "removed": []}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {
        "concepts": [dict(c) for c in data.get("concepts", [])],
        "removed": list(data.get("removed", [])),
    }


def reload_overlay() -> None:
    """Drop the cached overlay so the next read re-parses the file (call after a write)."""
    _overlay.cache_clear()


def save_overlay(overlay: dict) -> None:
    """Persist the overlay atomically and drop the cache. ``overlay`` is ``{concepts, removed}``."""
    path = overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "concepts": [dict(c) for c in overlay.get("concepts", [])],
        "removed": list(overlay.get("removed", [])),
    }
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)
    reload_overlay()


def overlay_digest() -> Optional[str]:
    """A short content hash of the overlay, or ``None`` when it is empty (pristine vocabulary).

    Recorded in each run's ``ontology_versions`` so outputs under a customized vocabulary are
    distinguishable from the checked-in baseline.
    """
    ov = _overlay()
    if not ov["concepts"] and not ov["removed"]:
        return None
    blob = json.dumps(ov, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def builtin_concepts() -> list[dict]:
    """The checked-in curated concept records only (no overlay), deterministic order as authored."""
    return list(_raw()["concepts"])


def load_concepts() -> list[dict]:
    """Return the **effective** concept records: the curated vocabulary with the overlay applied.

    Built-in records come first in authored order (an overlay entry with the same ``concept_id``
    replaces the built-in in place); net-new overlay concepts follow in overlay order; ids listed
    in the overlay's ``removed`` are omitted. With no overlay this is exactly the checked-in list.
    """
    overlay = _overlay()
    overrides = {c["concept_id"]: c for c in overlay["concepts"]}
    removed = set(overlay["removed"])
    out: list[dict] = []
    seen: set[str] = set()
    for rec in _raw()["concepts"]:
        cid = rec["concept_id"]
        seen.add(cid)
        if cid in removed:
            continue
        out.append(dict(overrides.get(cid, rec)))
    for c in overlay["concepts"]:
        cid = c["concept_id"]
        if cid not in seen and cid not in removed:
            out.append(dict(c))
            seen.add(cid)
    return out


def builtin_ids() -> set[str]:
    """The set of concept ids defined in the checked-in vocabulary (ignores the overlay)."""
    return {rec["concept_id"] for rec in _raw()["concepts"]}


def concept_origin(concept_id: str) -> Optional[str]:
    """Where an *effective* concept comes from: ``builtin`` / ``overridden`` / ``custom``.

    ``None`` when the id is not an effective concept (unknown, or hidden via ``removed``).
    """
    ov = _overlay()
    if concept_id in set(ov["removed"]):
        return None
    is_builtin = concept_id in builtin_ids()
    is_overlay = any(c["concept_id"] == concept_id for c in ov["concepts"])
    if is_builtin:
        return "overridden" if is_overlay else "builtin"
    return "custom" if is_overlay else None


def list_effective_concepts() -> list[dict]:
    """:func:`load_concepts` with an ``origin`` (``builtin``/``overridden``/``custom``) on each."""
    return [{**rec, "origin": concept_origin(rec["concept_id"])} for rec in load_concepts()]


def upsert_concept(record: dict) -> None:
    """Add a new concept or replace an existing one (incl. editing a built-in's surface forms).

    Writes an overlay ``concepts`` entry keyed by ``concept_id`` (replacing any prior overlay entry
    for that id) and clears the id from ``removed`` so an edit also un-hides. ``record`` must carry
    ``concept_id``/``canonical_name``/``entity_type``/``surface_forms``.
    """
    clean = {k: record[k] for k in _CONCEPT_FIELDS}
    ov = _overlay()
    concepts = [dict(c) for c in ov["concepts"] if c["concept_id"] != clean["concept_id"]]
    concepts.append(clean)
    removed = [r for r in ov["removed"] if r != clean["concept_id"]]
    save_overlay({"concepts": concepts, "removed": removed})


def delete_concept(concept_id: str) -> bool:
    """Remove an effective concept. Returns ``False`` if it was not an effective concept.

    A **built-in** is hidden by adding its id to ``removed``; a **custom** (overlay-only) concept is
    dropped from the overlay outright. Either way it stops being recognised on the next extraction.
    """
    if concept_origin(concept_id) is None:
        return False
    ov = _overlay()
    concepts = [dict(c) for c in ov["concepts"] if c["concept_id"] != concept_id]
    removed = list(ov["removed"])
    if concept_id in builtin_ids() and concept_id not in removed:
        removed.append(concept_id)
    save_overlay({"concepts": concepts, "removed": removed})
    return True


def entity_types() -> list[str]:
    """Distinct entity types across the effective vocabulary, sorted — for the UI's type picker."""
    return sorted({rec["entity_type"] for rec in load_concepts()})


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
    "overlay_path",
    "reload_overlay",
    "save_overlay",
    "overlay_digest",
    "builtin_concepts",
    "builtin_ids",
    "concept_origin",
    "list_effective_concepts",
    "upsert_concept",
    "delete_concept",
    "entity_types",
    "load_concepts",
    "load_funders",
    "load_institutions",
    "load_countries",
    "load_disease_states",
    "load_food_sources",
    "load_modifier_relations",
]
