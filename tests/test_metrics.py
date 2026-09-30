"""Relation-layer quality metrics (self-loops as a label-free precision probe).

Pure and offline: the metric is a fold over already-extracted rows, so these tests drive it with
lightweight stubs plus one end-to-end check through the deterministic pipeline + store.
"""

from types import SimpleNamespace

from mehungry_extractor.knowledge.metrics import (
    LONG_WINDOW_CHARS,
    ObsGeometry,
    RelationQuality,
    aliasing_probability,
    relation_quality,
)


def _claim(subj, obj, predicate="associated_with", document_id="d1"):
    return SimpleNamespace(
        subject_concept_id=subj, object_concept_id=obj, predicate=predicate, document_id=document_id
    )


def _obs(oid, subj, obj, *, rule_id="rel_associated_with", subject_text="x", object_text="x"):
    return SimpleNamespace(
        observation_id=oid,
        subject_concept_id=subj,
        object_concept_id=obj,
        predicate="associated_with",
        rule_id=rule_id,
        subject_text=subject_text,
        object_text=object_text,
    )


def _mention(sentence_id, concept_id, start, end):
    return SimpleNamespace(sentence_id=sentence_id, concept_id=concept_id, start_char=start, end_char=end)


# --- core counting -----------------------------------------------------------------


def test_self_loop_rate_is_observed_floor():
    claims = [_claim("A", "A"), _claim("A", "B"), _claim("B", "B"), _claim("C", "D")]
    rq = relation_quality(claims, [])
    assert rq.total_claims == 4
    assert rq.self_loops == 2
    assert rq.self_loop_rate == 0.5


def test_estimated_fp_is_floor_over_detectability_clamped():
    claims = [_claim("A", "A")] + [_claim("A", "B") for _ in range(3)]  # 1/4 self-loops
    rq = relation_quality(claims, [], p_alias=0.5)
    # floor 0.25 / p_alias 0.5 = 0.5
    assert rq.estimated_fp_rate == 0.5
    # A tiny p_alias would push the estimate over 1.0 → clamp.
    rq2 = relation_quality(claims, [], p_alias=0.1)
    assert rq2.estimated_fp_rate == 1.0
    # No p_alias → only the floor is honest; estimate is None.
    assert relation_quality(claims, []).estimated_fp_rate is None


def test_by_predicate_and_by_rule_attribution():
    claims = [_claim("A", "A", "increases"), _claim("A", "B", "increases"), _claim("C", "C", "causes")]
    obs = [
        _obs("o1", "A", "A", rule_id="rel_increases"),
        _obs("o2", "A", "B", rule_id="rel_increases"),
        _obs("o3", "C", "C", rule_id="rel_causes"),
    ]
    rq = relation_quality(claims, obs)
    assert rq.by_predicate["increases"] == (1, 2)
    assert rq.by_predicate["causes"] == (1, 1)
    assert rq.by_rule_id["rel_increases"] == (1, 2)
    assert rq.by_rule_id["rel_causes"] == (1, 1)


# --- mechanism buckets -------------------------------------------------------------


def test_mechanism_sub_phrase_when_surfaces_differ():
    # Same concept, different surface text → compound/head-noun match ("dietary fiber" / "fiber").
    obs = [_obs("o1", "N", "N", subject_text="dietary fiber", object_text="fiber")]
    rq = relation_quality([], obs)
    assert rq.mechanism["sub_phrase"] == 1


def test_mechanism_long_window_vs_topic_repeat():
    obs = [_obs("o_far", "A", "A"), _obs("o_near", "A", "A")]
    geom = {
        "o_far": ObsGeometry(distance=LONG_WINDOW_CHARS + 50, connecting_text=" had a 44% increased risk of developing T2D, while patients with "),
        "o_near": ObsGeometry(distance=10, connecting_text=" reduced "),
    }
    rq = relation_quality([], obs, geometry=geom)
    assert rq.mechanism["long_window"] == 1
    assert rq.mechanism["topic_repeat"] == 1


def test_mechanism_list_like_pure_separators():
    obs = [_obs("o1", "O", "O")]
    geom = {"o1": ObsGeometry(distance=6, connecting_text=" and ")}
    rq = relation_quality([], obs, geometry=geom)
    assert rq.mechanism["list_like"] == 1


def test_missing_geometry_degrades_to_topic_repeat_not_error():
    obs = [_obs("o1", "A", "A")]
    rq = relation_quality([], obs)  # no geometry supplied
    assert rq.mechanism["topic_repeat"] == 1
    assert sum(rq.mechanism.values()) == 1


# --- p_alias -----------------------------------------------------------------------


def test_aliasing_probability_counts_adjacent_both_normalized_pairs():
    mentions = [
        _mention("s1", "A", 0, 5),
        _mention("s1", "A", 10, 15),   # adjacent to prev, same concept → aliased pair
        _mention("s1", "B", 20, 25),   # adjacent to prev, different concept
        _mention("s2", None, 0, 5),    # unnormalized → excluded
        _mention("s2", "C", 10, 15),
    ]
    # s1: pairs (A,A) aliased, (A,B) not → 1/2 ; s2: single normalized mention → no pair
    assert aliasing_probability(mentions) == 0.5


def test_aliasing_probability_skips_overlapping_spans():
    mentions = [_mention("s1", "A", 0, 20), _mention("s1", "B", 5, 25)]  # overlap → not a pair
    assert aliasing_probability(mentions) is None


def test_aliasing_probability_none_when_no_pairs():
    assert aliasing_probability([]) is None


# --- end-to-end through the store --------------------------------------------------


def test_report_and_render_end_to_end(tmp_path, pubmed_xml, pmc_xml):
    """Through the real store: normalize → extract → persist → report + render."""
    from mehungry_extractor.knowledge.audit import render_relation_quality
    from mehungry_extractor.knowledge.corpus import CorpusStore
    from mehungry_extractor.knowledge.db import get_engine
    from mehungry_extractor.knowledge.ingest import normalize_document
    from mehungry_extractor.knowledge.pipeline import extract_document
    from mehungry_extractor.knowledge.query import relation_quality_report

    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": pubmed_xml, "pmc.xml": pmc_xml})
    engine = get_engine(tmp_path / "db.sqlite")
    normalize_document("12345678", corpus=store, engine=engine)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)

    report = relation_quality_report(engine)
    assert report is not None
    assert report["total_claims"] >= 1
    # The self-loop rate is a well-formed fraction of the claims.
    assert 0.0 <= report["self_loop_rate"] <= 1.0
    assert report["self_loops"] == sum(v[0] for v in report["by_predicate"].values())
    # Mechanism buckets account for exactly the self-loops.
    assert sum(report["mechanism"].values()) == sum(
        v[0] for v in report["by_rule_id"].values()
    )

    rendered = render_relation_quality(engine)
    assert rendered is not None
    assert "RELATION QUALITY" in rendered
    assert "false-positive FLOOR" in rendered
