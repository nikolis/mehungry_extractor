"""Immutable, idempotent on-disk source archive (spec §2).

Layout per document::

    data/corpus/pmid_12345678/
      raw/
        pubmed.xml
        pmc.xml          # only when the article is in PMC OA
        abstract.txt
      canonical/
        document.json
      metadata.json
      checksums.json

Guarantees:
- **Idempotent:** if raw already exists it is reused; a re-fetch only happens with
  ``force=True``.
- **Never silently overwrite raw:** ``save_raw`` on a populated ``raw/`` without ``force``
  raises. Downloaded source material is never thrown away after extraction.

This module is storage-only — it takes/returns bytes, dicts, and canonical
:class:`Document` objects, and makes no parsing or network decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from .canonical import Document, document_from_json, to_canonical_json
from .ids import document_id, normalize_pmid


def default_root() -> Path:
    return Path(os.environ.get("MEHUNGRY_CORPUS_DIR", "data/corpus"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj) -> None:
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class CorpusStore:
    def __init__(self, root: Optional[str | Path] = None):
        self.root = Path(root) if root is not None else default_root()

    # --- paths ---
    def doc_dir(self, pmid: str | int) -> Path:
        return self.root / document_id(pmid)

    def raw_dir(self, pmid: str | int) -> Path:
        return self.doc_dir(pmid) / "raw"

    def canonical_dir(self, pmid: str | int) -> Path:
        return self.doc_dir(pmid) / "canonical"

    # --- existence ---
    def has_raw(self, pmid: str | int) -> bool:
        rd = self.raw_dir(pmid)
        return rd.is_dir() and any(rd.iterdir())

    def has_canonical(self, pmid: str | int) -> bool:
        return (self.canonical_dir(pmid) / "document.json").is_file()

    # --- raw ---
    def save_raw(self, pmid: str | int, files: dict[str, bytes], force: bool = False) -> dict[str, str]:
        """Write raw artifacts and their checksums. Raises if raw exists and ``not force``."""
        if self.has_raw(pmid) and not force:
            raise FileExistsError(
                f"raw sources for {document_id(pmid)} already exist; pass force=True to refresh"
            )
        rd = self.raw_dir(pmid)
        rd.mkdir(parents=True, exist_ok=True)
        checksums: dict[str, str] = {}
        for name in sorted(files):
            data = files[name]
            (rd / name).write_bytes(data)
            checksums[name] = sha256(data)
        _write_json(self.doc_dir(pmid) / "checksums.json", checksums)
        return checksums

    def read_raw(self, pmid: str | int) -> dict[str, bytes]:
        rd = self.raw_dir(pmid)
        if not rd.is_dir():
            raise FileNotFoundError(f"no raw sources for {document_id(pmid)}")
        return {p.name: p.read_bytes() for p in sorted(rd.iterdir()) if p.is_file()}

    def read_checksums(self, pmid: str | int) -> dict[str, str]:
        path = self.doc_dir(pmid) / "checksums.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    # --- metadata ---
    def save_metadata(self, pmid: str | int, metadata: dict) -> None:
        _write_json(self.doc_dir(pmid) / "metadata.json", metadata)

    def read_metadata(self, pmid: str | int) -> dict:
        path = self.doc_dir(pmid) / "metadata.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    # --- canonical ---
    def save_canonical(self, document: Document) -> None:
        cd = self.canonical_dir(document.pmid)
        cd.mkdir(parents=True, exist_ok=True)
        (cd / "document.json").write_text(to_canonical_json(document), encoding="utf-8")

    def read_canonical(self, pmid: str | int) -> Document:
        path = self.canonical_dir(pmid) / "document.json"
        return document_from_json(path.read_text(encoding="utf-8"))
