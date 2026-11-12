"""Cleaning layer: dedupe, normalize, enrich the pitch and score leads."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .validate import BEGINNER_SIGNALS, contact_paths, lead_timestamp, parse_timestamp

LEGAL_SUFFIXES = (
    "private limited",
    "pvt ltd",
    "pvt",
    "limited",
    "ltd",
    "llc",
    "inc",
    "gmbh",
    "corp",
    "co",
)

# Ordered by how specific the deliverable is: the first rule that matches the
# stated need wins, and industry keywords only decide when no deliverable is named.
ANGLE_RULES = (
    (("chatbot", "chat bot", "whatsapp", "meta business", "dm "), "WhatsApp"),
    (
        (
            "customer support",
            "customer service",
            "customer care",
            "support ticket",
            "support tickets",
            "ticket",
            "helpdesk",
        ),
        "Customer Support",
    ),
    (
        (
            "inbox",
            "email management",
            "email triage",
            "email support",
            "data entry",
            "admin support",
            "administrative support",
            "virtual assistant",
            "scheduling",
        ),
        "Inbox",
    ),
    (("crm", "lead qualification", "lead routing", "lead generation"), "Lead Qualification"),
    (("n8n", "workflow automation", "automation", "zapier", "make.com", "reporting", "slack"), "n8n"),
    (
        (
            "landing page",
            "website",
            "web site",
            "web design",
            "web development",
            "wordpress",
            "shopify",
            "webflow",
            "framer",
            "bug fix",
        ),
        "Website",
    ),
    (("backend", "back end", "back-end", "rest api", "api integration", "database"), "Backend"),
    (("social media", "instagram", "reel", "content", "community"), "Social Media"),
    (("real estate", "property", "realtor", "clinic", "dental", "appointment"), "Lead Qualification"),
)


def normalize_business(name: str) -> str:
    """Lowercase a business name and strip punctuation and legal suffixes."""
    text = str(name or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = " ".join(text.split())
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            if text.endswith(" " + suffix):
                text = text[: -(len(suffix) + 1)].strip()
                changed = True
    return text


def names_match(left: str, right: str) -> bool:
    """Treat a name and its branch/location variant as the same business."""
    a, b = normalize_business(left), normalize_business(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 6 and (longer.startswith(shorter) or longer.endswith(shorter)):
        return True
    return False


def _freshness(lead: Mapping[str, Any]) -> datetime:
    return lead_timestamp(lead) or datetime.min.replace(tzinfo=timezone.utc)


def _merge_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    group = sorted(group, key=_freshness, reverse=True)
    merged = dict(group[0])
    sources = {str(lead.get("source")) for lead in group if lead.get("source")}
    passed = set(merged.get("validation_passed") or [])

    for lead in group[1:]:
        passed.update(lead.get("validation_passed") or [])
        for key, value in lead.items():
            if key in {"validation_passed", "sources"}:
                continue
            if merged.get(key) in (None, "", [], {}):
                merged[key] = value

    merged["sources"] = sorted(sources)
    merged["validation_passed"] = [
        check
        for check in ("remote", "recency", "relevance", "fit", "contact")
        if check in passed
    ]
    return merged


def dedupe(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge leads whose business names match, keeping the freshest record."""
    groups: list[list[dict[str, Any]]] = []
    for lead in sorted(leads, key=_freshness, reverse=True):
        business = str(lead.get("business") or "")
        for group in groups:
            if names_match(str(group[0].get("business") or ""), business):
                group.append(lead)
                break
        else:
            groups.append([lead])
    return [_merge_group(group) for group in groups]


