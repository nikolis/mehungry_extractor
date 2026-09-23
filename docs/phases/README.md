# Phase handovers — deterministic knowledge/evidence engine

This directory is the build plan for evolving `mehungry_extractor` into a deterministic,
offline biomedical evidence engine. Each file is a self-contained handover for one phase:
what exists to build on, what to add, the determinism/provenance constraints, the tests
that gate the next phase, and the done criteria.

| Phase | File | Scope | Status |
|---|---|---|---|
| 1 | [phase-1-foundation.md](phase-1-foundation.md) | Acquisition · immutable corpus · canonical doc + offsets · SQLite · provenance · reproducibility | **Done** |
| 2 | [phase-2-entities.md](phase-2-entities.md) | Deterministic biomedical entity extraction + normalization | Not started |
| 3 | [phase-3-relations-claims.md](phase-3-relations-claims.md) | Rule-based relations → observations → claims; negation + uncertainty | Not started |
| 4 | [phase-4-study-funding.md](phase-4-study-funding.md) | Study design, population, funding, affiliations, conflicts | Not started |
| 5 | [phase-5-assessment-audit.md](phase-5-assessment-audit.md) | Assessment frameworks · filtering query API · human-readable audit CLI | Not started |

## Rules that hold for every phase

1. **Determinism.** No LLM, no external inference, no embeddings/vector DB, no random
   sampling. Same source + same pipeline/ruleset version ⇒ byte-identical output. If a
   library has stochastic behavior, pin it and configure it deterministically, and record
   the version on the run.
2. **Provenance is mandatory.** No knowledge object (entity mention, observation, claim,
   funding relationship, study characteristic, assessment) may exist without an
   `EvidenceRef` (`knowledge/provenance.py`) pointing back to the source. Use
   `ProvenancePrecision` to mark how exact the span is; never silently upgrade precision.
3. **Offset contract.** All new spans are absolute character offsets into the document's
   canonical `text`, so `document.text[start:end] == span_text`. Reuse it; don't invent a
   second coordinate system.
4. **Additive.** The legacy LLM path (`cli.py`, `extract.py`, `client.py`, `ner.py`,
   `models.py`, `pmc.py`) and the `/api/local_ai/*` seam stay working and untouched.
5. **Reproducibility bookkeeping.** Bump the relevant version constant in
   `knowledge/__init__.py` (`PIPELINE_VERSION` / `RULESET_VERSION` / `EXTRACTOR_VERSION` /
   `SCHEMA_VERSION`) when behavior or schema changes, and record model/ontology versions on
   `ExtractionRun` (`knowledge/run.py`) so v1 and v2 outputs are distinguishable.
6. **Tests gate the next phase.** Add the phase's deterministic tests (including a
   run-twice byte-equivalence test) before starting the following phase.
7. **New tables via `create_all` + `SCHEMA_VERSION`.** Alembic stays deferred until the
   schema stabilizes. Add tables in `knowledge/db/schema.py`, export them from
   `knowledge/db/__init__.py`, and let `init_db` create them.

## The guiding question

> If the system tells you a fact, can it show exactly which paper, section, sentence,
> phrase, extraction rule, and pipeline version caused it to believe that fact?

If a phase's design can't answer "yes", redesign that part before implementing it.
