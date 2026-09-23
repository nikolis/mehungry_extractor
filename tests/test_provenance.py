"""EvidenceRef source-text reconstruction and precision (spec §4, §15, §17)."""

from mehungry_extractor.knowledge import EXTRACTOR_VERSION
from mehungry_extractor.knowledge.ingest import assemble_document
from mehungry_extractor.knowledge.provenance import EvidenceRef, ProvenancePrecision


def test_evidence_ref_reconstructs_exact_source(pubmed_xml, pmc_xml):
    doc, _ = assemble_document("12345678", pubmed_xml, pmc_xml)
    for sent in doc.iter_sentences():
        ref = EvidenceRef.for_sentence(doc, sent)
        # The ref's quoted text is sliced from the canonical doc at the stored offsets.
        assert ref.quoted_text == doc.text[ref.start_char : ref.end_char] == sent.text
        assert ref.document_id == doc.document_id
        assert ref.sentence_id == sent.sentence_id
        assert ref.paragraph_id == sent.paragraph_id
        assert ref.section_id == sent.section_id
        assert ref.precision == ProvenancePrecision.SENTENCE
        assert ref.extractor_version == EXTRACTOR_VERSION


def test_precision_levels_are_ordered_best_first():
    order = list(ProvenancePrecision)
    assert order[0] == ProvenancePrecision.EXACT_SPAN
    assert order[-1] == ProvenancePrecision.METADATA


def test_evidence_ref_defaults_carry_rule_provenance():
    ref = EvidenceRef(document_id="pmid_1")
    assert ref.extraction_rule == "canonical_ingest"
    assert ref.extraction_rule_version == "0.1.0"
