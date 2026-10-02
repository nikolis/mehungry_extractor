# The Stage Observer web app

A small web application for **watching a PubMed paper move through every stage of the
`mehungry_extractor` pipeline** — and inspecting the exact output of each stage, down to the
provenance span and the per-sentence predicate selection.

It is **purely observational**. It never changes extraction behavior: every piece of it either
runs an existing stage entry point or reads what a stage already persisted. Conceptually it sits
*beside* the engine, not inside it.

- Backend (new): `mehungry_extractor/knowledge/api/observe.py` + routes in `api/app.py`.
- Frontend (new): `webapp/` — a Vite + React + TypeScript single-page app.
- HTTP reference: the `/observe/*` section of [`api.md`](api.md).
- The "why" behind each stage it shows: [`concepts.md`](concepts.md).

---

## 1. What it shows (stage → UI)

The app has two modes that share the same screens:

- **Run live** — type a PMID and run `Ingest → Analyze → Extract` (or "Run all"). Ingest hits the
  network once; the rest is offline. A run log reports each stage's counts.
- **Observe cached** — pick any already-processed paper from the corpus/DB and browse its persisted
  stage outputs without running anything.

Most tabs are **per-paper**. Two are **global** (not tied to the selected PMID): **Batch synthesis**
and **Vocabulary**.

| UI tab | Pipeline stage(s) | Engine concept(s) | Source |
|---|---|---|---|
| **Canonical** | Acquisition → canonical document (one offset-addressable text) | 1, 2 | `query.get_document` / `corpus.read_canonical` |
| **Entities** | Entity mentions (+ restrictive modifiers) | 5, 6, 19 | `query.list_entities_for_document`, `list_modifiers_for_document` |
| **Sentence deconstruction** | The entities → observations **sub-pipeline** | 7, 8, 14, 15 | mirrors `observations._parse_bind_sentence` over a fresh parse |
| **Observations** | Observations (audit layer) | 7, 8, 9 | `query.list_observations_for_document` |
| **Claims** | Claims (concept layer) + provenance drill-down | 3, 9 | `query.list_claims_for_document`, `explain_claim`, `find_evidence` |
| **Facts & assessment** | Study / funding / affiliations / assessments | 10, 11 | read directly in `observe.get_facts` |
| **Open relations** | Phase 11 relation-bearing spans (opt-in sandbox) | 18 | `query.list_open_observations_for_document` |
| **Batch synthesis** | Cohesion + cross-paper synthesis | 12, 13, 16, 17 | `service.analyze_batch` + `synthesis.synthesize` |
| **Vocabulary** | The recognisable-entity list + add/edit/remove | 5 (writable overlay) | `/vocab/*` → `api/vocab.py` → `vocab` overlay |

### The Vocabulary tab (global, read **and** write)

Unlike every other tab, this one **mutates** engine state rather than observing it. It lists every
concept the entity recognizer can match — `concept_id`, `canonical_name`, `entity_type`,
`surface_forms`, and an `origin` tag (`builtin` / `overridden` / `custom`) — with search and an
entity-type filter. A form adds a new concept or edits an existing one's surface forms (PUT), and
each row has **Edit** / **Remove** actions. A header badge shows whether the vocabulary is
`pristine` or `customized · <overlay_digest>`.

Edits persist to the writable overlay on top of the checked-in vocabulary and take effect for the
**next extraction** (already-extracted papers are not reprocessed) — so a typical loop is *edit the
vocabulary → re-run Analyze/Extract on a paper → see the new concept recognised in the Entities
tab*. This is the concept behind it; see `docs/concepts.md` (Concept 5, "The writable overlay").

---

## 2. Running it

### Backend

From the repo root:

```bash
uv pip install -e '.[api]'     # FastAPI + uvicorn (the API extra)
mehungry-api                   # serves http://127.0.0.1:8000
```

