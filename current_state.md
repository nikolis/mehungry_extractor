# Current state — how the pipeline runs, step by step, starting from a PMID

This is the "follow the PMID" tour of the offline evidence engine. Each step names the
**exact file and function** that does the work, in the order they execute for a single request.
(The engine *today* is deterministic end to end; that is now an implementation property, not a
requirement — see [`docs/concepts.md`](docs/concepts.md). The one guarantee that must hold is
provenance: every fact traces to a source span.)

Two entry points reach the same engine:

- **REST**: `POST /analyze` with a list of PMIDs → `mehungry_extractor/knowledge/api/`.
- **CLI**: `mehungry ingest/analyze/extract/audit` → `mehungry_extractor/knowledge/stagecli.py`.

The steps below follow the **REST path** (a batch of PMIDs in, synthesized conclusions out),
because it exercises every stage. The CLI runs the same underlying functions, just one stage at a
time; where it differs, it's called out.

Everything after the network fetch runs **offline**, and today it is also deterministic (no LLM, no
embeddings). Determinism is no longer required, though: **local** non-deterministic techniques
(embeddings, similarity, learned scorers) are permitted going forward, and only hosted/online models
are excluded. What is required, and holds regardless, is that every conclusion traces back to a
source character span.

---

## Step 0 — The request arrives at FastAPI

**File:** `mehungry_extractor/knowledge/api/app.py`
**Function:** `analyze()` (the `@app.post("/analyze")` handler)

1. `app.py:49` — FastAPI receives `POST /analyze`. The JSON body is validated against
   `AnalyzeRequest` (`api/models.py:48`): a non-empty `pmids` list plus tuning `options`.
   The validator `AnalyzeRequest._clean_pmids` (`models.py:52`) strips blanks and de-duplicates.
2. `app.py:33` — the `get_context()` dependency builds the shared per-request context: a
   `CorpusStore()` (on-disk paper cache) and a SQLAlchemy `engine` (SQLite DB), and `init_db()`
   creates tables if missing. **In tests this dependency is overridden** so it runs offline
   against a pre-seeded corpus.
3. `app.py:50` — the handler delegates to `service.analyze_batch(...)`, passing
   `ingest = ctx["ingest"] and req.options.ingest` (network access is gated here).

The handler is deliberately thin — all orchestration lives in the next step.

---

## Step 1 — Batch orchestration

**File:** `mehungry_extractor/knowledge/api/service.py`
**Function:** `analyze_batch()` (`service.py:168`)

This one function is the whole feature. It does not import FastAPI, so it also runs directly from
unit tests. Per PMID (one failure never aborts the batch — mirrors `stagecli._stage_extract`):

1. `service.py:191` — normalize each PMID via `ids.normalize_pmid()` and compute its
   `ids.document_id()` (the stable `pmid:<n>`-derived id used everywhere).
2. `service.py:201` — if the paper isn't already cached (`corpus.has_canonical(pmid)` is false):
   - if `ingest` is on → **Step 2** (`ingest_pmid`, network),
   - if `ingest` is off → record a per-paper error and continue.
3. `service.py:209` — run the full knowledge pass → **Step 3** (`pipeline.extract_document`).
4. After all PMIDs: score topic cohesion (**Step 9**), synthesize conclusions (**Step 10**),
   and assemble the response (**Step 11**).

---

## Step 2 — Ingest: acquire → archive → canonical → persist (needs network)

**File:** `mehungry_extractor/knowledge/ingest.py`
**Function:** `ingest_pmid()` (`ingest.py:110`)

Only runs for PMIDs not already in the corpus. Idempotent: if raw bytes are already cached it
skips the network and calls `normalize_document()` (`ingest.py:151`) instead.

### 2a — Fetch raw XML from NCBI
**File:** `mehungry_extractor/knowledge/acquire.py` — **Function:** `fetch()` (`acquire.py:39`)
- `GET efetch.fcgi?db=pubmed` → PubMed XML bytes.
- `pmc._pmid_to_pmcid()` resolves a PMCID; if the article is in PMC open access, a second
  `efetch.fcgi?db=pmc` → PMC full-text JATS XML bytes.
