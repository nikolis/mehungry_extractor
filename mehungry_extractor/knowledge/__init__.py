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
# Unchanged since Phase 1 — Phases 2-5 are additive and never alter canonical offsets.
PIPELINE_VERSION = "0.1.0"

# The deterministic rule-based extraction ruleset (entity dictionary in Phase 2; relation
# rules + negation/uncertainty in Phase 3; study/funding text rules in Phase 4; disease-state
# qualifier cues in Phase 6). Bump when the extraction rules change produced knowledge. 0.4.0
# covered Phases 2-4; 0.5.0 added the Phase 6 qualifier cue rules; 0.6.0 added the Phase 7 clause
# segmentation ruleset and the population/dose/duration/route/severity qualifier cues; 0.7.0 adds
# the A4 outcome-attainment relation rule (achieves/maintains/sustains); 0.8.0 adds the Phase 10
# verb-lemma → predicate map used by the parse-based argument binder on the model path; 0.9.0 adds
# the Phase 12 entity-modifier detectors (restrictive prep-phrase modifiers on entity heads); 0.10.0
# widens the prevents cue to "protect* <word>{0,2} against" (Phase 4c — "protective factor against");
# 0.11.0 adds the Phase 13 descriptive ``characterized_by`` predicate (flat cue + verb-map entry) used
# by hierarchical (nested) relations; 0.12.0 adds the Phase 14 descriptive *abundance-manifestation*
# predicates (``has_decreased_abundance_of``/``has_increased_abundance_of`` — flat cues + the
# free-adjunct-participle re-label), the parse-derived ``manifestation`` qualifier, and the gut-taxa
# entity-dictionary concepts (Firmicutes/Bifidobacterium/Faecalibacterium prausnitzii); 0.13.0 makes a
# ``characterized_by`` object the WHOLE descriptor phrase ("alterations in the composition and function
# of the gut microbiota") rather than the deep entity (*gut microbiota*) the resolver would descend to,
# and retires the now-superseded ``manifestation`` qualifier (Phase 15 — descriptive-object binding).
RULESET_VERSION = "0.13.0"

# The extractor code version (mirrors the package version). 0.3.0 delivered Phases 3-5; 0.4.0
# shipped the Phase 6 qualifier layer; 0.5.0 shipped the Phase 7 clause-scoped extraction engine;
# 0.6.0 ships the Phase 10 parse-based relation-argument binder on the model path (the model-free
# flat floor is byte-unchanged); 0.7.0 ships the Phase 12 restrictive entity-modifier layer
# (representation/provenance/display; claim keys unchanged); 0.8.0 folds a key-bearing endpoint
# modifier into the claim key (A2), so a localized entity pair forms its own claim; 0.9.0 ships the
# Phase 13 hierarchical (nested) relations — the descriptive `characterized_by` predicate, the
# participle free-adjunct subject rule, and the observation/claim parent links; 0.10.0 ships Phase 14
# — a directional participle over a *state* is re-read as a descriptive abundance manifestation,
# apposition list-items are recovered in object coordination, and a descriptive relation's abstract
# object head-phrase is kept as a `manifestation` qualifier (the object stays the normalized concept);
# 0.11.0 ships Phase 15 — a `characterized_by` object becomes the whole descriptor head-noun phrase
# verbatim (a free-text object, no concept), replacing the `manifestation` qualifier entirely.
EXTRACTOR_VERSION = "0.11.0"

# On-disk / DB schema version. We use ``create_all`` guarded by this constant until the schema
# stabilizes; Alembic is deferred (spec §13). 0.2.0 added Phase 2 entity tables; 0.5.0 added the
# Phase 3 relation/claim, Phase 4 study/funding/affiliation, and Phase 5 assessment tables; 0.6.0
# added the Phase 6 observation_qualifiers/claim_qualifiers tables; 0.7.0 adds the Phase 11
# open_observations table (relation-bearing spans, isolated from claims); 0.8.0 adds the Phase 12
# entity_modifiers table (restrictive concept→concept edges on entity mentions); 0.9.0 adds the
# claim_modifiers table (per-endpoint modifiers on a claim, A2); 0.10.0 adds the Phase 13 nested-
# relation parent columns (observations.parent_observation_id, claims.parent_claim_id).
SCHEMA_VERSION = "0.10.0"

# The cross-paper synthesis / batch-analysis view exposed over the REST API (``knowledge/api``).
# This is a *read-side* version: the synthesis and topic-cohesion logic add no new evidence and
# never alter extraction, so bumping it does not change any stored knowledge object — it only
# marks a change in how batches are aggregated/reported. Recorded on every API response so a
# caller can tell which aggregation logic produced a conclusion. 0.2.0 groups conclusions by
# qualifier signature (Phase 6), so one entity pair yields one conclusion per disease state; 0.3.0
# annotates each conclusion with a clinical benefit/harm reading (A2, :mod:`.valence`); 0.4.0 adds
# the derived food-level conclusion section bridged via the composition ontology (5b, :mod:`.foods`);
# 0.5.0 folds a key-bearing endpoint-modifier signature into the grouping key (A2), so a localized
# entity pair (e.g. dysbiosis of the gut microbiome) is its own conclusion rather than merged.
SYNTHESIS_VERSION = "0.5.0"
