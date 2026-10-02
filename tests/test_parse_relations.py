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
    the flat adjacency binder would have reached by luck. The "reduced" on the object noun makes it a
    *directional* association (``associated_with_reduced``), not a bare one."""
    doc, observations = _obs(
        "Dietary fiber was associated with reduced disease activity during remission."
    )
    assert len(observations) == 1
    o = observations[0]
    assert o.subject_concept_id == "NUTR:dietary_fiber"
    assert o.predicate == "associated_with_reduced"
    # remission is a condition, never the endpoint.
    assert o.object_text != "remission" and o.object_concept_id != "OUT:remission"
    quals = {(q.qualifier_type, q.value_concept_id) for q in o.qualifiers}
    assert ("disease_state", "DS:remission") in quals
    assert "binder:parse" in (o.context or "")


def test_controlled_predicate_inherits_governing_clause_condition():
    """*"…, the Mediterranean diet has been shown **in patients with active diseases** to reduce
    disease activity and markers of inflammation, such as fecal calprotectin and C-reactive
    protein (CRP)."*

    Three things the engine used to get wrong, now asserted together:

    * **A1** — *disease activity* is a curated outcome concept, so it binds as a *normalized*
      endpoint (``OUT:disease_activity``) instead of collapsing into the ``inflammation`` entity
      buried deeper in the same object phrase.
    * **B1** — ``reduce`` is a control (``xcomp``) predicate that inherits its subject from the
      governing ``shown``; the condition *"in patients with active diseases"* hangs off that
      governor, and is inherited onto every controlled relation rather than being lost.
    * **B2** — the plural *"active diseases"* normalizes to ``DS:active_disease``.
    """
    doc, observations = _obs(
        "Additionally, in patients with active diseases, the Mediterranean diet has been shown "
        "to reduce disease activity and markers of inflammation, such as fecal calprotectin and "
        "C-reactive protein (CRP)."
    )
    assert observations, "expected the controlled 'reduce' relation to bind"
    # Every bound relation is Mediterranean diet → decreases → <endpoint>, under the inherited
    # disease-state condition.
    assert all(o.subject_concept_id == "INT:mediterranean_diet" for o in observations)
    assert all(o.predicate == "decreases" for o in observations)
    objects = {o.object_concept_id for o in observations}
    # A1: the important clinical endpoint is captured as a normalized concept.
    assert "OUT:disease_activity" in objects
    # B1 + B2: the governing-clause condition is inherited and normalized on every relation.
    for o in observations:
        quals = {(q.qualifier_type, q.value_concept_id) for q in o.qualifiers}
        assert ("disease_state", "DS:active_disease") in quals, o.qualifiers
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


# --- descent drop-guard: a non-normalizing modifier is not a fabricated subject ------


def test_descent_subject_must_normalize_or_relation_is_dropped():
    """*"…early-life diet influences IBD risk, with … increased consumption of vegetables …"*.

    The scispaCy parser reads *increased consumption of vegetables* as a finite verb coordinated
    with *influences*, and tags *early-life* (an ``amod`` of *diet*) as a DISEASE false positive
    that never normalizes. Without the descent drop-guard the subject resolver descended that
    ``amod`` and fabricated *early-life → increases → vegetables*. The guard rejects a descended
    subject that carries no concept, so the predicate resolves to no subject; because *diet* is an
    overt non-entity noun that ``block_fallback`` then suppresses the flat binder — the relation is
    dropped, not fabricated."""
    doc, observations = _obs(
        "Similarly, a cohort study demonstrated that early-life diet influences IBD risk, "
        "with high fish intake and increased consumption of vegetables and fruits at age 1 "
        "associated with lower risk, while sugary drink intake correlated with increased risk."
    )
    assert all(o.subject_text.lower() != "early-life" for o in observations)
    assert ("early-life", "increases", "vegetables") not in _triples(observations)
    assert ("early-life", "increases", "fruits") not in _triples(observations)


def test_descent_subject_that_normalizes_is_still_bound():
    """The drop-guard keeps the *useful* half of the descent: when the entity modifying a
    measure-noun subject head normalizes, it is still promoted to the subject. *"High vegetable
    intake lowered the risk of colorectal cancer"* — ``intake`` is the ``nsubj`` head, *vegetable*
    is its (normalizing) modifier — binds *vegetable → reduces_risk → colorectal cancer*."""
    doc, observations = _obs("High vegetable intake lowered the risk of colorectal cancer.")
    assert _triples(observations) == [
        ("FOOD:vegetables", "reduces_risk", "DIS:colorectal_cancer"),
    ]


# --- participial / reduced-relative predicate recovery -------------------------------


def test_participial_predicate_is_bound_from_structure():
    """A relation stated in a participle (``acl``) hanging off an argument noun — *"Increased
    consumption of vegetables, **associated** with reduced colorectal cancer, was reported"* — is now
    reached by :meth:`predicate_heads`. Its antecedent noun *consumption* is a measure noun unwrapped
    to *vegetables*, and the participle binds *vegetables → associated_with → colorectal cancer* from
    the parse (the unmapped ROOT *reported* would otherwise leave this to flat-fallback luck)."""
    doc, observations = _obs(
        "Increased consumption of vegetables, associated with reduced colorectal cancer, was reported."
    )
    # "reduced" on the object makes this a directional association (Concept — directional associations).
    assert ("FOOD:vegetables", "associated_with_reduced", "DIS:colorectal_cancer") in _triples(observations)
    assert any("binder:parse" in (o.context or "") for o in observations)


def test_measure_noun_subject_is_unwrapped_to_its_content_genitive():
    """The subject-side mirror of the measure-noun object unwrap: *"**Intake of red meat** increased
    CRP"* binds *red meat → increases → CRP*. Previously the strict subject resolver never entered the
    ``of``-phrase, so the subject stayed unresolved and the relation was dropped."""
    doc, observations = _obs("Intake of red meat increased CRP.")
    assert _triples(observations) == [("FOOD:red_meat", "increases", "BIOM:crp")]


def test_participle_with_nonentity_antecedent_defers_to_fallback_not_blocked():
    """A participle whose antecedent is a non-entity head keeping its real entity in a PP the strict
    resolver won't enter — *"A diet rich in fiber, **associated** with lower colorectal cancer risk,
    is recommended"* (antecedent *diet*, entity *fiber* under "rich **in** fiber") — must **not**
    block the flat fallback the way a main-clause non-entity subject does. The relation is still
    recovered (by the flat binder) rather than lost."""
    doc, observations = _obs(
        "A diet rich in fiber, associated with lower colorectal cancer risk, is recommended."
    )
    assert ("NUTR:dietary_fiber", "associated_with_reduced", "DIS:colorectal_cancer") in _triples(observations)


# --- directional associations --------------------------------------------------------


def test_association_with_reduced_object_is_directional():
    """*"Vegetables were associated with lower CRP"*: the direction word *lower* on the object promotes
    the bare association to ``associated_with_reduced`` (the association analogue of ``decreases``), so
    the clinical valence reads *beneficial* (a lowered undesirable marker) instead of the *harmful*
    reading bare ``associated_with`` on an undesirable object would give."""
    from mehungry_extractor.knowledge import valence as _valence

    doc, observations = _obs("Vegetables were associated with lower CRP.")
    assert _triples(observations) == [("FOOD:vegetables", "associated_with_reduced", "BIOM:crp")]
    o = observations[0]
    assert _valence.clinical_direction(o.predicate, o.object_concept_id) == _valence.BENEFICIAL


def test_association_with_increased_object_is_directional():
    """*"Red meat was associated with higher CRP"*: *higher* promotes to ``associated_with_increased``
    (analogue of ``increases``), reading *harmful* on the undesirable marker."""
    from mehungry_extractor.knowledge import valence as _valence

    doc, observations = _obs("Red meat was associated with higher CRP.")
    assert _triples(observations) == [("FOOD:red_meat", "associated_with_increased", "BIOM:crp")]
    o = observations[0]
    assert _valence.clinical_direction(o.predicate, o.object_concept_id) == _valence.HARMFUL


def test_association_with_a_lower_risk_reads_as_reduces_risk_not_bare_association():
    """*"The Mediterranean diet is linked to a **lower risk** of cardiovascular diseases …"* must not
    collapse to a bare, direction-less ``associated_with`` (which reads as a plain association *with*
    the disease — the opposite message). The direction word on the ``risk`` object promotes it to the
    causal ``reduces_risk`` — the mirror of the reviewer-confirmed *"higher risk → increases_risk"*."""
    _, observations = _obs(
        "The Mediterranean diet is linked to a lower risk of cardiovascular diseases and chronic conditions."
    )
    assert observations, "expected a protective risk relation, got nothing"
    assert all(o.predicate != "associated_with" for o in observations)
    assert any(o.predicate == "reduces_risk" for o in observations)


def test_lower_risk_flat_cue_is_symmetric_with_higher_risk():
    """Regression for the asymmetric cue: the flat ``reduces_risk`` rule must match bare "lower risk"
    exactly as ``increases_risk`` matches bare "higher risk" (the old ``lower\\w+`` demanded a suffix
    and silently missed it)."""
    from mehungry_extractor.knowledge import relations as _relations

    assert _relations.match("is linked to a lower risk of").predicate == "reduces_risk"
    assert _relations.match("associated with a higher risk of").predicate == "increases_risk"


def test_association_with_a_risk_object_is_not_promoted_to_directional_association():
    """The directional-association promotion is scoped to **non-risk** objects: a direction word on a
    *risk* object ("associated with a **higher risk** of …") must never become
    ``associated_with_increased``/``associated_with_reduced`` — the causal ``increases_risk`` reading
    (reviewer-confirmed for *"red meat … higher risk of developing IBD"*) is preserved, and
    ``increases_risk`` is what the flat binder yields for such a phrasing."""
    _, observations = _obs("Red meat has been associated with a higher risk of colorectal cancer.")
    assert all(
        o.predicate not in ("associated_with_increased", "associated_with_reduced")
        for o in observations
    )
    # The reviewer-confirmed gold phrasing yields the causal risk predicate via the flat binder.
    _, gold = _obs("Specifically, red meat has been associated with a higher risk of developing IBD.")
    assert ("FOOD:red_meat", "increases_risk", "DIS:ibd") in _triples(gold)


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
    endpoints = [sp.mention_for_token(t).concept_id for t in sp.measure_objects(risk_obj[0])]
    assert endpoints == ["DIS:colorectal_cancer", "DIS:osteoporosis"]
