"""The topical title filter at the mouth of the pipeline (ingest gate)."""

import pytest

from mehungry_extractor.knowledge import acquire, ingest
from mehungry_extractor.knowledge.acquire import RawSources
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine
from mehungry_extractor.knowledge.titlefilter import TitleFiltered, title_matches


@pytest.mark.parametrize(
    "title, expected",
    [
        # on-topic — direct keyword, case-insensitive
        ("Effects of a plant-based diet on Body Mass Index", True),
        ("NUTRITIONAL interventions in adults", True),
        ("A study of protein intake", True),
        ("Metabolic syndrome and lifestyle", True),
        ("Vegetarian eating patterns", True),
        ("Food-Based dietary guidelines", True),
        # on-topic — regular plurals of singular keywords
        ("Diets high in fiber", True),
        ("Comparison of meals and foods", True),
        ("Lifestyles of centenarians", True),
        # off-topic — whole-word guard must not fire on embedded substrings
        ("Renal transplantation outcomes", False),
        ("Implantation of cardiac devices", False),
        ("Plantar fasciitis in runners", False),
        ("Proteinuria in diabetic patients", False),
        ("Metabolism of glucose", False),
        ("Machine learning for radiology", False),
        # empty / missing titles never pass
        ("", False),
        (None, False),
    ],
)
def test_title_matches(title, expected):
    assert title_matches(title) is expected


def _raw_with_title(pmid: str, title: str) -> RawSources:
    xml = (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
        f"<PMID>{pmid}</PMID><Article><ArticleTitle>{title}</ArticleTitle>"
        "<Journal><Title>Test Journal</Title></Journal></Article>"
        "</MedlineCitation></PubmedArticle></PubmedArticleSet>"
    ).encode("utf-8")
    return RawSources(pmid=pmid, pmcid=None, pubmed_xml=xml, pmc_xml=None)


def test_ingest_on_topic_title_is_processed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        acquire, "fetch", lambda pmid, timeout=30: _raw_with_title(pmid, "Dietary fiber and health")
    )
    store = CorpusStore(tmp_path)
    engine = get_engine(tmp_path / "db.sqlite")

    doc = ingest.ingest_pmid("12345678", corpus=store, engine=engine)

    assert doc.metadata.title == "Dietary fiber and health"
    assert store.has_raw("12345678")  # archived → downstream stages can run


def test_ingest_off_topic_title_is_filtered(tmp_path, monkeypatch):
    monkeypatch.setattr(
        acquire, "fetch", lambda pmid, timeout=30: _raw_with_title(pmid, "Renal transplantation outcomes")
    )
    store = CorpusStore(tmp_path)
    engine = get_engine(tmp_path / "db.sqlite")

    with pytest.raises(TitleFiltered) as excinfo:
        ingest.ingest_pmid("87654321", corpus=store, engine=engine)

    assert excinfo.value.title == "Renal transplantation outcomes"
    # A filtered paper never enters the immutable corpus.
    assert not store.has_raw("87654321")
    assert not store.has_canonical("87654321")
