"""Source adapters.

Only sources with a working, permitted data path are wired here. Everything
else is reachable through `--raw <file>` (an export of the provider's JSON), so
adding a provider never changes the qualification layer.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Any, Iterable

from . import spec as spec_mod
from .discover import (
    REMOTEOK_URL,
    REMOTIVE_URL,
    WWR_URL,
    DiscoveryError,
    fetch,
    load_raw,
    parse_remoteok,
    parse_remotive,
    parse_wwr,
)
from .validate import check_fit

AUTOMATION_TERMS = (
    "automation",
    "automate",
    "workflow",
    "integration",
    "api",
    "webhook",
    "n8n",
    "zapier",
    "make.com",
    "ai agent",
    "chatbot",
    "crm",
)
MANUAL_TERMS = (
    "manual",
    "repetitive",
    "recurring",
    "data entry",
    "spreadsheet",
    "copy paste",
    "copy-paste",
    "tickets",
    "inbox",
    "follow up",
    "follow-up",
)


def _log(message: str) -> None:
    print(f"[sources] {message}", file=sys.stderr)


# --- social ---------------------------------------------------------------
# SocialCrawl's cross-platform search replaces the per-platform adapters: one
# query covers Reddit, X, Threads, Instagram, Facebook, LinkedIn and more.
# Each query costs 20 credits, so the list stays short and purposeful.
SOCIALCRAWL_SEARCH = "/v1/search/everywhere"
SOCIAL_LOOKBACK_DAYS = 60
SOCIAL_DEFAULT_QUERIES = (
    "need help automating lead follow up for my small business",
    "hiring someone to automate our manual data entry workflow",
    "looking for someone to build whatsapp automation for my business",
    "we need a crm integration to automate our sales pipeline",
)
# Demand-side markers: the author is asking for the work.
DEMAND_MARKERS = (
    "i need",
    "we need",
    "need help",
    "need an",
    "need a",
    "looking for",
    "looking to hire",
    "hiring",
    "anyone know",
    "any recommendations",
    "recommend a",
    "help me",
    "struggling with",
    "spend hours",
    "wasting hours",
    "frustrated with",
)
# Supply-side and tutorial markers: the author is selling or teaching, not buying.
SUPPLY_MARKERS = (
    "[for hire]",
    "for hire",
    "hire me",
    "i build",
    "we build",
    "i can help",
    "i'll build",
    "dm me",
    "my agency",
    "tutorial",
    "how to",
    "course",
    "free guide",
    "check out my",
    "launching",
    "i created",
)
# The business must name itself in the post; a bare "I need X" has no identity
# and inventing one is exactly what the source spec forbids.
BUSINESS_PATTERNS = (
    re.compile(
        r"\b(?:my|our)\s+(?:business|company|store|shop|agency|firm|brand|startup)\s+"
        r"(?:called|named)\s+([A-Za-z0-9][\w&.\-']*(?:\s+[A-Za-z0-9][\w&.\-']*){0,3})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([A-Z][\w&.\-']*(?:\s+[A-Z][\w&.\-']*){0,3})\s+(?:is|are)\s+(?:a|an|the)\s+"
        r"[\w\s]{3,40}\b(?:business|company|agency|store|shop|firm)\b"
    ),
    re.compile(
        r"\b(?:founder|owner|co-?founder|ceo)\s+(?:of|at)\s+"
        r"([A-Z][\w&.\-']*(?:\s+[A-Z][\w&.\-']*){0,3})"
    ),
)


def _extract_business(text: str) -> str | None:
    """Return the business a post names about itself, or None."""
    for pattern in BUSINESS_PATTERNS:
        match = pattern.search(text)
        if match:
            name = match.group(1).strip(" .,-\u2019'")
            if 2 <= len(name) <= 60:
                return name
    return None


def _social_get(url: str, key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"x-api-key": key, "User-Agent": "ClientOutreach/1.0"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode())


def _social(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Demand-side public posts from SocialCrawl's universal search.

    Keeps only posts where the author asks for automation work AND names the
    business it concerns. Everything else is dropped rather than stored under a
    guessed identity.
    """
    key = os.environ.get("SOCIALCRAWL_API_KEY")
    base = (os.environ.get("SOCIALCRAWL_BASE_URL") or "https://www.socialcrawl.dev").rstrip("/")
    if not key:
        _log("social: SOCIALCRAWL_API_KEY not set")
        return []

    queries = list(spec.get("discovery_queries") or SOCIAL_DEFAULT_QUERIES)
    try:
        cap = int(os.environ.get("SOCIALCRAWL_MAX_QUERIES") or len(queries))
    except ValueError:
        cap = len(queries)
    queries = queries[: max(1, cap)]

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        url = (
            f"{base}{SOCIALCRAWL_SEARCH}?query={urllib.parse.quote(query)}"
            f"&lookback_days={SOCIAL_LOOKBACK_DAYS}"
        )
        try:
            payload = _social_get(url, key)
        except Exception as exc:  # provider failure must not kill the run
            _log(f"social: {query!r}: {exc}")
            continue
        if not payload.get("success"):
            error = payload.get("error") or {}
            _log(f"social: {query!r}: {error.get('message') or 'query failed'}")
            continue

        items = (payload.get("data") or {}).get("items") or []
        kept = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            link = str(item.get("url") or "").strip()
            text = f"{title} {snippet}".strip()
            if not text or not link or link in seen:
                continue
            low = text.lower()
            if any(marker in low for marker in SUPPLY_MARKERS):
                continue
            if not any(marker in low for marker in DEMAND_MARKERS):
                continue
            business = _extract_business(text)
            if not business:
                continue
            seen.add(link)
            kept += 1
            exp = {
                "social_post": title,
                "source_platform": item.get("source"),
                "post_url": link,
                "discovery_query": query,
                "business_identity": "named by the author in the post",
            }
            candidates.append(
                {
                    "business": business,
                    "need": title or snippet[:120],
                    "description": snippet or title,
                    "url": link,
                    "post_url": link,
                    "platform": str(item.get("source") or "social").capitalize(),
                    "evidence_tier": "B",
                    "confirmed_requirement": "yes",
                    "confirmed_buying_intent": "unknown",
                    "evidence_classification": "confirmed_requirement",
                    "details": exp,
                }
            )
        _log(f"social: {query!r}: {len(items)} items, {kept} demand posts naming a business")
    return candidates


