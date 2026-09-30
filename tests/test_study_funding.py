"""Phase 4 gate: study design, funding, affiliations as provenance-carrying facts (spec §9-§10).

Deterministic and offline. Uses the shared fixture for study design (its publication type is a
Randomized Controlled Trial) and inline JATS snippets for funding/COI parsing.
"""

from mehungry_extractor.knowledge import affiliations as aff
from mehungry_extractor.knowledge import funding
from mehungry_extractor.knowledge import jats, study
from mehungry_extractor.knowledge.canonical import (
    DocumentMetadata,
    ParsedSection,
    build_document,
)
from mehungry_extractor.knowledge.funding import FUNDER_TYPE_UNKNOWN
from mehungry_extractor.knowledge.ingest import assemble_document


def _doc(*paragraphs, pub_types=None, pub_date=None):
    meta = DocumentMetadata(
        pmid="99999999", source_type="abstract",
        publication_types=pub_types or [], publication_date=pub_date,
    )
    return build_document(meta, [ParsedSection(title="Body", paragraphs=list(paragraphs))])


# --- study design -------------------------------------------------------------------


def test_publication_type_wins_over_text(pubmed_xml, pmc_xml):
    """RCT publication type is authoritative even though the body says "cohort study"."""
    doc, _ = assemble_document("12345678", pubmed_xml, pmc_xml)
    chars = {c.field: c for c in study.classify(doc)}
    design = chars["study_design"]
    assert design.value == "randomized_controlled_trial"
    assert design.classification_source == "pubmed_publication_type"

    # Text-derived facts reconstruct from their offsets.
    assert chars["sample_size"].value == "412"
    assert doc.text[chars["sample_size"].evidence_ref.start_char : chars["sample_size"].evidence_ref.end_char] == "412"
    assert "two years" in chars["follow_up"].value


def test_design_falls_back_to_text_rule():
    doc = _doc("This was a prospective cohort study of adults.")
    design = {c.field: c for c in study.classify(doc)}["study_design"]
    assert design.value == "cohort"
    assert design.classification_source == "text_rule"


def test_ambiguous_design_is_unknown_not_guessed():
    doc = _doc("We discuss dietary patterns in general terms.")
    design = {c.field: c for c in study.classify(doc)}["study_design"]
    assert design.value == "unknown"


def test_publication_year_from_metadata():
    doc = _doc("Body text.", pub_date="2019-05-01")
    year = {c.field: c for c in study.classify(doc)}["publication_year"]
    assert year.value == "2019"
    assert year.classification_source == "metadata"


# --- funding ------------------------------------------------------------------------


def _funding_xml(source_xml: str) -> bytes:
    return f"""<article><front><article-meta>
      <funding-group>{source_xml}</funding-group>
    </article-meta></front><body><sec><p>Body.</p></sec></body></article>""".encode()


def test_pharmaceutical_funder_typed_from_dictionary():
    xml = _funding_xml("<award-group><funding-source>Pfizer Inc</funding-source></award-group>")
    rels = funding.extract("pmid_1", jats.parse_funding(xml))
    assert len(rels) == 1
    assert rels[0].funder_type == "pharmaceutical"
    assert rels[0].source == "pmc_award_group"
    assert rels[0].evidence_ref.precision.value == "METADATA"


def test_government_funder_in_free_text_statement():
    xml = _funding_xml(
        "<funding-statement>This work was supported by the National Institutes of Health.</funding-statement>"
    )
    rels = funding.extract("pmid_1", jats.parse_funding(xml))
    assert any(r.funder_type == "government" for r in rels)


def test_unmatched_funder_is_unknown_never_guessed():
    xml = _funding_xml("<award-group><funding-source>Acme Widgets Ltd</funding-source></award-group>")
    rels = funding.extract("pmid_1", jats.parse_funding(xml))
    assert len(rels) == 1
    assert rels[0].funder_type == FUNDER_TYPE_UNKNOWN


def test_no_funding_statement_yields_no_relationship():
    """Absence is not evidence: no funding-group ⇒ no relationship, never "independent"."""
    xml = b"<article><body><sec><p>No funding here.</p></sec></body></article>"
    assert funding.extract("pmid_1", jats.parse_funding(xml)) == []
    assert jats.parse_back_matter(xml) == []


def test_conflict_statement_parsed_from_back_matter():
    xml = b"""<article><back>
      <fn-group><fn fn-type="conflict"><p>J.S. received fees from Novartis.</p></fn></fn-group>
    </back></article>"""
    statements = jats.parse_back_matter(xml)
    assert statements and statements[0].source == "coi_statement"
    rels = funding.extract("pmid_1", statements)
    assert any(r.funder_type == "pharmaceutical" for r in rels)


# --- affiliations -------------------------------------------------------------------


def test_authors_affiliations_institutions_structured(pubmed_xml):
    ex = aff.extract("pmid_12345678", pubmed_xml)
    names = [a.name for a in ex.authors]
    assert names == ["Smith Jane A", "Doe John"]

    # The one affiliation string is preserved raw and linked to its author + institution.
    assert len(ex.affiliations) == 1
    affil = ex.affiliations[0]
    assert "University of Example" in affil.raw_text
    inst = {i.institution_id: i for i in ex.institutions}[affil.institution_id]
    assert inst.canonical_name == "University of Example"
    assert inst.institution_type == "university"

    links = {(l.author_id, l.affiliation_id) for l in ex.author_affiliations}
    assert (ex.authors[0].author_id, affil.affiliation_id) in links


def test_unmatched_institution_kept_with_unknown_type():
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <Article><AuthorList><Author><LastName>Roe</LastName><ForeName>Ann</ForeName>
        <AffiliationInfo><Affiliation>Institute of Nowhere, Faraway.</Affiliation></AffiliationInfo>
      </Author></AuthorList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    ex = aff.extract("pmid_2", xml)
    inst = ex.institutions[0]
    assert inst.institution_type == "unknown"
    assert inst.canonical_name == "Institute of Nowhere, Faraway."  # raw preserved
