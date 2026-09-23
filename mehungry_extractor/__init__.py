"""Offline phase-aware recommendation extractor for Mehungry.

A non-deployed sibling of ``apps/mehungry_local_ai`` — it consumes the same
token-guarded ``/api/local_ai/*`` REST seam but owns the whole extraction chain for
*condition* recommendations: it pulls ``(study, condition)`` pairs discovered by the
reverse crawl, fetches PubMed/PMC text, extracts **phase-tagged** dietary
recommendations with an LLM (optionally grounded by biomedical NER), and posts
review-gated candidates back. Nothing is ever auto-promoted.
"""

__version__ = "0.1.0"
