# Mehungry stage observer (web UI)

A small Vite + React + TypeScript single-page app for **observing the output of every
pipeline stage** of `mehungry_extractor`. It is purely observational — it only calls the
`/observe/*` HTTP surface (see [`../docs/api.md`](../docs/api.md)) and never changes
extraction behavior.

## What you can see

- **Canonical** — the single offset-addressable document text (with entity spans highlighted).
- **Entities** — every mention with its span, concept, normalization status, and modifiers.
- **Observations** — the audit layer (rule-based relations + modality + qualifiers).
- **Claims** — the concept layer, with a click-through **provenance drawer** (claim → evidence
  → document → sentence → exact phrase → rule → run), self-verifying against the canonical text.
- **Facts & assessment** — study characteristics, funding, affiliations, assessment grades.
- **Open relations** — the opt-in Phase 11 sandbox (shown if any spans were discovered).
- **Batch synthesis** — cohesion (topic core + outliers) and cross-paper conclusions.

You can **run stages live** for a PMID (Ingest → Analyze → Extract, or "Run all"), or
**observe cached** papers already in the corpus/DB without re-running anything.

## Prerequisites

Run the backend (from the repo root):

```bash
uv pip install -e '.[api]'
mehungry-api                       # serves http://127.0.0.1:8000
```

## Develop

```bash
cd webapp
npm install
npm run dev                        # http://localhost:5173 (proxies /observe,/analyze to :8000)
```

## Build (served by FastAPI)

```bash
npm run build                      # emits webapp/dist
```

Once built, `mehungry-api` serves the UI at **http://127.0.0.1:8000/app/** (the app
mounts `webapp/dist` automatically when it exists).
