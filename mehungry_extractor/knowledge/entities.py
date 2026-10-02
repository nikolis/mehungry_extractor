"""Deterministic entity-mention extraction over a canonical :class:`Document` (spec §6).

This is the first layer that produces *knowledge*, so provenance discipline starts here:
every mention carries an ``EXACT_SPAN`` :class:`~.provenance.EvidenceRef` and is
reconstructable from its offsets (``document.text[start:end] == surface_text``).

Two span sources, unified through the same offline normalizer (:mod:`.normalize`):

* **scispaCy** (the primary detector for its labels) — the ``en_ner_bc5cdr_md`` model tags
  Chemical/Disease spans and is the *authoritative* source for those two types: it runs
  first and wins any overlap with the dictionary. It is a **required** runtime dependency of
  the default path; when ``use_model=True`` (the default) and the model cannot be loaded,
  extraction raises :class:`ModelUnavailableError` rather than silently degrading.
* **Dictionary matching** (the auditable floor + coverage for everything else) — the
  checked-in vocabulary's surface forms are matched longest-first, case-insensitively, within
  each canonical sentence. It is the *sole* source for the six entity types scispaCy cannot
  see (food, nutrient, biomarker, intervention, symptom, outcome), it gap-fills
  Chemical/Disease spans the model missed, and — crucially — it still performs *all* concept
  normalization, including for scispaCy's spans.

Pass ``use_model=False`` for the dictionary-only floor (deterministic, model-free); it is the
one path that neither touches nor requires the model. A mention that fails to normalize is
kept with ``status=unmatched`` — never dropped.
"""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from .modifiers import EntityModifier
from .normalize import (
    NORMALIZATION_SOURCE,
    concepts_for,
    normalize,
    surface_forms,
)
from .provenance import EvidenceRef
from .vocab import VOCAB_VERSION

if TYPE_CHECKING:  # avoid import cycles / heavy imports at module load
    from .canonical import Document

_DICT_RULE = "entity_dictionary"

# scispaCy model that tags Chemicals and Diseases, mapped to our
# controlled entity types.
_SCISPACY_MODEL = "en_ner_bc5cdr_md"
_SCISPACY_LABELS = {"CHEMICAL": "chemical", "DISEASE": "disease"}


class EntityMention(BaseModel):
    """One entity mention located in the canonical text, with its normalization outcome.

    ``surface_text`` is the exact source slice (``document.text[start_char:end_char]``);
    ``concept_id``/``normalized_text`` are populated only when ``status == "normalized"``.
    The mention always keeps its surface span regardless of normalization outcome.
    """

    mention_id: str
    document_id: str
    sentence_id: Optional[str] = None
    surface_text: str
    normalized_text: Optional[str] = None
    concept_id: Optional[str] = None
    entity_type: str
    start_char: int
    end_char: int
    normalization_source: str
    status: str
    evidence_ref: EvidenceRef
    # Phase 12 — restrictive modifiers of this head (e.g. "dysbiosis OF the gut microbiome").
    # Populated by a post-pass (:mod:`.modifiers`) after the parse is available; empty otherwise.
    modifiers: list[EntityModifier] = []


def _mention_id(document_id: str, start: int, end: int, entity_type: str) -> str:
    """Deterministic id: a span+type is unique per document (matches are non-overlapping)."""
    return f"{document_id}_m{start:06d}_{end:06d}_{entity_type}"


def reload_vocabulary() -> None:
    """Rebuild every cache derived from the entity vocabulary after the overlay changes.

    Clears the parsed overlay (:func:`vocab.reload_overlay`), the normalization index
    (:func:`normalize.invalidate_caches`), and this module's surface-form matcher regex — so an
    add/remove/edit through the ``/vocab`` API is recognised by the next extraction in the same
    process, with no restart. The scispaCy model cache is vocabulary-independent and left intact.
    """
    from . import normalize as _normalize  # noqa: PLC0415  (avoid import cycle at module load)
    from . import vocab as _vocab  # noqa: PLC0415

    _vocab.reload_overlay()
    _normalize.invalidate_caches()
    _matcher.cache_clear()


@functools.lru_cache(maxsize=1)
def _matcher() -> Optional[re.Pattern]:
    """Case-insensitive, word-bounded alternation of every vocabulary surface form.

    Terms are ordered longest-first (see :func:`normalize.surface_forms`) so the leftmost
    match at any position is the longest phrase, and ``finditer`` yields non-overlapping
    matches — both deterministic.
    """
    forms = surface_forms()
    if not forms:
        return None
    alternation = "|".join(re.escape(f) for f in forms)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


