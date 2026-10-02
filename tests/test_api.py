"""Batch analysis REST API (knowledge/api).

Runs fully offline: a temp corpus is pre-seeded with canonical docs and the FastAPI
``get_context`` dependency is overridden so no network ingest happens (``ingest=False``).
Skipped if the optional ``[api]`` extra (fastapi) is not installed.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from mehungry_extractor.knowledge.api.app import app, get_context  # noqa: E402
from mehungry_extractor.knowledge.canonical import (  # noqa: E402
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.corpus import CorpusStore  # noqa: E402
from mehungry_extractor.knowledge.db import get_engine, init_db  # noqa: E402

# A batch clustered on dietary fiber / remission, with agreement, a dissenter, and an outlier.
BATCH = {
    "10000001": "Dietary fiber improved remission.",
    "10000002": "Dietary fiber improved remission.",
    "10000003": "Dietary fiber did not improve remission.",
    "10000004": "Vitamin D improved bone density.",  # off-topic concepts -> cohesion outlier
}

# Every paper's TITLE passes the topical barrier (each names a nutrition/diet keyword). The
# outlier is an outlier by shared-CONCEPT overlap, not by title, so it too carries a diet title.
TITLES = {
    "10000001": "Dietary fiber and remission in Crohn disease",
    "10000002": "Dietary fiber and remission in Crohn disease",
    "10000003": "Dietary fiber and remission in ulcerative colitis",
    "10000004": "Dietary vitamin D and bone density",
}


@pytest.fixture
def client(tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    for pmid, sentence in BATCH.items():
        meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=TITLES[pmid])
        doc = build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])
        store.save_canonical(doc)

    app.dependency_overrides[get_context] = lambda: {
        "corpus": store,
        "engine": engine,
        "ingest": False,  # offline: never hit the network in tests
    }
    yield TestClient(app)
    app.dependency_overrides.clear()


def _analyze(client, pmids):
    return client.post(
        "/analyze", json={"pmids": pmids, "options": {"use_model": False}}
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_flags_outlier_and_returns_per_paper_claims(client):
    r = _analyze(client, list(BATCH))
    assert r.status_code == 200
    body = r.json()

    # Synthesis output is no longer returned — only the normalized per-paper claim layer.
    assert "conclusions" not in body
    assert "derived_conclusions" not in body
    assert "facts" not in body

    # The off-topic paper is still reported as an outlier.
    assert [o["pmid"] for o in body["outliers"]] == ["10000004"]
    assert set(body["included_pmids"]) == {"10000001", "10000002", "10000003"}

    # One per-paper entry for every successfully-extracted paper (outlier included), in request order.
    pc = body["paper_claims"]
    assert [p["pmid"] for p in pc] == ["10000001", "10000002", "10000003", "10000004"]
    by_pmid = {p["pmid"]: p for p in pc}

    # Each entry carries the paper's title, public PubMed URL, and the text it was extracted from.
    assert by_pmid["10000001"]["paper_title"] == "Dietary fiber and remission in Crohn disease"
    assert by_pmid["10000001"]["paper_url"] == "https://pubmed.ncbi.nlm.nih.gov/10000001/"
    assert by_pmid["10000001"]["source_type"] == "abstract"

    # The affirmative paper yields one fully-detailed, reconstructable claim.
    claims = by_pmid["10000001"]["claims_list"]
    assert len(claims) == 1
    c = claims[0]
    assert c["claim_id"]
    assert c["subject_concept_id"] == "NUTR:dietary_fiber"
    assert c["object_concept_id"] == "OUT:remission"
    assert c["predicate"] == "improves"
    assert c["polarity"] == "positive"
    assert c["certainty"] == "asserted"
    # A claim's evidence is the deduplicated union of its observations' spans, each re-sliced from
    # the canonical text so it reconstructs verbatim.
    assert [e["quoted_text"] for e in c["evidence"]] == ["Dietary fiber improved remission."]
    assert c["evidence"][0]["reconstructed_text"] == "Dietary fiber improved remission."

    # The dissenting paper produces the same relation with flipped polarity (negation).
    assert by_pmid["10000003"]["claims_list"][0]["polarity"] == "negative"

    # Reproducibility metadata is still present.
    assert body["run"]["pipeline_version"]

    # The outlier still appears in the per-paper report (never silently dropped).
    statuses = {p["pmid"]: p["status"] for p in body["papers"]}
    assert statuses == {
        "10000001": "included", "10000002": "included",
        "10000003": "included", "10000004": "outlier",
    }


def test_analyze_is_deterministic(client):
    a = _analyze(client, list(BATCH)).json()
    b = _analyze(client, list(BATCH)).json()
    assert a["paper_claims"] == b["paper_claims"]
    assert a["outliers"] == b["outliers"]
    assert a["topic"] == b["topic"]


def test_analyze_claim_exposes_restrictive_modifiers(tmp_path):
    """An endpoint's restrictive modifier (Concept 19) reaches the response, and ``object_label``
    renders the target back in — so "Dysbiosis" is served as "Dysbiosis of the gut microbiome",
    not collapsed to the bare head."""
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    batch = {
        "20000001": "IBD is strongly associated with dysbiosis of the gut microbiome.",
        "20000002": "IBD is associated with dysbiosis of the gut microbiome.",
    }
    for pmid, sentence in batch.items():
        # Title names a keyword so the paper clears the topical barrier (Concept 1's gate).
        meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=f"Dietary patterns and dysbiosis (paper {pmid})")
        store.save_canonical(build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])]))

    app.dependency_overrides[get_context] = lambda: {
        "corpus": store, "engine": engine, "ingest": False,
    }
    try:
        client = TestClient(app)
        body = client.post(
            "/analyze", json={"pmids": list(batch), "options": {"use_model": False}}
        ).json()
    finally:
        app.dependency_overrides.clear()

    pc = {p["pmid"]: p for p in body["paper_claims"]}
    o = pc["20000001"]["claims_list"][0]
    assert o["object_concept_id"] == "DIS:dysbiosis"
    # The restrictive modifier survives serialization as structured data …
    assert o["object_modifiers"] == [{
        "relation": "localized_in",
        "preposition": "of",
        "value_concept_id": "BIOM:gut_microbiota",
        "value_text": "gut microbiome",
        "value_type": o["object_modifiers"][0]["value_type"],
        "status": "normalized",
    }]
    # … and the composed label folds the target back into the collapsed display.
    assert o["object_label"] == "Dysbiosis of gut microbiome"
    assert o["subject_modifiers"] == [] and o["subject_label"] == "Inflammatory bowel disease"


def test_analyze_excludes_cached_off_topic_title(tmp_path):
    """A paper already in the corpus whose TITLE names no topical keyword is excluded from the
    response — the barrier applies to cache hits, not just freshly-ingested papers."""
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    seeded = {
        # on-topic title -> included
        "30000001": ("Dietary fiber and remission", "Dietary fiber improved remission."),
        # off-topic title -> must be excluded even though it is cached
        "30000002": ("A study of cardiac stents", "Dietary fiber improved remission."),
    }
    for pmid, (title, sentence) in seeded.items():
        meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=title)
        store.save_canonical(build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])]))

    app.dependency_overrides[get_context] = lambda: {
        "corpus": store, "engine": engine, "ingest": False,
    }
    try:
        body = client_post(list(seeded))
    finally:
        app.dependency_overrides.clear()

    # The off-topic-titled paper contributes NO results: absent from claims and inclusions.
    assert [p["pmid"] for p in body["paper_claims"]] == ["30000001"]
    assert "30000002" not in set(body["included_pmids"])
    # It is still reported per-paper (honest bookkeeping), but only as an error — never a result.
    status = {p["pmid"]: p["status"] for p in body["papers"]}
    assert status["30000002"] == "error"
    # … and its exclusion is surfaced honestly as a title-filter warning, not a crash.
    assert any("excluded by title filter" in w and "30000002" in w for w in body["warnings"])


def client_post(pmids):
    return TestClient(app).post(
        "/analyze", json={"pmids": pmids, "options": {"use_model": False}}
    ).json()


def test_empty_pmids_rejected(client):
    assert _analyze(client, []).status_code == 422


def test_too_many_pmids_rejected(client):
    from mehungry_extractor.knowledge.api.models import MAX_PMIDS

    assert _analyze(client, [str(i) for i in range(MAX_PMIDS + 1)]).status_code == 422
