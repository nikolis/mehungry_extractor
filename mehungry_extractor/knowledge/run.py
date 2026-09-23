"""Reproducibility: the :class:`ExtractionRun` record (spec §12).

Every persisted object is tied to a run so that outputs from different pipeline/ruleset/
tool versions are distinguishable and auditable. The ``run_id`` is a *deterministic* hash of
the version fields + processed document IDs (not a random UUID): re-running the exact same
pipeline over the exact same documents yields the same ``run_id``, which makes DB writes
idempotent. The wall-clock ``timestamp`` is recorded but deliberately excluded from the
``run_id`` hash.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata as _im
from pathlib import Path
from typing import Optional

from . import EXTRACTOR_VERSION, PIPELINE_VERSION, RULESET_VERSION


def _tool_version(name: str) -> Optional[str]:
    try:
        return _im.version(name)
    except _im.PackageNotFoundError:
        return None


def _git_commit() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@dataclass
class ExtractionRun:
    run_id: str
    timestamp: str
    git_commit: Optional[str]
    pipeline_version: str
    ruleset_version: str
    extractor_version: str
    python_version: str
    spacy_version: Optional[str]
    scispacy_version: Optional[str]
    ontology_versions: dict = field(default_factory=dict)
    document_ids: list[str] = field(default_factory=list)


def build_run(document_ids: list[str], timestamp: Optional[str] = None) -> ExtractionRun:
    doc_ids = sorted(set(document_ids))
    git_commit = _git_commit()
    spacy_version = _tool_version("spacy")
    scispacy_version = _tool_version("scispacy")
    python_version = platform.python_version()

    fingerprint = "\n".join(
        [
            PIPELINE_VERSION,
            RULESET_VERSION,
            EXTRACTOR_VERSION,
            python_version,
            spacy_version or "",
            scispacy_version or "",
            git_commit or "",
            *doc_ids,
        ]
    )
    run_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

    return ExtractionRun(
        run_id=run_id,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        git_commit=git_commit,
        pipeline_version=PIPELINE_VERSION,
        ruleset_version=RULESET_VERSION,
        extractor_version=EXTRACTOR_VERSION,
        python_version=python_version,
        spacy_version=spacy_version,
        scispacy_version=scispacy_version,
        ontology_versions={},
        document_ids=doc_ids,
    )
