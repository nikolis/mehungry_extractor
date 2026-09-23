"""Offline unit tests — no network, no Anthropic. Cover the server contract
(pydantic validation) and the JATS/abstract text extraction."""

import pytest

from mehungry_extractor.models import Finding, Findings
from mehungry_extractor import pmc


def test_finding_validates_and_defaults():
    f = Finding(raw_term="insoluble fiber", target_kind="nutrient", direction="avoid",
                condition_state_slug="active_flare", severity="high", confidence=0.8)
    assert f.condition_state_slug == "active_flare"
    d = f.model_dump(exclude_none=True)
    assert d["raw_term"] == "insoluble fiber" and d["direction"] == "avoid"


def test_finding_defaults_state_to_general():
    f = Finding(raw_term="omega-3", target_kind="nutrient", direction="encourage")
    assert f.condition_state_slug == "general"
    assert f.confidence == 0.5


def test_finding_rejects_bad_enum():
    with pytest.raises(Exception):
        Finding(raw_term="x", target_kind="vitamin", direction="avoid")


def test_findings_empty_default():
    assert Findings().findings == []


def test_jats_to_text_extracts_body():
    xml = """<article><front><article-meta><title-group>
      <article-title>Ignore me</article-title></title-group></article-meta></front>
      <body><sec><p>Low residue diets   help during   flares.</p></sec></body></article>"""
    text = pmc._jats_to_text(xml)
    assert "Low residue diets help during flares." in text


def test_jats_to_text_bad_xml_returns_none():
    assert pmc._jats_to_text("<not valid") is None
