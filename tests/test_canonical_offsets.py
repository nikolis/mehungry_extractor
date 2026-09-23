"""The offset/reconstruction contract and canonical structure (spec §3, §17)."""

from mehungry_extractor.knowledge.canonical import (
    ParsedSection,
    build_document,
    DocumentMetadata,
)
from mehungry_extractor.knowledge.ingest import assemble_document


def _assert_invariant(doc):
    """document.text[start:end] == obj.text for every section/paragraph/sentence."""
    assert doc.text  # non-empty for these fixtures
    for sec in doc.sections:
        assert doc.text[sec.start_char : sec.end_char] == sec.text
        for par in sec.paragraphs:
            assert doc.text[par.start_char : par.end_char] == par.text
            assert par.start_char >= sec.start_char and par.end_char <= sec.end_char
            for sent in par.sentences:
                assert doc.text[sent.start_char : sent.end_char] == sent.text
                assert par.start_char <= sent.start_char <= sent.end_char <= par.end_char


def test_full_text_invariant_and_structure(pubmed_xml, pmc_xml):
    doc, _rec = assemble_document("12345678", pubmed_xml, pmc_xml, pmcid="PMC1234567")
    assert doc.source_type == "open_access"
    _assert_invariant(doc)

    titles = [s.title for s in doc.sections]
    # Title (synthetic) + JATS sections in document order (nested sec flattened after parent).
    assert titles == ["Title", "Introduction", "Methods", "Statistical analysis", "Results"]

    # Deterministic, hierarchical IDs.
    first = doc.sections[0]
    assert first.section_id == "pmid_12345678_sec000"
    assert first.paragraphs[0].paragraph_id == "pmid_12345678_sec000_p000"
    assert first.paragraphs[0].sentences[0].sentence_id == "pmid_12345678_sec000_p000_s00"


def test_abstract_only_fallback(pubmed_xml):
    doc, _rec = assemble_document("12345678", pubmed_xml, None)
    assert doc.source_type == "abstract"
    _assert_invariant(doc)
    # Title section + one section per labeled AbstractText.
    assert [s.title for s in doc.sections] == ["Title", "Background", "Results"]


def test_empty_paragraphs_are_dropped():
    meta = DocumentMetadata(pmid="1", source_type="abstract")
    doc = build_document(meta, [ParsedSection("S", ["   ", "real text here.", ""])])
    assert len(doc.sections) == 1
    assert len(doc.sections[0].paragraphs) == 1
    assert doc.text == "real text here."


def test_section_with_no_content_is_dropped():
    meta = DocumentMetadata(pmid="1", source_type="abstract")
    doc = build_document(meta, [ParsedSection("Empty", ["  "]), ParsedSection("Kept", ["x."])])
    assert [s.title for s in doc.sections] == ["Kept"]
