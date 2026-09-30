"""Phase 3 gate: relations → observations → claims, with negation + uncertainty (spec §7-§8).

All offline and deterministic — synthetic sentences over the curated vocabulary plus the shared
fixture. No network, no model required.
"""

import pytest

from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.claims import Claim, normalize
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.enums import Certainty, Polarity
from mehungry_extractor.knowledge.ingest import assemble_document, normalize_document
from mehungry_extractor.knowledge.observations import Observation, extract as extract_observations
from mehungry_extractor.knowledge.pipeline import extract_document
from mehungry_extractor.knowledge.provenance import EvidenceRef, ProvenancePrecision
from mehungry_extractor.knowledge.query import (
    find_claims,
    find_evidence,
    list_claims_for_document,
)


def _doc(sentence: str):
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=[sentence])])


def _one_obs(sentence: str) -> Observation:
    doc = _doc(sentence)
    obs = extract_observations(doc, extract_entities(doc))
    assert len(obs) == 1, f"expected exactly one observation for {sentence!r}, got {obs}"
    return obs[0]


# --- relations + negation + uncertainty ---------------------------------------------


def test_positive_asserted_association():
    o = _one_obs("Dietary fiber was associated with remission.")
    assert (o.subject_concept_id, o.object_concept_id) == ("NUTR:dietary_fiber", "OUT:remission")
    assert o.predicate == "associated_with"
    assert o.polarity == Polarity.POSITIVE.value
    assert o.certainty == Certainty.ASSERTED.value


def test_explicit_no_association_is_negative():
    o = _one_obs("Dietary fiber had no association with remission.")
    assert o.predicate == "no_association"
    assert o.polarity == Polarity.NEGATIVE.value


def test_negation_flips_polarity_on_directional_verb():
    o = _one_obs("Dietary fiber did not improve remission.")
    assert o.predicate == "improves"
    assert o.polarity == Polarity.NEGATIVE.value
    assert o.certainty == Certainty.ASSERTED.value
    assert o.context and "not" in o.context.lower()


def test_uncertainty_marks_possible():
    o = _one_obs("Dietary fiber may be associated with remission.")
    assert o.predicate == "associated_with"
    assert o.polarity == Polarity.POSITIVE.value
    assert o.certainty == Certainty.POSSIBLE.value


def test_insufficient_evidence_is_neutral():
    o = _one_obs(
        "There was insufficient evidence that dietary fiber is associated with remission."
    )
    assert o.polarity == Polarity.NEUTRAL.value
    assert o.certainty == Certainty.INSUFFICIENT_EVIDENCE.value


def test_hypothetical_certainty():
    o = _one_obs("Dietary fiber was hypothesized to be associated with remission.")
    assert o.certainty == Certainty.HYPOTHETICAL.value


def test_same_endpoints_different_modality():
    """The four modalities differ only in polarity/certainty on identical subject/object."""
    pos = _one_obs("Dietary fiber was associated with remission.")
    poss = _one_obs("Dietary fiber may be associated with remission.")
    assert (pos.subject_concept_id, pos.object_concept_id) == (
        poss.subject_concept_id,
        poss.object_concept_id,
    )
    assert pos.certainty != poss.certainty


# --- evidence -----------------------------------------------------------------------


def test_every_observation_has_reconstructable_evidence():
    doc = _doc("Dietary fiber was associated with remission.")
    for o in extract_observations(doc, extract_entities(doc)):
        assert o.evidence_refs
        for ref in o.evidence_refs:
            assert doc.text[ref.start_char : ref.end_char] == ref.quoted_text
        assert o.rule_id


# --- claim normalization (pure function, incl. the drop path) -----------------------


