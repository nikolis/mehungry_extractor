"""HTTP-agnostic service for the entity-vocabulary management surface (``/vocab``).

Lists every concept the entity recognizer *can* match and applies add/edit/remove edits. Edits are
persisted to the writable overlay (:func:`vocab.save_overlay`) that layers on top of the checked-in
vocabulary, and each one invalidates the recognizer's caches (:func:`entities.reload_vocabulary`) so
it takes effect for the next extraction in the same process — no restart.

Like :mod:`.service`, this imports no FastAPI; :mod:`.app` is a thin shell over it and the unit
tests drive these functions directly.
"""

from __future__ import annotations

from .. import entities as _entities
from .. import vocab as _vocab
from ..vocab import VOCAB_VERSION, VOCABULARY_NAME
from .models import ConceptModel, ConceptRecord, VocabResponse


class ConceptExistsError(ValueError):
    """Raised when adding a concept whose id already exists (map to HTTP 409)."""


class ConceptNotFoundError(KeyError):
    """Raised when editing/removing a concept that is not in the effective vocabulary (HTTP 404)."""


def _record(concept: dict) -> ConceptRecord:
    return ConceptRecord(
        concept_id=concept["concept_id"],
        canonical_name=concept["canonical_name"],
        entity_type=concept["entity_type"],
        surface_forms=list(concept["surface_forms"]),
        origin=concept["origin"],
    )


def list_vocab() -> VocabResponse:
    """The effective vocabulary: every recognisable concept, each tagged with its ``origin``."""
    return VocabResponse(
        vocabulary=VOCABULARY_NAME,
        version=VOCAB_VERSION,
        overlay_digest=_vocab.overlay_digest(),
        entity_types=_vocab.entity_types(),
        concepts=[_record(c) for c in _vocab.list_effective_concepts()],
    )


def add_concept(model: ConceptModel) -> ConceptRecord:
    """Add a brand-new concept. Raises :class:`ConceptExistsError` if the id already exists."""
    if _vocab.concept_origin(model.concept_id) is not None:
        raise ConceptExistsError(model.concept_id)
    _vocab.upsert_concept(model.model_dump())
    _entities.reload_vocabulary()
    return _effective_or_raise(model.concept_id)


def replace_concept(concept_id: str, model: ConceptModel) -> ConceptRecord:
    """Replace an existing concept's fields (this is how surface forms are edited).

    The path id is authoritative — the body's ``concept_id`` must match it. Raises
    :class:`ConceptNotFoundError` if the id is not currently an effective concept.
    """
    if concept_id != model.concept_id:
        raise ValueError("concept_id in the path and body must match")
    if _vocab.concept_origin(concept_id) is None:
        raise ConceptNotFoundError(concept_id)
    _vocab.upsert_concept(model.model_dump())
    _entities.reload_vocabulary()
    return _effective_or_raise(concept_id)


def remove_concept(concept_id: str) -> None:
    """Remove a concept (hide a built-in, or drop a custom one). Raises if it is not effective."""
    if not _vocab.delete_concept(concept_id):
        raise ConceptNotFoundError(concept_id)
    _entities.reload_vocabulary()


def _effective_or_raise(concept_id: str) -> ConceptRecord:
    for c in _vocab.list_effective_concepts():
        if c["concept_id"] == concept_id:
            return _record(c)
    raise ConceptNotFoundError(concept_id)  # pragma: no cover — written just above
