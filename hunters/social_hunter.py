"""Social media lead hunter.

Flow (planned):
  1. build_queries() derives phrases from persona.json using
     prompts/04_social_query_generator.txt.
  2. run() searches Instagram, Threads and Facebook Groups via the Apify
     scrapers (or Meta Graph API where access exists), keeping posts from
     the last config.json:recency_days days.
  3. Results are normalized to lead dicts: business, need, platform,
     profile_url, post_url, recency.
"""

from __future__ import annotations

from typing import Any


def build_queries(persona: dict[str, Any]) -> list[str]:
    """Return social search phrases for the persona."""
    raise NotImplementedError("wire up prompt 04 and an LLM call")


def run(queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Scrape recent social posts for the given queries and return leads."""
    raise NotImplementedError("wire up Apify social scrapers or Meta Graph API")
