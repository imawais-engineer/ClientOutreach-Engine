"""Google Maps lead hunter.

Flow (planned):
  1. build_queries() derives local-business searches from persona.json
     using prompts/03_maps_query_generator.txt.
  2. run() feeds those queries to SerpAPI Google Maps or the Apify
     Google Maps scraper.
  3. Results are normalized to lead dicts: name, address, phone, website,
     rating, review_count, query.
"""

from __future__ import annotations

from typing import Any


def build_queries(persona: dict[str, Any]) -> list[str]:
    """Return Google Maps queries for the persona's target cities/verticals."""
    raise NotImplementedError("wire up prompt 03 and an LLM call")


def run(queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Scrape Maps for the given queries (or generated ones) and return leads."""
    raise NotImplementedError("wire up SerpAPI or the Apify Google Maps actor")
