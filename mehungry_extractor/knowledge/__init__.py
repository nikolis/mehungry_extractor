"""Deterministic, offline biomedical knowledge/evidence engine.

This subpackage sits *underneath* the existing LLM recommendation path
(``mehungry_extractor.extract``) and never touches an LLM, the network for
inference, embeddings, or randomness. Its job is to turn PubMed/PMC source into a
canonical, offset-addressable document and to preserve complete provenance so that
every downstream fact can be traced back to the exact source span, paper, extraction
rule, and pipeline version.

Phase 1 (implemented): acquisition, immutable corpus cache, canonical
Document/Section/Paragraph/Sentence with absolute character offsets, SQLite
persistence, and the provenance/reproducibility foundation. Phases 2-5 (entities,
relations/claims, study/funding metadata, assessments/audit rendering) build on top.

Version constants below are stamped onto every :class:`ExtractionRun` and every
:class:`EvidenceRef`. Bump them when extraction behavior changes so that outputs from
different versions are distinguishable (spec §12).
"""

from __future__ import annotations

# The canonical-representation / offset contract, the JATS→canonical assembly, and the
# sentence segmentation ruleset. Bump when any of those change the produced offsets.
PIPELINE_VERSION = "0.1.0"

# The deterministic rule-based extraction ruleset (relations/observations/claims).
# No rules exist yet in Phase 1; present so runs are labelled from the start.
RULESET_VERSION = "0.0.0"

# The extractor code version (mirrors the package version).
EXTRACTOR_VERSION = "0.2.0"

# On-disk / DB schema version. We use ``create_all`` guarded by this constant until the
# schema stabilizes; Alembic is deferred (spec §13).
SCHEMA_VERSION = "0.1.0"
