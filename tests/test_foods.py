"""5a gate: the compound→food composition ontology (knowledge/foods.py) + A4 outcome attainment.

Pure, offline, deterministic. The composition graph is a checked-in ontology keyed to the entity
vocabulary; every edge endpoint must resolve, lookups are bidirectional and stable, and it asserts
no food↔disease relation (it is only the linking layer — 5b is the separate derived layer). A4 is
covered by the new outcome-attainment predicate flowing through extraction + valence.
"""

from mehungry_extractor.knowledge import foods
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, init_db
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.ids import document_id
from mehungry_extractor.knowledge.normalize import concept_by_id
from mehungry_extractor.knowledge.observations import extract as extract_observations
from mehungry_extractor.knowledge.pipeline import extract_document
from mehungry_extractor.knowledge.synthesis import synthesize
from mehungry_extractor.knowledge.valence import BENEFICIAL, clinical_direction


# --- 5a: composition ontology --------------------------------------------------------


def test_every_edge_endpoint_resolves_and_is_typed():
    edges = foods.load_edges()
    assert edges
    for e in edges:
        assert concept_by_id(e.source_concept_id) is not None
        assert concept_by_id(e.food_concept_id) is not None
        # sources are compounds/nutrients; foods are foods.
        assert e.source_type in ("chemical", "nutrient")
        assert concept_by_id(e.food_concept_id).entity_type == "food"
        assert e.relation == foods.FOUND_IN
        assert e.ontology_version  # provenance for any derived (5b) leap


def test_ontology_has_broad_coverage():
    edges = foods.load_edges()
    assert len(edges) >= 300  # expanded well beyond the initial seed set
    sources = {e.source_concept_id for e in edges}
    assert len(sources) >= 40  # many compounds/nutrients covered
    # a nutrient-dense food resolves to many of its constituents (reverse lookup is rich)
    assert len(foods.compounds_in("FOOD:kale")) >= 5
    assert len(foods.compounds_in("FOOD:fish")) >= 5
    # no duplicate edges
    keys = [(e.source_concept_id, e.food_concept_id) for e in edges]
    assert len(keys) == len(set(keys))


def test_flagship_lookups():
    assert [e.food_name for e in foods.food_sources_for("CHEM:curcumin")] == ["Turmeric"]
    omega3 = {e.food_concept_id for e in foods.food_sources_for("NUTR:omega_3")}
    assert {"FOOD:fish", "FOOD:flaxseed", "FOOD:walnuts"} <= omega3


def test_lookups_are_inverse_and_deterministic():
    # If curcumin is found_in turmeric, turmeric must list curcumin among its compounds.
    assert any(e.source_concept_id == "CHEM:curcumin" for e in foods.compounds_in("FOOD:turmeric"))
    # Deterministic ordering on repeated calls.
    assert [e.food_concept_id for e in foods.food_sources_for("CHEM:polyphenols")] == \
           [e.food_concept_id for e in foods.food_sources_for("CHEM:polyphenols")]


def test_unknown_concept_has_no_edges():
    assert foods.food_sources_for("CHEM:does_not_exist") == []
    assert foods.compounds_in("FOOD:does_not_exist") == []


# --- A4: outcome attainment predicate ------------------------------------------------


def _obs_one(text: str):
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    doc = build_document(meta, [ParsedSection(title="Body", paragraphs=[text])])
    obs = extract_observations(doc, extract_entities(doc, use_model=False), use_model=False)
    assert len(obs) == 1, obs
    return obs[0]


def test_achieves_predicate_for_outcome_attainment():
    o = _obs_one("Exclusive enteral nutrition achieved remission in children.")
    assert o.predicate == "achieves"
    assert (o.subject_concept_id, o.object_concept_id) == (
        "INT:exclusive_enteral_nutrition", "OUT:remission",
    )
    # attaining a desirable outcome reads as beneficial
    assert clinical_direction(o.predicate, o.object_concept_id) == BENEFICIAL


def test_maintains_maps_to_achieves():
    o = _obs_one("Curcumin maintained mucosal healing during remission.")
    assert o.predicate == "achieves"
    assert o.object_concept_id == "OUT:mucosal_healing"


# --- 5b: derived food-level conclusions ----------------------------------------------


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


def test_compound_conclusion_derives_labeled_food_conclusion(tmp_path):
    engine = _seed(tmp_path, {"10000001": "Curcumin improved mucosal healing during remission."})
    result = synthesize(engine, [document_id("10000001")])

    # The direct (observed) conclusion is about the compound, not a food.
    assert all(c.subject_concept_id != "FOOD:turmeric" for c in result.conclusions)

    # Turmeric shows up only in the *derived* section, bridged from curcumin.
    turmeric = [d for d in result.derived_conclusions if d.food_concept_id == "FOOD:turmeric"]
    assert len(turmeric) == 1
    d = turmeric[0]
    assert d.predicate == "improves" and d.object_concept_id == "OUT:mucosal_healing"
    assert d.clinical_direction == BENEFICIAL          # inherited from the compound conclusion
    assert d.derived_via == "composition_ontology"
    # provenance for the leap: the found_in edge + version, naming the source compound.
    assert [v.source_concept_id for v in d.via] == ["CHEM:curcumin"]
    assert d.via[0].relation == "found_in" and d.via[0].ontology_version
    # evidence is the *compound* claim's span (no new evidence invented).
    assert d.evidence and d.evidence[0].quoted_text.startswith("Curcumin improved")
    # and it carries the disease-state condition through.
    assert any(q["value_concept_id"] == "DS:remission" for q in d.qualifiers)


def test_one_compound_derives_all_its_foods(tmp_path):
    engine = _seed(tmp_path, {"10000001": "Omega-3 fatty acids reduced fecal calprotectin."})
    foods_hit = {
        d.food_concept_id
        for d in synthesize(engine, [document_id("10000001")]).derived_conclusions
        if d.object_concept_id == "BIOM:fecal_calprotectin"
    }
    assert {"FOOD:fish", "FOOD:flaxseed", "FOOD:walnuts"} <= foods_hit


def test_no_food_edges_means_no_derivation(tmp_path):
    # dietary_intervention has no composition edges → nothing derived from it.
    engine = _seed(tmp_path, {"10000001": "Dietary intervention improved remission."})
    result = synthesize(engine, [document_id("10000001")])
    assert result.conclusions  # the direct conclusion still exists
    assert result.derived_conclusions == []


def test_derivation_is_deterministic(tmp_path):
    engine = _seed(tmp_path, {"10000001": "Curcumin improved mucosal healing during remission."})
    docs = [document_id("10000001")]

    def snap():
        return [
            (d.food_concept_id, d.predicate, d.object_concept_id, d.clinical_direction,
             tuple(v.source_concept_id for v in d.via))
            for d in synthesize(engine, docs).derived_conclusions
        ]

    assert snap() == snap()
