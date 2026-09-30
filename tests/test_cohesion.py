"""Deterministic topic-cohesion / outlier detection (knowledge/cohesion.py).

Pure set arithmetic over synthetic concept sets — no DB, no network, no model.
"""

from mehungry_extractor.knowledge.cohesion import (
    REASON_NO_CONCEPTS,
    REASON_OFF_TOPIC,
    detect,
)

# A batch clustered on {A, B} with one clearly off-topic paper.
ON_TOPIC = {
    "pmid_1": {"A", "B", "C"},
    "pmid_2": {"A", "B", "D"},
    "pmid_3": {"A", "B"},
    "pmid_4": {"X", "Y", "Z"},  # shares nothing with the core
}


def test_core_is_the_shared_concepts():
    result = detect(ON_TOPIC)
    # N=4 → min_core_papers = max(2, ceil(0.5*4)) = 2. A and B appear in 3 papers each.
    assert result.min_core_papers == 2
    assert result.core_concepts == ["A", "B"]


def test_off_topic_paper_is_flagged_and_reports_missing_core():
    result = detect(ON_TOPIC)
    by_id = {p.document_id: p for p in result.papers}
    assert by_id["pmid_4"].is_outlier
    assert by_id["pmid_4"].reason == REASON_OFF_TOPIC
    assert by_id["pmid_4"].score == 0.0
    assert by_id["pmid_4"].missing_core_concepts == ["A", "B"]
    # The on-topic papers cover the whole core and are included.
    assert result.included == ["pmid_1", "pmid_2", "pmid_3"]
    assert all(by_id[d].score == 1.0 for d in result.included)


def test_partial_overlap_respects_threshold():
    sets = {
        "pmid_1": {"A", "B", "C", "D"},
        "pmid_2": {"A", "B", "C", "D"},
        "pmid_3": {"A", "B", "C", "D"},
        "pmid_4": {"A"},  # covers 1/4 of the core
    }
    # Core = {A,B,C,D}. pmid_4 scores 0.25.
    assert detect(sets, outlier_threshold=0.2).included == [
        "pmid_1", "pmid_2", "pmid_3", "pmid_4",
    ]  # 0.25 >= 0.2 → kept
    lax = {p.document_id: p for p in detect(sets, outlier_threshold=0.5).papers}
    assert lax["pmid_4"].is_outlier and lax["pmid_4"].score == 0.25  # 0.25 < 0.5 → dropped


def test_paper_with_no_concepts_excluded_with_distinct_reason():
    sets = {"pmid_1": {"A", "B"}, "pmid_2": {"A", "B"}, "pmid_3": set()}
    result = detect(sets)
    empty = next(p for p in result.papers if p.document_id == "pmid_3")
    assert empty.is_outlier and empty.reason == REASON_NO_CONCEPTS and empty.score is None
    assert any("no normalized concepts" in w for w in result.warnings)


def test_no_shared_core_keeps_everyone_and_warns():
    sets = {"pmid_1": {"A"}, "pmid_2": {"B"}, "pmid_3": {"C"}}
    result = detect(sets)
    assert result.core_concepts == []
    assert result.included == ["pmid_1", "pmid_2", "pmid_3"]  # nothing excluded
    assert any("no shared topic core" in w for w in result.warnings)
    assert all(p.score is None for p in result.papers)


def test_deterministic():
    a = detect(ON_TOPIC)
    b = detect(ON_TOPIC)
    assert a.core_concepts == b.core_concepts
    assert [(p.document_id, p.score, p.is_outlier, p.missing_core_concepts) for p in a.papers] == [
        (p.document_id, p.score, p.is_outlier, p.missing_core_concepts) for p in b.papers
    ]