def recency_label(lead: Mapping[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    ts = lead_timestamp(lead)
    if not ts:
        return str(lead.get("recency") or "unknown")
    age = (now - ts).days
    if age <= 0:
        relative = "today"
    elif age == 1:
        relative = "yesterday"
    else:
        relative = f"{age} days ago"
    return f"{ts.date().isoformat()} - {relative}"


def _first_angle(text: str, angles: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the angle for the first ordered rule that matches `text`."""
    for keywords, hint in ANGLE_RULES:
        if not any(keyword in text for keyword in keywords):
            continue
        for angle in angles:
            if hint.lower() in str(angle.get("service", "")).lower():
                return angle
    return None


def select_angle(
    lead: Mapping[str, Any], profile: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Pick the outreach angle whose service best matches the lead.

    The stated need wins over the business name: "Digital Services Links Shopify"
    is an ecommerce client, not a Shopify build, unless the post asks for one.
    """
    angles = profile.get("outreach_angles") or []
    stated = " ".join(
        str(lead.get(key) or "") for key in ("title", "need", "description")
    ).lower()
    angle = _first_angle(stated, angles)
    if angle is None:
        angle = _first_angle(str(lead.get("business") or "").lower(), angles)
    if angle is not None:
        return angle
    for angle in angles:
        if "n8n" in str(angle.get("service", "")).lower():
            return angle
    return angles[0] if angles else None


def why_suits(lead: Mapping[str, Any], profile: Mapping[str, Any]) -> tuple[str, str]:
    """Return (why_suits, service_to_pitch) grounded in a real case study."""
    angle = select_angle(lead, profile)
    if not angle:
        return "Automation build matched to the lead's stated need.", ""
    service = str(angle.get("service") or "")
    hook = str(angle.get("hook") or "").strip()
    proof = str(angle.get("case_study_to_link") or "").strip()
    text = f"{hook} Proof: {proof}" if proof else hook
    return text, service


def score_lead(lead: Mapping[str, Any], now: datetime | None = None) -> int:
    """Score a validated lead from 0 to 10."""
    now = now or datetime.now(timezone.utc)
    score = 0
    if lead.get("remote_eligible_pak"):
        score += 2

    ts = lead_timestamp(lead)
    if ts and lead.get("recency_source") == "observed_live":
        # A live listing is fresh but not intent-signal fresh.
        score += 1
    elif ts:
        age_days = (now - ts).total_seconds() / 86400
        if age_days <= 2:
            score += 2
        elif age_days <= 7:
            score += 1

    paths = lead.get("contact_paths") or contact_paths(lead)
    if "email" in paths or "phone" in paths:
        score += 2
    elif paths:
        score += 1

    relevance = int(lead.get("relevance_score") or 0)
    if relevance >= 3:
        score += 2
    elif relevance == 2:
        score += 1

    source = str(lead.get("source") or "").lower()
    if source in {"social", "threads", "instagram", "facebook", "facebook_group", "linkedin", "linkedin_post"}:
        score += 1
    elif source in {"google_maps", "maps"} and lead.get("remote_eligible_pak"):
        score += 1

    text = " ".join(
        str(lead.get(key) or "") for key in ("title", "need", "description")
    ).lower()
    if any(
        keyword in text
        for keyword in (
            "chatbot",
            "ai agent",
            "n8n",
            "meta business suite",
            "virtual assistant",
            "social media manager",
        )
    ):
        score += 1
    # Small, part-time or explicitly beginner-friendly work is the best fit.
    if any(signal in text for signal in BEGINNER_SIGNALS):
        score += 1
    return min(score, 10)


def to_output(
    lead: Mapping[str, Any], profile: Mapping[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Convert a cleaned lead into the shape the website consumes."""
    pitch, service = why_suits(lead, profile)
    contact = {
        "profile_url": lead.get("profile_url"),
        "dm_link": lead.get("dm_link"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "application_url": lead.get("application_url"),
        "website": lead.get("website"),
        "post_url": lead.get("post_url"),
    }
    return {
        "business": lead.get("business"),
        "need": lead.get("need") or lead.get("title") or "",
        "why_suits": pitch,
        "service_to_pitch": service,
        "contact": contact,
        "recency": recency_label(lead, now),
        "platform": lead.get("platform") or lead.get("source") or "",
        "remote_eligible_pak": bool(lead.get("remote_eligible_pak")),
        "validation_passed": lead.get("validation_passed") or [],
        "score": score_lead(lead, now),
        "source_url": (
            lead.get("url")
            or lead.get("application_url")
            or lead.get("profile_url")
            or lead.get("dm_link")
        ),
    }


def clean_leads(
    leads: list[dict[str, Any]],
    profile: Mapping[str, Any],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Filter to validated leads, dedupe, build output records and rank them."""
    now = now or datetime.now(timezone.utc)
    validated = [
        lead for lead in leads if lead.get("passed") and str(lead.get("business") or "").strip()
    ]
    merged = dedupe(validated)
    output = [to_output(lead, profile, now) for lead in merged]
    floor = datetime.min.replace(tzinfo=timezone.utc)
    output.sort(
        key=lambda item: (
            item["score"],
            parse_timestamp(item["recency"].split(" ")[0]) or floor,
        ),
        reverse=True,
    )
    return output
