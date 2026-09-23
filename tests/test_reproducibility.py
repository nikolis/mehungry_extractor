"""Byte-level reproducibility of the deterministic pipeline (spec §12, §17)."""

from mehungry_extractor.knowledge.canonical import to_canonical_json
from mehungry_extractor.knowledge.corpus import CorpusStore
from mehungry_extractor.knowledge.db import get_engine
from mehungry_extractor.knowledge.ingest import assemble_document, normalize_document
from mehungry_extractor.knowledge.run import build_run


def test_assemble_is_byte_identical(pubmed_xml, pmc_xml):
    d1, _ = assemble_document("12345678", pubmed_xml, pmc_xml, pmcid="PMC1234567")
    d2, _ = assemble_document("12345678", pubmed_xml, pmc_xml, pmcid="PMC1234567")
    assert to_canonical_json(d1) == to_canonical_json(d2)


def test_run_id_deterministic_excluding_timestamp():
    r1 = build_run(["pmid_12345678"], timestamp="2020-01-01T00:00:00+00:00")
    r2 = build_run(["pmid_12345678"], timestamp="2099-12-31T23:59:59+00:00")
    assert r1.run_id == r2.run_id  # timestamp excluded from the fingerprint
    assert r1.pipeline_version == r2.pipeline_version
    assert r1.extractor_version == r2.extractor_version
    assert r1.python_version == r2.python_version
    # different document sets → different run_id
    assert build_run(["pmid_1"]).run_id != build_run(["pmid_2"]).run_id


def test_normalize_document_offline_is_reproducible(tmp_path, pubmed_xml, pmc_xml):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": pubmed_xml, "pmc.xml": pmc_xml})
    engine = get_engine(tmp_path / "db.sqlite")

    doc1 = normalize_document("12345678", corpus=store, engine=engine)
    bytes1 = (store.canonical_dir("12345678") / "document.json").read_bytes()

    doc2 = normalize_document("12345678", corpus=store, engine=engine)
    bytes2 = (store.canonical_dir("12345678") / "document.json").read_bytes()

    assert bytes1 == bytes2
    assert doc1.source_type == doc2.source_type == "open_access"
    # the reverse trace: canonical retains the raw-source checksums
    assert "pubmed.xml" in doc1.checksums and "pmc.xml" in doc1.checksums
