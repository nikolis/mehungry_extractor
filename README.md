<p align="center"> <img src="logo_medical_extractor.png" alt="mehungry-extractor logo" width="200"> </p>

<p align="center"> <strong>mehungry-extractor Offline, **non-deployed** biomedical literature tooling for Mehungry.</strong> </p>
It has two layers:

1. **Offline knowledge/evidence engine** (`mehungry_extractor.knowledge`, CLI
   `mehungry`) — the source of truth. It archives PubMed/PMC sources immutably, builds an
   offset-addressable canonical document, and preserves complete provenance back to the exact
   source span. **Runs entirely offline (no hosted APIs); every fact traces to its source
   span.** Non-deterministic local techniques (embeddings, similarity, learned scorers) are
   allowed as long as provenance is preserved. See [Offline engine](#offline-engine-mehungry) below.
2. **Legacy LLM recommendation extractor** (CLI `mehungry-extract`) — the original
   phase-aware dietary-recommendation path, a Python sibling of `apps/mehungry_local_ai`.
   Unchanged; documented under [LLM recommendation path](#llm-recommendation-path).

The guiding requirement for layer 1: *if the system tells you a fact, it can show which paper,
and — wherever the text supports it — which section, sentence, or phrase produced it.*
No normalized knowledge object exists without provenance. Determinism is **not** a requirement;
provenance is the guarantee we keep.

---

## Offline engine (`mehungry`)

### Pipeline (Phase 1)

```
PubMed / PMC
  → acquire.py     raw XML fetch (reuses the E-utilities plumbing in pmc.py)
  → corpus.py      immutable, idempotent local archive (never overwrites raw)
  → jats.py / pubmed.py   structured section/paragraph + metadata parsing
  → segment.py     deterministic sentence segmentation (spaCy blank + sentencizer)
  → canonical.py   Document → Section → Paragraph → Sentence, absolute char offsets
  → db/            SQLite persistence (SQLAlchemy)
  + provenance.py  EvidenceRef + precision  ·  run.py  ExtractionRun (versions/git)
```

**Offset contract:** every document has one canonical `text`; every section/paragraph/
sentence offset is absolute, so `document.text[start:end] == obj.text`. This is the
reconstruction invariant provenance relies on, and it is enforced by tests.

### Corpus layout

```
data/corpus/pmid_12345678/
  raw/{pubmed.xml, pmc.xml, abstract.txt}   # immutable; never silently overwritten
  canonical/document.json                    # the offset-addressable canonical doc
  metadata.json                              # bibliographic + acquisition provenance + checksums
  checksums.json                             # SHA-256 of every raw file
```

### Stages (each independently rerunnable)

```bash
mehungry ingest    --pmid 12345678           # network: acquire → archive → canonical → DB
mehungry ingest    --pmids 111,222 --force   # batch; --force refreshes cached raw
mehungry normalize --document pmid:12345678  # offline: rebuild canonical from cached raw
mehungry export    --document pmid:12345678  # print canonical document.json
mehungry list                                # list ingested documents
# mehungry analyze/extract/audit → Phases 2/3/5 (registered stubs)
```

Acquisition and canonicalization are deterministic parsing steps: same source + same pipeline
version ⇒ byte-identical `document.json`. Downstream extraction (`analyze`/`extract`) may use
non-deterministic **local** techniques (embeddings, similarity, learned scorers), as long as every
fact keeps its provenance span. `analyze` (entities), `extract` (relations/claims), and `audit` are
scaffolded stubs for later phases; no recommendation generation and no **hosted-model** calls live
in this engine — the legacy `mehungry-extract` path owns those.

---

## LLM recommendation path

Phase-aware recommendation extractor — a Python sibling of `apps/mehungry_local_ai`. It
consumes the same token-guarded `/api/local_ai/*` REST seam and owns the whole extraction
chain for *condition* recommendations.

Why a separate service (and why Python): disease-phase advice (e.g. "avoid fiber during
an active flare, encourage it in remission") lives only in study prose — PubTator
relations are state-blind — so extraction is an LLM reasoning task best grounded by the
biomedical NLP stack (scispaCy). Keeping it offline means `anthropic`/`scispacy` are
never in the deployed release. Nothing it posts is auto-promoted; every finding lands in
the `/professional/health` review queue.

### Pipeline

```
GET  /api/local_ai/condition_pending           → [{study_id, pmid, condition, states}]
  fetch PubMed/PMC text  (pmc.py — OA full text, abstract fallback)
  optional scispaCy NER grounding  (ner.py)
  LLM extraction → phase-tagged findings  (extract.py, pydantic-validated)
POST /api/local_ai/condition_recommendation_candidates   ← review-gated candidates
```

The server ledgers each `(study, condition)` attempt on POST, so a pair leaves the
pending set once processed — the batch terminates.

## Setup

```bash
cd mehungry_extractor
uv venv && uv pip install -e .
# optional biomedical NER grounding (heavy; GPU/NLP box):
uv pip install -e '.[ner]'
python -m spacy download en_ner_bc5cdr_md
```

## Run

```bash
export LOCAL_AI_SERVER_URL=https://www.m3hungry.com
export LOCAL_AI_API_TOKEN=…            # same :metrics-style shared secret as local_ai
export ANTHROPIC_API_KEY=…
export NCBI_API_KEY=…                  # optional, lifts the E-utilities rate limit
export EXTRACTOR_MODEL=claude-sonnet-5 # optional override

mehungry-extract --limit 50            # or: python -m mehungry_extractor --limit 50
mehungry-extract --limit 10 --dry-run  # extract + print, don't POST
mehungry-extract --no-ner              # skip scispaCy grounding
```

## Layout

Offline engine (`mehungry_extractor/knowledge/`):

| File | Role |
|---|---|
| `ids.py` | Deterministic, position-based IDs (`pmid_x`, `..._sec003_p007_s02`). |
| `acquire.py` | Raw PubMed/PMC XML fetch (reuses `pmc.py` E-utilities plumbing). |
| `corpus.py` | Immutable, idempotent on-disk archive + SHA-256 checksums. |
| `jats.py` / `pubmed.py` | Structured section/paragraph + bibliographic-metadata parsing. |
| `segment.py` | Deterministic sentence segmentation (spaCy blank + sentencizer). |
| `canonical.py` | Canonical `Document/Section/Paragraph/Sentence` + absolute offsets. |
| `provenance.py` | `EvidenceRef` + `ProvenancePrecision`. |
| `run.py` | `ExtractionRun` — git commit + tool versions for reproducibility. |
| `ingest.py` | Orchestration: `ingest_pmid` (network) / `normalize_document` (offline). |
| `db/` | SQLAlchemy tables + persist/load (SQLite). |
| `query.py` | Deterministic query + provenance API (Phase-1 subset; later stubs). |
| `stagecli.py` | The `mehungry` staged CLI. |

Legacy LLM path:

| File | Role |
|---|---|
| `client.py` | REST client for the two `/api/local_ai/*` endpoints (Bearer auth). |
| `pmc.py` | NCBI fetch: PMC OA full text → PubMed abstract fallback. |
| `ner.py` | Optional scispaCy Chemical-span grounding (no-op if not installed). |
| `extract.py` | Anthropic tool-use extraction → pydantic-validated `Findings`. |
| `models.py` | `Finding` / `Findings` schema — the contract with the server. |
| `cli.py` | Batch loop (pull → fetch → extract → post). |

## Tests

```bash
uv pip install pytest
pytest
```