- Reuses the legacy `mehungry_extractor/pmc.py` plumbing (`EUTILS`, `_params`, API-key handling)
  so there's one source of truth for the endpoint. Returns a `RawSources` dataclass (bytes +
  source URLs + retrieval timestamp). **This is the only step that touches the network.**

### 2b — Archive the raw bytes immutably
**Function:** `CorpusStore.save_raw()` (`corpus.py:75`), called from `ingest.py:139`
- Writes `pubmed.xml`, `pmc.xml`, `abstract.txt` under `data/corpus/<doc>/raw/` and returns
  their SHA-256 checksums. Raw bytes are kept forever → re-runs are offline and reproducible.

### 2c — Build the canonical document
**Function:** `assemble_document()` (`ingest.py:26`) → `canonical.build_document()` (`canonical.py:130`)
- `pubmed.parse()` (`pubmed.py:104`) → `PubMedRecord` (title, authors, abstract, journal, DOI,
  publication date, affiliations, publication types).
- `jats.parse()` (`jats.py:53`) → body `ParsedSection`s from PMC full text (if present).
- Source type is decided: `open_access` (PMC body) > `abstract` (abstract only) > `none`.
- `build_document()` concatenates whitespace-normalized paragraphs into one canonical `text`
  string and records absolute `start_char`/`end_char` offsets for every section, paragraph, and
  sentence. **The offset contract** (`canonical.py` module docstring): `text[start:end]` always
  reproduces the element's text. Sentence spans come from `segment.segment()` (`segment.py:28`).
  This invariant is what all provenance later relies on.

### 2d — Persist
- `CorpusStore.save_metadata()` + `save_canonical()` (`corpus.py:102`, `:110`) write
  `metadata.json` and `canonical/document.json` (deterministic, sorted-key JSON).
- `ingest._persist()` (`ingest.py:101`) → `db.persist_document()` (`db/schema.py:168`) writes the
  document/section/paragraph/sentence rows to SQLite under one `ExtractionRun`
  (`run.build_run()`, `run.py:63`, which captures git commit + tool versions).

---

## Step 3 — The full knowledge pass

**File:** `mehungry_extractor/knowledge/pipeline.py`
**Function:** `extract_document()` (`pipeline.py:102`)

This is exactly what `mehungry extract` runs. Offline, deterministic, idempotent. It loads the
canonical document (corpus first, DB second — `pipeline.py:131`) and the cached raw XML
(`pmc.xml` drives funding, `pubmed.xml` drives affiliations), then runs Steps 4–8 in order and
persists everything under one `ExtractionRun`.

---

## Step 4 — Entity mentions (Phase 2)

**File:** `mehungry_extractor/knowledge/entities.py` — **Function:** `extract()` (`entities.py:135`)
Called at `pipeline.py:156`.

Scans the canonical text **sentence by sentence** (`document.iter_sentences()`):

1. **Dictionary backbone** — `_matcher()` (`entities.py:75`) is a case-insensitive, word-bounded,
   longest-first regex alternation of every vocabulary surface form (`normalize.surface_forms()`).
   `finditer` yields non-overlapping matches; each is normalized via `normalize.normalize()`.
2. **Optional scispaCy** — if the `[ner]` extra + `en_ner_bc5cdr_md` model are installed
   (`model_available()`, `entities.py:104`), Chemical/Disease spans are added **only where the
   dictionary found nothing**, then normalized against the same vocabulary. When absent, this is
   a no-op and results are identical.

Each hit becomes an `EntityMention` with an `EXACT_SPAN` `EvidenceRef`
(`provenance.EvidenceRef.for_span`, `provenance.py:98`). A mention that fails to normalize is kept
with `status="unmatched"` — **never dropped**. Output is sorted by `(start, end, type, surface)`.