# --- scispaCy NER: the primary detector for Chemical/Disease (required) -------------


class ModelUnavailableError(RuntimeError):
    """Raised when the required scispaCy NER model cannot be loaded.

    The default extraction path treats scispaCy as a first-class, required detector, so a
    missing or broken model is a loud error — not a silent downgrade. Callers that
    deliberately want the model-free floor pass ``use_model=False`` instead.
    """


@functools.lru_cache(maxsize=1)
def _nlp():
    """Load and cache the scispaCy NER model, raising :class:`ModelUnavailableError` if it
    cannot be loaded. (``lru_cache`` does not memoize exceptions, so a transient failure is
    retried on the next call while a successful load stays cached.)"""
    try:
        import spacy  # noqa: PLC0415  (heavy import kept lazy)

        return spacy.load(_SCISPACY_MODEL)
    except Exception as exc:  # ImportError, OSError (model absent), config errors, ...
        raise ModelUnavailableError(
            f"required scispaCy model {_SCISPACY_MODEL!r} could not be loaded: {exc}. "
            f"Install it with `python -m spacy download {_SCISPACY_MODEL}` (see the [ner] "
            f"extra), or call extract(..., use_model=False) for the dictionary-only floor."
        ) from exc


def model_available() -> bool:
    """True iff the scispaCy model can be loaded (never raises — for introspection)."""
    try:
        return _nlp() is not None
    except ModelUnavailableError:
        return False


