"""Phase 7 gate: clause scope + richer clause-scoped qualifier extraction (docs/phases/phase-7-*).

All offline and deterministic — synthetic sentences over the curated vocabulary + disease-state
cues, no network, no model. Covers the motivating contrastive sentence (opposite valence under
two conditions), clause-bounded negation, an intervening qualifier concept between subject and
object, dose/population qualifiers, the additive relation-template framework, optional-model
parity, and determinism.
"""

from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.clauses import segment as segment_clauses
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.enums import Certainty, Polarity
from mehungry_extractor.knowledge.observations import extract as extract_observations
from mehungry_extractor.knowledge.qualifiers import QualifierType
from mehungry_extractor.knowledge.relations import (
    RULES,
    RelationTemplate,
    Slot,
    match,
    match_template,
)


def _doc(*sentences: str):
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=list(sentences))])


def _obs(text: str, *, use_model: bool = False):
    doc = _doc(text)
    mentions = extract_entities(doc, use_model=use_model)
    return doc, extract_observations(doc, mentions, use_model=use_model)


def _find(obs, predicate):
    return [o for o in obs if o.predicate == predicate]


def _states(o):
    return {q.value_concept_id or q.value_text
            for q in o.qualifiers if q.qualifier_type == QualifierType.DISEASE_STATE.value}


# --- clause segmentation ------------------------------------------------------------


def test_clauses_are_sub_spans_and_record_marker():
    doc = _doc("Dietary fiber helps during remission but harms during active flare.")
    sent = next(doc.iter_sentences())
    clauses = segment_clauses(doc, sent)
    assert len(clauses) == 2
    for c in clauses:
        # Offset contract: a clause is a reconstructable sub-span of the sentence.
        assert doc.text[c.start_char : c.end_char] == c.text
        assert sent.start_char <= c.start_char <= c.end_char <= sent.end_char
    assert clauses[0].marker is None and clauses[0].contrastive is False
    assert clauses[1].marker == "but" and clauses[1].contrastive is True


def test_sentence_without_marker_is_one_clause():
    doc = _doc("Dietary fiber was associated with remission.")
    sent = next(doc.iter_sentences())
    clauses = segment_clauses(doc, sent)
    assert len(clauses) == 1
    assert clauses[0].marker is None and clauses[0].contrastive is False


# --- the motivating sentence: opposite valence under two conditions -----------------


def test_contrastive_sentence_yields_two_scoped_oppositely_signed_observations():
    _, obs = _obs(
        "Dietary fiber improves remission during remission "
        "but may aggravate abdominal pain during active flare."
    )
    improves = _find(obs, "improves")
    worsens = _find(obs, "worsens")
    assert len(improves) == 1 and len(worsens) == 1, obs

    # Same subject, opposite valence (improves vs worsens), each scoped to its own condition.
    assert improves[0].subject_concept_id == worsens[0].subject_concept_id == "NUTR:dietary_fiber"
    assert _states(improves[0]) == {"DS:remission"}
    assert _states(worsens[0]) == {"DS:active_disease"}

    # The uncertainty cue "may" lands only on the clause that carries it.
    assert improves[0].certainty == Certainty.ASSERTED.value
    assert worsens[0].certainty == Certainty.POSSIBLE.value

    # The clause marker + contrastive flag are recorded on the contrastive clause's observation.
    assert improves[0].context is None or "clause:" not in improves[0].context
    assert "clause:but(contrastive)" in (worsens[0].context or "")


# --- clause-bounded negation --------------------------------------------------------


def test_negation_in_one_clause_does_not_flip_a_relation_in_another():
    _, obs = _obs("Dietary fiber does not improve remission but worsens abdominal pain.")
    improves = _find(obs, "improves")[0]
    worsens = _find(obs, "worsens")[0]
    # Clause A negation flips improves → negative; clause B is untouched → positive asserted.
    assert improves.polarity == Polarity.NEGATIVE.value
    assert worsens.polarity == Polarity.POSITIVE.value
    assert worsens.certainty == Certainty.ASSERTED.value


# --- intervening qualifier concept between subject and object -----------------------


def test_intervening_qualifier_mention_does_not_break_the_pair():
    _, obs = _obs("Dietary fiber during remission reduces abdominal pain.")
    # remission sits between the subject and object as a disease_state cue, not an endpoint;
    # the pair still binds, and the condition is attached.
    assert len(obs) == 1, obs
    o = obs[0]
    assert (o.subject_concept_id, o.object_concept_id) == ("NUTR:dietary_fiber", "SYMP:abdominal_pain")
    assert o.predicate == "decreases"
    assert _states(o) == {"DS:remission"}


# --- dose / population --------------------------------------------------------------


def test_dose_and_population_qualifiers_populate_with_provenance():
    doc, obs = _obs("High-dose dietary iron in children caused abdominal pain.")
    assert len(obs) == 1, obs
    o = obs[0]
    assert o.predicate == "causes"
    by_type = {q.qualifier_type: q for q in o.qualifiers}
    assert QualifierType.DOSE.value in by_type
    assert QualifierType.POPULATION.value in by_type
    # Every qualifier is ruled and reconstructs exactly from its offsets.
    for q in o.qualifiers:
        assert q.rule_id and q.rule_version
        for ref in q.evidence_refs:
            assert doc.text[ref.start_char : ref.end_char] == q.value_text
    assert by_type[QualifierType.POPULATION.value].value_text == "children"


# --- templated rules coexist with the flat rules ------------------------------------


def test_relation_templates_are_additive_equivalents_of_flat_rules():
    # Every flat rule has a subject·qualifier?·cue·object template equivalent.
    for rule in RULES:
        t = RelationTemplate.from_rule(rule)
        assert t.rule_id == rule.rule_id and t.version == rule.version
        assert t.slots == (Slot.SUBJECT, Slot.QUALIFIER, Slot.CUE, Slot.OBJECT)
    # match_template mirrors match on the same cue text.
    connecting = " was associated with "
    assert match(connecting) is not None
    assert match_template(connecting).rule_id == match(connecting).rule_id
    assert match_template(" no cue here ") is None


# --- optional-model parity + determinism --------------------------------------------


def test_floor_is_model_free_and_stable():
    """The ``use_model=False`` floor is byte-stable across runs and needs no model.

    Before Phase 10 the parse assist was a no-op, so ``use_model=True`` and ``False`` were
    identical. Phase 10 deliberately makes the model path bind arguments from the dependency
    parse, so the two paths now diverge (that divergence is asserted by the parse gate tests). The
    invariant that remains is the one the deterministic engine depends on: the flat floor is
    reproducible and model-independent.
    """
    text = ("Dietary fiber improves remission during remission "
            "but may aggravate abdominal pain during active flare.")
    doc = _doc(text)
    mentions = extract_entities(doc, use_model=False)
    first = [o.model_dump() for o in extract_observations(doc, mentions, use_model=False)]
    second = [o.model_dump() for o in extract_observations(doc, mentions, use_model=False)]
    assert first == second and first


def test_determinism_two_runs_identical():
    text = ("Dietary fiber improves remission during remission "
            "but aggravates abdominal pain during active flare.")
    doc = _doc(text)
    mentions = extract_entities(doc, use_model=False)
    first = [o.model_dump() for o in extract_observations(doc, mentions, use_model=False)]
    second = [o.model_dump() for o in extract_observations(doc, mentions, use_model=False)]
    assert first == second
