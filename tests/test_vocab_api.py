"""The writable vocabulary overlay + the /vocab management surface.

The checked-in ``dictionaries.json`` is the reproducible baseline; a user's add/edit/remove live in
a writable overlay layered on top (``vocab.save_overlay``) and take effect for the *next* extraction
in the same process (caches are invalidated). These tests cover the overlay merge, the recognition
effect end-to-end through the real entity stage, the REST CRUD + its status codes, and the
provenance digest that flags a customized vocabulary.
"""

import pytest
from fastapi.testclient import TestClient

from mehungry_extractor.knowledge import entities, vocab
from mehungry_extractor.knowledge.api.app import app, get_context
from mehungry_extractor.knowledge.canonical import DocumentMetadata, ParsedSection, build_document
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, init_db
from mehungry_extractor.knowledge.normalize import STATUS_NORMALIZED, normalize


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """Point the vocabulary overlay at an isolated temp file; leave the vocabulary pristine after."""
    path = tmp_path / "vocab_overlay.json"
    monkeypatch.setenv("MEHUNGRY_VOCAB_OVERLAY", str(path))
    entities.reload_vocabulary()  # start from a pristine (absent) overlay under the new path
    yield path
    # Restore a pristine vocabulary for other tests regardless of what this one wrote.
    monkeypatch.setenv("MEHUNGRY_VOCAB_OVERLAY", str(tmp_path / "gone.json"))
    entities.reload_vocabulary()


_NEW = {
    "concept_id": "NUTR:unobtainium",
    "canonical_name": "Unobtainium",
    "entity_type": "nutrient",
    "surface_forms": ["unobtainium", "wonder nutrient"],
}


# --- overlay data layer -------------------------------------------------------------


def test_add_concept_is_recognised_and_tagged_custom(overlay):
    assert normalize("unobtainium").concept is None  # unknown before
    vocab.upsert_concept(_NEW)
    entities.reload_vocabulary()

    n = normalize("wonder nutrient")
    assert n.status == STATUS_NORMALIZED and n.concept.concept_id == "NUTR:unobtainium"
    assert vocab.concept_origin("NUTR:unobtainium") == "custom"
    assert overlay.exists()  # persisted


def test_edit_builtin_surface_forms_is_overridden(overlay):
    rec = dict(next(c for c in vocab.load_concepts() if c["concept_id"] == "NUTR:dietary_fiber"))
    rec["surface_forms"] = rec["surface_forms"] + ["wonder fibre"]
    vocab.upsert_concept(rec)
    entities.reload_vocabulary()

    assert vocab.concept_origin("NUTR:dietary_fiber") == "overridden"
    assert normalize("wonder fibre").concept.concept_id == "NUTR:dietary_fiber"
    assert normalize("dietary fiber").concept.concept_id == "NUTR:dietary_fiber"  # original kept


def test_remove_builtin_hides_it_without_touching_the_bundled_file(overlay):
    before = len(vocab.builtin_concepts())
    assert vocab.delete_concept("NUTR:dietary_fiber") is True
    entities.reload_vocabulary()

    assert normalize("dietary fiber").concept is None
    assert not any(c["concept_id"] == "NUTR:dietary_fiber" for c in vocab.load_concepts())
    # The checked-in vocabulary is untouched — only the overlay hides it.
    assert len(vocab.builtin_concepts()) == before
    assert any(c["concept_id"] == "NUTR:dietary_fiber" for c in vocab.builtin_concepts())


def test_overlay_digest_none_when_pristine_then_set(overlay):
    assert vocab.overlay_digest() is None
    vocab.upsert_concept(_NEW)
    assert vocab.overlay_digest() is not None


# --- REST CRUD ----------------------------------------------------------------------


def test_vocab_crud_over_http(overlay):
    client = TestClient(app)

    body = client.get("/vocab/concepts").json()
    baseline = len(body["concepts"])
    assert body["overlay_digest"] is None
    assert all("origin" in c for c in body["concepts"])

    # add
    r = client.post("/vocab/concepts", json=_NEW)
    assert r.status_code == 201
    body = r.json()
    assert body["overlay_digest"] is not None
    assert len(body["concepts"]) == baseline + 1

    # duplicate add -> 409
    assert client.post("/vocab/concepts", json=_NEW).status_code == 409

    # edit surface forms via PUT
    edited = {**_NEW, "surface_forms": ["unobtainium", "wonder-nutrient"]}
    r = client.put("/vocab/concepts/NUTR:unobtainium", json=edited)
    assert r.status_code == 200
    rec = next(c for c in r.json()["concepts"] if c["concept_id"] == "NUTR:unobtainium")
    assert rec["surface_forms"] == ["unobtainium", "wonder-nutrient"]

    # PUT with mismatched path/body id -> 422
    assert client.put("/vocab/concepts/NUTR:unobtainium", json={**_NEW, "concept_id": "NUTR:other"}).status_code == 422

    # delete -> gone; deleting again -> 404
    assert client.delete("/vocab/concepts/NUTR:unobtainium").status_code == 200
    assert client.delete("/vocab/concepts/NUTR:unobtainium").status_code == 404

    # delete unknown -> 404; malformed id on add -> 422
    assert client.delete("/vocab/concepts/NUTR:nope").status_code == 404
    assert client.post("/vocab/concepts", json={**_NEW, "concept_id": "bad id"}).status_code == 422


# --- recognition through the real entity stage + provenance -------------------------


def test_added_concept_is_recognised_by_the_extraction_pipeline(overlay, tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    meta = DocumentMetadata(pmid="70000001", source_type="abstract", title="Dietary intake of unobtainium")
    store.save_canonical(
        build_document(meta, [ParsedSection(title="Body", paragraphs=["Patients consumed unobtainium daily."])])
    )
    app.dependency_overrides[get_context] = lambda: {"corpus": store, "engine": engine, "ingest": False}
    try:
        client = TestClient(app)
        # Not recognised before the concept exists.
        before = client.post("/observe/analyze", json={"pmid": "70000001", "use_model": False}).json()
        assert not any(e["concept_id"] == "NUTR:unobtainium" for e in before["entities"])

        # Add it, then the SAME entity stage recognises it (caches were invalidated).
        assert client.post("/vocab/concepts", json=_NEW).status_code == 201
        after = client.post("/observe/analyze", json={"pmid": "70000001", "use_model": False}).json()
        assert any(
            e["concept_id"] == "NUTR:unobtainium" and e["status"] == "normalized"
            for e in after["entities"]
        )
    finally:
        app.dependency_overrides.clear()


def test_analyze_run_records_overlay_digest_when_customized(overlay, tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    meta = DocumentMetadata(pmid="70000002", source_type="abstract", title="Dietary fiber and remission")
    store.save_canonical(
        build_document(meta, [ParsedSection(title="Body", paragraphs=["Dietary fiber improved remission."])])
    )
    app.dependency_overrides[get_context] = lambda: {"corpus": store, "engine": engine, "ingest": False}
    try:
        client = TestClient(app)
        pristine = client.post("/analyze", json={"pmids": ["70000002"], "options": {"use_model": False}}).json()
        assert "mehungry_curated_overlay" not in pristine["run"]["ontology_versions"]

        client.post("/vocab/concepts", json=_NEW)
        customized = client.post("/analyze", json={"pmids": ["70000002"], "options": {"use_model": False}}).json()
        assert customized["run"]["ontology_versions"]["mehungry_curated_overlay"] == vocab.overlay_digest()
    finally:
        app.dependency_overrides.clear()