> The CLI stage `mehungry analyze` stops here: `entities.analyze_document()` (`entities.py:227`)
> runs just this step and persists mentions.

---

## Step 5 — Observations (Phase 3, audit layer)

**File:** `mehungry_extractor/knowledge/observations.py` — **Function:** `extract()` (`observations.py:63`)
Called at `pipeline.py:157`.

Within each sentence, mentions are scanned in span order and every **consecutive pair** is tested:

- The text **between** the two mentions (`document.text[subj.end:obj.start]`) is matched against
  the ordered relation registry: `relations.match()` (`relations.py:152`). `relations.py` holds
  the priority-ordered rule list (`RULES`, `relations.py:62`) — lexical/regex cues like
  `no_association`, `reduces_risk`, `associated_with`, `causes`, … Each rule carries a stable
  `rule_id` + `version`. First match wins.
- The sentence region up to the object is judged for polarity + certainty:
  `negation.analyze()` (`negation.py:57`) — cue rules for insufficient-evidence, hypothetical,
  negation (flips polarity), and uncertainty.

Each match becomes an `Observation` (subject/object mentions, predicate, polarity, certainty, the
triggering cue text, and a SENTENCE-precision `EvidenceRef`). Observations are kept even when an
endpoint didn't normalize — they just can't become a claim.

---

## Step 6 — Claims (Phase 3, concept layer)

**File:** `mehungry_extractor/knowledge/claims.py` — **Function:** `normalize()` (`claims.py:75`)
Called at `pipeline.py:158`.

Pure function of the observations — no new evidence. Observations that agree on
`(subject_concept, predicate, object_concept, polarity, certainty)` are folded into one `Claim`.
A claim can only form when **both** endpoints normalized to a concept id; observations that don't
qualify are counted in `NormalizeResult.dropped` (surfaced, never silently swallowed). The
`claim_id` is a hash of the canonical key (`_claim_id`, `claims.py:55`) so re-runs reproduce it.
A claim's evidence is the deduplicated union of its observations' `EvidenceRef`s.

---

## Step 7 — Study, funding, affiliations (Phase 4)

Called at `pipeline.py:161–167`.

- **Study characteristics** — `study.classify()` (`study.py:163`): study design (from PubMed
  publication types + text cues), publication year, sample size, country. → `StudyCharacteristic`s.
- **Funding** — `jats.parse_funding()` + `jats.parse_back_matter()` (`jats.py:121`, `:153`) pull
  funding statements from the PMC XML; `funding.extract()` (`funding.py:92`) maps funder names to
  a curated funder vocabulary → `FundingRelationship`s (with a `funder_type`).
- **Affiliations** — `affiliations.extract()` (`affiliations.py:94`) parses the PubMed author list
  + institutions → authors/affiliations.

---

## Step 8 — Evidence assessment (Phase 5)

**File:** `mehungry_extractor/knowledge/assessment.py` — **Function:** `assess()` (`assessment.py:100`)
Facts assembled by `pipeline._build_facts()` (`pipeline.py:58`), called at `pipeline.py:170`.

A **pure, versioned function** (`mehungry_evidence_v1`) of the Phase-4 facts only. Grades four
criteria — `study_design_strength`, `recency`, `sample_size_adequacy`, `funding_independence` —
into an `Assessment` per criterion. It **never mutates the evidence**; results live in the
separate `assessments` table. `unknown` funding is treated as *not* independent (never assume
independence from missing info).

### Persistence (end of Step 3)
`pipeline.py:173–187` writes everything under one `ExtractionRun` via the delete-then-insert
helpers in `db/schema.py`: `persist_entities`, `persist_relations`, `persist_study`,
`persist_funding`, `persist_affiliations`, `persist_assessments`. Idempotent — re-running replaces
a document's rows rather than duplicating them.

`extract_document` returns an `ExtractionSummary` (counts), which the CLI prints.

---

## Step 9 — Topic cohesion / outlier detection (batch only)

