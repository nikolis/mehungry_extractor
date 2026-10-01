"""Open relation discovery (Phase 11 — relation-bearing spans, for human review).

All deterministic and offline: a fake :class:`RelationBearingDetector` returns fixed spans, so the
plumbing — EXACT_SPAN anchoring, idempotent persistence, query ordering, and API serialization — is
exercised without loading a real model. A model-gated integration test (skipped when the optional
``[openrel]`` extra is absent) runs the real detector over a fixture paper.
"""

import pytest

from mehungry_extractor.knowledge import openrel
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, init_db
from mehungry_extractor.knowledge.openrel import DetectedSpan, extract_open_observations
from mehungry_extractor.knowledge.pipeline import discover_open_relations
from mehungry_extractor.knowledge.query import list_open_observations_for_document

# A paper with two relation-bearing sentences and one neutral one.
PMID = "20000001"
PARAGRAPHS = [
    "Dietary fiber reduces inflammation markers.",  # relational
    "The samples were stored at room temperature.",  # neutral
    "Vitamin D supports bone mineral density.",  # relational
]


class FakeDetector:
    """Fires on sentences containing a relational cue; assigns a fixed per-cue score. No model."""

    name = "fake"
    version = "1.0"

    _SCORES = {"reduces": 0.9, "supports": 0.7}

    def detect(self, document):
        spans = []
        for sent in document.iter_sentences():
            for cue, score in self._SCORES.items():
                if cue in sent.text:
                    spans.append(
                        DetectedSpan(
                            sentence_id=sent.sentence_id,
                            start_char=sent.start_char,
                            end_char=sent.end_char,
                            score=score,
                        )
                    )
                    break
        return spans


def _document(pmid=PMID):
    # Title names a keyword so the paper clears the topical barrier the /discover path enforces.
    meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=f"Dietary fiber and inflammation (paper {pmid})")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=PARAGRAPHS)])


def test_extract_anchors_exact_spans():
    doc = _document()
    obs = extract_open_observations(doc, detector=FakeDetector())

    assert len(obs) == 2
    for o in obs:
        # EXACT_SPAN by construction: the recorded text is exactly the source slice.
        assert o.text == doc.text[o.start_char : o.end_char]
        assert o.detector_name == "fake" and o.detector_version == "1.0"
        # A single EXACT_SPAN ref whose quoted text also reconstructs.
        assert len(o.evidence_refs) == 1
        ref = o.evidence_refs[0]
        assert ref.precision.value == "EXACT_SPAN"
        assert ref.quoted_text == doc.text[o.start_char : o.end_char]
        # No structure was imposed — the model is only a detector.
        assert not hasattr(o, "predicate")

    # Most-confident first: the "reduces" sentence (0.9) precedes the "supports" one (0.7).
    assert [round(o.score, 3) for o in obs] == [0.9, 0.7]
    assert obs[0].text == "Dietary fiber reduces inflammation markers."


def test_ids_are_deterministic_and_detector_scoped():
    doc = _document()
    a = extract_open_observations(doc, detector=FakeDetector())
    b = extract_open_observations(doc, detector=FakeDetector())
    assert [o.open_observation_id for o in a] == [o.open_observation_id for o in b]

    class OtherDetector(FakeDetector):
        name = "other"

    other = extract_open_observations(doc, detector=OtherDetector())
    # Same spans, different detector name → different ids (attributable to the detector).
    assert {o.open_observation_id for o in a}.isdisjoint({o.open_observation_id for o in other})


def test_persistence_is_idempotent(tmp_path):
    corpus = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    corpus.save_canonical(_document())

    def run():
        discover_open_relations(PMID, corpus=corpus, engine=engine, persist=True, detector=FakeDetector())
        return list_open_observations_for_document(engine, PMID)

    first = run()
    second = run()
    # Re-running the same detector replaces (does not duplicate) the document's spans.
    assert len(first) == 2
    assert [o["open_observation_id"] for o in first] == [o["open_observation_id"] for o in second]


def test_query_orders_by_score_desc(tmp_path):
    corpus = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    corpus.save_canonical(_document())

    discover_open_relations(PMID, corpus=corpus, engine=engine, persist=True, detector=FakeDetector())
    rows = list_open_observations_for_document(engine, PMID)
    assert [r["score"] for r in rows] == [0.9, 0.7]
    assert rows[0]["text"] == "Dietary fiber reduces inflammation markers."
    # Read side carries the verbatim text + exact span + a single EXACT_SPAN ref.
    assert rows[0]["evidence_refs"][0]["precision"] == "EXACT_SPAN"


def test_service_serialization(tmp_path):
    pytest.importorskip("fastapi")  # OpenRelationResponse is pydantic, but keep parity with test_api
    from mehungry_extractor.knowledge.api import service

    corpus = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    corpus.save_canonical(_document())

    resp = service.discover_relations_batch(
        [PMID], corpus=corpus, engine=engine, ingest=False, extract=True, detector=FakeDetector()
    )
    assert [p.pmid for p in resp.papers] == [PMID]
    paper = resp.papers[0]
    assert paper.paper_title == f"Dietary fiber and inflammation (paper {PMID})"
    assert paper.paper_url == f"https://pubmed.ncbi.nlm.nih.gov/{PMID}/"
    assert [round(r.score, 3) for r in paper.relations] == [0.9, 0.7]
    assert paper.relations[0].text == "Dietary fiber reduces inflammation markers."
    # The detector's identity is recorded on the run (reproducibility).
    assert resp.run.ontology_versions.get("fake") == "1.0"

    # GET path returns the already-persisted spans without recomputing.
    single = service.get_open_relations(PMID, engine=engine)
    assert [r.open_observation_id for r in single.relations] == [
        r.open_observation_id for r in paper.relations
    ]


@pytest.mark.skipif(not openrel.available(), reason="requires the optional [openrel] detector")
def test_real_detector_smoke(tmp_path):
    """Model-gated: the real default detector runs over a document and yields well-formed spans."""
    doc = _document("20000002")
    obs = extract_open_observations(doc)  # default detector
    for o in obs:
        assert o.text == doc.text[o.start_char : o.end_char]
        assert 0.0 <= o.score <= 1.0
        assert o.detector_name == "rebel"
