"""Phase 6 gate: typed qualifiers (the context model) on relations (docs/phases/phase-6-*).

All offline and deterministic — synthetic sentences over the curated vocabulary + disease-state
cues. No network, no model. Covers: distinction (remission vs active disease do NOT merge),
backward compatibility (no cue → empty signature → byte-identical Phase-5 claim id), provenance
(every qualifier reconstructs from offsets), the unmatched-cue path, conditional cross-paper
synthesis, and determinism.
"""

import hashlib

from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.claims import normalize
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine, init_db
from mehungry_extractor.knowledge.entities import extract as extract_entities
from mehungry_extractor.knowledge.ids import document_id
from mehungry_extractor.knowledge.observations import extract as extract_observations
from mehungry_extractor.knowledge.pipeline import extract_document
from mehungry_extractor.knowledge.qualifiers import (
    QualifierType,
    extract as extract_qualifiers,
    signature,
)
from mehungry_extractor.knowledge.query import (
    explain_claim,
    find_claims,
    list_claims_for_document,
)
from mehungry_extractor.knowledge.synthesis import synthesize


def _doc(*sentences: str):
    meta = DocumentMetadata(pmid="99999999", source_type="abstract")
    return build_document(meta, [ParsedSection(title="Body", paragraphs=list(sentences))])


def _claims(*sentences: str):
    doc = _doc(*sentences)
    obs = extract_observations(doc, extract_entities(doc, use_model=False))
    return doc, obs, normalize(obs, concept_name=lambda cid: cid).claims


# --- extractor unit behavior --------------------------------------------------------


def test_cue_normalizes_to_disease_state_concept():
    doc = _doc("Dietary fiber improved remission during remission.")
    sent = next(doc.iter_sentences())
    quals = extract_qualifiers(doc, sent)
    assert len(quals) == 1
    q = quals[0]
    assert q.qualifier_type == QualifierType.DISEASE_STATE.value
    assert q.value_concept_id == "DS:remission"
    assert q.value_text == "remission"
    # A qualifier is a condition, not an assertion — it has no polarity field.
    assert not hasattr(q, "polarity")


def test_unrecognized_cue_kept_unmatched_with_surface():
    doc = _doc("Dietary fiber improved remission during severe flare.")
    sent = next(doc.iter_sentences())
    quals = extract_qualifiers(doc, sent)
    assert len(quals) == 1
    assert quals[0].value_concept_id is None  # "severe flare" isn't in the vocab
    assert quals[0].value_text == "severe flare"  # kept, never dropped


def test_with_connective_is_not_a_disease_state_cue():
    """'associated **with** remission' — the noun is the object, not a condition."""
    doc = _doc("Dietary fiber was associated with remission.")
    sent = next(doc.iter_sentences())
    assert extract_qualifiers(doc, sent) == []


# --- distinction: differing conditions must NOT merge -------------------------------


def test_distinct_disease_states_do_not_merge():
    _, _, claims = _claims(
        "Dietary fiber improved remission during remission.",
        "Dietary fiber improved remission during active disease.",
    )
    # Same (subject, predicate, object, polarity, certainty) — split only by disease state.
    hits = [
        c for c in claims
        if (c.subject_concept_id, c.predicate, c.object_concept_id)
        == ("NUTR:dietary_fiber", "improves", "OUT:remission")
    ]
    assert len(hits) == 2, hits
    states = {q.value_concept_id for c in hits for q in c.qualifiers}
    assert states == {"DS:remission", "DS:active_disease"}
    assert hits[0].claim_id != hits[1].claim_id


def test_unmatched_cue_still_keys_the_claim_distinctly():
    _, _, claims = _claims(
        "Dietary fiber improved remission during remission.",
        "Dietary fiber improved remission during severe flare.",
    )
    hits = [c for c in claims if c.predicate == "improves"
            and c.object_concept_id == "OUT:remission"]
    assert len(hits) == 2  # "severe flare" (unmatched) is a distinct condition from remission
    sigs = {signature(c.qualifiers) for c in hits}
    assert sigs == {"disease_state=DS:remission", "disease_state=severe flare"}


# --- backward compatibility: no cue → byte-identical Phase-5 claim id ---------------


def test_no_cue_yields_empty_signature_and_phase5_claim_id():
    doc, _, claims = _claims("Dietary fiber improved remission.")
    assert len(claims) == 1
    c = claims[0]
    assert c.qualifiers == []
    assert signature(c.qualifiers) == ""

    # Reproduce the pre-Phase-6 id formula (5-tuple, no qualifier component) independently and
    # assert it is byte-identical — proof the empty-signature path did not perturb the hash.
    key = (c.subject_concept_id, c.predicate, c.object_concept_id, c.polarity, c.certainty)
    digest = hashlib.sha256("␟".join(key).encode("utf-8")).hexdigest()[:12]
    assert c.claim_id == f"{doc.document_id}_claim_{digest}"


# --- provenance: every qualifier reconstructs from its offsets ----------------------


