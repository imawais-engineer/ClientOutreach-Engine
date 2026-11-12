"""Discovery layer.

Keyless sources run out of the box: RemoteOK, Remotive and We Work Remotely.
`maps` adds Google Maps businesses through Apify (needs APIFY_TOKEN and costs
credits, so it is never part of the default run). Firecrawl / SocialCrawl
adapters are still pending; exported provider JSON can always be loaded with
`load_raw`.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

USER_AGENT = "ClientOutreach/0.1 (+https://imawais.engineer)"
REMOTEOK_URL = "https://remoteok.com/api"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs?limit=100"
WWR_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"

KEYLESS_SOURCES = ("remoteok", "remotive", "wwr")
MAPS_SOURCES = {"maps", "google_maps", "apify_maps"}
SOURCES = KEYLESS_SOURCES + ("maps",)


class DiscoveryError(RuntimeError):
    """Raised when a source cannot be fetched or parsed."""


def fetch(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/rss+xml, text/xml, */*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # network, DNS, TLS, HTTP errors
        raise DiscoveryError(f"{url}: {exc}") from exc


def strip_html(value: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.replace("&nbsp;", " ").split())


def _text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return value


def parse_remoteok(payload: list[Any]) -> list[dict[str, Any]]:
    leads = []
    for item in payload:
        if not isinstance(item, dict) or "legal" in item:
            continue
        business = item.get("company") or ""
        title = item.get("position") or ""
        if not business or not title:
            continue
        url = item.get("url") or item.get("apply_url")
        leads.append(
            {
                "source": "remoteok",
                "platform": "RemoteOK",
                "business": business,
                "title": title,
                "need": title,
                "description": strip_html(item.get("description") or ""),
                "url": url,
                "application_url": item.get("apply_url") or url,
                "location": item.get("location") or "",
                "published_at": item.get("date"),
                "profile_url": None,
                "dm_link": None,
                "email": None,
                "phone": None,
                "tags": item.get("tags") or [],
            }
        )
    return leads


def parse_remotive(payload: dict[str, Any]) -> list[dict[str, Any]]:
    leads = []
    for job in payload.get("jobs", []):
        business = job.get("company_name") or ""
        title = job.get("title") or ""
        if not business or not title:
            continue
        url = job.get("url")
        leads.append(
            {
                "source": "remotive",
                "platform": "Remotive",
                "business": business,
                "title": title,
                "need": title,
                "description": strip_html(job.get("description") or ""),
                "url": url,
                "application_url": url,
                "location": job.get("candidate_required_location") or "",
                "published_at": job.get("publication_date"),
                "profile_url": None,
                "dm_link": None,
                "email": None,
                "phone": None,
                "tags": job.get("tags") or [],
            }
        )
    return leads


def parse_wwr(xml_bytes: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DiscoveryError(f"WWR RSS parse failed: {exc}") from exc

    leads = []
    for item in root.iter("item"):
        title = _text(item, "title")
        company, _, role = title.partition(": ")
        if not role:
            company, role = "", title
        link = _text(item, "link")
        leads.append(
            {
                "source": "wwr",
                "platform": "We Work Remotely",
                "business": company,
                "title": role,
                "need": role,
                "description": strip_html(_text(item, "description")),
                "url": link,
                "application_url": link,
                "location": _text(item, "region"),
                "published_at": _iso(_text(item, "pubDate")),
                "profile_url": None,
                "dm_link": None,
                "email": None,
                "phone": None,
                "tags": [],
            }
        )
    return leads


def discover(
    sources: list[str] | None = None,
    timeout: int = 30,
    profile: dict[str, Any] | None = None,
    cities: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch every requested source, logging and skipping failures."""
    leads: list[dict[str, Any]] = []
    requested = [s.lower().strip() for s in (sources or list(KEYLESS_SOURCES))]
    if "all" in requested:
        requested = list(SOURCES)
    for name in requested:
        try:
            if name in MAPS_SOURCES:
                from .maps import MapsError
                from .maps import discover as discover_maps

                if not profile:
                    raise DiscoveryError("maps source needs profile.json")
                leads.extend(discover_maps(profile, cities=cities))
            elif name == "remoteok":
                leads.extend(parse_remoteok(json.loads(fetch(REMOTEOK_URL, timeout))))
            elif name == "remotive":
                leads.extend(parse_remotive(json.loads(fetch(REMOTIVE_URL, timeout))))
            elif name == "wwr":
                leads.extend(parse_wwr(fetch(WWR_URL, timeout)))
            else:
                raise DiscoveryError(f"unknown source '{name}'")
        except Exception as exc:  # network, parse, MapsError, missing token
            print(f"[discover] {name}: {exc}", file=sys.stderr)
    return leads


def load_raw(path: str | Path) -> list[dict[str, Any]]:
    """Load raw leads exported from Apify / SocialCrawl or a previous run."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("leads", [])
    if not isinstance(data, list):
        raise DiscoveryError(f"{path}: expected a JSON list or a dict with 'leads'")
    return [lead for lead in data if isinstance(lead, dict)]


def save_raw(leads: list[dict[str, Any]], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(leads, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
