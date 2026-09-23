"""REST client for the deployed app's token-guarded ``/api/local_ai/*`` endpoints.

Mirrors ``MehungryLocalAi.Client`` (Elixir) — Bearer auth, JSON in/out. Only the two
condition-extraction endpoints are used here:

    GET  /api/local_ai/condition_pending
    POST /api/local_ai/condition_recommendation_candidates
"""

from __future__ import annotations

import os
from typing import Any

import requests


class Client:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int = 30):
        self.base_url = (base_url or os.environ["LOCAL_AI_SERVER_URL"]).rstrip("/")
        self.token = token or os.environ["LOCAL_AI_API_TOKEN"]
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def pending(self, limit: int = 25) -> dict[str, Any]:
        """`{pairs: [{study_id, pmid, condition, states}], total}`."""
        resp = requests.get(
            f"{self.base_url}/api/local_ai/condition_pending",
            params={"limit": limit},
            headers=self._headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def post_candidates(self, study_id: int, condition_id: int, findings: list[dict]) -> dict[str, Any]:
        """Post one pair's findings; the server ledgers the attempt and upserts each."""
        resp = requests.post(
            f"{self.base_url}/api/local_ai/condition_recommendation_candidates",
            json={"study_id": study_id, "condition_id": condition_id, "findings": findings},
            headers=self._headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