The server reads/writes the shared corpus + SQLite DB (`MEHUNGRY_CORPUS_DIR`, `MEHUNGRY_DB`).
Entity/observation stages use the scispaCy model, which is a core dependency; the first request
after startup is slower while the model loads.

### Frontend — develop

```bash
cd webapp
npm install
npm run dev                    # http://localhost:5173, proxies /observe,/analyze,/health → :8000
```

### Frontend — build and serve from FastAPI

```bash
cd webapp
npm run build                  # emits webapp/dist
```

Once `webapp/dist` exists, `mehungry-api` auto-mounts it and serves the UI at
**http://127.0.0.1:8000/app/** (the mount is guarded, so the API still boots when the UI is not
built). The Vite `base` is `/app/` for builds and `/` for dev, so asset URLs resolve in both modes.

---

## 3. Backend — the `/observe/*` surface

All routes live in `api/app.py`; the HTTP-agnostic logic (no FastAPI import, unit-testable offline)
lives in `api/observe.py`. Request bodies use models in `api/models.py` (`ObserveIngestRequest`,
`ObserveStageRequest`, `ObserveSynthesizeRequest`). See [`api.md`](api.md) for full request/response
shapes; summary:

**Read persisted stage output**

| Method & path | `observe.py` function |
|---|---|
| `GET /observe/documents` | `list_cached_documents` |
| `GET /observe/document/{pmid}` | `get_canonical` |
| `GET /observe/entities/{pmid}` | `get_entities` |
| `GET /observe/observations/{pmid}` | `get_observations` |
| `GET /observe/claims/{pmid}` | `get_claims` |
| `GET /observe/facts/{pmid}` | `get_facts` |
| `GET /observe/open-relations/{pmid}` | `get_open_relations` |
| `GET /observe/deconstruct/{pmid}` | `deconstruct` (see §4) |
| `GET /observe/provenance/claim/{claim_id}` | `explain_claim` |

**Run a stage live**

| Method & path | `observe.py` function | Reuses |
|---|---|---|
| `POST /observe/ingest` | `run_ingest` | `ingest.ingest_pmid` |
| `POST /observe/analyze` | `run_analyze` | `entities.analyze_document` |
| `POST /observe/extract` | `run_extract` | `pipeline.extract_document` |
| `POST /observe/synthesize` | `synthesize` | `service.analyze_batch` + `synthesis.synthesize` |

Notes:
- **Title filter** — `run_ingest` reports a title-filtered paper as `status: "excluded"` (a
  deliberate topical exclusion), never an error; with `ingest:false` an un-cached paper returns
  `status: "not_ingested"` instead of hitting the network.
- **Synthesis** — `/analyze` returns only the observation layer; `/observe/synthesize` is where the
  cross-paper `conclusions` (plus `derived_conclusions`, batch `facts`) are surfaced. It returns
  `{ "report": <AnalyzeResponse>, "synthesis": {...} }`.
- **Facts** — read inside `observe.get_facts` (not a new `query.py` helper) so no code lands outside
  `api/`, mirroring `service._paper_facts`.

---

## 4. Sentence deconstruction — the entities → observations sub-pipeline

What the pipeline calls "observations" is actually the *output* of a per-sentence sub-pipeline that
`observations.extract` runs and then discards. `GET /observe/deconstruct/{pmid}` makes that visible.

### How it works

- It **rebuilds `EntityMention` objects from the persisted entity rows** (`_mentions_from_rows`) — no
  NER re-run; the parse binder only needs each mention's span/concept/status.
- For every **candidate sentence** (≥2 mentions, or one that produced an observation) it mirrors the
  real parse binder (`observations._parse_bind_sentence`) over a fresh `parse.SentenceParse`, so the
  trace matches what the engine actually did (the spaCy parse is deterministic for a given model).
- It **persists nothing and changes no engine behavior** — it reuses `clauses.segment`,
  `parse.SentenceParse`, and `relations.rule_for_verb`.

### What each sentence returns

