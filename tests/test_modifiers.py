"""Phase 12 gate: restrictive entity modifiers (docs/phases/phase-12-qualified-entities.md).

A modifier recovers the restrictive prep-phrase an entity head loses on its own — "dysbiosis OF
the gut microbiome" → ``dysbiosis —localized_in→ gut microbiome`` — as a text-derived concept→concept
edge with its own provenance. Covered here: the deterministic model-free floor (happy path,
provenance, determinism), the two guards (a condition ``in`` phrase is not a modifier; a
``risk of X`` object is not double-counted), the DB round-trip, and — gated on the model — the parse
detector plus the unmatched-object path. The claim key is untouched in this phase, so a
backward-compatibility check asserts modifier-free claim ids are unchanged.
"""

import pytest

from mehungry_extractor.knowledge import modifiers, parse as _parse
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.db import get_engine, init_db, persist_document
from mehungry_extractor.knowledge.db.schema import EntityModifierRow, persist_entities
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.modifiers import ModifierRelation, annotate, extract, signature
from mehungry_extractor.knowledge.run import build_run


def _doc(*sentences: str):
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=list(sentences))])


# --- the deterministic, model-free floor --------------------------------------------


def test_floor_localizes_dysbiosis_to_the_gut_microbiome():
    doc = _doc("IBD is strongly associated with dysbiosis of the gut microbiome.")
    mentions = extract_entities(doc, use_model=False)
    mods = extract(doc, mentions, use_model=False)

    dysbiosis = next(m for m in mentions if m.concept_id == "DIS:dysbiosis")
    attached = mods[dysbiosis.mention_id]
    assert len(attached) == 1
    mod = attached[0]
    assert mod.relation == ModifierRelation.LOCALIZED_IN.value
    assert mod.value_concept_id == "BIOM:gut_microbiota"
    assert mod.value_text == "gut microbiome"
    assert mod.status == "normalized"
    assert mod.preposition == "of"


def test_floor_modifier_evidence_reconstructs_from_offsets():
    doc = _doc("IBD is associated with dysbiosis of the gut microbiome.")
    mentions = extract_entities(doc, use_model=False)
    mods = extract(doc, mentions, use_model=False)
    ref = next(iter(mods.values()))[0].evidence_refs[0]
    assert doc.text[ref.start_char:ref.end_char] == "of the gut microbiome"
    assert ref.quoted_text == "of the gut microbiome"


def test_floor_is_deterministic():
    doc = _doc("Dysbiosis of the gut microbiome drives inflammation.")
    mentions = extract_entities(doc, use_model=False)
    a = extract(doc, mentions, use_model=False)
    b = extract(doc, mentions, use_model=False)
    flatten = lambda r: sorted(
        (mid, m.relation, m.value_concept_id, m.value_text)
        for mid, mods in r.items()
        for m in mods
    )
    assert flatten(a) == flatten(b)


# --- guards --------------------------------------------------------------------------


def test_condition_in_phrase_is_not_a_modifier():
    # "in remission" is a Phase-6 disease-state qualifier, never a modifier: "in" is not an
    # attributive preposition and remission is not a curated site, so the cue table skips it.
    doc = _doc("Fiber in remission of symptoms is beneficial.")
    mentions = extract_entities(doc, use_model=False)
    mods = extract(doc, mentions, use_model=False)
    for attached in mods.values():
        assert all(m.relation != ModifierRelation.LOCALIZED_IN.value for m in attached)
        assert all(m.preposition != "in" for m in attached)


def test_relation_typing_rules():
    # A site concept under a localizing preposition → localized_in.
    assert modifiers._relation_for("of", "BIOM:gut_microbiota", "normalized") == "localized_in"
    # A non-site entity under an attributive preposition → the generic, non-key-bearing fallback.
    assert modifiers._relation_for("of", "DIS:cancer", "normalized") == "qualified_by"
    # An unmatched object still keeps an attributive "of" edge (never dropped)...
    assert modifiers._relation_for("of", None, "unmatched") == "qualified_by"
    # ...but a condition "in" phrase is not a modifier at all.
    assert modifiers._relation_for("in", None, "unmatched") is None
    assert modifiers._relation_for("in", "DIS:cancer", "normalized") is None


# --- signature is empty in Phase 12 (claim keys unchanged) ---------------------------


def test_signature_empty_without_modifiers():
    assert signature([]) == ""


def test_signature_ignores_non_key_bearing_relations():
    doc = _doc("Dysbiosis of the gut microbiome.")
    mentions = extract_entities(doc, use_model=False)
    mods = next(iter(extract(doc, mentions, use_model=False).values()))
    # localized_in is key-bearing; a hypothetical generic edge would not contribute.
    assert signature(mods) == "localized_in=BIOM:gut_microbiota"
    generic = [m.model_copy(update={"relation": ModifierRelation.QUALIFIED_BY.value}) for m in mods]
    assert signature(generic) == ""


