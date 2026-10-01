"""FastAPI wiring for the batch analysis API (requires the optional ``[api]`` extra).

Thin by design: it validates the request, resolves a ``(corpus, engine, ingest)`` context via a
FastAPI dependency (overridable in tests so they run offline against a pre-seeded corpus), and
delegates to :func:`.service.analyze_batch`.

Run it with::

    mehungry-api                                             # console script
    uvicorn mehungry_extractor.knowledge.api.app:app --reload
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .. import SYNTHESIS_VERSION
from ..corpus import CorpusStore
from ..db import get_engine, init_db
from . import observe, service
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DiscoverRequest,
    ObserveIngestRequest,
    ObserveStageRequest,
    ObserveSynthesizeRequest,
    OpenRelationResponse,
    PaperOpenRelations,
)

app = FastAPI(
    title="Mehungry deterministic evidence API",
    version=SYNTHESIS_VERSION,
    description="Batch PMID topic analysis over the deterministic, provenance-tracking evidence "
    "engine. No LLM, no inference — it returns the raw per-paper observations, each tracing back "
    "to a source span.",
)


def get_context() -> dict[str, Any]:
    """The shared corpus + DB engine for a request. Overridden in tests for offline runs."""
    engine = get_engine()
    init_db(engine)
    return {"corpus": CorpusStore(), "engine": engine, "ingest": True}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "synthesis_version": SYNTHESIS_VERSION,
        "model_available": service._entities.model_available(),
    }


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest, ctx: dict[str, Any] = Depends(get_context)) -> AnalyzeResponse:
    return service.analyze_batch(
        req.pmids,
        req.options,
        corpus=ctx["corpus"],
        engine=ctx["engine"],
        ingest=ctx["ingest"] and req.options.ingest,
    )


@app.post("/discover/relations", response_model=OpenRelationResponse)
def discover_relations(
    req: DiscoverRequest, ctx: dict[str, Any] = Depends(get_context)
) -> OpenRelationResponse:
    """Phase 11 — flag relation-bearing spans for a batch of PMIDs ("run + review").

    Kept off the trusted ``/analyze`` path: these spans impose no structure and never enter
    claims/synthesis. Requires the optional ``[openrel]`` detector; papers where it is unavailable
    come back as warnings, not a failure.
    """
    return service.discover_relations_batch(
        req.pmids,
        corpus=ctx["corpus"],
        engine=ctx["engine"],
        ingest=ctx["ingest"],
        extract=req.extract,
    )


@app.get("/discover/relations/{pmid}", response_model=PaperOpenRelations)
def get_discovered_relations(
    pmid: str, ctx: dict[str, Any] = Depends(get_context)
) -> PaperOpenRelations:
    """Return the already-persisted relation-bearing spans for one paper (no re-extraction)."""
    return service.get_open_relations(pmid, engine=ctx["engine"])


# =====================================================================================
# Observe surface — inspect the output of EVERY pipeline stage for a PMID.
#
# Purely observational: these routes either run an existing stage entry point or read what a
# stage already persisted (via :mod:`.observe`). They never change extraction behavior.
# =====================================================================================


@app.get("/observe/documents")
def observe_documents(ctx: dict[str, Any] = Depends(get_context)) -> list[dict]:
    """Every ingested paper (for the cached-paper picker)."""
    return observe.list_cached_documents(engine=ctx["engine"])


@app.get("/observe/document/{pmid}")
def observe_document(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> dict:
    """Stage: canonical document (one offset-addressable text + its structure)."""
    try:
        return observe.get_canonical(pmid, corpus=ctx["corpus"], engine=ctx["engine"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/observe/entities/{pmid}")
def observe_entities(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> list[dict]:
    """Stage: entity mentions (spans, concepts, status, restrictive modifiers)."""
    return observe.get_entities(pmid, engine=ctx["engine"])


@app.get("/observe/observations/{pmid}")
def observe_observations(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> list[dict]:
    """Stage: observations (the audit layer — rule-based relations + modality + qualifiers)."""
    return observe.get_observations(pmid, engine=ctx["engine"])


@app.get("/observe/claims/{pmid}")
def observe_claims(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> list[dict]:
    """Stage: claims (the concept layer — observations folded by canonical relation)."""
    return observe.get_claims(pmid, engine=ctx["engine"])


@app.get("/observe/facts/{pmid}")
def observe_facts(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> dict:
    """Stage: paper facts — study characteristics, funding, affiliations, assessments."""
    return observe.get_facts(pmid, engine=ctx["engine"])


@app.get("/observe/open-relations/{pmid}")
def observe_open_relations(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> list[dict]:
    """Opt-in sandbox: persisted relation-bearing spans (Phase 11). Empty if none/not run."""
    return observe.get_open_relations(pmid, engine=ctx["engine"])


@app.get("/observe/deconstruct/{pmid}")
def observe_deconstruct(pmid: str, ctx: dict[str, Any] = Depends(get_context)) -> dict:
    """Sub-pipeline: how each sentence is deconstructed into observations (clauses, dependency
    parse, predicate-head discovery, predicate selection, argument binding)."""
    try:
        return observe.deconstruct(pmid, corpus=ctx["corpus"], engine=ctx["engine"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/observe/provenance/claim/{claim_id}")
def observe_provenance(claim_id: str, ctx: dict[str, Any] = Depends(get_context)) -> dict:
    """Full provenance for one claim: claim → evidence → document → sentence → exact phrase."""
    block = observe.explain_claim(claim_id, engine=ctx["engine"])
    if block is None:
        raise HTTPException(status_code=404, detail=f"no claim {claim_id!r}")
    return block


@app.post("/observe/ingest")
def observe_ingest(
    req: ObserveIngestRequest, ctx: dict[str, Any] = Depends(get_context)
) -> dict:
    """Run the acquisition → canonical stage live for one PMID (network, once)."""
    return observe.run_ingest(
        req.pmid,
        force=req.force,
        corpus=ctx["corpus"],
        engine=ctx["engine"],
        ingest=ctx["ingest"] and req.ingest,
    )


@app.post("/observe/analyze")
def observe_analyze(
    req: ObserveStageRequest, ctx: dict[str, Any] = Depends(get_context)
) -> dict:
    """Run the entity stage live, returning the persisted mentions."""
    try:
        return observe.run_analyze(
            req.pmid, corpus=ctx["corpus"], engine=ctx["engine"], use_model=req.use_model
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/observe/extract")
def observe_extract(
    req: ObserveStageRequest, ctx: dict[str, Any] = Depends(get_context)
) -> dict:
    """Run the full knowledge pass live (observations/claims/facts/assessments)."""
    try:
        return observe.run_extract(
            req.pmid, corpus=ctx["corpus"], engine=ctx["engine"], use_model=req.use_model
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/observe/synthesize")
def observe_synthesize(
    req: ObserveSynthesizeRequest, ctx: dict[str, Any] = Depends(get_context)
) -> dict:
    """Run the batch stage (cohesion + synthesis) over several PMIDs."""
    return observe.synthesize(
        req.pmids,
        options=req.options,
        corpus=ctx["corpus"],
        engine=ctx["engine"],
        ingest=ctx["ingest"] and req.options.ingest,
    )


# Serve the built single-page app (webapp/dist) if it has been built. Guarded so the API still
# boots when the UI is absent. Mounted last so it never shadows the /observe + /analyze routes.
_WEBAPP_DIST = Path(__file__).resolve().parents[3] / "webapp" / "dist"
if _WEBAPP_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=str(_WEBAPP_DIST), html=True), name="webapp")


def run() -> None:
    """Console-script entry point (``mehungry-api``): serve the app with uvicorn."""
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("MEHUNGRY_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("MEHUNGRY_API_PORT", "8000")),
    )
