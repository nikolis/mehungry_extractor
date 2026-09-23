"""Structured JATS + PubMed parsing (spec §3, §9)."""

from mehungry_extractor.knowledge import jats, pubmed


def test_jats_preserves_section_hierarchy(pmc_xml):
    sections = jats.parse(pmc_xml)
    assert [s.title for s in sections] == [
        "Introduction",
        "Methods",
        "Statistical analysis",  # nested <sec> flattened after its parent
        "Results",
    ]
    assert sections[0].paragraphs[0].startswith("Crohn disease is a chronic")
    assert len(sections[1].paragraphs) == 1


def test_jats_bad_xml_returns_empty():
    assert jats.parse("<not valid") == []


def test_pubmed_metadata(pubmed_xml):
    rec = pubmed.parse(pubmed_xml)
    assert rec.pmid == "12345678"
    assert rec.title == "Dietary fiber and remission in Crohn disease."
    assert rec.journal == "Journal of Deterministic Nutrition"
    assert rec.publication_date == "2010-03-15"
    assert rec.doi == "10.1000/example.2010.001"
    assert rec.pmcid == "PMC1234567"
    assert rec.authors == ["Smith Jane A", "Doe John"]
    assert "Randomized Controlled Trial" in rec.publication_types
    assert rec.affiliations and rec.affiliations[0].startswith("Department of Gastroenterology")


def test_pubmed_structured_abstract_sections(pubmed_xml):
    rec = pubmed.parse(pubmed_xml)
    assert [s.title for s in rec.abstract_sections] == ["Background", "Results"]


def test_pubmed_missing_fields_are_none():
    rec = pubmed.parse("<PubmedArticleSet><PubmedArticle><MedlineCitation>"
                        "<PMID>7</PMID></MedlineCitation></PubmedArticle></PubmedArticleSet>")
    assert rec.pmid == "7"
    assert rec.title is None
    assert rec.doi is None
    assert rec.abstract_sections == []
