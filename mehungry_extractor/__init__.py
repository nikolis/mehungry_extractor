"""Deterministic, offline biomedical literature ingestion and evidence/provenance engine.

Archives PubMed/PMC sources immutably, builds an offset-addressable canonical document, and
extracts fully-provenanced knowledge (entities, relations, claims, study facts, assessments) with
no LLM, no embeddings, and no randomness — see :mod:`mehungry_extractor.knowledge`.
"""

__version__ = "0.3.0"
