"""Phase 10 gate: parse-based relation-argument binding (the model path).

These tests require the scispaCy model (its pipeline supplies the parser). They assert the
behaviours the flat, adjacency-based binder cannot achieve: coordination cross-products,
contrastive shared subjects, dependency-``neg`` negation, and prep-phrase conditions bound as
qualifiers rather than mis-read as objects. The deterministic ``use_model=False`` floor is
asserted to stay byte-stable and model-free.
"""

import pytest

from mehungry_extractor.knowledge import parse as _parse
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.enums import Polarity
from mehungry_extractor.knowledge.observations import extract as extract_observations

pytestmark = pytest.mark.skipif(
    not _parse.available(), reason="scispaCy parser model not installed"
)


def _doc(sentence: str):
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])


def _obs(sentence: str, *, use_model: bool = True):
    doc = _doc(sentence)
    mentions = extract_entities(doc, use_model=use_model)
    return doc, extract_observations(doc, mentions, use_model=use_model)


def _triples(observations):
    return sorted(
        (o.subject_concept_id or o.subject_text, o.predicate, o.object_concept_id or o.object_text)
        for o in observations
    )


# --- fragmentation: intervening modifier span + prep-phrase condition ----------------


def test_prep_phrase_condition_becomes_qualifier_not_object():
    """*"…associated with reduced disease activity during remission"*: the parse binds one
    association whose object is the disease-activity noun phrase (the disease entity the model
    tags inside it), with ``remission`` as a ``disease_state`` **qualifier** — not as the object
    the flat adjacency binder would have reached by luck."""
    doc, observations = _obs(
        "Dietary fiber was associated with reduced disease activity during remission."
    )
    assert len(observations) == 1
    o = observations[0]
    assert o.subject_concept_id == "NUTR:dietary_fiber"
    assert o.predicate == "associated_with"
    # remission is a condition, never the endpoint.
    assert o.object_text != "remission" and o.object_concept_id != "OUT:remission"
    quals = {(q.qualifier_type, q.value_concept_id) for q in o.qualifiers}
    assert ("disease_state", "DS:remission") in quals
    assert "binder:parse" in (o.context or "")


# --- coordination cross-product ------------------------------------------------------


def test_subject_and_object_coordination_expands_to_cross_product():
    """*"Vitamin D and calcium reduced the risk of colorectal cancer and osteoporosis"* →
    exactly the four ``reduces_risk`` observations of the 2×2 coordination."""
    doc, observations = _obs(
        "Vitamin D and calcium reduced the risk of colorectal cancer and osteoporosis."
    )
    assert len(observations) == 4
    assert all(o.predicate == "reduces_risk" for o in observations)
    assert all(o.polarity == Polarity.POSITIVE.value for o in observations)
    subjects = {o.subject_concept_id for o in observations}
    objects = {o.object_concept_id for o in observations}
    assert subjects == {"NUTR:vitamin_d", "NUTR:calcium"}
    assert objects == {"DIS:colorectal_cancer", "DIS:osteoporosis"}
    # Every endpoint pairing is present and every observation reconstructs from offsets.
    assert _triples(observations) == [
        ("NUTR:calcium", "reduces_risk", "DIS:colorectal_cancer"),
        ("NUTR:calcium", "reduces_risk", "DIS:osteoporosis"),
        ("NUTR:vitamin_d", "reduces_risk", "DIS:colorectal_cancer"),
        ("NUTR:vitamin_d", "reduces_risk", "DIS:osteoporosis"),
    ]
    for o in observations:
        for ref in o.evidence_refs:
            assert doc.text[ref.start_char:ref.end_char] == ref.quoted_text


# --- contrastive coordination with a shared subject ----------------------------------


def test_contrastive_shared_subject_splits_into_two_predicates():
    """*"Fiber reduced CRP but increased bloating"* → two observations sharing the subject, with
    opposite valence (decreases vs increases) — the ``conj`` predicate inherits the subject."""
    doc, observations = _obs("Fiber reduced CRP but increased bloating.")
    assert _triples(observations) == [
        ("NUTR:dietary_fiber", "decreases", "BIOM:crp"),
        ("NUTR:dietary_fiber", "increases", "SYMP:bloating"),
    ]
    assert all(o.subject_concept_id == "NUTR:dietary_fiber" for o in observations)


# --- negation via the dependency neg arc ---------------------------------------------


def test_negation_via_neg_arc_flips_polarity():
    """*"Fiber did not reduce CRP"* → the ``neg`` arc flips the ``decreases`` polarity."""
    doc, observations = _obs("Fiber did not reduce CRP.")
    assert len(observations) == 1
    o = observations[0]
    assert o.predicate == "decreases"
    assert o.polarity == Polarity.NEGATIVE.value


# --- fallback safety -----------------------------------------------------------------


def test_lexical_predicate_falls_back_to_flat_binder():
    """A relation whose predicate is lexical (not a single verb) has no mapped verb, so the parse
    binder yields nothing for the sentence and the flat floor binds it — nothing is lost."""
    doc, observations = _obs("Dietary fiber had no association with remission.")
    assert len(observations) == 1
    o = observations[0]
    assert o.predicate == "no_association"
    # Bound by the flat fallback, so it carries no parse-binder tag.
    assert "binder:parse" not in (o.context or "")


# --- floor parity / determinism ------------------------------------------------------


def test_floor_is_model_free_and_deterministic():
    sentence = "Vitamin D and calcium reduced the risk of colorectal cancer and osteoporosis."
    doc = _doc(sentence)
    mentions = extract_entities(doc, use_model=False)
    first = [o.model_dump() for o in extract_observations(doc, mentions, use_model=False)]
    second = [o.model_dump() for o in extract_observations(doc, mentions, use_model=False)]
    assert first == second
    # The floor never emits the parse-binder tag.
    assert all("binder:parse" not in (o["context"] or "") for o in first)


# --- parse helper arc structure ------------------------------------------------------


def test_parse_helpers_expose_expected_arc_structure():
    doc = _doc("Vitamin D and calcium reduced the risk of colorectal cancer and osteoporosis.")
    mentions = extract_entities(doc, use_model=True)
    sentence = next(doc.iter_sentences())
    sp = _parse.SentenceParse(sentence, mentions)

    roots = sp.roots()
    assert [r.lemma_ for r in roots] == ["reduce"]
    root = roots[0]

    subjects = [sp.mention_for_token(t).concept_id for t in sp.subject_tokens(root)]
    assert subjects == ["NUTR:vitamin_d", "NUTR:calcium"]

    risk_obj = sp.object_tokens(root)
    assert [t.lemma_ for t in risk_obj] == ["risk"]
    endpoints = [sp.mention_for_token(t).concept_id for t in sp.risk_objects(risk_obj[0])]
    assert endpoints == ["DIS:colorectal_cancer", "DIS:osteoporosis"]
