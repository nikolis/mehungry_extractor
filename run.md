# Running `mehungry_extractor`

This project ships **three separate command-line programs** plus a **REST API**, all installed
from the same package. This document explains how to set it up and what every command does, with
copy-pasteable examples.

- [`mehungry`](#1-mehungry--the-deterministic-evidence-engine) — the deterministic, offline
  evidence engine (ingest papers, extract claims with full provenance). **Start here.**
- [`mehungry-api`](#2-mehungry-api--the-batch-analysis-rest-api) — a REST server that analyzes a
  batch of PMIDs and returns synthesized conclusions.
- [`mehungry-extract`](#3-mehungry-extract--the-legacy-llm-recommendation-batch) — the legacy LLM
  batch job (unrelated to the deterministic engine).
- [`pytest`](#4-pytest--the-test-suite) — the test suite.

---

## 0. One-time setup

You need Python ≥ 3.10 (the project is developed on 3.12) and [`uv`](https://github.com/astral-sh/uv)
(a fast installer). Then:

```bash
# create the isolated environment (holds this project's Python + libraries)
uv venv

# activate it — after this, `python`, `mehungry`, etc. mean THIS project's versions
source .venv/bin/activate

# install the project. Pick the variant you need:
uv pip install -e .            # core library + `mehungry` CLI
uv pip install -e '.[api]'     # ...also the REST API (fastapi + uvicorn)
uv pip install -e '.[ner]'     # ...also optional scispaCy NER grounding (heavy)
uv pip install -e '.[api,ner]' # ...both extras
```

`-e` means **editable**: edits to the source are live, no reinstall needed.

> **Where does data live?** The deterministic engine caches raw papers and a SQLite DB on disk:
> - Corpus (raw + canonical papers): `data/corpus/` — override with `MEHUNGRY_CORPUS_DIR` or `--corpus`.
> - SQLite database: `data/mehungry.sqlite` — override with `MEHUNGRY_DB` or `--db`.
>
> Papers are cached immutably, so re-running is fast and works offline after the first fetch.

---

## 1. `mehungry` — the deterministic evidence engine

The main tool. It works in **stages**, each independently re-runnable, reading/writing the same
corpus + database so stages compose without hidden state. The normal order is:

```
ingest → analyze → extract → audit          (with export / list / normalize as helpers)
```

General form:

```bash
mehungry <stage> [selectors] [options]
```

**Selectors** (how you point at papers — most stages accept these):

| Selector | Meaning |
|---|---|
| `--pmid 32005824` | one PMID |
| `--document pmid:32005824` | alias for `--pmid` (also accepts the `pmid:` prefix) |
| `--pmids 111,222,333` | several PMIDs, comma-separated |
| `--file pmids.txt` | a file with one PMID per line |

**Common options** (most stages):

| Option | Meaning |
|---|---|
| `--corpus <dir>` | corpus root dir (default `$MEHUNGRY_CORPUS_DIR` or `data/corpus`) |
| `--db <path>` | SQLite path (default `$MEHUNGRY_DB` or `data/mehungry.sqlite`) |
| `--no-persist` | do the work but don't write to the database |

### `mehungry ingest` — fetch + archive + build canonical *(needs network)*

Downloads a paper from PubMed/PMC, archives the raw bytes immutably, builds the canonical
offset-addressable document, and stores it.

```bash
mehungry ingest --pmid 32005824
mehungry ingest --pmids 32005824,33515494 --force   # re-download even if cached
```

- Extra option `--force` refreshes the cached raw sources.
- One failing PMID does **not** abort the batch — it's reported and the rest continue.
- Prints, per paper: source type, section/sentence/character counts.

### `mehungry normalize` — rebuild canonical from cache *(offline)*

Rebuilds the canonical document from the **already-cached** raw bytes — no network. Use it to
re-derive documents after a pipeline change. Byte-for-byte reproducible.

```bash
mehungry normalize --document pmid:32005824
```

### `mehungry export` — print the canonical document JSON *(offline)*

Writes the canonical `document.json` (the parsed, offset-addressable text) to stdout.

```bash
mehungry export --document pmid:32005824 | head
```

### `mehungry list` — list ingested documents *(offline)*

Prints all documents currently in the database as JSON (id, PMID, PMCID, DOI, title, …).

```bash
mehungry list
```

### `mehungry analyze` — extract + normalize entities (Phase 2) *(offline)*

Finds biomedical entity mentions in the canonical text and normalizes them to controlled
concepts. Persists the mentions.

```bash
mehungry analyze --document pmid:32005824
```

Prints, per paper: number of mentions found and how many normalized to a concept.

### `mehungry extract` — relations/claims + study/funding + assessments (Phases 3–5) *(offline)*

The full knowledge pass: entities → observations → normalized **claims**, plus study
characteristics, funding, affiliations, and a derived evidence assessment. Persists everything.

```bash
mehungry extract --document pmid:32005824
```

Prints, per paper: counts of mentions, observations, claims (and dropped observations), study
facts, funders, and assessments.

### `mehungry audit` — print a claim's full provenance *(offline)*

The payoff of the whole engine: show *exactly* where a fact came from.

```bash
# print one claim's complete provenance block (source sentence, rule, versions, funding, hash…)
mehungry audit --claim <claim_id>

# or list a paper's claim ids first, so you know what to audit
mehungry audit --document pmid:32005824
```

### A complete first run

```bash
mehungry ingest  --pmid 32005824      # fetch it (network)
mehungry analyze --pmid 32005824      # entities
mehungry extract --pmid 32005824      # claims + facts + assessments
mehungry audit   --pmid 32005824      # list claim ids
mehungry audit   --claim <id>         # inspect one claim's provenance
```

---

## 2. `mehungry-api` — the batch analysis REST API

A web server that wraps the engine above: give it a small list of PMIDs and it ingests + extracts
them, filters off-topic outliers, and returns synthesized, evidence-backed conclusions. **Requires
the `[api]` extra** (`uv pip install -e '.[api]'`).

### Start the server

```bash
mehungry-api
```

Serves on `http://127.0.0.1:8000`. Environment variables to change the binding:

| Variable | Default | Meaning |
|---|---|---|
| `MEHUNGRY_API_HOST` | `127.0.0.1` | interface to bind |
| `MEHUNGRY_API_PORT` | `8000` | port to listen on |

```bash
MEHUNGRY_API_HOST=0.0.0.0 MEHUNGRY_API_PORT=9000 mehungry-api
```

Equivalent, with live-reload during development:

```bash
uvicorn mehungry_extractor.knowledge.api.app:app --reload
```

The server runs until you press **Ctrl-C**.

### Use it

```bash
# health check
curl -s http://127.0.0.1:8000/health

# analyze a batch of PMIDs
curl -s -X POST http://127.0.0.1:8000/analyze \
  -H 'content-type: application/json' \
  -d '{"pmids": ["32005824", "33515494", "28459824"]}' | python -m json.tool
```

Interactive API docs (auto-generated) are at **`http://127.0.0.1:8000/docs`**.
Full endpoint/field reference lives in [`docs/api.md`](docs/api.md).

> The API reads/writes the same corpus + DB as the CLI, so anything you already `ingest`ed is
> reused. It caches new PMIDs on first fetch.

---

## 3. `mehungry-extract` — the legacy LLM recommendation batch

The **original** pipeline, unrelated to the deterministic engine: it pulls pending study/condition
pairs from an internal service, fetches text, runs LLM-based extraction, and posts candidate
findings back. Kept working for backward compatibility.

```bash
mehungry-extract --limit 50
python -m mehungry_extractor --limit 50 --no-ner   # equivalent module form
```

| Option | Default | Meaning |
|---|---|---|
| `--limit N` | `100` | max study/condition pairs to process |
| `--no-ner` | off | skip the optional biomedical NER grounding step |
| `--dry-run` | off | run extraction but do **not** POST candidate findings |

**Requires environment variables** (it talks to external services + an LLM):
`LOCAL_AI_SERVER_URL`, `LOCAL_AI_API_TOKEN`, `ANTHROPIC_API_KEY`
(optional: `NCBI_API_KEY`, `EXTRACTOR_MODEL`).

> If you're working on the deterministic engine or the REST API, you can ignore this command.

---

## 4. `pytest` — the test suite

All tests are deterministic and offline (no network, no model required).

```bash
pytest                       # run everything
pytest -q                    # quieter output
pytest tests/test_api.py     # one file
pytest tests/test_synthesis.py tests/test_cohesion.py   # a few files
pytest -k outlier            # only tests whose name matches "outlier"
```

The API tests are skipped automatically if the `[api]` extra isn't installed.

---

## Quick reference

| I want to… | Command |
|---|---|
| Set up the project | `uv venv && source .venv/bin/activate && uv pip install -e '.[api]'` |
| Fetch a paper | `mehungry ingest --pmid <PMID>` |
| Extract claims + facts from it | `mehungry extract --pmid <PMID>` |
| See where a claim came from | `mehungry audit --claim <claim_id>` |
| List what's in the DB | `mehungry list` |
| Start the REST API | `mehungry-api` (then POST to `/analyze`) |
| Run the legacy LLM batch | `mehungry-extract --limit 50` |
| Run the tests | `pytest` |
