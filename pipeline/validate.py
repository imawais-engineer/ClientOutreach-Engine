"""Validation layer: remote/Pak eligibility, recency, relevance, contact path.

Every lead must pass all four checks before it can reach cleaning or the site.
The rules are intentionally strict: unknown job-board leads are rejected rather
than guessed.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

BLOCKER_PATTERNS = (
    "us citizen only",
    "u.s. citizen only",
    "w2 only",
    "must be in the us",
    "must reside in the us",
    "united states only",
    "us-only",
    "eu only",
    "security clearance",
    "must be authorized to work in the us",
)

# Roles written for senior specialists are a poor fit: the owner is a student who
# delivers small builds, not a 5-years-experience hire. These patterns reject a
# posting before it can reach the dashboard.
# Word boundaries matter here: "cto" must not match inside "contractor", and
# "intern" must not match "internal".
SENIORITY_RE = re.compile(
    r"\b(?:"
    r"senior|sr\.?|staff\s+(?:engineer|software|developer)|principal|"
    r"lead\s+(?:engineer|developer|software|designer|product)|tech\s+lead|team\s+lead|"
    r"engineering\s+manager|head\s+of\s+\w+|director\s+of\s+\w+|"
    r"vp\s+of\s+\w+|vice\s+president|chief\s+\w+\s+officer|cto|architect"
    r")\b"
)

# "5+ years of experience", "3-5 years experience", "2 plus years industry experience"
EXPERIENCE_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?"
    r"(?:\s+of)?(?:\s+(?:professional|relevant|industry|hands-on|hands on|paid))?\s+experience"
)
MIN_EXPERIENCE_YEARS = 2

DEGREE_RE = re.compile(
    r"\b(?:bachelor'?s?|master'?s?\s+degree|ph\.?d|doctorate|mba|b\.tech|"
    r"degree\s+required)\b"
)

# Signals that a lead is small, part-time or explicitly open to someone starting out.
BEGINNER_SIGNALS = (
    "entry level",
    "entry-level",
    "junior",
    "jr.",
    "no experience",
    "no prior experience",
    "willing to train",
    "training provided",
    "we train",
    "internship",
    "part-time",
    "part time",
    "freelance",
    "gig",
    "one-off",
    "one off",
    "small project",
    "quick task",
    "student",
    "fresher",
    "beginner",
    "apprentice",
    "trainee",
    "no degree required",
)

ACCEPT_PATTERNS = (
    "worldwide",
    "work from anywhere",
    "anywhere in the world",
    "remote",
    "async",
    "contractor",
    "freelance",
    "anywhere",
    "pakistan",
    "global",
)

PAKISTAN_PATTERNS = (
    "pakistan",
    "lahore",
    "karachi",
    "islamabad",
    "rawalpindi",
    "sialkot",
    "gujranwala",
    "faisalabad",
    "multan",
    "peshawar",
    "+92",
)

MAP_SOURCES = {"google_maps", "maps"}
SOCIAL_SOURCES = {
    "social",
    "threads",
    "instagram",
    "facebook",
    "facebook_group",
    "linkedin",
    "linkedin_post",
}

RELEVANCE_KEYWORDS = (
    # AI automation / agents / chatbots
    "chatbot",
    "chat bot",
    "ai agent",
    "agentic",
    "n8n",
    "workflow automation",
    "automation",
    "crm",
    "lead qualification",
    "lead routing",
    "ai automation",
    "appointment booking",
    "lead generation",
    "gohighlevel",
    "make.com",
    "zapier",
    "whatsapp",
    "telegram",
    "meta business suite",
    "live chat",
    "chat support",
    # Websites, landing pages, small backends
    "website",
    "web site",
    "landing page",
    "web design",
    "web development",
    "website fix",
    "website update",
    "bug fix",
    "wordpress",
    "shopify",
    "webflow",
    "framer",
    "woocommerce",
    "frontend",
    "front-end",
    "backend",
    "back end",
    "back-end",
    "rest api",
    "api integration",
    "database",
    "contact form",
    "booking form",
    "booking system",
    "html",
    "css",
    # Virtual assistance, support, inbox and admin
    "virtual assistant",
    "personal assistant",
    "customer support",
    "customer service",
    "customer care",
    "inbox",
    "email management",
    "email triage",
    "email support",
    "support tickets",
    "helpdesk",
    "data entry",
    "admin support",
    "administrative support",
    "scheduling",
    # Social media
    "social media",
    "social media management",
    "content scheduling",
)

CORE_KEYWORDS = (
    "chatbot",
    "chat bot",
    "ai agent",
    "agentic",
    "n8n",
    "workflow automation",
    "crm",
    "lead qualification",
    "lead routing",
    "ai automation",
    "gohighlevel",
    "make.com",
    "zapier",
    "whatsapp",
    "telegram",
    "meta business suite",
    "virtual assistant",
    "customer support",
    "customer service",
    "inbox",
    "data entry",
    "admin support",
    "social media management",
    "website",
    "web site",
    "landing page",
    "web design",
    "web development",
    "wordpress",
    "shopify",
    "webflow",
    "frontend",
    "front-end",
    "backend",
    "back end",
    "back-end",
    "rest api",
    "api integration",
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO 8601, RFC 822 or epoch seconds into an aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def lead_timestamp(lead: Mapping[str, Any]) -> datetime | None:
    """Return the best freshness timestamp attached to a lead."""
    for key in ("published_at", "created_at", "last_post_at", "last_review_at", "date"):
        parsed = parse_timestamp(lead.get(key))
        if parsed:
            return parsed
    return None


def _lead_text(lead: Mapping[str, Any]) -> str:
    parts = [
        lead.get("title"),
        lead.get("need"),
        lead.get("description"),
        lead.get("location"),
        lead.get("business"),
        " ".join(lead.get("tags", []) or []),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def check_remote(lead: Mapping[str, Any]) -> tuple[bool, str]:
    """Check that the lead is remote and a Pakistan-based contractor can apply."""
    text = _lead_text(lead)
    for pattern in BLOCKER_PATTERNS:
        if pattern in text:
            return False, f"blocked by '{pattern}'"

    source = str(lead.get("source", "")).lower()
    if source in MAP_SOURCES and any(p in text for p in PAKISTAN_PATTERNS):
        return True, "Google Maps business located in Pakistan"
    if source in SOCIAL_SOURCES:
        return True, "direct social client, no location lock"

    for pattern in ACCEPT_PATTERNS:
        if pattern in text:
            return True, f"remote signal '{pattern}'"
    return False, "no remote/Pakistan eligibility signal"


def check_recency(
    lead: Mapping[str, Any], now: datetime | None = None, days: int = 7
) -> tuple[bool, str, datetime | None]:
    """Check the lead is no older than `days`."""
    now = now or datetime.now(timezone.utc)
    ts = lead_timestamp(lead)
    if ts is None:
        if lead.get("recency_source") == "observed_live":
            # Google Maps listings have no publish date. The business was
            # verified open on the crawl date, which is weaker than a post but
            # real: it means the listing is live right now.
            return True, f"live listing observed {now.date().isoformat()}", now
        return False, "no verifiable date", None
    if ts > now + timedelta(days=1):
        return False, "timestamp is in the future", ts
    if ts >= now - timedelta(days=days):
        return True, f"posted {ts.date().isoformat()}", ts
    return False, f"older than {days} days ({ts.date().isoformat()})", ts


def relevance_hits(lead: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> list[str]:
    """Return the distinct profile keywords present in the lead text."""
    text = _lead_text(lead)
    keywords = list(RELEVANCE_KEYWORDS)
    if profile:
        for service in profile.get("services", []):
            service_text = str(service).lower().strip()
            if len(service_text) > 3 and service_text not in keywords:
                keywords.append(service_text)
    return [keyword for keyword in keywords if keyword in text]


def check_relevance(
    lead: Mapping[str, Any], profile: Mapping[str, Any] | None = None
) -> tuple[bool, str, int]:
    """Require a core service keyword and a keyword score at or above the floor."""
    text = _lead_text(lead)
    hits = relevance_hits(lead, profile)
    core_hit = any(keyword in text for keyword in CORE_KEYWORDS)
    score = len(hits)
    floor = min_relevance_score()
    if not core_hit:
        return False, "no core service keyword", score
    if score < floor:
        return False, f"keyword score {score} < {floor}", score
    return True, f"keyword score {score}: {', '.join(hits[:4])}", score


def min_relevance_score() -> int:
    """Keyword-score floor, configurable via MIN_RELEVANCE_SCORE (default 2)."""
    try:
        return max(1, int(os.environ.get("MIN_RELEVANCE_SCORE", "2").strip()))
    except (TypeError, ValueError):
        return 2


def _degree_requirement(text: str) -> str | None:
    """Return a degree requirement unless the posting accepts equivalent experience."""
    if "or equivalent" in text:
        return None
    match = DEGREE_RE.search(text)
    return match.group(0) if match else None


def check_fit(lead: Mapping[str, Any]) -> tuple[bool, str]:
    """Reject postings aimed at senior specialists or degree-holding veterans.

    Direct clients (Google Maps businesses, social posts) are not job postings, so
    they skip the seniority rules entirely -- a "Senior Living Clinic" is still a
    client, not a senior role.
    """
    source = str(lead.get("source", "")).lower()
    if source in MAP_SOURCES or source in SOCIAL_SOURCES:
        return True, "direct client, no hiring bar"

    text = _lead_text(lead)
    seniority = SENIORITY_RE.search(text)
    if seniority:
        return False, f"seniority requirement '{seniority.group(0)}'"

    for match in EXPERIENCE_RE.finditer(text):
        years = [int(group) for group in match.groups() if group]
        if years and min(years) >= MIN_EXPERIENCE_YEARS:
            return False, f"asks for {min(years)}+ years of experience"

    degree = _degree_requirement(text)
    if degree:
        return False, f"education requirement '{degree}'"

    for signal in BEGINNER_SIGNALS:
        if signal in text:
            return True, f"beginner-friendly signal '{signal}'"
    return True, "no seniority or experience requirement"


def contact_paths(lead: Mapping[str, Any]) -> list[str]:
    """Return the contact paths present on a lead."""
    found: list[str] = []
    for key in ("profile_url", "dm_link", "application_url", "website"):
        value = str(lead.get(key) or "").strip()
        if value and URL_RE.match(value):
            found.append(key)
    email = str(lead.get("email") or "").strip()
    if email and EMAIL_RE.match(email):
        found.append("email")
    phone = str(lead.get("phone") or "").strip()
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 7:
        found.append("phone")
    return found


def check_contact(lead: Mapping[str, Any]) -> tuple[bool, str, list[str]]:
    """Require at least one usable outreach path."""
    found = contact_paths(lead)
    if not found:
        return False, "no contact pathway", found
    return True, f"contact via {', '.join(found)}", found


def validate_lead(
    lead: Mapping[str, Any],
    profile: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """Run all four checks and attach the validation outcome to the lead."""
    now = now or datetime.now(timezone.utc)
    remote_ok, remote_reason = check_remote(lead)
    recent_ok, recent_reason, ts = check_recency(lead, now, days)
    relevant_ok, relevant_reason, relevance = check_relevance(lead, profile)
    fit_ok, fit_reason = check_fit(lead)
    contact_ok, contact_reason, paths = check_contact(lead)

    checks = {
        "remote": (remote_ok, remote_reason),
        "recency": (recent_ok, recent_reason),
        "relevance": (relevant_ok, relevant_reason),
        "fit": (fit_ok, fit_reason),
        "contact": (contact_ok, contact_reason),
    }
    result = dict(lead)
    result["validation_passed"] = [name for name, (ok, _) in checks.items() if ok]
    result["validation_failed"] = [name for name, (ok, _) in checks.items() if not ok]
    result["validation_reasons"] = {name: reason for name, (_, reason) in checks.items()}
    result["remote_eligible_pak"] = remote_ok
    result["relevance_score"] = relevance
    result["contact_paths"] = paths
    result["passed"] = all(ok for ok, _ in checks.values())
    if ts:
        result["timestamp"] = ts.isoformat()
    return result
