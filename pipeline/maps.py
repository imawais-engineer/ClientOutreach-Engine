"""Google Maps hunter backed by Apify (runs on the account's included credits).

Why Apify and not SerpAPI: the token already exists, the actor returns phone,
website and review count per place, and the cost is roughly $0.005 per place
(measured: 3 places = $0.0152). No extra key or card.

A Maps listing has no publish date, so each lead carries
`recency_source = "observed_live"`: the business was verified open on the day
of the crawl. The validation layer treats that as weak freshness.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .env import require

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "compass~crawler-google-places"
RESULTS_PER_QUERY = 10
RUN_TIMEOUT_SECONDS = 180

# Category -> the offer we would pitch. Phrased so the lead's `need` carries the
# service keywords the relevance check looks for.
CATEGORY_PITCHES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "clinic",
            "dental",
            "derma",
            "skin",
            "laser",
            "hospital",
            "health",
            "medical",
            "aesthetic",
            "plastic surgery",
        ),
        "Appointment booking + WhatsApp bot for clinic patient intake",
    ),
    (
        ("real estate", "estate_agent", "property", "realtor"),
        "AI agent + lead qualification for property enquiries",
    ),
    (
        ("agency", "advertising", "marketing", "seo", "branding"),
        "CRM + lead qualification automation for inbound leads",
    ),
    (
        ("restaurant", "cafe", "food", "bakery"),
        "WhatsApp ordering + reservation automation",
    ),
    (
        ("salon", "spa", "beauty", "hair"),
        "WhatsApp booking + reminder automation",
    ),
    (
        ("gym", "fitness", "gymnasium"),
        "WhatsApp membership follow-up + reminder automation",
    ),
)

DEFAULT_PITCH = "Chatbot + workflow automation for customer support"


class MapsError(RuntimeError):
    """Raised when a Maps run cannot be started or completed."""


def pitch_for(text: str) -> str:
    """Pick the offer that matches a business category or search query."""
    haystack = str(text or "").lower()
    for needles, pitch in CATEGORY_PITCHES:
        if any(needle in haystack for needle in needles):
            return pitch
    return DEFAULT_PITCH


def build_queries(
    profile: Mapping[str, Any], cities: Iterable[str] | None = None
) -> list[str]:
    """Combine profile hunting categories with target cities.

    There is no built-in city list. The caller must name the cities it wants,
    because the target market is an explicit discovery parameter and must never
    be inferred from the operator's location or a previous run.
    """
    keywords = ((profile.get("hunting_keywords") or {}).get("google_maps")) or []
    city_list = [str(c).strip() for c in (cities or []) if str(c).strip()]
    if not keywords:
        raise MapsError("profile has no hunting_keywords.google_maps terms")
    if not city_list:
        raise MapsError(
            "no target cities supplied; geography must be given explicitly "
            "(see config/sources/*.json -> geography)"
        )
    queries = []
    for keyword in keywords:
        for city in city_list:
            queries.append(f"{keyword} {city}".strip())
    return queries


def _request(url: str, method: str = "GET", payload: dict | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def run_actor(
    queries: list[str],
    token: str | None = None,
    per_query: int = RESULTS_PER_QUERY,
    location: str | None = None,
    country: str | None = None,
) -> list[dict[str, Any]]:
    """Run the Maps actor for every query and return its dataset items.

    `location` and `country` are passed straight through as the actor's
    `locationQuery` / `countryCode` so the search is restricted to a market the
    caller named. Omitting both means the actor searches wherever the query text
    points, which is only acceptable when the query text itself carries the city.
    """
    if not queries:
        return []
    token = token or require("APIFY_TOKEN")
    actor = os.environ.get("APIFY_MAPS_ACTOR") or DEFAULT_ACTOR
    payload: dict[str, Any] = {
        "searchStringsArray": queries,
        "maxCrawledPlacesPerSearch": per_query,
        "language": "en",
        "skipClosedPlaces": True,
    }
    if location:
        payload["locationQuery"] = location
    if country:
        # The actor validates countryCode against lowercase ISO codes.
        payload["countryCode"] = str(country).strip().lower()
    start = _request(
        f"{APIFY_BASE}/acts/{actor}/runs?token={token}",
        method="POST",
        payload=payload,
    )["data"]

    run_id = start["id"]
    deadline = time.time() + RUN_TIMEOUT_SECONDS
    while time.time() < deadline:
        run = _request(f"{APIFY_BASE}/actor-runs/{run_id}?token={token}")["data"]
        if run["status"] in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
            break
        time.sleep(5)
    else:
        raise MapsError(f"actor run {run_id} did not finish in {RUN_TIMEOUT_SECONDS}s")

    if run["status"] != "SUCCEEDED":
        raise MapsError(f"actor run {run_id} ended {run['status']}")

    items = _request(
        f"{APIFY_BASE}/datasets/{run['defaultDatasetId']}/items"
        f"?token={token}&limit={max(50, per_query * len(queries))}"
    )
    return [item for item in items if isinstance(item, dict)]


def item_to_lead(item: Mapping[str, Any], query: str, now: datetime | None = None) -> dict[str, Any]:
    """Map one Apify place item to the pipeline's lead shape."""
    now = now or datetime.now(timezone.utc)
    categories = [str(c) for c in (item.get("categories") or []) if c]
    name = str(item.get("title") or "").strip()
    phone = item.get("phone") or next(iter(item.get("phones") or []), None)
    city = item.get("city") or item.get("neighborhood") or ""
    country = item.get("countryCode") or ""
    address = item.get("address") or ""
    website = item.get("website")
    pitch = pitch_for(" ".join(categories) or query)
    if not website:
        # No site on the listing is a concrete, verifiable gap -- and a website
        # or landing page is the smallest thing that fixes it.
        pitch = "Website + landing page build (no website on their Google listing)"

    return {
        "source": "maps",
        "platform": "Google Maps",
        "business": name,
        "title": " ".join(categories) or query,
        "need": pitch,
        "description": " ".join(
            part for part in [address, city, country, query, ", ".join(categories)] if part
        ),
        "location": ", ".join(part for part in [city, country] if part) or query,
        "phone": phone,
        "website": website,
        "email": item.get("email"),
        "profile_url": None,
        "dm_link": None,
        "post_url": item.get("url"),
        "url": item.get("url"),
        "application_url": None,
        "published_at": None,
        "recency": now.isoformat(),
        "recency_source": "observed_live",
        "rating": item.get("totalScore"),
        "reviews_count": item.get("reviewsCount"),
        "tags": categories,
    }


