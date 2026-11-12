"""Meta Business Suite / WhatsApp lead hunter.

Flow (planned):
  1. Detect businesses asking for Meta Business Suite managers, WhatsApp
     bots, Instagram DM automation or Facebook page management.
  2. run() searches the social sources and site signals already covered by
     the social hunter, but keeps only Meta/WhatsApp-specific intent.
  3. Results are normalized to lead dicts: business, need, platform,
     profile_url, post_url, recency.
"""

from __future__ import annotations

from typing import Any


def build_queries(persona: dict[str, Any]) -> list[str]:
    """Return Meta Business Suite / WhatsApp intent phrases for the persona."""
    raise NotImplementedError("derive Meta/WhatsApp phrases from persona.json")


def run(queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Collect recent Meta/WhatsApp intent leads."""
    raise NotImplementedError("wire up Apify/Meta sources filtered to Meta intent")
