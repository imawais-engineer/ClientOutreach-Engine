"""Job board lead hunter.

Flow (planned):
  1. build_queries() derives role searches from persona.json using
     prompts/05_jobs_query_generator.txt.
  2. run() queries RemoteOK, Relomote, We Work Remotely, LinkedIn Jobs and
     Indeed (SerpAPI Google Jobs), excluding Upwork, keeping posts younger
     than config.json:recency_days.
  3. Results are normalized to lead dicts: business, role, need, url,
     posted_at, platform.
"""

from __future__ import annotations

from typing import Any


def build_queries(persona: dict[str, Any]) -> list[str]:
    """Return job board queries for the persona."""
    raise NotImplementedError("wire up prompt 05 and an LLM call")


def run(queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch recent job posts for the given queries and return leads."""
    raise NotImplementedError("wire up RemoteOK/Relomote APIs and SerpAPI Google Jobs")
