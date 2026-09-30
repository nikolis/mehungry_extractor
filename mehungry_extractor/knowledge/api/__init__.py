"""REST API over the deterministic evidence engine (batch PMID topic analysis).

A caller (another part of the Mehungry system) posts a small list of PMIDs — usually about one
topic, possibly with off-topic outliers — to ``POST /analyze`` and receives an organized,
evidence-backed conclusion: per-paper facts, the detected topic core, a report of any outliers
(deterministically detected and excluded from the conclusions, never silently dropped), and the
synthesized cross-paper conclusions with source provenance.

Layering:

* :mod:`.models` — pydantic request/response schemas (only depends on pydantic, a core dep).
* :mod:`.service` — HTTP-agnostic orchestration (``analyze_batch``); directly unit-testable.
* :mod:`.app` — the thin FastAPI wiring (requires the optional ``[api]`` extra).

The API adds no inference: it ingests + extracts via the existing pipeline and aggregates the
persisted, deterministic facts. Same PMIDs + same engine/ruleset versions ⇒ same response.
"""
