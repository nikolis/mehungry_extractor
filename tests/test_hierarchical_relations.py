"""Phase 13 — hierarchical (nested) relations.

Covers the three additions that let a relation nest beneath the one it elaborates:

* the descriptive ``characterized_by`` predicate (flat cue + verb-map entry),
* the free-adjunct participle subject rule (``advcl`` ``VBG``/``VBN`` → the governing object),
* the parent-link post-pass on observations and its lift to ``parent_claim_id`` on claims.

The parse-binding tests require the scispaCy parser model; the claim-folding and flat-cue tests are
deterministic and model-free.
"""

import pytest

from mehungry_extractor.knowledge import parse as _parse
from mehungry_extractor.knowledge import relations as _relations
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.claims import normalize as normalize_claims
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.observations import Observation, extract as extract_observations
from mehungry_extractor.knowledge.provenance import EvidenceRef, ProvenancePrecision


# --- model-free: the descriptive predicate is a first-class flat cue ------------------


def test_characterized_by_is_a_flat_cue_and_a_known_predicate():
    rule = _relations.match("dysbiosis characterized by alterations in the gut microbiota")
    assert rule is not None and rule.predicate == "characterized_by"
    # Descriptive: neutral and never flipped by a negation cue.
    assert rule.base_polarity == "neutral" and rule.flip_on_negation is False
    # Mapped on the model path too, resolving to the same rule.
    verb_rule = _relations.rule_for_verb("characterize")
    assert verb_rule is not None and verb_rule.predicate == "characterized_by"


def test_abundance_manifestation_is_a_flat_cue_and_a_descriptive_predicate():
    # An explicit "decreased abundance of" is the descriptive manifestation on the model-free floor
    # too (both binders share the rule set). It is clinically neutral and never flipped by negation.
    rule = _relations.match("dysbiosis with a decreased abundance of Firmicutes")
    assert rule is not None and rule.predicate == "has_decreased_abundance_of"
    assert rule.base_polarity == "neutral" and rule.flip_on_negation is False
    # Its parse-path source: a directional participle over a state is re-labelled to this predicate.
    assert _relations.abundance_manifestation_for("decreases").predicate == "has_decreased_abundance_of"
    assert _relations.abundance_manifestation_for("increases").predicate == "has_increased_abundance_of"
    # Only the two directional effects are re-labelled; everything else is left untouched.
    assert _relations.abundance_manifestation_for("causes") is None

    # A *bare* directional cue must NOT be swallowed by the descriptive rule — "reduced Firmicutes"
    # (no abundance/level noun) is still the ordinary active decrease.
    assert _relations.match("red meat reduced Firmicutes").predicate == "decreases"


# --- model-free: parent-claim derivation folds the observation link up ----------------


def _obs(oid, subj_mid, subj_cid, predicate, obj_mid, obj_cid, *, parent=None):
    return Observation(
        observation_id=oid,
        document_id="pmid_1",
        sentence_id="pmid_1_sec000_p000_s00",
        parent_observation_id=parent,
        subject_mention_id=subj_mid,
        subject_text=subj_mid,
        subject_concept_id=subj_cid,
        predicate=predicate,
        object_mention_id=obj_mid,
        object_text=obj_mid,
        object_concept_id=obj_cid,
        polarity="positive",
        certainty="asserted",
        rule_id="r",
        rule_version="0",
        evidence_refs=[
            EvidenceRef(document_id="pmid_1", precision=ProvenancePrecision.SENTENCE)
        ],
    )


def test_parent_claim_id_is_lifted_when_unambiguous():
    # A: c1 -causes-> c2 (top-level). B: c2 -decreases-> c3, nested under A.
    a = _obs("obsA", "m1", "c1", "causes", "m2", "c2")
    b = _obs("obsB", "m2", "c2", "decreases", "m3", "c3", parent="obsA")
    result = normalize_claims([a, b], concept_name=lambda cid: cid)
    by_pred = {c.predicate: c for c in result.claims}
    assert by_pred["causes"].parent_claim_id is None  # top-level
    assert by_pred["decreases"].parent_claim_id == by_pred["causes"].claim_id


def test_parent_claim_id_null_when_parent_formed_no_claim():
    # The parent observation's subject never normalized (c1 is None) → it forms no claim, so the
    # child's claim-level parent is honestly null even though the observation link is recorded.
    a = _obs("obsA", "m1", None, "causes", "m2", "c2")  # dropped: no subject concept
    b = _obs("obsB", "m2", "c2", "characterized_by", "m3", "c3", parent="obsA")
    result = normalize_claims([a, b], concept_name=lambda cid: cid)
    assert len(result.claims) == 1 and result.dropped == 1
    assert result.claims[0].predicate == "characterized_by"
    assert result.claims[0].parent_claim_id is None