- `clauses` — the flat binder's clause segmentation (split on `but`/`whereas`/`however`/`while`/
  `although`/`;`), each with its `marker` and `contrastive` flag. *(Concept 15.)*
- `parse`:
  - `tokens` — the dependency parse: `i`, `text`, `lemma`, `pos`, `dep`, `head`, `start_char`.
  - `predicate_heads` — the ROOT + `conj` + subordinate verbs the binder considers, each with:
    - `verb` / `lemma` / `dep`, `negated`, `is_participial`;
    - `nested` — true when this head nests under the relation whose object is its subject *(Concept 20)*;
    - `object_is_risk` and `object_direction` — the promotions (`risk` → `reduces_risk`/
      `increases_risk`; a direction word → `associated_with_reduced`/`_increased`);
    - `rule` — the selected `RelationRule` (`predicate`, `rule_id`, `version`) or `null`;
    - `subjects` / `objects` — the resolved endpoints (or `unresolved`);
    - `conditions` — prep-phrase conditions that become qualifiers *(Concept 14)*;
    - `note` — a plain-language outcome, e.g. *bound 1×1*, *subject is a non-entity → relation
      dropped; flat fallback suppressed*, *verb not in predicate map → left to the flat fallback*.
  - `clausal_subjects` — entities recovered from a gerund clausal subject.
- `observations` — the observations actually emitted for that sentence, each with its `context`
  binder tag (`binder:parse`, or the cue text for the flat floor), its `parent_observation_id` —
  the earlier same-sentence relation this one nests beneath, or `null` for a top-level relation
  *(Concept 20)* — and its `qualifiers` (`{ qualifier_type, value_concept_id, value_text }`). A
  descriptive `characterized_by` observation's `object_text` is the whole descriptor phrase
  (e.g. *"alterations in the composition and function of the gut microbiota"*), with a `null`
  `object_concept_id` when that phrase is not a vocabulary concept *(Concept 20)*.

The envelope also carries `parse_available`, `sentence_count`, `deconstructed_count`,
`entities_present`, `observations_present`.

### In the UI

The **Sentence deconstruction** tab renders each candidate sentence as a collapsible card showing
mentions, clause segmentation, the predicate-head selection trace, a collapsible dependency-parse
table, and the emitted observations. A filter switches between *sentences that produced
observations*, *dropped* (bound nothing — the instructive cases), and *all candidates*.

