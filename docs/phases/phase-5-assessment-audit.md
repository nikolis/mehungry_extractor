# Phase 5 — Assessment frameworks, filtering, audit CLI

**Goal:** let different grading frameworks be applied *on top of* the evidence without
mutating it (spec §11), complete the deterministic filtering query API (spec §14), and ship
the human-readable provenance/audit output (spec §15–§16). After this phase the system can
answer the guiding question end-to-end from the CLI.

**Prerequisite:** Phases 1–4. Assessments reference existing claim/paper facts; the audit
renderer walks the provenance graph those phases populate.

## Deliverables

### Assessment layer (kept separate from evidence — spec §11)
Models/tables:
- `assessment_frameworks` — `framework_id`, `name` (e.g. `mehungry_evidence_v1`), `version`.
- `assessment_criteria` — the criteria a framework defines.
- `assessments` — `paper_id` (or `claim_id`), `framework_id`, `criterion`, `value`,
  `rationale`, `evidence_refs`.

An assessment **derives** values from Phase-4 facts (study_design, year, industry_funding,
sample_size, …) via a deterministic, versioned framework function. It never writes a
`quality_score` back onto a claim/paper. Multiple frameworks coexist; re-running a framework
replaces only that framework's assessment rows.

### Query API — finish `knowledge/query.py`
Implement the stubs deterministically (no fuzzy/semantic search):
- `find_claims(condition=..., subject=..., funder_type=..., require_independent_funding=True)`
- `find_papers(study_design=..., publication_year_from=...)`
- `find_evidence(claim_id)` / `list_claims_for_document(document_id)`
- `explain_claim(claim_id)` — returns the full provenance object:
  `Claim → EvidenceRef[] → Document → Section → Paragraph → Sentence → exact phrase → rule →
  extractor/pipeline version → ExtractionRun`. `get_source_span(...)` already exists (Phase 1)
  — reuse it.

### Audit CLI — implement the `audit` stage in `knowledge/stagecli.py`
`mehungry audit --claim <claim_id>` prints the block from spec §16: CLAIM, CONTEXT, SOURCE
(PMID/PMCID/year), STUDY (design, sample size), EVIDENCE (section/paragraph/sentence id +
the exact quoted sentence), MATCH (rule id/version, extractor version), FUNDING (type +
source), DOCUMENT HASH (from `checksums.json`), EXTRACTION RUN. Add
`knowledge/audit.py` for the rendering, keeping the CLI thin.

## Provenance constraints (offline; determinism optional)
- **Facts vs assessments stay separate.** `study_design=RCT`, `publication_year=2010`,
  `industry_funding=false`, `sample_size=120` are facts (Phase 4). A framework may map those
  to values; that mapping is versioned and reproducible and lives only in `assessments`.
- Assessment functions are pure and deterministic; the framework `version` participates in
  reproducibility. Changing a framework must not alter any evidence row.
- The audit output must be reconstructable purely from stored data — every quoted sentence
  comes from slicing the canonical `text` at stored offsets (verify equality when rendering),
  every rule/version/hash comes from the DB / `checksums.json` / `ExtractionRun`.
- Filters are exact/deterministic; `require_independent_funding=True` relies on Phase-4
  facts and treats `unknown` funding as *not* independent (never assume independence).

## Suggested steps
1. Add assessment tables + a `mehungry_evidence_v1` framework as a pure function over
   Phase-4 facts.
2. Implement the query filters + `explain_claim` provenance assembler.
3. Build `audit.py` renderer + wire the `audit` CLI stage.
4. Tests, then the deterministic engine is feature-complete for the current mandate.

## Tests to add (gate)
- **Separation:** applying/re-applying a framework changes only `assessments`; evidence and
  claim rows are byte-identical before/after.
- **Filtering:** `find_papers(study_design=..., publication_year_from=...)` and
  `find_claims(..., funder_type=..., require_independent_funding=True)` return the expected
  deterministic sets; `unknown` funding is excluded from "independent".
- **Provenance round-trip:** `explain_claim(claim_id)` yields a chain whose quoted phrase
  reconstructs from offsets and whose paper hash matches `checksums.json`.
- **Audit output:** rendering a known claim produces the expected block (snapshot test),
  with the exact source sentence and correct rule/version/hash.
- **Determinism:** run twice → identical assessments and identical audit text.

## Done criteria
`mehungry audit --claim <id>` prints a complete, correct provenance block for a claim
extracted end-to-end; frameworks can be applied without touching evidence; the deterministic
query API answers the spec §14 questions. Bump `EXTRACTOR_VERSION`/`RULESET_VERSION` and
`SCHEMA_VERSION` as appropriate.

## Future-LLM boundary (spec §21)
A later LLM service may synthesize interpretations, but it must **reference existing
`claim_id`/`evidence_id`/`document_id`** and never create unsupported evidence. Keep the
assessment/query/audit interfaces returning stable IDs so that boundary stays clean; the
deterministic engine remains the source of truth.