**File:** `mehungry_extractor/knowledge/cohesion.py` — **Function:** `detect()` (`cohesion.py:69`)
Called from `service.py:219`.

Back in `analyze_batch`, each successfully-extracted paper's set of normalized concept ids is read
via `service._concept_set()` (`service.py:70`, using `query.list_entities_for_document`). Then
`cohesion.detect()`:

- Builds the **topic core** = concepts appearing in at least `max(2, ceil(core_fraction * N))`
  papers.
- Scores each paper as the fraction of the core it covers; a paper below `outlier_threshold` is an
  **outlier**. Pure set arithmetic — deterministic, no embeddings.
- Nothing is silently dropped: outliers are fully reported (score + which core concepts they lack)
  and only excluded from the synthesized conclusions.

---

## Step 10 — Cross-paper synthesis (batch only)

**File:** `mehungry_extractor/knowledge/synthesis.py` — **Function:** `synthesize()` (`synthesis.py:167`)
Called from `service.py:228` with the **included** (non-outlier) document ids.

Read-side only — reads persisted claims via `query.list_claims_for_document` and groups them by
canonical relation `(subject_concept, predicate, object_concept)`:

- Direction per relation: `SUPPORTED` / `REFUTED` / `CONFLICTING` (both polarities present) /
  `INCONCLUSIVE`. Conflicts are surfaced, never averaged away.
- Annotates each conclusion with supporting/contradicting/neutral papers, certainty spread,
  study-design-strength spread (from the Phase-5 assessments), and a provenance-carrying evidence
  quote (`_first_quote` → `query.find_evidence`, which reconstructs the exact source span).
- Conclusions are ranked most-corroborated-first with a fully deterministic tie-break.
- `_batch_facts()` (`synthesis.py:125`) rolls up study designs, year range, funding independence,
  and source types across the batch.

---

## Step 11 — Assemble and return the response

**File:** `mehungry_extractor/knowledge/api/service.py` (tail of `analyze_batch`, `service.py:231–363`)

- `_paper_facts()` (`service.py:79`) reads per-paper facts (title, source type, year, design,
  sample size, funder types, claim count) in one DB pass.
- Builds a `PaperReport` per requested PMID (`included` / `outlier` / `error`), a `TopicInfo`
  (named core concepts), `OutlierReport`s, `ConclusionModel`s, `BatchFactsModel`, and a `RunInfo`
  (`_run_info`, `service.py:144` — pipeline/ruleset/extractor/schema/synthesis versions + git
  commit + ontology versions, for reproducibility).
- Warnings are collected throughout (e.g. abstract-only papers, missing scispaCy model) and
  de-duplicated.
- Returns `AnalyzeResponse` (`api/models.py:164`). FastAPI serializes it via the declared
  `response_model` and sends the JSON back to the client.

---

## The CLI path, for comparison

`mehungry_extractor/knowledge/stagecli.py` exposes the same engine as independently re-runnable
stages (`main()` → `build_parser()`):

| Command | Function | Runs |
|---|---|---|
| `mehungry ingest` | `_stage_ingest` (`stagecli.py:66`) | Step 2 (`ingest_pmid`) |
| `mehungry analyze` | `_stage_analyze` (`stagecli.py:140`) | Step 4 only (`entities.analyze_document`) |
| `mehungry extract` | `_stage_extract` (`stagecli.py:168`) | Steps 3–8 (`extract_document`) |
| `mehungry audit` | `_stage_audit` (`stagecli.py:195`) | reads back provenance (`audit.render_claim` / `query.explain_claim`) |

The batch API (Steps 9–10, cohesion + synthesis) has **no CLI equivalent** — it only runs through
`/analyze`.

---

## One-line mental model

`PMID → fetch raw XML (acquire) → immutable corpus + canonical text with offsets (ingest) →
entities → observations → claims → study/funding/assessment (extract) → SQLite → [batch: cohesion
→ synthesis] → JSON`, where **every** output row carries an `EvidenceRef` back to an exact source
character span.
