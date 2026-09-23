# mehungry-extractor

Offline, **non-deployed** phase-aware recommendation extractor for Mehungry — a Python
sibling of `apps/mehungry_local_ai`. It consumes the same token-guarded
`/api/local_ai/*` REST seam but owns the whole extraction chain for *condition*
recommendations.

Why a separate service (and why Python): disease-phase advice (e.g. "avoid fiber during
an active flare, encourage it in remission") lives only in study prose — PubTator
relations are state-blind — so extraction is an LLM reasoning task best grounded by the
biomedical NLP stack (scispaCy). Keeping it offline means `anthropic`/`scispacy` are
never in the deployed release. Nothing it posts is auto-promoted; every finding lands in
the `/professional/health` review queue.

## Pipeline

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