The emitted observations are shown as a **hierarchy** rather than a flat list: the UI rebuilds the
parent→children tree from each observation's `parent_observation_id` and nests a relation beneath the
one whose object is its subject *(Concept 20)*, with gutter lines and elbow connectors marking the
levels, and a count of how many are nested under a parent. Where the same nesting decision is visible
at the parse level, the predicate-head trace carries a `↳ nested` tag (from the head's `nested`
flag). A top-level relation is simply an un-indented root. Any `qualifiers` on an observation render
as small typed chips beneath its row. A descriptive `characterized_by` relation shows the whole
descriptor phrase as its object (e.g. *"alterations in the composition and function of the gut
microbiota"*) rather than the deep entity *gut microbiota* — so the row reads as the full
characterization, not a misleading edge to the modifier noun.

As a lighter companion, the **Observations** tab has a "How bound" column surfacing the persisted
`context`.

---

## 5. Frontend structure (`webapp/`)

```
webapp/
  package.json          # deps: react; dev: vite, @vitejs/plugin-react, typescript
  vite.config.ts        # base=/app/ for build, / for dev; proxy to :8000
  tsconfig.json
  index.html
  README.md
  src/
    main.tsx            # React root
    styles.css          # all styling (hand-written; light/neutral theme)
    types.ts            # TypeScript shapes for every /observe/* and /vocab payload
    api.ts              # typed fetch client over /observe/* and /vocab/*
    App.tsx             # shell: sidebar (PMID input, run controls, cached picker, run log) + tabs
    components/
      ui.tsx            # useAsync hook, AsyncView, Tag, Count
      CanonicalPanel.tsx     # canonical text + inline entity-span highlighting
      EntitiesPanel.tsx      # mentions table + status filter
      DeconstructPanel.tsx   # §4 — the sub-pipeline view
      ObservationsPanel.tsx  # audit-layer table (incl. "How bound" context)
      ClaimsPanel.tsx        # claims table + provenance drawer
      FactsPanel.tsx         # study/funding/affiliations/assessments
      OpenRelationsPanel.tsx # Phase 11 spans
      BatchPanel.tsx         # cohesion + synthesis over many PMIDs (global, not per-paper)
      VocabPanel.tsx         # vocabulary management: list + add/edit/remove (global, read+write)
```

Conventions:
- Every panel keys on `${pmid}:${refreshKey}` so running a stage re-fetches its view.
- `useAsync` + `AsyncView` centralize loading/error/empty states.
- `api.ts` uses relative URLs, so the same build works under the dev proxy and when served at `/app`.

---

## 6. Design principles

- **Observation-only.** No `/observe/*` route mutates engine logic. Live runs call the same stage
  entry points the CLI uses (`ingest_pmid`, `analyze_document`, `extract_document`,
  `analyze_batch`, `synthesis.synthesize`); reads use the existing `query.*` functions.
- **Reuse over reinvention.** Serialization reuses the query layer's dicts; the deconstruction trace
  mirrors the real binder rather than forking its decisions into stored data.
- **Provenance stays visible.** The claims drawer shows each evidence ref's `quoted_text` next to the
  `reconstructed_text` re-sliced from the canonical text, so the offset contract is verified in the UI.
- **Contained blast radius.** All backend code is inside `api/`, so the change is a REST-API-layer
  addition (see §8).

---

## 7. Extending it

- **A new per-paper stage view** — add a read function in `observe.py`, a route in `app.py`, a typed
  method in `src/api.ts`, a `*Panel.tsx`, and a tab entry in `App.tsx`.
- **Deeper deconstruction** — `_predicate_trace` in `observe.py` is where the binder mirror lives; add
  fields there (it already exposes risk/direction promotions, control resolution outcomes, etc.).
- **Open-relation discovery live-run** — currently read-only via `get_open_relations`; a
  `POST /observe/discover` wrapping `pipeline.discover_open_relations` would add it (needs the
  `[openrel]` extra).
- **Richer dependency-tree rendering** — `parse.tokens` already carries `head`/`dep`; swap the table
  in `DeconstructPanel` for an SVG arc diagram.

---

## 8. Relationship to the project doc rules

- **RULE 2 (`docs/api.md`)** — the `/observe/*` routes are documented there and must be kept in sync
  with any change to them.
- **RULE 1 (`docs/concepts.md`)** — this app is confined to the REST-API layer plus an SPA client and
  introduces **no** new concept, pipeline behavior, or data model (it exposes existing internal steps),
  so it falls under the documented REST-API-only exclusion. If a future change here touches a
  concept/pipeline/data model, update `concepts.md` too.

---

## 9. Verification

- Backend offline reads against a cached PMID: `GET /observe/{document,entities,observations,claims,
  facts,provenance/claim}` return that stage's data; provenance spans reconstruct (`quoted_text ==
  reconstructed_text`).
- Deconstruct: `GET /observe/deconstruct/{pmid}` returns per-sentence traces (~1s for a ~200-sentence
  paper, reusing the warm parser).
- Live run: `POST /observe/{ingest,analyze,extract}` counts match `mehungry extract --pmid <id>`;
  an off-topic PMID is reported as title-filtered, not an error.
- Batch: `POST /observe/synthesize` returns cohesion core/outliers + synthesis conclusions.
- Frontend: `npm run build` type-checks and builds; `mehungry-api` serves it at `/app/`.
- Regression: `pytest` (the API suite is `tests/test_api.py`).