def test_every_qualifier_reconstructs_and_is_ruled():
    doc, obs, claims = _claims(
        "Dietary fiber improved remission during remission.",
        "Dietary fiber worsened abdominal pain during active disease.",
    )
    all_quals = [q for o in obs for q in o.qualifiers] + [q for c in claims for q in c.qualifiers]
    assert all_quals
    for q in all_quals:
        assert q.evidence_refs
        assert q.rule_id and q.rule_version
        for ref in q.evidence_refs:
            assert doc.text[ref.start_char : ref.end_char] == q.value_text


# --- determinism --------------------------------------------------------------------


def test_determinism_two_runs_identical():
    doc = _doc("Dietary fiber improved remission during active disease.")
    mentions = extract_entities(doc, use_model=False)
    first = [o.model_dump() for o in extract_observations(doc, mentions)]
    second = [o.model_dump() for o in extract_observations(doc, mentions)]
    assert first == second


# --- persistence + query + audit (DB round-trip) ------------------------------------


def _seed(tmp_path, sentences: dict[str, list[str]]):
    store = CorpusStore(tmp_path / "corpus")
    engine = get_engine(tmp_path / "db.sqlite")
    init_db(engine)
    for pmid, paras in sentences.items():
        meta = DocumentMetadata(pmid=pmid, source_type="abstract", title=f"Paper {pmid}")
        doc = build_document(meta, [ParsedSection(title="Body", paragraphs=paras)])
        store.save_canonical(doc)
        extract_document(pmid, corpus=store, engine=engine, persist=True, use_model=False)
    return store, engine


def test_persist_and_find_claims_disease_state_filter(tmp_path):
    _, engine = _seed(
        tmp_path,
        {"10000001": [
            "Dietary fiber improved remission during remission.",
            "Dietary fiber improved remission during active disease.",
        ]},
    )
    claims = list_claims_for_document(engine, "10000001")
    improves = [c for c in claims if c["predicate"] == "improves"
                and c["object_concept_id"] == "OUT:remission"]
    assert len(improves) == 2
    assert all(c["qualifiers"] for c in improves)

    # The disease_state filter is exact + deterministic, by concept id or surface.
    only_remission = find_claims(engine, predicate="improves", disease_state="DS:remission")
    assert len(only_remission) == 1
    assert only_remission[0]["qualifiers"][0]["value_concept_id"] == "DS:remission"
    assert find_claims(engine, disease_state="remission")  # surface also matches
    assert find_claims(engine, disease_state="DS:active_disease")
    assert find_claims(engine, disease_state="DS:flare") == []  # not present in this paper


def test_persist_is_idempotent_for_qualifiers(tmp_path):
    store, engine = _seed(
        tmp_path,
        {"10000001": ["Dietary fiber improved remission during remission."]},
    )
    def snap():
        c = list_claims_for_document(engine, "10000001")
        return [(x["claim_id"], tuple((q["qualifier_type"], q["value_concept_id"]) for q in x["qualifiers"])) for x in c]
    first = snap()
    extract_document("10000001", corpus=store, engine=engine, persist=True, use_model=False)
    assert snap() == first


def test_audit_renders_qualifier_inline(tmp_path):
    from mehungry_extractor.knowledge.audit import render_claim

    _, engine = _seed(
        tmp_path,
        {"10000001": ["Dietary fiber improved remission during remission."]},
    )
    c = [x for x in list_claims_for_document(engine, "10000001")
         if x["predicate"] == "improves"][0]
    block = render_claim(engine, c["claim_id"])
    assert "QUALIFIER" in block
    assert "disease_state" in block and "DS:remission" in block

    # The provenance object also carries the qualifiers.
    data = explain_claim(engine, c["claim_id"])
    assert data["claim"]["qualifiers"][0]["value_concept_id"] == "DS:remission"


# --- conditional cross-paper synthesis ----------------------------------------------


def test_conditional_synthesis_splits_by_disease_state(tmp_path):
    _, engine = _seed(
        tmp_path,
        {
            "10000001": ["Dietary fiber improved remission during remission."],
            "10000002": ["Dietary fiber improved remission during active disease."],
        },
    )
    docs = [document_id("10000001"), document_id("10000002")]
    result = synthesize(engine, docs)
    # Same (subject, predicate, object) — but two conditions → two conclusions, not one averaged.
    fiber = [
        c for c in result.conclusions
        if (c.subject_concept_id, c.predicate, c.object_concept_id)
        == ("NUTR:dietary_fiber", "improves", "OUT:remission")
    ]
    assert len(fiber) == 2
    states = {q["value_concept_id"] for c in fiber for q in c.qualifiers}
    assert states == {"DS:remission", "DS:active_disease"}
    assert all(c.paper_count == 1 for c in fiber)


def test_synthesis_without_qualifiers_unchanged(tmp_path):
    """A relation with no disease-state cue synthesizes exactly as in Phase 5 (empty qualifiers)."""
    _, engine = _seed(
        tmp_path,
        {
            "10000001": ["Dietary fiber improved remission."],
            "10000002": ["Dietary fiber improved remission."],
        },
    )
    docs = [document_id("10000001"), document_id("10000002")]
    result = synthesize(engine, docs)
    assert len(result.conclusions) == 1
    assert result.conclusions[0].qualifiers == []
    assert result.conclusions[0].paper_count == 2
