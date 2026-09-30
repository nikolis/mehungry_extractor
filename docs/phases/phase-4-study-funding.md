# Phase 4 — Study metadata, funding, affiliations, conflicts

**Goal:** extract and normalize study characteristics, funding, author affiliations, and
conflict-of-interest information — as **facts with provenance**, independent of any claim or
quality judgment (spec §9–§11). "Absence of funding info" is recorded as unknown, never
interpreted as independence.

**Prerequisite:** Phases 1–3. `pubmed.PubMedRecord.publication_types` and `.affiliations`
are already captured in Phase 1 — reuse them.

## Deliverables

### Modules
- `knowledge/study.py` — study-design classification + population/sample-size/etc. extraction.
- `knowledge/funding.py` — funder + funder-type extraction.
- `knowledge/affiliations.py` — author ↔ affiliation ↔ institution structuring.
- **Extend `knowledge/jats.py`** to parse JATS front/back matter it currently ignores:
  `<funding-group>`/`<award-group>`, `<fn fn-type="conflict">`/COI statements, `<ack>`
  acknowledgements. Keep `parse()` (body) intact; add `parse_funding(xml)` /
  `parse_back_matter(xml)` returning text spans so funding gets real `EvidenceRef`s.

### Study characteristics (spec §9)
Fields: `publication_year, study_design, population, sample_size, intervention, comparator,
outcomes, follow_up, country, institutions`. Design categories:
`systematic_review, meta_analysis, randomized_controlled_trial, controlled_trial, cohort,
case_control, cross_sectional, case_series, case_report, animal, in_vitro, guideline,
review, editorial, letter, unknown`.
Each characteristic stores `classification, classification_source (pubmed_publication_type |
text_rule | ...), evidence_ref, rule_id`. **Map from `publication_types` first** (highest
authority), fall back to deterministic text rules; when insufficient → `unknown` (do not
guess).

### Funding (spec §10)
`FundingRelationship`: `paper_id, funder, funder_type, source, evidence_ref`.
`funder_type ∈ {government, university, charity, foundation, pharmaceutical, biotechnology,
medical_device, food_industry, private_company, unknown}`. **Never infer a category without
a deterministic rule or authoritative metadata** — a curated funder→type dictionary under
`knowledge/vocab/` is the right mechanism; unmatched → `unknown`. `source` records where it
came from (`pmc_funding_statement | coi_statement | acknowledgements | pubmed_grant`).

### Affiliations (spec §10)
Model `Author`, `Affiliation`, `Institution`, `AuthorAffiliation` so later queries can ask
"papers involving university X / company Y". Parse from `PubMedRecord.affiliations` +
JATS. Preserve the raw affiliation string; institution normalization is deterministic
(dictionary) and keeps the original.

### DB tables
`study_characteristics`, `funders`, `funding_relationships`, `authors`, `affiliations`,
`author_affiliations`. Index on `funder_type`, `study_design`, `document_id`. Each row
carries `run_id` and (for facts derived from text) an evidence reference.

### CLI
Fold extraction into a document metadata pass — extend the `analyze`/`extract` pipeline (or
add a dedicated pass invoked by both). Keep it independently rerunnable per document.

## Provenance constraints (offline; determinism optional)
- Prefer authoritative structured metadata (PubMed publication types, JATS funding-group)
  over text mining. Record `classification_source` so the origin is auditable.
- Text-derived facts (sample size "n = 412", follow-up "two years", country) come from
  deterministic regex/`Matcher` rules with a `rule_id`/version and an `EvidenceRef` at
  `EXACT_SPAN`/`SENTENCE`. Structured-metadata facts use `METADATA` precision.
- **Absence is not evidence.** No funding statement ⇒ `funder_type=unknown` and a
  `FundingRelationship` may simply be absent — never synthesize "independent".
- Funder-type and institution categorization come only from curated dictionaries or
  authoritative metadata; unmatched → `unknown`.

## Suggested steps
1. Extend `jats.py` with front/back-matter parsers (funding, COI, ack).
2. Curated `knowledge/vocab/funders.*` (funder name → type) + institutions.
3. `study.classify(record, document) -> list[StudyCharacteristic]` (publication_types first).
4. `funding.extract(...)`, `affiliations.extract(...)`.
5. Tables + persist + wire into the pipeline.
6. Tests, then stop (gate Phase 5).

## Tests to add (gate)
- **Study design:** one fixture per supported category → correct `classification` +
  `classification_source`; ambiguous input → `unknown` (not a guess).
- **Funding:** pharmaceutical / government / university / charity / unknown cases →
  correct `funder_type` + `source` + evidence; unmatched funder → `unknown`.
- **Affiliations:** author→affiliation→institution structured correctly; raw string kept.
- **No-funding:** a paper with no funding statement yields no independence claim and
  `unknown` where applicable.
- **Determinism:** run twice → identical rows.

## Done criteria
Study design, funding, and affiliations are persisted as provenance-carrying facts; queries
like "papers with government funding" / "papers involving company Y" are answerable
deterministically; unknowns are explicit. Bump `RULESET_VERSION` and `SCHEMA_VERSION`.