def test_parent_claim_id_null_when_ambiguous():
    # Two observations fold into one child claim but carry different parents → no single parent claim.
    p1 = _obs("obsP1", "m1", "c1", "causes", "m2", "c2")
    p2 = _obs("obsP2", "m9", "c9", "causes", "m2", "c2")  # a different parent, same object concept
    # Both children are (c2 -decreases-> c3) so they fold into ONE claim, but parents differ.
    c1 = _obs("obsC1", "m2", "c2", "decreases", "m3", "c3", parent="obsP1")
    c2 = _obs("obsC2", "m2", "c2", "decreases", "m3", "c3", parent="obsP2")
    result = normalize_claims([p1, p2, c1, c2], concept_name=lambda cid: cid)
    child = next(c for c in result.claims if c.predicate == "decreases")
    assert child.parent_claim_id is None


# --- model path: the full tree over the real parse ------------------------------------

pytestmark = pytest.mark.skipif(
    not _parse.available(), reason="scispaCy parser model not installed"
)


def _extract(sentence: str, *, use_model: bool = True):
    meta = DocumentMetadata(pmid="90000102", source_type="abstract")
    doc = build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])
    mentions = extract_entities(doc, use_model=use_model)
    return doc, extract_observations(doc, mentions, use_model=use_model)


_FIXTURE = (
    "Chronic inflammation induces dysbiosis characterized by alterations in the composition "
    "and function of the gut microbiota, decreasing Firmicutes, the Bifidobacterium genus, "
    "and Faecalibacterium prausnitzii."
)


def test_characterized_by_and_decreasing_nest_under_the_causes_relation():
    _doc, observations = _extract(_FIXTURE)
    by_pred = {o.predicate: o for o in observations}

    # The ROOT causal relation is top-level.
    causes = by_pred["causes"]
    assert causes.subject_text == "inflammation"
    assert causes.object_concept_id == "DIS:dysbiosis"
    assert causes.parent_observation_id is None

    # The reduced relative "characterized by …" binds dysbiosis → the whole descriptor phrase,
    # nested under causes.
    char = by_pred["characterized_by"]
    assert char.subject_concept_id == "DIS:dysbiosis"
    # Phase 15: the object is the ENTIRE head-noun phrase, verbatim — not the deep entity the general
    # resolver would otherwise descend to (*gut microbiota*), which would assert the wrong thing. It
    # is a free-text descriptor, so it carries no concept id (and forms no concept-level claim).
    assert char.object_text == "alterations in the composition and function of the gut microbiota"
    assert char.object_concept_id is None
    assert char.parent_observation_id == causes.observation_id

    # Phase 15: the old isolated `manifestation` qualifier is gone — the descriptive phrase is the
    # object now, not a side-channel qualifier on an entity object.
    assert not any(q.qualifier_type == "manifestation" for q in char.qualifiers)

    # Phase 14: the participle adjunct ", decreasing <taxa>" predicates over the *state* (dysbiosis,
    # the governing object), so it is re-read as a descriptive abundance manifestation — clinically
    # neutral, never an active "decreases" — and its coordinated list binds **all three** taxa (the
    # middle one is an apposition the parser split off the conjunction). All nest under causes.
    abund = [o for o in observations if o.predicate == "has_decreased_abundance_of"]
    assert {o.object_concept_id for o in abund} == {
        "BIOM:firmicutes",
        "BIOM:bifidobacterium",
        "BIOM:faecalibacterium_prausnitzii",
    }
    assert all(o.subject_concept_id == "DIS:dysbiosis" for o in abund)
    assert all(o.parent_observation_id == causes.observation_id for o in abund)
    assert all(o.polarity == "neutral" for o in abund)
    # The active-effect predicate is gone — a state does not "decrease" its own taxa.
    assert "decreases" not in by_pred

    # No relation is ever fabricated onto the inflammation subject by the descriptive clause.
    assert not any(
        o.predicate == "characterized_by" and o.subject_text == "inflammation"
        for o in observations
    )


def test_increasing_participle_is_relabelled_to_increased_abundance():
    """The symmetric direction: ", increasing <taxon>" → has_increased_abundance_of (neutral)."""
    _doc, observations = _extract(
        "Chronic inflammation induces dysbiosis, increasing Faecalibacterium prausnitzii."
    )
    inc = [o for o in observations if o.predicate == "has_increased_abundance_of"]
    assert inc, "expected a descriptive increased-abundance manifestation"
    assert all(o.subject_concept_id == "DIS:dysbiosis" and o.polarity == "neutral" for o in inc)
    assert {o.object_concept_id for o in inc} == {"BIOM:faecalibacterium_prausnitzii"}
    # Not read as an active "increases".
    assert not any(o.predicate == "increases" for o in observations)


def test_parent_link_is_not_part_of_the_observation_id():
    """Adding the nesting must not move ids (idempotency, Concept 4)."""
    _doc, observations = _extract(_FIXTURE)
    char = next(o for o in observations if o.predicate == "characterized_by")
    # The id encodes only spans + predicate — never the parent.
    assert char.parent_observation_id is not None
    assert char.parent_observation_id not in char.observation_id