def _obs(subject_cid, object_cid, predicate="associated_with"):
    ev = EvidenceRef(
        document_id="pmid_1", sentence_id="pmid_1_sec000_p000_s00",
        start_char=0, end_char=5, quoted_text="fiber",
        precision=ProvenancePrecision.SENTENCE, extraction_rule="rel_associated_with",
        extraction_rule_version="0.4.0",
    )
    return Observation(
        observation_id=f"pmid_1_obs_{subject_cid}_{object_cid}",
        document_id="pmid_1", sentence_id="pmid_1_sec000_p000_s00",
        subject_mention_id="m1", subject_text="fiber", subject_concept_id=subject_cid,
        predicate=predicate,
        object_mention_id="m2", object_text="remission", object_concept_id=object_cid,
        context=None, polarity=Polarity.POSITIVE.value, certainty=Certainty.ASSERTED.value,
        rule_id="rel_associated_with", rule_version="0.4.0", evidence_refs=[ev],
    )


def test_claim_forms_only_when_both_endpoints_normalize():
    observations = [
        _obs("NUTR:dietary_fiber", "OUT:remission"),
        _obs("NUTR:dietary_fiber", None),  # object didn't normalize → dropped, not a claim
    ]
    result = normalize(observations, concept_name=lambda cid: cid)
    assert result.dropped == 1
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert isinstance(claim, Claim)
    assert claim.subject_concept_id == "NUTR:dietary_fiber"
    assert claim.object_concept_id == "OUT:remission"


def test_claim_evidence_is_union_of_observations():
    observations = [_obs("NUTR:dietary_fiber", "OUT:remission")]
    result = normalize(observations, concept_name=lambda cid: cid)
    claim = result.claims[0]
    assert claim.evidence_refs
    assert claim.observation_ids == [observations[0].observation_id]


# --- determinism + persistence + query ----------------------------------------------


def test_determinism_two_runs_identical():
    doc = _doc("Dietary fiber was associated with remission and improved abdominal pain.")
    mentions = extract_entities(doc)
    first = [o.model_dump() for o in extract_observations(doc, mentions)]
    second = [o.model_dump() for o in extract_observations(doc, mentions)]
    assert first == second


# These integration tests exercise persistence/query/audit machinery, which is binder-independent.
# They run on the deterministic ``use_model=False`` floor: Phase 10's parse binder reads the
# fixture's "…associated with reduced disease activity during remission" as an association to the
# (unnormalized) disease-activity object with remission as a disease_state qualifier — a *more*
# correct reading that yields no normalized claim for this sentence — whereas the flat floor still
# binds the fiber→remission claim these tests assert against.
def _seed(tmp_path, pubmed_xml, pmc_xml):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": pubmed_xml, "pmc.xml": pmc_xml})
    engine = get_engine(tmp_path / "db.sqlite")
    normalize_document("12345678", corpus=store, engine=engine)
    return store, engine


def test_extract_persists_claims_and_find_claims(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)

    claims = list_claims_for_document(engine, "12345678")
    assert claims
    # The fixture's Results sentence links dietary fiber and remission.
    hit = [c for c in claims if c["subject_concept_id"] == "NUTR:dietary_fiber"
           and c["object_concept_id"] == "OUT:remission"]
    assert hit, claims

    # find_claims filters deterministically and accepts id or canonical name.
    by_id = find_claims(engine, subject="NUTR:dietary_fiber")
    by_name = find_claims(engine, subject="Dietary fiber")
    assert by_id == by_name and by_id

    # Evidence reconstructs from offsets.
    evidence = find_evidence(engine, hit[0]["claim_id"])
    assert evidence
    for ev in evidence:
        if ev["reconstructed_text"] is not None:
            assert ev["reconstructed_text"] == ev["quoted_text"]


def test_extract_is_idempotent(tmp_path, pubmed_xml, pmc_xml):
    store, engine = _seed(tmp_path, pubmed_xml, pmc_xml)
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    first = {c["claim_id"] for c in list_claims_for_document(engine, "12345678")}
    extract_document("12345678", corpus=store, engine=engine, use_model=False)
    second = {c["claim_id"] for c in list_claims_for_document(engine, "12345678")}
    assert first == second
