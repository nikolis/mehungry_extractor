"""Phase 5 gate: assessment frameworks, filtering, provenance round-trip, audit (spec §11-§16).

Deterministic and offline. Assessments derive from Phase-4 facts and never mutate evidence;
``explain_claim``/``audit`` reconstruct every quoted phrase from the stored offsets.
"""

from mehungry_extractor.knowledge.assessment import PaperFacts, assess
from mehungry_extractor.knowledge.audit import render_claim
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, session_scope
from mehungry_extractor.knowledge.db.schema import (
    AssessmentRow,
    ClaimEvidenceRow,
    ClaimRow,
)
from mehungry_extractor.knowledge.ingest import normalize_document
from mehungry_extractor.knowledge.pipeline import extract_document
from mehungry_extractor.knowledge.query import (
    explain_claim,
    find_papers,
    list_claims_for_document,
)


# Run on the deterministic ``use_model=False`` floor (see note in test_relations_claims): the
# audit/assessment/persistence machinery under test is binder-independent, and the floor keeps the
# fiber→remission claim these round-trips render against. The Phase-10 parse binder is covered by
# its own gate tests in test_relations_claims.
def _seed(tmp_path, pubmed_xml, pmc_xml):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": pubmed_xml, "pmc.xml": pmc_xml})
    engine = get_engine(tmp_path / "db.sqlite")
    normalize_document("12345678", corpus=store, engine=engine)
    return store, engine


# --- the framework is a pure function of facts --------------------------------------


def test_framework_maps_facts_to_values():
    facts = PaperFacts(
        document_id="pmid_1", study_design="randomized_controlled_trial",
        publication_year=2020, sample_size=412, industry_funding=False, funder_types=["government"],
    )
    values = {a.criterion: a.value for a in assess(facts)}
    assert values["study_design_strength"] == "high"
    assert values["recency"] == "recent"
    assert values["sample_size_adequacy"] == "adequate"
    assert values["funding_independence"] == "independent"


def test_unknown_funding_is_not_independent():
    facts = PaperFacts(document_id="pmid_1", industry_funding=None)
    values = {a.criterion: a.value for a in assess(facts)}
    assert values["funding_independence"] == "unknown"  # absence never assumed independent


def test_industry_funding_flagged():
    facts = PaperFacts(document_id="pmid_1", industry_funding=True, funder_types=["pharmaceutical"])
    values = {a.criterion: a.value for a in assess(facts)}
    assert values["funding_independence"] == "industry_funded"


# --- separation: re-applying the framework never mutates evidence -------------------


def _snapshot(engine):
    with session_scope(engine) as session:
        claims = sorted(
            (c.claim_id, c.subject_concept_id, c.predicate, c.object_concept_id, c.polarity, c.certainty)
            for c in session.query(ClaimRow).all()
        )
        evidence = sorted(
            (e.claim_id, e.sentence_id, e.start_char, e.end_char, e.quoted_text, e.extraction_rule)
            for e in session.query(ClaimEvidenceRow).all()
        )
        assessments = sorted(
            (a.framework_id, a.criterion, a.value, a.rationale) for a in session.query(AssessmentRow).all()
        )
    return claims, evidence, assessments


def test_rerun_is_byte_identical_and_evidence_untouched(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    before = _snapshot(engine)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    after = _snapshot(engine)
    assert before == after
    # And there is real content to compare (not vacuously equal).
    assert before[0] and before[2]


# --- filtering ----------------------------------------------------------------------


def test_find_papers_by_design_and_year(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)

    assert find_papers(engine, study_design="randomized_controlled_trial")
    assert find_papers(engine, publication_year_from=2005)
    # Year filter excludes when the paper is too old.
    assert find_papers(engine, publication_year_from=2020) == []
    # Unknown funding is never "independent" (fixture has no funding statement).
    assert find_papers(engine, require_independent_funding=True) == []


# --- provenance round-trip + audit --------------------------------------------------


def test_explain_claim_round_trip(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    claim_id = list_claims_for_document(engine, "12345678")[0]["claim_id"]

    data = explain_claim(engine, claim_id)
    assert data is not None
    # Every located evidence phrase reconstructs from the canonical text.
    assert data["evidence"]
    for ev in data["evidence"]:
        if ev["reconstructed_text"] is not None:
            assert ev["reconstructed_text"] == ev["quoted_text"]
    # The paper hash is carried through from checksums.json.
    assert data["document"]["checksums"]
    # The run stamps the reproducibility versions.
    assert data["run"]["pipeline_version"] and data["run"]["ruleset_version"]
    assert data["study"]["study_design"]["value"] == "randomized_controlled_trial"


def test_audit_block_renders_expected_sections(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    claim_id = list_claims_for_document(engine, "12345678")[0]["claim_id"]

    block = render_claim(engine, claim_id)
    assert block is not None
    for header in ("CLAIM", "SOURCE", "STUDY", "EVIDENCE", "MATCH", "ASSESSMENT", "DOCUMENT HASH", "EXTRACTION RUN"):
        assert header in block, header
    assert claim_id in block
    # The exact source sentence appears verbatim.
    assert "Dietary fiber was associated with reduced disease activity during remission." in block
    # Study design and pipeline version are shown.
    assert "randomized_controlled_trial" in block
    assert "PMID 12345678" in block


def test_audit_unknown_claim_returns_none(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    assert render_claim(engine, "pmid_12345678_claim_deadbeef") is None
