"""Deterministic sentence segmentation.

Uses a blank English spaCy pipeline with only the rule-based ``sentencizer`` component.
This needs **no model download**, has no learned/stochastic weights, and produces the same
boundaries on every run — which is what the reproducibility guarantee (spec §12) requires.
The same spaCy install is reused by the Phase 2 entity stack.

``segment`` returns ``(start_char, end_char, text)`` triples whose offsets are relative to
the input string. :mod:`mehungry_extractor.knowledge.canonical` rebases them to absolute
document offsets. Offsets always satisfy ``text[start:end] == returned_text``.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def _nlp():
    import spacy  # imported lazily so the package imports without spaCy present

    nlp = spacy.blank("en")
    # add_pipe("sentencizer") is a deterministic punctuation-rule component.
    nlp.add_pipe("sentencizer")
    return nlp


def segment(text: str) -> list[tuple[int, int, str]]:
    """Split ``text`` into sentences as ``(start_char, end_char, sentence_text)``.

    Boundaries come from the sentencizer; the returned text is sliced from the input at the
    computed offsets so it is exactly reconstructable. Whitespace-only sentences are
    dropped (they carry no content and would produce empty provenance spans).
    """
    if not text.strip():
        return []

    doc = _nlp()(text)
    out: list[tuple[int, int, str]] = []
    for sent in doc.sents:
        start, end = sent.start_char, sent.end_char
        # Trim leading/trailing whitespace so spans are tight around real content.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            out.append((start, end, text[start:end]))
    return out