def _scispacy_spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, entity_type)`` for Chemical/Disease spans in ``text``.

    Assumes the model is loadable — callers gate on ``use_model`` and :func:`extract`
    validates the model up front; returns empty only for empty input.
    """
    if not text:
        return []
    doc = _nlp()(text)
    out = []
    for ent in doc.ents:
        etype = _SCISPACY_LABELS.get(ent.label_)
        if etype is not None:
            out.append((ent.start_char, ent.end_char, etype))
    return out


# --- extraction ---------------------------------------------------------------------


def _dictionary_type(surface: str, norm) -> str:
    """Entity type for a dictionary hit: the concept's type, or a stable representative
    type when the surface is ambiguous across types (concept unresolved)."""
    if norm.concept is not None:
        return norm.concept.entity_type
    types = sorted({c.entity_type for c in concepts_for(surface)})
    return types[0] if types else "entity"


def extract(document: "Document", *, use_model: bool = True) -> list[EntityMention]:
    """Extract normalized entity mentions from a canonical document.

    Scans sentence by sentence (so each mention's owning sentence and absolute offsets are
    known by construction). scispaCy leads for Chemical/Disease and wins overlaps; the
    dictionary supplies every other type, gap-fills Chemical/Disease, and normalizes every
    span. The returned list is ordered by ``(start_char, end_char, entity_type)``.

    With ``use_model=True`` (default) the scispaCy model is **required**: if it cannot be
    loaded this raises :class:`ModelUnavailableError`. Pass ``use_model=False`` for the
    deterministic, model-free dictionary-only floor.
    """
    if use_model:
        _nlp()  # fail loudly up front if the required model can't load (vs silently degrade)

    matcher = _matcher()
    mentions: list[EntityMention] = []
    seen_ids: set[str] = set()

    for sent in document.iter_sentences():
        base = sent.start_char
        occupied: list[tuple[int, int]] = []  # sentence-relative spans already claimed

        # 1) scispaCy: authoritative Chemical/Disease spans, claimed first so they win overlaps.
        if use_model:
            model_rule = f"scispacy:{_SCISPACY_MODEL}"
            model_ver = _model_version()
            for rel_start, rel_end, model_label in _scispacy_spans(sent.text):
                surface = sent.text[rel_start:rel_end]
                # The vocab decides the concept AND type: BC5CDR's coarse CHEMICAL/DISEASE
                # labels conflate our finer types (chemical/nutrient/biomarker, disease/
                # symptom/outcome), so narrowing on them would wrongly block resolution (e.g.
                # "Vitamin D" tagged CHEMICAL but stored as a nutrient). The model's label is
                # only a fallback type for spans the vocabulary doesn't recognize.
                norm = normalize(surface)
                etype = norm.concept.entity_type if norm.concept is not None else model_label
                occupied.append((rel_start, rel_end))
                mentions.append(
                    _build_mention(
                        document, sent, base + rel_start, base + rel_end, surface,
                        etype, norm, extraction_rule=model_rule,
                        extraction_rule_version=model_ver,
                    )
                )

        # 2) Dictionary: every other entity type + gap-fill where scispaCy was silent. A
        #    dictionary match overlapping a scispaCy span is skipped — the model wins.
        if matcher is not None:
            for m in matcher.finditer(sent.text):
                rel_start, rel_end = m.start(), m.end()
                if any(rel_start < oe and os < rel_end for (os, oe) in occupied):
                    continue
                surface = m.group()
                norm = normalize(surface)
                etype = _dictionary_type(surface, norm)
                occupied.append((rel_start, rel_end))
                mentions.append(
                    _build_mention(
                        document, sent, base + rel_start, base + rel_end, surface,
                        etype, norm, extraction_rule=_DICT_RULE,
                        extraction_rule_version=VOCAB_VERSION,
                    )
                )

    # Deduplicate on the deterministic mention id and impose a stable global order.
    unique = [m for m in mentions if not (m.mention_id in seen_ids or seen_ids.add(m.mention_id))]
    unique.sort(key=lambda m: (m.start_char, m.end_char, m.entity_type, m.surface_text))
    return unique


def _build_mention(
    document, sentence, start: int, end: int, surface: str, entity_type: str, norm,
    *, extraction_rule: str, extraction_rule_version: str,
) -> EntityMention:
    concept = norm.concept
    return EntityMention(
        mention_id=_mention_id(document.document_id, start, end, entity_type),
        document_id=document.document_id,
        sentence_id=sentence.sentence_id,
        surface_text=surface,
        normalized_text=concept.canonical_name if concept else None,
        concept_id=concept.concept_id if concept else None,
        entity_type=entity_type,
        start_char=start,
        end_char=end,
        normalization_source=norm.normalization_source or NORMALIZATION_SOURCE,
        status=norm.status,
        evidence_ref=EvidenceRef.for_span(
            document, start, end, sentence=sentence,
            extraction_rule=extraction_rule,
            extraction_rule_version=extraction_rule_version,
        ),
    )


def _model_version() -> str:
    from importlib import metadata as _im

    try:
        return _im.version("scispacy")
    except _im.PackageNotFoundError:
        return "unknown"


# --- orchestration ------------------------------------------------------------------


def analyze_document(
    pmid: str | int,
    *,
    corpus=None,
    engine=None,
    persist: bool = True,
    use_model: bool = True,
) -> tuple["Document", list[EntityMention]]:
    """Load a canonical document, extract + normalize entities, and (optionally) persist.

    Offline and independently rerunnable: reads the canonical doc from the corpus (or the DB
    if not archived locally), never the network. Persistence is delete-then-insert per
    document, so re-running ``analyze`` replaces a document's mentions rather than duplicating
    them.
    """
    from .corpus import CorpusStore
    from .db import get_engine, init_db, persist_document, session_scope
    from .db.schema import DocumentRow, persist_entities
    from .ids import normalize_pmid
    from .run import build_run

    pmid = normalize_pmid(pmid)
    corpus = corpus or CorpusStore()

    if corpus.has_canonical(pmid):
        document = corpus.read_canonical(pmid)
    else:
        from .query import get_document

        if engine is None:
            engine = get_engine()
            init_db(engine)
        document = get_document(engine, pmid)
        if document is None:
            from .ids import document_id

            raise FileNotFoundError(
                f"no canonical document for {document_id(pmid)}; run "
                f"`mehungry ingest --pmid {pmid}` first"
            )

    mentions = extract(document, use_model=use_model)
    from . import modifiers as _modifiers  # lazy: parse is only needed when annotating

    _modifiers.annotate(document, mentions, use_model=use_model)  # Phase 12 — restrictive modifiers

    if persist:
        if engine is None:
            engine = get_engine()
            init_db(engine)
        run = build_run([document.document_id])
        run.ontology_versions = _ontology_versions(use_model)
        with session_scope(engine) as session:
            # Ensure the document rows the mentions reference exist (analyze can run on a
            # corpus-only doc that was never DB-persisted). If it's already persisted (the
            # normal ingest→analyze flow) leave its structure untouched.
            if session.get(DocumentRow, document.document_id) is None:
                persist_document(session, document, run)
            persist_entities(session, document, mentions, run)

    return document, mentions


def _ontology_versions(use_model: bool) -> dict:
    versions = {"mehungry_curated": VOCAB_VERSION}
    if use_model and model_available():
        versions[_SCISPACY_MODEL] = _model_version()
    return versions
