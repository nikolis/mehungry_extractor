"""Deterministic cross-paper synthesis (knowledge/synthesis.py).

Offline: canonical documents are built from synthetic sentences over the curated vocabulary
(as in test_relations_claims), extracted through the real pipeline, then synthesized. No network,
no model.
"""

from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, init_db
from mehungry_extractor.knowledge.ids import document_id
from mehungry_extractor.knowledge.pipeline import extract_document
from mehungry_extractor.knowledge.synthesis import (
    CONFLICTING,
    SUPPORTED,
    synthesize,
)


def _seed(tmp_path, sentences: dict[str, str]):
    """Seed a corpus with one abstract-only canonical doc per PMID and extract each."""
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    for pmid, sentence in sentences.items():
        meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=f"Paper {pmid}")
        doc = build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])
        store.save_canonical(doc)
        extract_document(pmid, corpus=store, engine=engine, persist=True, use_model=False)
    return store, engine


def test_agreeing_papers_form_a_supported_conclusion(tmp_path):
    _, engine = _seed(
        tmp_path,
        {
            "10000001": "Dietary fiber improved remission.",
            "10000002": "Dietary fiber improved remission.",
        },
    )
    docs = [document_id("10000001"), document_id("10000002")]
    result = synthesize(engine, docs)
    assert len(result.conclusions) == 1
    c = result.conclusions[0]
    assert (c.subject_concept_id, c.predicate, c.object_concept_id) == (
        "NUTR:dietary_fiber", "improves", "OUT:remission",
    )
    assert c.direction == SUPPORTED
    assert c.paper_count == 2
    assert c.supporting_papers == docs
    assert not c.contradicting_papers


def test_disagreeing_papers_form_a_conflicting_conclusion(tmp_path):
    _, engine = _seed(
        tmp_path,
        {
            "10000001": "Dietary fiber improved remission.",
            "10000002": "Dietary fiber improved remission.",
            "10000003": "Dietary fiber did not improve remission.",
        },
    )
    docs = [document_id(p) for p in ("10000001", "10000002", "10000003")]
    result = synthesize(engine, docs)
    assert len(result.conclusions) == 1
    c = result.conclusions[0]
    assert c.direction == CONFLICTING
    assert c.paper_count == 3
    assert c.supporting_papers == [document_id("10000001"), document_id("10000002")]
    assert c.contradicting_papers == [document_id("10000003")]


def test_evidence_reconstructs_from_offsets(tmp_path):
    _, engine = _seed(
        tmp_path,
        {
            "10000001": "Dietary fiber improved remission.",
            "10000003": "Dietary fiber did not improve remission.",
        },
    )
    docs = [document_id("10000001"), document_id("10000003")]
    c = synthesize(engine, docs).conclusions[0]
    assert c.direction == CONFLICTING
    # One quote per direction; each is a real span with rule provenance.
    quotes = {e.document_id: e.quoted_text for e in c.evidence}
    assert quotes[document_id("10000001")] == "Dietary fiber improved remission."
    assert quotes[document_id("10000003")] == "Dietary fiber did not improve remission."
    assert all(e.extraction_rule and e.extraction_rule_version for e in c.evidence)


def test_batch_facts_rollup(tmp_path):
    _, engine = _seed(
        tmp_path,
        {"10000001": "Dietary fiber improved remission."},
    )
    facts = synthesize(engine, [document_id("10000001")]).facts
    assert facts.paper_count == 1
    assert facts.source_types == {"abstract": 1}
    assert facts.study_designs == {"unknown": 1}  # no publication types → unknown, not a guess


def test_deterministic(tmp_path):
    _, engine = _seed(
        tmp_path,
        {
            "10000001": "Dietary fiber improved remission.",
            "10000003": "Dietary fiber did not improve remission.",
        },
    )
    docs = [document_id("10000001"), document_id("10000003")]

    def snap():
        return [
            (
                c.subject_concept_id, c.predicate, c.object_concept_id, c.direction,
                tuple(c.supporting_papers), tuple(c.contradicting_papers),
                tuple((e.document_id, e.quoted_text) for e in c.evidence),
            )
            for c in synthesize(engine, docs).conclusions
        ]

    assert snap() == snap()