# --- annotate + DB round-trip --------------------------------------------------------


def test_annotate_and_persist_round_trip(tmp_path):
    doc = _doc("IBD is associated with dysbiosis of the gut microbiome.")
    mentions = annotate(doc, extract_entities(doc, use_model=False), use_model=False)
    assert any(m.modifiers for m in mentions)

    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    run = build_run([doc.document_id])
    from mehungry_extractor.knowledge.db import session_scope

    with session_scope(engine) as session:
        persist_document(session, doc, run)
        persist_entities(session, doc, mentions, run)

    with session_scope(engine) as session:
        rows = session.query(EntityModifierRow).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.relation == "localized_in"
        assert r.value_concept_id == "BIOM:gut_microbiota"
        assert r.evidence_refs and r.evidence_refs[0]["quoted_text"] == "of the gut microbiome"

    # Re-persist replaces rather than duplicates (idempotent delete-then-insert).
    with session_scope(engine) as session:
        persist_entities(session, doc, mentions, run)
    with session_scope(engine) as session:
        assert session.query(EntityModifierRow).count() == 1


def test_mentions_without_modifiers_have_empty_list():
    doc = _doc("Vitamin D reduces inflammation.")
    mentions = annotate(doc, extract_entities(doc, use_model=False), use_model=False)
    assert all(m.modifiers == [] for m in mentions)


# --- parse detector (model path) -----------------------------------------------------


@pytest.mark.skipif(not _parse.available(), reason="scispaCy parser model not installed")
def test_parse_detector_localizes_dysbiosis():
    doc = _doc("IBD is strongly associated with dysbiosis of the gut microbiome.")
    mentions = extract_entities(doc, use_model=True)
    mods = extract(doc, mentions, use_model=True)
    dysbiosis = next(m for m in mentions if m.concept_id == "DIS:dysbiosis")
    attached = mods[dysbiosis.mention_id]
    assert [(m.relation, m.value_concept_id) for m in attached] == [
        ("localized_in", "BIOM:gut_microbiota")
    ]
    assert attached[0].rule_id == "entity_modifier:parse"


@pytest.mark.skipif(not _parse.available(), reason="scispaCy parser model not installed")
def test_parse_unmatched_object_is_kept_with_surface():
    # An attributive "of" phrase whose object is not in the vocabulary is kept unmatched, not
    # dropped — provenance survives a normalization miss.
    doc = _doc("Patients showed dysbiosis of the microflora.")
    mentions = extract_entities(doc, use_model=True)
    mods = extract(doc, mentions, use_model=True)
    dysbiosis = next(m for m in mentions if m.concept_id == "DIS:dysbiosis")
    attached = mods.get(dysbiosis.mention_id, [])
    assert len(attached) == 1
    assert attached[0].status == "unmatched"
    assert attached[0].value_concept_id is None
    assert "microflora" in attached[0].value_text


# --- A2: a discriminating modifier widens the claim key --------------------------------


def _observation(subj_cid, obj_cid, *, object_modifiers=None):
    """A minimal normalized Observation for claim-keying tests (no model, no document)."""
    from mehungry_extractor.knowledge.observations import Observation
    from mehungry_extractor.knowledge.provenance import EvidenceRef, ProvenancePrecision

    ev = EvidenceRef(
        document_id="pmid_1", sentence_id="s1", start_char=0, end_char=5, quoted_text="x",
        precision=ProvenancePrecision.SENTENCE, extraction_rule="r", extraction_rule_version="1",
        extractor_version="1",
    )
    return Observation(
        observation_id=f"pmid_1_obs_{subj_cid}_{obj_cid}_{'mod' if object_modifiers else 'bare'}",
        document_id="pmid_1", sentence_id="s1",
        subject_mention_id="m1", subject_text="IBD", subject_concept_id=subj_cid,
        predicate="associated_with",
        object_mention_id="m2", object_text="dysbiosis", object_concept_id=obj_cid,
        object_modifiers=object_modifiers or [],
        polarity="positive", certainty="asserted",
        rule_id="r", rule_version="1", evidence_refs=[ev],
    )


