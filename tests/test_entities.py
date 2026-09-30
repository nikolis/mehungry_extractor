"""Phase 2 gate: deterministic entity extraction + normalization (spec §6).

All offline — the dictionary path must produce deterministic results with no network and no
scispaCy model installed.
"""

from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, session_scope
from mehungry_extractor.knowledge.db.schema import EntityMentionRow, EntityRow
from mehungry_extractor.knowledge.entities import analyze_document, extract
from mehungry_extractor.knowledge.ingest import assemble_document, normalize_document
from mehungry_extractor.knowledge.normalize import (
    STATUS_NORMALIZED,
    STATUS_UNMATCHED,
    normalize,
)
from mehungry_extractor.knowledge.provenance import ProvenancePrecision
from mehungry_extractor.knowledge.query import (
    find_mentions,
    list_entities_for_document,
)


def _doc(*paragraphs: str):
    """Build a canonical document from raw paragraphs in a single section."""
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=list(paragraphs))])


# --- extraction invariants ----------------------------------------------------------


def test_offset_reconstruction(pubmed_xml, pmc_xml):
    """Every mention's span slices back to its surface text (the provenance invariant)."""
    doc, _ = assemble_document("12345678", pubmed_xml, pmc_xml)
    mentions = extract(doc)
    assert mentions
    for m in mentions:
        assert doc.text[m.start_char : m.end_char] == m.surface_text
        assert m.evidence_ref.precision == ProvenancePrecision.EXACT_SPAN
        assert m.evidence_ref.quoted_text == m.surface_text
        # The evidence ref resolves to the owning sentence.
        assert m.evidence_ref.sentence_id == m.sentence_id


def test_dictionary_hits_produce_expected_types():
    doc = _doc("Whole grains and dietary fiber were compared in ulcerative colitis.")
    by_surface = {m.surface_text.lower(): m for m in extract(doc)}
    assert by_surface["whole grains"].entity_type == "food"
    assert by_surface["dietary fiber"].entity_type == "nutrient"
    assert by_surface["ulcerative colitis"].entity_type == "disease"
    assert all(m.status == STATUS_NORMALIZED for m in by_surface.values())


def test_longest_match_wins():
    """`dietary fiber` is matched as one nutrient, not a bare `fiber` inside it."""
    doc = _doc("We measured dietary fiber intake.")
    fiber = [m for m in extract(doc) if "fiber" in m.surface_text.lower()]
    assert len(fiber) == 1
    assert fiber[0].surface_text == "dietary fiber"
    assert fiber[0].concept_id == "NUTR:dietary_fiber"


def test_unmatched_surface_is_kept_not_invented():
    """A surface with no vocabulary entry never fabricates a concept."""
    n = normalize("quinoa flavonoids")
    assert n.status == STATUS_UNMATCHED
    assert n.concept is None


def test_normalization_maps_known_surfaces():
    n = normalize("Crohn's disease")
    assert n.status == STATUS_NORMALIZED
    assert n.concept is not None
    assert n.concept.concept_id == "DIS:crohn_disease"
    assert n.concept.canonical_name == "Crohn disease"


def test_determinism_two_runs_identical(pubmed_xml, pmc_xml):
    doc, _ = assemble_document("12345678", pubmed_xml, pmc_xml)
    first = [m.model_dump() for m in extract(doc)]
    second = [m.model_dump() for m in extract(doc)]
    assert first == second


def test_mentions_are_ordered_by_span(pubmed_xml, pmc_xml):
    doc, _ = assemble_document("12345678", pubmed_xml, pmc_xml)
    mentions = extract(doc)
    keys = [(m.start_char, m.end_char, m.entity_type) for m in mentions]
    assert keys == sorted(keys)


# --- persistence + query ------------------------------------------------------------


def _seed(tmp_path, pubmed_xml, pmc_xml):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": pubmed_xml, "pmc.xml": pmc_xml})
    engine = get_engine(tmp_path / "db.sqlite")
    normalize_document("12345678", corpus=store, engine=engine)
    return store, engine


def test_analyze_persists_and_queries(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    doc, mentions = analyze_document("12345678", corpus=store, engine=engine)

    rows = list_entities_for_document(engine, "12345678")
    assert len(rows) == len(mentions)
    # Persisted spans reconstruct from the canonical text (bidirectional trace).
    for r in rows:
        assert doc.text[r.start_char : r.end_char] == r.surface_text

    # Distinct normalized concepts are recorded once in `entities`.
    with session_scope(engine) as session:
        concept_ids = {c.concept_id for c in session.query(EntityRow).all()}
    normalized = {m.concept_id for m in mentions if m.concept_id}
    assert normalized <= concept_ids

    crohn = find_mentions(engine, concept_id="DIS:crohn_disease")
    assert crohn and all(r.concept_id == "DIS:crohn_disease" for r in crohn)


def test_analyze_is_idempotent(tmp_path, pubmed_xml, pmc_xml):
    """Re-running analyze replaces mentions rather than duplicating them."""
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    analyze_document("12345678", corpus=store, engine=engine)
    first = {r.mention_id for r in list_entities_for_document(engine, "12345678")}
    analyze_document("12345678", corpus=store, engine=engine)
    second = {r.mention_id for r in list_entities_for_document(engine, "12345678")}
    assert first == second


def test_reanalyze_survives_document_renormalize(tmp_path, pubmed_xml, pmc_xml):
    """A re-ingest (delete-then-insert of sentences) must not break with mentions present."""
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    analyze_document("12345678", corpus=store, engine=engine)
    # Re-normalize deletes+reinserts the sentence rows; mentions must not block it.
    normalize_document("12345678", corpus=store, engine=engine)
    rows = list_entities_for_document(engine, "12345678")
    assert rows


def test_run_records_vocab_version(tmp_path, pubmed_xml, pmc_xml):
    from mehungry_extractor.knowledge.db.schema import ExtractionRunRow
    from mehungry_extractor.knowledge.vocab import VOCAB_VERSION

    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    analyze_document("12345678", corpus=store, engine=engine)
    with session_scope(engine) as session:
        runs = session.query(ExtractionRunRow).all()
    assert any(r.ontology_versions.get("mehungry_curated") == VOCAB_VERSION for r in runs)