def discover(
    profile: Mapping[str, Any],
    cities: Iterable[str] | None = None,
    per_query: int = RESULTS_PER_QUERY,
    token: str | None = None,
    now: datetime | None = None,
    location: str | None = None,
    allowed_countries: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Full Maps hunter: queries -> actor run -> lead dicts.

    `allowed_countries` is the explicit market list for this run. When it is
    supplied, a result outside those countries is discarded. When it is empty or
    None, no country filter is applied and the caller is responsible for checking
    each result's location against the market it searched.
    """
    now = now or datetime.now(timezone.utc)
    queries = build_queries(profile, cities)
    allowed = {str(c).upper() for c in (allowed_countries or []) if str(c).strip()}
    items = run_actor(
        queries,
        token=token,
        per_query=per_query,
        location=location,
        country=next(iter(allowed)) if len(allowed) == 1 else None,
    )

    leads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        country = str(item.get("countryCode") or "").upper()
        if allowed and country and country not in allowed:
            # Outside the markets this run explicitly targeted.
            continue
        # Prefer the actor's own search string so the pitch matches this place,
        # not whichever query happened to run first.
        query = str(item.get("searchString") or item.get("city") or "")
        lead = item_to_lead(item, query, now)
        key = str(lead.get("post_url") or lead.get("business") or "").lower()
        if not lead["business"] or key in seen:
            continue
        seen.add(key)
        leads.append(lead)
    return leads
