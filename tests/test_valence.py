"""A2 gate: the clinical benefit/harm axis (knowledge/valence.py) + its surfacing on conclusions.

Pure, offline, deterministic. Covers the object-desirability logic (type defaults + per-concept
overrides), the predicate→direction mapping, the polarity-independence contract (negation is the
synthesis agreement axis, not this one), and the end-to-end surfacing of a state-stratified
"beneficial in remission / harmful in flare" reading on synthesized conclusions.
"""

from mehungry_extractor.knowledge import valence as v
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, init_db
from mehungry_extractor.knowledge.ids import document_id
from mehungry_extractor.knowledge.pipeline import extract_document
from mehungry_extractor.knowledge.synthesis import synthesize


# --- object desirability -------------------------------------------------------------


def test_desirability_type_defaults():
    assert v.object_desirability("DIS:ulcerative_colitis") == v.UNDESIRABLE
    assert v.object_desirability("SYMP:diarrhea") == v.UNDESIRABLE
    assert v.object_desirability("OUT:remission") == v.DESIRABLE
    assert v.object_desirability("NUTR:calcium") == v.NEUTRAL_TARGET


def test_desirability_per_concept_overrides():
    # Biomarkers have no inherent direction by type — the overrides supply it.
    assert v.object_desirability("BIOM:crp") == v.UNDESIRABLE
    assert v.object_desirability("BIOM:ldl") == v.UNDESIRABLE
    assert v.object_desirability("BIOM:hdl") == v.DESIRABLE
    assert v.object_desirability("BIOM:ferritin") == v.DESIRABLE
    # An undesirable outcome overrides the "outcome → desirable" default.
    assert v.object_desirability("OUT:hospitalization") == v.UNDESIRABLE


def test_desirability_falls_back_to_id_prefix_for_unknown_concept():
    assert v.object_desirability("DIS:made_up_disease") == v.UNDESIRABLE
    assert v.object_desirability("OUT:made_up_outcome") == v.DESIRABLE


# --- predicate → clinical direction --------------------------------------------------


def test_direction_depends_on_object_desirability():
    # The same predicate flips meaning with the object's desirability.
    assert v.clinical_direction("decreases", "BIOM:crp") == v.BENEFICIAL
    assert v.clinical_direction("decreases", "OUT:remission") == v.HARMFUL
    assert v.clinical_direction("increases", "BIOM:hdl") == v.BENEFICIAL
    assert v.clinical_direction("increases", "BIOM:ldl") == v.HARMFUL


def test_lexically_valenced_and_risk_predicates():
    assert v.clinical_direction("improves", "OUT:remission") == v.BENEFICIAL
    assert v.clinical_direction("worsens", "SYMP:diarrhea") == v.HARMFUL
    assert v.clinical_direction("reduces_risk", "DIS:colorectal_cancer") == v.BENEFICIAL
    assert v.clinical_direction("increases_risk", "DIS:cardiovascular_disease") == v.HARMFUL
    assert v.clinical_direction("prevents", "DIS:osteoporosis") == v.BENEFICIAL


def test_safety_and_null_predicates():
    assert v.clinical_direction("associated_with_adverse_event", "SYMP:diarrhea") == v.CAUTION
    assert v.clinical_direction("contraindicated", "DIS:ibd") == v.CAUTION
    assert v.clinical_direction("no_effect", "OUT:remission") == v.NEUTRAL
    assert v.clinical_direction("no_association", "DIS:ibd") == v.NEUTRAL


def test_neutral_object_yields_neutral_direction():
    # A nutrient/food as the object has no health direction to read.
    assert v.clinical_direction("decreases", "NUTR:calcium") == v.NEUTRAL
    assert v.clinical_direction("increases", "FOOD:fish") == v.NEUTRAL


# --- surfaced on synthesized conclusions (state-stratified) --------------------------


def _seed(tmp_path, sentences: dict[str, str]):
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    for pmid, sentence in sentences.items():
        meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=f"Paper {pmid}")
        doc = build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])
        store.save_canonical(doc)
        extract_document(pmid, corpus=store, engine=engine, persist=True, use_model=False)
    return engine


def test_state_stratified_conclusions_get_opposite_clinical_readings(tmp_path):
    """The headline: one contrastive sentence → beneficial in remission, harmful in flare."""
    engine = _seed(
        tmp_path,
        {"10000001": (
            "Curcumin improved mucosal healing during remission "
            "but worsened diarrhea during active flare."
        )},
    )
    result = synthesize(engine, [document_id("10000001")])
    by_state = {
        next((q["value_concept_id"] for q in c.qualifiers), None): c
        for c in result.conclusions
        if c.subject_concept_id == "CHEM:curcumin"
    }
    assert by_state["DS:remission"].clinical_direction == v.BENEFICIAL
    assert by_state["DS:active_disease"].clinical_direction == v.HARMFUL


def test_clinical_direction_is_polarity_independent(tmp_path):
    """A negated claim is 'no benefit shown' (refuted), NOT 'harmful' — valence stays beneficial."""
    engine = _seed(
        tmp_path,
        {"10000001": "Dietary fiber did not improve remission."},
    )
    c = synthesize(engine, [document_id("10000001")]).conclusions[0]
    assert c.direction == "refuted"          # agreement axis carries the negation
    assert c.clinical_direction == v.BENEFICIAL  # the reading of the relation itself is unchanged
