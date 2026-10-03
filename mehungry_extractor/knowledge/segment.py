"""Sentence segmentation — model-based with a rule-based floor.

Two segmenters, selected by whether the scispaCy model is in play:

* **Model path (default).** Sentence boundaries come from the scispaCy dependency parser's own
  ``doc.sents`` (the same ``en_ner_bc5cdr_md`` pipeline the entity/parse stages use, obtained via
  the shared :func:`mehungry_extractor.knowledge.entities.analyze` cache). This **unifies**
  segmentation with downstream relation extraction: the parser runs per sentence, so delimiting
  sentences with the *parser's own* notion of a sentence means a sentence is never a fragment the
  parser would have re-joined. The previous rule-based splitter could disagree with the parser and
  hand it a fragment, silently dropping any relation that crossed the false boundary.

* **Rule floor (``pysbd``).** When the model is unavailable — the ``use_model=False`` path, or any
  environment without the model installed — boundaries come from `pysbd
  <https://github.com/nipunsadvilkar/pySBD>`_, a biomedical-aware rule-based segmenter that keeps
  abbreviations, decimals, units, and citations (``approx.``, ``2.5 mg.``, ``E. coli``,
  ``p = 0.03``) whole. It needs no model, so canonical ingest and the dictionary-only floor still
  run offline.

Which segmenter produced a document's boundaries is recorded on the ``Document`` (``segmenter``
field) so a model-derived segmentation is never silently confused with the rule floor — the same
auditability the engine applies to any model-assisted step (see ``docs/concepts.md``).

``segment`` returns ``(start_char, end_char, text)`` triples whose offsets are relative to the
input string. :mod:`mehungry_extractor.knowledge.canonical` rebases them to absolute document
offsets. Offsets always satisfy ``text[start:end] == returned_text`` for **either** segmenter —
that contract is the one non-negotiable.
"""

from __future__ import annotations

import functools

# Stable ids for the segmenter that produced a document's boundaries (recorded on ``Document``).
SEGMENTER_MODEL = "scispacy-parser"
SEGMENTER_RULE = "pysbd"


def _trimmed(text: str, spans) -> list[tuple[int, int, str]]:
    """Common tail for both segmenters: trim whitespace off each ``(start, end)`` span so spans
    are tight around real content, drop whitespace-only spans, and slice the text at the final
    offsets so it is exactly reconstructable."""
    out: list[tuple[int, int, str]] = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            out.append((start, end, text[start:end]))
    return out


@functools.lru_cache(maxsize=1)
def _rule_segmenter():
    import pysbd  # imported lazily so the package imports without pysbd present

    # char_span=True yields (start, end) offsets into the input; clean=False keeps the text
    # byte-for-byte so the offset contract holds.
    return pysbd.Segmenter(language="en", clean=False, char_span=True)


def _segment_rule(text: str) -> list[tuple[int, int, str]]:
    """Rule floor: ``pysbd`` biomedical boundaries, no model."""
    return _trimmed(text, [(s.start, s.end) for s in _rule_segmenter().segment(text)])


def _segment_model(text: str) -> list[tuple[int, int, str]]:
    """Model path: boundaries from the scispaCy parser's ``doc.sents``.

    Reuses :func:`mehungry_extractor.knowledge.entities.analyze` — the same cached ``Doc`` the NER
    and parse stages read — so segmenting a paragraph and later parsing its sentences share one
    model. Raises :class:`~mehungry_extractor.knowledge.entities.ModelUnavailableError` if the
    model cannot load; callers fall back to the rule floor.
    """
    from .entities import analyze  # lazy: keep the heavy model import out of package import

    doc = analyze(text)
    return _trimmed(text, [(s.start_char, s.end_char) for s in doc.sents])


def model_available() -> bool:
    """True iff the model-based segmenter can run (delegates to the entity stack)."""
    from .entities import model_available as _ma

    return _ma()


def active_segmenter(use_model: bool = True) -> str:
    """The :data:`SEGMENTER_MODEL`/:data:`SEGMENTER_RULE` id that :func:`segment` will use for the
    given ``use_model``, accounting for model availability. Recorded on the ``Document``."""
    return SEGMENTER_MODEL if (use_model and model_available()) else SEGMENTER_RULE


def segment(text: str, *, use_model: bool = True) -> list[tuple[int, int, str]]:
    """Split ``text`` into sentences as ``(start_char, end_char, sentence_text)``.

    With ``use_model=True`` (default) boundaries come from the scispaCy parser; if the model cannot
    load this falls back to the ``pysbd`` rule floor. ``use_model=False`` always uses the floor.
    The returned text is sliced from the input at the computed offsets so it is exactly
    reconstructable; whitespace is trimmed and whitespace-only sentences are dropped.
    """
    if not text.strip():
        return []
    if use_model:
        from .entities import ModelUnavailableError

        try:
            return _segment_model(text)
        except ModelUnavailableError:
            pass  # degrade to the rule floor; the choice is recorded via active_segmenter()
    return _segment_rule(text)
