# Batch PMID Analysis API

A small REST service that takes a batch of PubMed IDs (1–200, usually about one topic) and
returns the **raw per-paper observations** extracted from them. It runs on top of the offline
evidence engine (`mehungry_extractor/knowledge/`): **no online integrations** — every observation
traces back to an exact source sentence and pipeline version. The engine is deterministic today (so
identical input + engine versions currently produce the same response), but determinism is no longer
a requirement — local non-deterministic techniques are allowed as long as provenance is preserved.

What it does with a batch:

1. **Ingests** each PMID (fetches from PubMed/PMC and caches it, unless already cached). Ingestion
   applies a **topical title filter**: a paper whose title names no nutrition/diet keyword is
   excluded (see below), never cached, and never extracted.
2. **Extracts** entities → observations → study/funding facts through the existing pipeline.
3. **Detects off-topic outliers** deterministically (by shared-concept overlap) and **reports**
   them — they are flagged, but their observations are still returned, never silently dropped.
4. **Returns**, per paper, every rule-detected observation with its full detail and source span.

> **Note:** `/analyze` returns the *observation* layer only. Cross-paper synthesis (`conclusions` /
> `derived_conclusions` / `facts`) is computed by the engine but is not part of *its* response — it is
> surfaced by the observation endpoint [`POST /observe/synthesize`](#observe-surface--inspect-the-output-of-every-pipeline-stage).

---

## Run it in 3 steps

### 1. Install with the API extra

The web server dependencies are optional, so install the `[api]` extra:

```bash
uv pip install -e '.[api]'
```

> This adds `fastapi` + `uvicorn` on top of the core library. If you only want the CLI/library,
> a plain `uv pip install -e .` is enough — but then the API won't start.

### 2. Start the server

```bash
mehungry-api
```

This serves on `http://127.0.0.1:8000`. To change host/port:

```bash
MEHUNGRY_API_HOST=0.0.0.0 MEHUNGRY_API_PORT=9000 mehungry-api
```

Equivalent, if you prefer uvicorn directly (e.g. with auto-reload during development):

```bash
uvicorn mehungry_extractor.knowledge.api.app:app --reload
```

> **Storage:** the server reads/writes the shared corpus + SQLite DB. By default that is
> `data/corpus/` and `data/mehungry.sqlite`. Point them elsewhere with the `MEHUNGRY_CORPUS_DIR`
> and `MEHUNGRY_DB` environment variables. Ingested papers are cached, so re-analyzing the same
> PMIDs is fast and offline.

### 3. Send a request

```bash
curl -s -X POST http://127.0.0.1:8000/analyze \
  -H 'content-type: application/json' \
  -d '{"pmids": ["32005824", "33515494", "28459824"]}' | python -m json.tool
```

Interactive docs (Swagger UI) are auto-generated at **`http://127.0.0.1:8000/docs`**.

---

## REST API reference

Base URL: `http://<host>:<port>` (default `http://127.0.0.1:8000`).

### `GET /health`

Liveness + version check. No parameters.

**200 response**

```json
{
  "status": "ok",
  "synthesis_version": "0.1.0",
  "model_available": false
}
```

`model_available` is `true` only when the optional scispaCy NER model is installed (it widens
entity recall; the engine works deterministically without it).

---

### `POST /analyze`

Analyze a batch of PMIDs and return the per-paper observations.

#### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `pmids` | `string[]` | **yes** | 1–200 PubMed IDs. Bare (`"32005824"`) or `"pmid:32005824"`. Blanks/duplicates are dropped. |
| `options` | `object` | no | Tunables (see below). Omit for sensible defaults. |

**`options` object**

| Field | Type | Default | Description |
|---|---|---|---|
| `core_fraction` | `float` (0–1] | `0.5` | A concept joins the "topic core" if it appears in at least this fraction of the papers. |
| `outlier_threshold` | `float` [0–1] | `0.2` | A paper covering less than this fraction of the topic core is flagged as an outlier. |
| `ingest` | `bool` | `true` | Fetch PMIDs not already cached from PubMed/PMC (network). If `false`, un-ingested PMIDs are reported as per-paper errors instead. |
| `use_model` | `bool` | `true` | Use the optional scispaCy model as an entity candidate generator, when installed. |

**Example**

```json
{
  "pmids": ["32005824", "33515494", "28459824"],
  "options": { "core_fraction": 0.5, "outlier_threshold": 0.2 }
}
```

#### Response body (`200`)

Top-level shape:

| Field | Type | Description |
|---|---|---|
| `run` | `object` | The versions that produced this response (reproducibility). |
| `requested_pmids` | `string[]` | The PMIDs analyzed (normalized, de-duplicated). |
| `included_pmids` | `string[]` | PMIDs that cohered with the detected topic core. |
| `papers` | `object[]` | Per-paper outcome + facts (**every** requested paper, incl. outliers/errors). |
| `topic` | `object` | The detected shared topic core + the detection settings used. |
| `outliers` | `object[]` | Papers flagged as off-topic, with the reason. |
| `paper_observations` | `object[]` | Per paper, the raw observations extracted from it (see below). |
| `warnings` | `string[]` | Human-readable notes (abstract-only papers, empty topic core, failures, …). |

**`run`**

```
pipeline_version, ruleset_version, extractor_version, schema_version,
synthesis_version, clinical_valence_version, git_commit, python_version,
model_available, ontology_versions
```

**`papers[]`** — one entry per requested PMID:

| Field | Type | Description |
|---|---|---|
| `pmid`, `document_id` | `string` | Input PMID and its canonical document id. |
| `status` | `string` | `included`, `outlier`, or `error`. |
| `title`, `publication_year`, `source_type` | | Basic metadata. `source_type` ∈ `open_access` / `abstract` / `none`. |
| `study_design`, `sample_size` | | Extracted study facts (`unknown`/`null` when not determinable — never guessed). |
| `funder_types`, `funding_independence` | | Funding facts (`unknown` when absent — absence is never read as "independent"). |
| `n_claims`, `concept_count` | `int` | Claims extracted and normalized concepts found. |
| `cohesion_score` | `float`/`null` | Fraction of the topic core the paper covers (`null` when there is no core to score against). |
| `outlier_reason`, `missing_core_concepts` | | Why it was flagged, and which core concepts it lacks. |
| `error` | `string`/`null` | Set when `status == "error"`. |

**`topic`**

| Field | Type | Description |
|---|---|---|
| `core_concepts` | `object[]` | The shared concepts: `{ concept_id, name, paper_count }`. |
| `method` | `string` | Always `normalized_concept_overlap`. |
| `core_fraction`, `outlier_threshold`, `min_core_papers` | | The settings/threshold actually applied. |

**`outliers[]`**: `{ pmid, document_id, reason, cohesion_score, missing_core_concepts }`.
`reason` is `off_topic` (below the threshold) or `no_extractable_concepts` (nothing to score).

**`paper_observations[]`** — one entry per successfully-extracted paper (in request order;
outliers are included, papers that failed to ingest/extract are omitted here but still reported
under `papers` with `status: "error"`):

| Field | Type | Description |
|---|---|---|
| `pmid`, `document_id` | `string` | Input PMID and its canonical document id. |
| `paper_title` | `string`/`null` | The paper's title. |
| `paper_url` | `string` | Public PubMed URL, `https://pubmed.ncbi.nlm.nih.gov/<pmid>/`. |
| `observations_list` | `object[]` | Every rule-detected observation in the paper (may be empty). |

Each **observation** carries the relation plus every detail around it:

| Field | Type | Description |
|---|---|---|
| `observation_id` | `string` | Deterministic id (subject/object spans + predicate). |
| `subject_text`, `object_text` | `string` | The exact surface text of each endpoint. |
| `subject_concept_id`, `object_concept_id` | `string`/`null` | Normalized concept ids; `null` when the mention didn't normalize (the observation is still returned). |
| `subject_name`, `object_name` | `string`/`null` | Canonical names of the resolved concepts. |
| `subject_modifiers`, `object_modifiers` | `object[]` | Restrictive modifiers on each endpoint head (Concept 19): `{ relation, preposition, value_concept_id, value_text, value_type, status }`. `relation` is `localized_in`/`qualified_by`/…; e.g. `dysbiosis` carries `{ relation: "localized_in", preposition: "of", value_text: "gut microbiome", … }`. Empty when the head has none. |
| `subject_label`, `object_label` | `string` | The endpoint rendered *with* its modifiers folded back in — so a bare `Dysbiosis` is served as `Dysbiosis of gut microbiome` and the collapsed display keeps the target the head resolves away. Falls back to the plain name/surface when there are no modifiers. |
| `predicate` | `string` | The relation the rule assigned (e.g. `improves`). |
| `polarity` | `string` | `positive` or `negative` (negation flips the rule's base polarity). |
| `certainty` | `string` | `asserted` or `hedged`. |
| `context` | `string`/`null` | Negation/uncertainty cue + clause marker, for audit. |
| `sentence_id` | `string`/`null` | The sentence the observation was matched in. |
| `rule_id`, `rule_version` | `string` | The relation rule that fired. |
| `qualifiers` | `object[]` | Clause-scoped typed conditions: `{ qualifier_type, value_concept_id, value_text }` (e.g. a `disease_state`). |
| `evidence` | `object[]` | Source span(s): `{ document_id, section_id, paragraph_id, sentence_id, start_char, end_char, quoted_text, precision, extraction_rule, extraction_rule_version }`. `quoted_text` is re-sliced from the canonical text, so it is a verbatim source span. |

#### Error responses

| Status | When |
|---|---|
| `422` | Request validation failed — e.g. empty `pmids`, more than 200, or an out-of-range option. |

Note: a **single PMID failing to ingest/extract does not fail the request**. That paper comes
back with `status: "error"` and an `error` message, and a matching entry appears in `warnings`;
the rest of the batch is still analyzed.

A paper **excluded by the topical title filter** (its title matches none of the nutrition/diet
keywords) is handled the same way: it contributes **no** results — it is absent from
`paper_observations` and `included_pmids` — while still being reported per-paper with
`status: "error"`, an `error` of `TitleFiltered: …`, and a `warnings` note of the form
`<doc_id>: excluded by title filter (<title>).`. The barrier is applied to **every** paper in the
response, **including cache hits**: a paper already in the corpus is re-screened by its archived
title before its results are returned, so an off-topic paper cannot leak through by having been
cached earlier (e.g. ingested directly via the CLI before the filter existed).

---

### `POST /discover/relations`

**Open relation discovery (Phase 11) — a separate sandbox, off the trusted `/analyze` path.**

Flags the **sentences/clauses that assert *some* relationship** and returns them **verbatim**, most
-confident first, for a human to read *before* any schema is imposed. This is the deliberate opposite
of a triple extractor: there is **no** `subject`/`predicate`/`object` field and no predicate
highlighting, because none has been decided — the reviewable payload is just `text` + `score`. These
records never enter claims/synthesis. Detection is a **model** feature; it requires the optional
`[openrel]` extra (`uv pip install -e '.[openrel]'`). Papers where the detector is unavailable come
back as a `warnings` note, not a failure.

This endpoint applies the **same topical title barrier** as `/analyze`: a paper whose title names
no nutrition/diet keyword is excluded from `papers` and reported as a `warnings` note
(`<doc_id>: excluded by title filter (<title>).`) — enforced on cache hits too, not only on
freshly-ingested papers.

#### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `pmids` | `string[]` | **yes** | 1–250 PubMed IDs. Bare or `"pmid:NNNN"`. Blanks/duplicates dropped. |
| `extract` | `bool` | no (default `true`) | Run discovery + persist for each paper ("run + review"). If `false`, only the already-persisted spans are returned (no recomputation). |

#### Response body (`200`)

| Field | Type | Description |
|---|---|---|
| `run` | `object` | Reproducibility metadata (same shape as `/analyze`); the detector name → version appears under `ontology_versions`. |
| `papers` | `object[]` | One entry per PMID that succeeded (failures are reported in `warnings`). |
| `warnings` | `string[]` | Per-paper failures, and a note when the detector isn't installed. |

**`papers[]`**:

| Field | Type | Description |
|---|---|---|
| `pmid`, `document_id` | `string` | Input PMID and its canonical document id. |
| `paper_title` | `string`/`null` | The paper's title. |
| `paper_url` | `string` | Public PubMed URL. |
| `relations` | `object[]` | The flagged relation-bearing spans, **sorted by `score` desc** (most-confident first). |

Each **relation** (`OpenRelationDetail`):

| Field | Type | Description |
|---|---|---|
| `open_observation_id` | `string` | Deterministic id (`hash(document, span, detector)`). |
| `text` | `string` | The relation-bearing sentence/clause, **verbatim** — the thing you read. |
| `sentence_id`, `clause_index` | | Owning sentence; `clause_index` set only when localized to a clause. |
| `start_char`, `end_char` | `int` | Exact offsets into the canonical text (`text == document.text[start:end]`). |
| `score` | `float` | The detector's confidence. |
| `detector_name`, `detector_version` | `string` | How it was produced (auditable, non-reproducible). |
| `entities` | `object[]` | **Optional** context: known entities appearing in the span (`{ surface_text, entity_type, concept_id, start_char, end_char }`), reusing the Concept-6 mentions already persisted for the paper. Imposes no relation structure; empty if the paper's entities weren't extracted. |
| `evidence` | `object[]` | The single `EXACT_SPAN` source span: `{ document_id, sentence_id, start_char, end_char, quoted_text, precision }`. |

There is intentionally **no** `subject`/`predicate`/`object` and no highlighted predicate.

---

### `GET /discover/relations/{pmid}`

Return the already-persisted relation-bearing spans for **one** paper (no re-extraction), so a
reviewer can re-open results without recomputing. Response is a single `papers[]` entry
(`PaperOpenRelations`, shape as above). A paper with no persisted spans comes back with an empty
`relations` list — never a 404.

---

## Observe surface — inspect the output of every pipeline stage

The `/observe/*` routes expose the engine's **intermediate stage outputs** for a single paper (and
the batch stage over several), so you can watch a PMID move through the pipeline one stage at a time.
They are **purely observational**: each route either runs an existing stage entry point or reads what
a stage already persisted — none of them change extraction behavior. They power the bundled web UI
(see "Web UI" below) but are usable directly.

All of these honor the same storage/env config (`MEHUNGRY_CORPUS_DIR`, `MEHUNGRY_DB`) and the same
title-filter discipline as `/analyze`.

### Read a stage's persisted output

| Method & path | Returns |
|---|---|
| `GET /observe/documents` | Every ingested paper (`document_id`, `pmid`, `title`, `source_type`, …) — the paper picker. |
| `GET /observe/document/{pmid}` | The **canonical document**: the single offset-addressable `text`, its `sections`/`paragraphs`/`sentences` (with absolute char offsets), `metadata`, and a `summary` (section/sentence/char counts). `404` if the paper was never ingested. |
| `GET /observe/entities/{pmid}` | **Entity mentions**: each with `surface_text`, `entity_type`, `concept_id`, `status` (normalized/ambiguous/unmatched), `start_char`/`end_char`, and restrictive `modifiers`. |
| `GET /observe/observations/{pmid}` | **Observations** (audit layer): subject/predicate/object, `polarity`/`certainty`, `qualifiers`, the `rule_id`/`rule_version` that fired, and inline `evidence_refs`. |
| `GET /observe/claims/{pmid}` | **Claims** (concept layer): grouped `(subject, predicate, object, polarity, certainty)` with `qualifiers`. |
| `GET /observe/facts/{pmid}` | **Paper facts**: `study_characteristics`, `funding`, `affiliations` (authors + institutions), and `assessments`. |
| `GET /observe/open-relations/{pmid}` | The opt-in **Phase 11** relation-bearing spans (most-confident first). Empty list if none were discovered. |
| `GET /observe/provenance/claim/{claim_id}` | The **full provenance block** for one claim (`explain_claim`): claim → `evidence` refs (each with `quoted_text` and a `reconstructed_text` re-sliced from the canonical text) → `document` → `run` → `study`/`funding`/`assessments`/`observations`. `404` for an unknown claim id. |

### `GET /observe/deconstruct/{pmid}` — the entities → observations sub-pipeline

Exposes **how each sentence becomes observations** — the sub-pipeline that `observations.extract` runs
internally and otherwise discards. It rebuilds mentions from the persisted entity rows (no NER re-run)
and, for every candidate sentence (≥2 mentions, or one that produced an observation), returns:

- `clauses` — the flat binder's clause segmentation (`but`/`whereas`/`;` …), each with its `marker`/`contrastive` flag.
- `parse` — the dependency parse `tokens` (`text`, `lemma`, `pos`, `dep`, `head`), the discovered
  `predicate_heads`, and any `clausal_subjects`. Each predicate head records the verb/lemma, the
  selected `rule` (predicate + `rule_id`/`version`) or `null`, the `object_is_risk`/`object_direction`
  promotions, `negated`, the resolved `subjects`/`objects` (or `unresolved`), condition phrases, and a
  plain-language `note` explaining the binding outcome (bound / dropped / deferred to the flat binder).
- `observations` — the observations actually emitted for that sentence (with their `context` binder tag).

Response envelope also carries `parse_available`, `sentence_count`, and `deconstructed_count`. It
mirrors the real parse binder (`observations._parse_bind_sentence`) over a fresh parse — it changes no
engine behavior and persists nothing.

### Run a stage live

| Method & path | Body | Does |
|---|---|---|
| `POST /observe/ingest` | `{ "pmid": "…", "force": false, "ingest": true }` | Acquisition → canonical stage (network, once). A title-filtered paper returns `status: "excluded"` (not an error); with `ingest:false` an un-cached paper returns `status: "not_ingested"`. |
| `POST /observe/analyze` | `{ "pmid": "…", "use_model": true }` | Entity stage; returns the counts plus the persisted `entities` (same shape as the GET). |
| `POST /observe/extract` | `{ "pmid": "…", "use_model": true }` | The full knowledge pass (observations/claims/facts/assessments); returns the `ExtractionSummary` counts. |
| `POST /observe/synthesize` | `{ "pmids": ["…"], "options": {…} }` | The **batch stage**: reuses `/analyze`'s ingest + extract + cohesion, then additionally runs cross-paper **synthesis**. Returns `{ "report": <AnalyzeResponse>, "synthesis": { conclusions, derived_conclusions, facts, warnings } }` — this is where the synthesized `conclusions` (which `/analyze` omits) are surfaced. |

### Web UI

A single-page app under [`webapp/`](../webapp) (Vite + React + TypeScript) consumes these routes to
let you walk a PMID through every stage, drill into a claim's provenance, and run batch synthesis.
Build it (`cd webapp && npm install && npm run build`) and `mehungry-api` serves it at
**`http://127.0.0.1:8000/app/`**; or run it in dev with `npm run dev` (proxying to the API on
`:8000`). See [`webapp/README.md`](../webapp/README.md).

---

## How outlier detection works (today: pure set arithmetic)

Each paper contributes the set of **normalized concept ids** its text resolved to. The batch's
**topic core** is the set of concepts shared by at least `max(2, ceil(core_fraction × N))` papers.
A paper's `cohesion_score` is the fraction of that core it covers; if the score is below
`outlier_threshold`, the paper is flagged `off_topic` (but its observations are still returned and
it is reported in full). A paper with no normalized concepts can't be scored and is reported as
`no_extractable_concepts`. If the batch shares nothing at all, no paper is excluded and a
`warnings` note explains why.

Tune `core_fraction` up to demand a tighter shared topic, or `outlier_threshold` up to exclude
more aggressively.

---

## Notes

- **Provenance & reproducibility:** the API adds no inference of its own — it only ingests,
  extracts, filters, and aggregates provenanced facts. Today the underlying engine is deterministic,
  so identical input + engine versions ⇒ identical output (the `run` block tells you which versions
  produced a given response). Determinism is not guaranteed going forward; the standing guarantee is
  that every returned fact traces to a source span.
- **Vocabulary scope:** the built-in concept vocabulary is nutrition-focused (Mehungry's domain),
  so general biomedical abstracts may yield few concepts. The `warnings` block flags this;
  installing the optional `[ner]` scispaCy model broadens entity recall.
- **First call is slower:** an uncached PMID is fetched over the network on first use, then cached
  immutably — subsequent analyses of the same PMID are fast and offline.