def _localized(concept_id="BIOM:gut_microbiota"):
    from mehungry_extractor.knowledge.provenance import EvidenceRef, ProvenancePrecision

    ev = EvidenceRef(
        document_id="pmid_1", sentence_id="s1", start_char=0, end_char=5, quoted_text="of x",
        precision=ProvenancePrecision.SENTENCE, extraction_rule="entity_modifier:floor",
        extraction_rule_version="0.1.0", extractor_version="1",
    )
    return modifiers.EntityModifier(
        relation=ModifierRelation.LOCALIZED_IN.value, preposition="of",
        value_concept_id=concept_id, value_text="gut microbiome", value_type="biomarker",
        status="normalized", evidence_refs=[ev], rule_id="entity_modifier:floor", rule_version="0.1.0",
    )


def _qualified_by():
    m = _localized()
    return m.model_copy(update={"relation": ModifierRelation.QUALIFIED_BY.value})


def test_localized_modifier_forks_the_claim():
    from mehungry_extractor.knowledge.claims import normalize

    bare = _observation("DIS:ibd", "DIS:dysbiosis")
    localized = _observation("DIS:ibd", "DIS:dysbiosis", object_modifiers=[_localized()])
    result = normalize([bare, localized], concept_name=lambda cid: cid)
    # Same bare (subject, predicate, object) concepts, yet TWO distinct claims: the localized
    # endpoint does not merge into the unlocalized one.
    assert len(result.claims) == 2
    assert len({c.claim_id for c in result.claims}) == 2
    localized_claim = next(c for c in result.claims if c.object_modifiers)
    assert localized_claim.object_modifiers[0].value_concept_id == "BIOM:gut_microbiota"


def test_unmodified_claim_id_is_unchanged_by_a2():
    from mehungry_extractor.knowledge.claims import _claim_id, normalize

    bare = _observation("DIS:ibd", "DIS:dysbiosis")
    [claim] = normalize([bare], concept_name=lambda cid: cid).claims
    # The id of a modifier-free claim is byte-identical to the pre-A2 (Phase 5/6) hash — no modifier
    # signature is appended when the endpoints carry none.
    expected = _claim_id("pmid_1", ("DIS:ibd", "associated_with", "DIS:dysbiosis", "positive", "asserted"))
    assert claim.claim_id == expected


def test_qualified_by_modifier_does_not_fork_the_claim():
    from mehungry_extractor.knowledge.claims import normalize

    bare = _observation("DIS:ibd", "DIS:dysbiosis")
    generic = _observation("DIS:ibd", "DIS:dysbiosis", object_modifiers=[_qualified_by()])
    # A generic (non-key-bearing) modifier contributes an empty signature, so the two observations
    # merge into a single claim — noise never splits claims.
    result = normalize([bare, generic], concept_name=lambda cid: cid)
    assert len(result.claims) == 1


def test_synthesis_splits_localized_from_bare(tmp_path):
    from mehungry_extractor.knowledge import claims as _claims, observations as _obs
    from mehungry_extractor.knowledge.db import persist_document, persist_relations, session_scope
    from mehungry_extractor.knowledge.synthesis import synthesize

    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)

    def _ingest(pmid, sentence):
        meta = DocumentMetadata(pmid=pmid, source_type="abstract")
        doc = build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])
        ms = annotate(doc, extract_entities(doc, use_model=False), use_model=False)
        ob = _obs.extract(doc, ms, use_model=False)
        cr = _claims.normalize(ob, concept_name=lambda cid: cid)
        run = build_run([doc.document_id])
        with session_scope(engine) as s:
            persist_document(s, doc, run)
            from mehungry_extractor.knowledge.db.schema import persist_entities as _pe

            _pe(s, doc, ms, run)
            persist_relations(s, doc, ob, cr.claims, run)
        return doc.document_id

    d1 = _ingest("11111111", "IBD is associated with dysbiosis of the gut microbiome.")
    d2 = _ingest("22222222", "IBD is associated with dysbiosis.")

    syn = synthesize(engine, [d1, d2])
    dysbiosis = [c for c in syn.conclusions if c.object_concept_id == "DIS:dysbiosis"]
    # The localized and the bare relation are two separate conclusions, not one merged pair.
    assert len(dysbiosis) == 2
    localized = [c for c in dysbiosis if c.object_modifiers]
    assert len(localized) == 1
    assert localized[0].object_modifiers[0]["value_concept_id"] == "BIOM:gut_microbiota"


@pytest.mark.skipif(not _parse.available(), reason="scispaCy parser model not installed")
def test_risk_of_object_is_not_a_modifier():
    # "reduced the risk of cancer": cancer is a relation endpoint under "risk", not a modifier of
    # an entity head — no spurious edge is emitted.
    doc = _doc("Dietary fiber reduced the risk of cancer.")
    mentions = extract_entities(doc, use_model=True)
    mods = extract(doc, mentions, use_model=True)
    for attached in mods.values():
        assert all(m.value_concept_id != "DIS:cancer" for m in attached)
