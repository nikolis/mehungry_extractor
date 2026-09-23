"""Shared fixtures for the deterministic-engine tests. All offline — no network."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pubmed_xml() -> bytes:
    return (FIXTURES / "pubmed_12345678.xml").read_bytes()


@pytest.fixture
def pmc_xml() -> bytes:
    return (FIXTURES / "pmc_12345678.xml").read_bytes()
