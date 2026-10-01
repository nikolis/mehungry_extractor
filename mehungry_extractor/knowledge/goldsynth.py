"""Synthetic-sentence gold evaluation — score the pipeline against self-contained gold records.

This is the store-free sibling of :func:`mehungry_extractor.knowledge.query.gold_eval_report`.
Where that path anchors gold to cached-corpus ``sentence_id``s and reads produced observations from
the SQLite store, this one carries the sentence *text* in each gold record (``sentence_quote``),
builds a one-sentence canonical :class:`~.canonical.Document` from it, runs the real relation
pipeline (:func:`.entities.extract` → :func:`.observations.extract`) on the model path, and folds the
result through the *same* pure scorer (:func:`.goldeval.score`). The point is to measure a coverage
or binding change on a curated set of sentences **without** depending on which papers happen to be in
``data/corpus`` — the curated reviewer examples that motivate the Fix-4 work are exactly this shape.

The gold file is the same schema as :func:`.goldeval.load_gold` expects, with one addition each
record must carry: a ``sentence_quote`` (the text to build the document from) and a ``sentence_id``
used purely as the join key between the built document's produced observations and the record's
``expected`` list. Everything downstream — endpoint alignment, the four scoring buckets, the
failure-mode histogram — is unchanged, so a synthetic scorecard is directly comparable to a
store-backed one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .goldeval import GoldEvalResult, default_gold_path, load_gold, score


def curated_gold_path() -> Path:
    """The checked-in curated set at ``<repo>/tests/gold/curated_examples.json``."""
    return default_gold_path().with_name("curated_examples.json")


def _produced_for_quote(sentence_quote: str, *, use_model: bool) -> list[dict]:
    """Run the pipeline on a one-sentence document and return produced-observation dicts.

    The dict shape mirrors :func:`...query.list_observations_for_document` for the fields the scorer
    reads (endpoints + predicate/polarity/certainty), so :func:`.goldeval.score` treats a synthetic
    run and a store-backed run identically.
    """
    # Imported lazily: this module is a thin eval harness, and the heavy pipeline imports (spaCy via
    # entities/parse) should not load merely because someone imported goldsynth.
    from .canonical import DocumentMetadata, ParsedSection, build_document
    from .entities import extract as extract_entities
    from .observations import extract as extract_observations

    document = build_document(
        DocumentMetadata(pmid="90000000", source_type="abstract"),
        [ParsedSection(title="Body", paragraphs=[sentence_quote])],
    )
    mentions = extract_entities(document, use_model=use_model)
    observations = extract_observations(document, mentions, use_model=use_model)
    return [
        {
            "sentence_id": o.sentence_id,
            "subject_text": o.subject_text,
            "subject_concept_id": o.subject_concept_id,
            "predicate": o.predicate,
            "object_text": o.object_text,
            "object_concept_id": o.object_concept_id,
            "polarity": o.polarity,
            "certainty": o.certainty,
        }
        for o in observations
    ]


def evaluate(
    gold_path: "str | Path | None" = None,
    *,
    use_model: bool = True,
) -> GoldEvalResult:
    """Score the pipeline over a synthetic gold set and return a :class:`.goldeval.GoldEvalResult`.

    Each gold record's ``sentence_quote`` is extracted independently; the produced observations are
    keyed under the record's ``sentence_id`` and handed to the shared scorer. ``use_model=True``
    exercises the parse binder (the path the Fix-4 changes target); ``use_model=False`` scores the
    deterministic floor for comparison.
    """
    gold = load_gold(gold_path if gold_path is not None else curated_gold_path())
    produced_by_sentence: dict[str, list[dict]] = {}
    for record in gold["sentences"]:
        quote = record.get("sentence_quote")
        sentence_id = record.get("sentence_id")
        if not quote or not sentence_id:
            continue
        # A record whose expected observations are *all* ``unscorable`` (a predicate/vocabulary the
        # pipeline cannot yet produce) is treated like an unlabeled sentence: we do not feed its
        # produced observations, so it is neither credited nor penalized. Otherwise a produced
        # observation the scorer has nothing to align against would be miscounted as spurious and
        # unfairly depress precision for a case we deliberately deferred. An *empty* expected list is
        # different — that is an intentional precision target (a perfect extractor emits nothing), so
        # its produced observations are fed and any output counts as spurious.
        expected = record.get("expected", [])
        if expected and all(e.get("unscorable") for e in expected):
            continue
        produced_by_sentence[sentence_id] = _produced_for_quote(quote, use_model=use_model)
    return score(gold["sentences"], produced_by_sentence)
