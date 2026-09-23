"""Immutable, idempotent corpus archive (spec §2, §17)."""

import pytest

from mehungry_extractor.knowledge.corpus import CorpusStore, sha256


def test_save_raw_writes_files_and_checksums(tmp_path):
    store = CorpusStore(tmp_path)
    files = {"pubmed.xml": b"<a/>", "pmc.xml": b"<b/>"}
    checksums = store.save_raw("12345678", files)

    assert store.has_raw("12345678")
    assert checksums == {"pubmed.xml": sha256(b"<a/>"), "pmc.xml": sha256(b"<b/>")}
    assert store.read_raw("12345678") == files
    assert store.read_checksums("12345678") == checksums
    assert (store.raw_dir("12345678") / "pubmed.xml").read_bytes() == b"<a/>"


def test_save_raw_never_silently_overwrites(tmp_path):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": b"<a/>"})
    with pytest.raises(FileExistsError):
        store.save_raw("12345678", {"pubmed.xml": b"<CHANGED/>"})
    # original bytes untouched
    assert store.read_raw("12345678")["pubmed.xml"] == b"<a/>"


def test_force_refreshes_raw(tmp_path):
    store = CorpusStore(tmp_path)
    store.save_raw("12345678", {"pubmed.xml": b"<a/>"})
    store.save_raw("12345678", {"pubmed.xml": b"<v2/>"}, force=True)
    assert store.read_raw("12345678")["pubmed.xml"] == b"<v2/>"


def test_read_raw_missing_raises(tmp_path):
    store = CorpusStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read_raw("999")
