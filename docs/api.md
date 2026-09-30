# Batch PMID Analysis API

A small REST service that takes a batch of PubMed IDs (1–200, usually about one topic) and
returns the **raw per-paper observations** extracted from them. It runs on top of the offline
evidence engine (`mehungry_extractor/knowledge/`): **no online integrations** — every observation
traces back to an exact source sentence and pipeline version. The engine is deterministic today (so
identical input + engine versions currently produce the same response), but determinism is no longer
a requirement — local non-deterministic techniques are allowed as long as provenance is preserved.

What it does with a batch:

1. **Ingests** each PMID (fetches from PubMed/PMC and caches it, unless already cached).
2. **Extracts** entities → observations → study/funding facts through the existing pipeline.
3. **Detects off-topic outliers** deterministically (by shared-concept overlap) and **reports**
   them — they are flagged, but their observations are still returned, never silently dropped.
4. **Returns**, per paper, every rule-detected observation with its full detail and source span.

> **Note:** this endpoint currently returns the *observation* layer only. Cross-paper synthesis
> (`conclusions` / `derived_conclusions` / `facts`) is computed by the engine but is not part of
> the response for now.

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