def _tier_for(text: str) -> str:
    haystack = text.lower()
    if any(term in haystack for term in AUTOMATION_TERMS):
        return "A"
    if any(term in haystack for term in MANUAL_TERMS):
        return "B"
    return "C"


def _market_slice(spec: dict[str, Any]) -> tuple[set[str], list[tuple[str, dict[str, Any]]]]:
    """Resolve the spec's geography into (allowed country codes, markets to search).

    Raises instead of falling back: an empty market list must never silently
    become 'search where we searched last time'.
    """
    geo = spec.get("geography") or {}
    if str(geo.get("targeting") or "") != "explicit":
        raise DiscoveryError(
            "geography.targeting must be 'explicit'; this source refuses to infer a market"
        )
    markets = geo.get("markets") or {}
    active = [str(c).upper() for c in (geo.get("active_markets") or []) if str(c).strip()]
    if not active:
        raise DiscoveryError(
            "geography.active_markets is empty; no market may be defaulted"
        )
    unknown = [code for code in active if code not in markets]
    if unknown:
        raise DiscoveryError(f"active_markets not defined in geography.markets: {unknown}")
    return set(active), [(code, markets[code]) for code in active]


def _maps(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Google Maps via the existing Apify actor, scoped to explicit target markets."""
    from .maps import item_to_lead, run_actor

    allowed, active_markets = _market_slice(spec)

    terms_by_category = [
        (
            str(category.get("name") or ""),
            [str(t).strip() for t in category.get("terms") or category.get("queries", []) if str(t).strip()],
        )
        for category in spec.get("target_categories", [])
    ]
    terms_by_category = [(name, terms) for name, terms in terms_by_category if terms]
    if not terms_by_category:
        raise DiscoveryError("source spec has no target_categories terms")

    search = spec.get("search") or {}
    per_query = int(search.get("max_results_per_query", 10))
    terms_per_market = int(search.get("terms_per_market") or 0)
    cities_per_market = int(search.get("cities_per_market") or 0)

    candidates: list[dict[str, Any]] = []
    for code, market in active_markets:
        cities = [str(c) for c in market.get("cities") or []]
        if cities_per_market:
            cities = cities[:cities_per_market]
        if not cities:
            raise DiscoveryError(
                f"market {code} has no cities; regional coverage must be declared, not assumed"
            )

        flat: list[tuple[str, str]] = []
        for name, terms in terms_by_category:
            flat.extend((name, term) for term in terms)
        if terms_per_market:
            flat = flat[:terms_per_market]
        queries = [f"{term} {city}" for term, _ in flat for city in cities]

        for item in run_actor(
            queries, per_query=per_query, location=market.get("country"), country=code
        ):
            found = str(item.get("countryCode") or "").upper()
            if found and found not in allowed:
                continue
            lead = item_to_lead(item, str(item.get("searchString") or item.get("city") or ""))
            if not lead.get("business"):
                continue
            matched = next(
                (name for name, term in flat if term.lower() in str(item.get("searchString") or "").lower()),
                "",
            )
            candidates.append(
                {
                    "business": lead["business"],
                    "need": lead["need"],
                    "description": lead.get("description"),
                    "url": lead.get("url"),
                    "post_url": lead.get("post_url"),
                    "website": lead.get("website"),
                    "phone": lead.get("phone"),
                    "email": lead.get("email"),
                    "city": item.get("city") or None,
                    "country": item.get("countryCode") or None,
                    "category": matched or None,
                    "platform": "Google Maps",
                    "tags": lead.get("tags") or [],
                    "evidence_tier": "C",
                    "remote_eligible_pak": str(item.get("countryCode") or "").upper() == "PK",
                    "details": {
                        "target_market": code,
                        "target_market_name": market.get("country"),
                        "market_level": market.get("level") or "country",
                        "discovered_for_market": code,
                        "location_verified_from": "Google Maps listing countryCode field",
                    },
                }
            )
    return candidates


def _job_boards(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Keyless feeds, filtered by the spec's job families and the fit gate."""
    families = [str(f).lower() for f in spec.get("job_families", [])]
    family_terms = {
        token
        for family in families
        for token in family.replace("/", " ").split()
        if len(token) > 3
    }

    feeds = [
        ("remoteok", lambda: parse_remoteok(json.loads(fetch(REMOTEOK_URL)))),
        ("remotive", lambda: parse_remotive(json.loads(fetch(REMOTIVE_URL)))),
        ("wwr", lambda: parse_wwr(fetch(WWR_URL))),
    ]

    candidates: list[dict[str, Any]] = []
    for name, loader in feeds:
        try:
            postings = loader()
        except Exception as exc:  # network or parse failure: skip the feed, keep the run
            _log(f"{name}: {exc}")
            continue
        for posting in postings:
            text = f"{posting.get('title', '')} {posting.get('description', '')}".lower()
            if family_terms and not any(term in text for term in family_terms):
                continue
            ok, _ = check_fit(posting)
            if not ok:
                continue
            candidates.append(
                {
                    "business": posting.get("business"),
                    "need": posting.get("title"),
                    "description": posting.get("description"),
                    "url": posting.get("url"),
                    "application_url": posting.get("application_url"),
                    "post_url": posting.get("url"),
                    "location": posting.get("location"),
                    "platform": "Job Boards",
                    "published_at": posting.get("published_at"),
                    "tags": posting.get("tags") or [],
                    "evidence_tier": _tier_for(text),
                }
            )
    return candidates


def _from_raw(path: str, default_source: str) -> list[dict[str, Any]]:
    raw = load_raw(path)
    candidates = []
    for item in raw:
        candidate = dict(item)
        candidate.setdefault("source", default_source)
        if not candidate.get("platform"):
            candidate["platform"] = candidate["source"]
        candidates.append(candidate)
    return candidates


def gather(
    source_id: str,
    raw_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return candidates for one source family. Never raises on a feed failure."""
    spec = spec_mod.source(source_id)

    if raw_path:
        return _from_raw(raw_path, source_id)

    try:
        if source_id == "google_maps":
            return _maps(spec)
        if source_id == "job_boards":
            return _job_boards(spec)
        if source_id == "social":
            return _social(spec)
    except Exception as exc:
        _log(f"{source_id}: {exc}")
        return []

    _log(
        f"{source_id}: no live adapter configured yet. "
        f"Import a provider export with --raw <file>."
    )
    return []
