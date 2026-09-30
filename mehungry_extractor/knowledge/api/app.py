"""FastAPI wiring for the batch analysis API (requires the optional ``[api]`` extra).

Thin by design: it validates the request, resolves a ``(corpus, engine, ingest)`` context via a
FastAPI dependency (overridable in tests so they run offline against a pre-seeded corpus), and
delegates to :func:`.service.analyze_batch`.

Run it with::

    mehungry-api                                             # console script
    uvicorn mehungry_extractor.knowledge.api.app:app --reload
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from .. import SYNTHESIS_VERSION
from ..corpus import CorpusStore
from ..db import get_engine, init_db
from . import service
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DiscoverRequest,
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


def run() -> None:
    """Console-script entry point (``mehungry-api``): serve the app with uvicorn."""
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("MEHUNGRY_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("MEHUNGRY_API_PORT", "8000")),
    )
