"""Qualification layer: candidate -> canonical record -> admit / merge.

Implements config/brain.json -> qualification_layer. Sources only describe what
they observed; this module decides whether it becomes a lead.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import spec as spec_mod
from .score import NEED_EVIDENCE_GATE, score_record

TIER_MAP = {
    "a": "direct",
    "1": "direct",
    "direct": "direct",
    "b": "plausible",
    "2": "plausible",
    "plausible": "plausible",
    "c": "weak",
    "3": "weak",
    "weak": "weak",
    "d": None,
}

LEGAL_SUFFIXES = ("private limited", "pvt ltd", "pvt", "limited", "ltd", "llc", "inc", "gmbh", "corp", "co")


def business_key(name: str) -> str:
    """Normalised identity used to merge one business across all sources."""
    text = str(name or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = " ".join(text.split())
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES + ("and", "the"):
            if text.endswith(" " + suffix):
                text = text[: -(len(suffix) + 1)].strip()
                changed = True
    slug = "-".join(text.split())
    return slug[:120]


def normalize_level(value: Any) -> str | None:
    if value is None:
        return None
    return TIER_MAP.get(str(value).strip().lower())


def _first(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evidence_entry(
    source_id: str,
    url: str | None,
    summary: str,
    tier: Any = None,
    verification: str = "unverified",
    observed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source_id,
        "source_type": "web_page" if url else "record",
        "url": url,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "summary": (summary or "").strip()[:600],
        "classification": normalize_level(tier) or "unconfirmed",
        "tier": str(tier) if tier is not None else None,
        "verification": verification,
    }


def _portfolio(text: str) -> dict[str, str] | None:
    """Pick the closest portfolio example by keyword overlap, never the same one always."""
    examples = spec_mod.brain().get("portfolio_examples", {}).get("examples", [])
    best: tuple[int, dict[str, str]] | None = None
    haystack = text.lower()
    for example in examples:
        words = [w for w in re.split(r"[^a-z0-9]+", str(example.get("service", "")).lower()) if len(w) > 3]
        hits = sum(1 for word in words if word in haystack)
        if hits and (best is None or hits > best[0]):
            best = (hits, example)
    return best[1] if best else None


def _service(text: str) -> str:
    terms = [
        ("whatsapp", "WhatsApp and messaging automation"),
        ("chatbot", "WhatsApp and messaging automation"),
        ("crm", "Lead qualification and CRM automation"),
        ("lead", "Lead qualification and CRM automation"),
        ("support", "AI-assisted customer support"),
        ("inbox", "AI-assisted customer support"),
        ("email", "AI-assisted customer support"),
        ("rag", "RAG and knowledge-base assistants"),
        ("knowledge", "RAG and knowledge-base assistants"),
        ("website", "Website and landing-page builds"),
        ("landing", "Website and landing-page builds"),
        ("n8n", "n8n and Make workflow automation"),
        ("automation", "n8n and Make workflow automation"),
        ("integration", "API and webhook integrations"),
        ("api", "API and webhook integrations"),
    ]
    haystack = text.lower()
    for token, service in terms:
        if token in haystack:
            return service
    return "Automation implementation"


def build_record(
    candidate: Mapping[str, Any],
    source_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Map one source candidate onto the canonical lead record."""
    now = now or datetime.now(timezone.utc)
    spec = spec_mod.source(source_id)

    business = _first(candidate, "business", "company", "name")
    need = _first(candidate, "need", "opportunity", "title") or _first(candidate, "description")[:160]
    description = _first(candidate, "description")
    url = _first(candidate, "listing_url", "url", "post_url", "application_url", "profile_url")
    website = _first(candidate, "website")

    city = _first(candidate, "city")
    country = _first(candidate, "country")
    location = _first(candidate, "location")
    if not city and location:
        city = location.split(",")[0].strip()

    tags = [str(t) for t in (candidate.get("tags") or candidate.get("categories") or []) if t]
    category = _first(candidate, "category") or (tags[0] if tags else "") or "Uncategorized"

    tier = candidate.get("evidence_tier") or candidate.get("tier")
    level = normalize_level(tier)

    observed = description[:600] if description else need
    evidence = list(candidate.get("evidence") or [])
    if url:
        evidence.append(
            evidence_entry(
                source_id,
                url,
                observed,
                tier=tier,
                verification="verified" if website else "unverified",
            )
        )

    text = f"{need} {description} {business} {' '.join(tags)}"
    portfolio = _portfolio(text)
    service = _first(candidate, "relevant_service") or _service(text)

    published = candidate.get("published_at") or candidate.get("posted_at")
    posted_at = published or now.isoformat()

    contact = {
        "profile_url": _first(candidate, "profile_url") or None,
        "dm_link": _first(candidate, "dm_link") or None,
        "email": _first(candidate, "email") or None,
        "phone": _first(candidate, "phone") or None,
        "post_url": _first(candidate, "post_url") or url or None,
        "application_url": _first(candidate, "application_url") or None,
        "website": website or None,
    }

    details: dict[str, Any] = {
        "evidence": evidence,
        "contacts": list(candidate.get("contacts") or []),
        "observed_evidence": observed,
        "confirmed_requirement": candidate.get("confirmed_requirement") or "unknown",
        "confirmed_buying_intent": candidate.get("confirmed_buying_intent") or "unknown",
        "missing_information": candidate.get("missing_information") or None,
        "next_research_step": candidate.get("next_research_step") or None,
        "verification_status": "verified" if website else "unverified",
        **(candidate.get("details") or {}),
    }

    record = {
        "business": business,
        "business_key": business_key(business),
        "category": category,
        "city": city or None,
        "country": country or None,
        "platform": _first(candidate, "platform") or str(spec.get("platform_values", ["Other"])[0]),
        "source": source_id,
        "listing_url": url or None,
        "posted_at": posted_at,
        "need": need,
        "why_suits": (
            f"{service}. Evidence: {observed[:200]}"
            if observed
            else service
        ),
        "relevant_service": service,
        "outreach_angle": None,
        "portfolio_proof": portfolio["url"] if portfolio else None,
        "evidence_tier": str(tier) if tier is not None else None,
        "evidence_level": level,
        "details": details,
        "tags": tags,
        "contact": contact,
        "remote_eligible_pak": bool(candidate.get("remote_eligible_pak")),
        "status": candidate.get("status") or "discovered",
    }
    if portfolio:
        record["outreach_angle"] = (
            f"Reference their {category.lower() if category else 'public'} page and the "
            f"{service.lower()} gap, then cite {portfolio['service']}."
        )
    return score_record(record, source_id)


def admit(record: Mapping[str, Any], source_id: str) -> tuple[bool, str]:
    """Hard gates. Returns (ok, reason-when-rejected).

    These gates decide only whether a candidate may be stored at all. They do
    NOT decide whether it may be reached: use `qualification_tier` for that.
    """
    # Gate 1: verified business identity.
    if not record.get("business") or not record.get("business_key"):
        return False, "no_business"
    if not record.get("need"):
        return False, "no_opportunity"

    details = record.get("details") or {}

    # Gate 2: at least one traceable, relevant evidence URL.
    evidence = details.get("evidence") or []
    if not any(e.get("url") for e in evidence):
        return False, "no_citable_evidence"

    # Gate 3: defensible service match. A category match alone does not qualify.
    if str(details.get("skill_fit") or "").lower() == "poor":
        return False, "capability"
    scores = details.get("scores") if isinstance(details.get("scores"), Mapping) else {}
    fit_entry = scores.get("service_fit") if isinstance(scores, Mapping) else None
    if fit_entry is not None:
        try:
            fit_value = float(fit_entry.get("score") or 0)
        except (TypeError, ValueError):
            fit_value = 0.0
        if fit_value < 2:  # below 10/25 on the 0.25-weighted fit axis
            return False, "no_service_match"

    # Gate 4: the recorded evidence says the available material does not support
    # a defensible service match at all. Negative findings are not leads.
    ev_cls = str(details.get("evidence_classification") or "").lower()
    if ev_cls == "no_relevant_opportunity":
        return False, "no_opportunity"

    # Gate 5: never prepare outreach for an opted-out or suppressed record.
    if record.get("opt_out") or record.get("status") in {"do_not_contact", "disqualified"}:
        return False, "do_not_contact"

    # Gate 6: a weak tier with nothing confirmed is contextual only.
    level = record.get("evidence_level")
    confirmed = str(details.get("confirmed_requirement") or "").lower()
    if level == "weak" and ev_cls not in {
        "confirmed_requirement",
        "observed_opportunity_signal",
    } and confirmed not in {"yes", "true"}:
        return False, "contextual_only"

    if record.get("classification") == "deprioritize":
        return False, "below_band"

    return True, ""


def qualification_tier(record: Mapping[str, Any]) -> str:
    """"qualified" or "research_only" -- may this record be reached, or only studied?

    Research-only records are stored in the research queue, never marked as a
    confirmed need and never handed to outreach. The split is made on the recorded
    evidence classification so a blended score can never override missing evidence.
    """
    details = record.get("details") or {}
    ev_cls = str(details.get("evidence_classification") or "").lower()
    confirmed = str(details.get("confirmed_requirement") or "").lower()

    if confirmed in {"yes", "true"} or ev_cls == "confirmed_requirement":
        return "qualified"
    if ev_cls == "observed_opportunity_signal":
        return "qualified"
    if ev_cls in {"hypothesis_only", "no_relevant_opportunity"}:
        return "research_only"

    # Unclassified: fall back to the computed need_evidence dimension.
    details_map = details.get("scores") if isinstance(details.get("scores"), Mapping) else {}
    entry = details_map.get("need_evidence") if isinstance(details_map, Mapping) else None
    points = 0.0
    if isinstance(entry, Mapping):
        try:
            points = (float(entry.get("score") or 0) / 5.0) * float(entry.get("weight") or 0) * 100.0
        except (TypeError, ValueError):
            points = 0.0
    return "qualified" if points >= NEED_EVIDENCE_GATE else "research_only"


def _registrable_domain(url: str | None) -> str:
    """Host lowercased with the www prefix stripped, for cross-source dedupe."""
    if not url:
        return ""
    text = str(url).strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if text.startswith("www."):
        text = text[4:]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text if "." in text else ""


def _identity_keys(record: Mapping[str, Any]) -> list[str]:
    """Every reliable identifier a record can be merged on, strongest first."""
    keys = []
    business = str(record.get("business_key") or "").strip()
    if business:
        keys.append(f"key:{business}")
    domain = _registrable_domain((record.get("contact") or {}).get("website"))
    if domain:
        keys.append(f"domain:{domain}")
    post = str((record.get("contact") or {}).get("post_url") or "").strip().lower()
    if post:
        keys.append(f"post:{post}")
    return keys


def _merge(winner: dict[str, Any], loser: dict[str, Any]) -> None:
    """Fold `loser` into `winner`, keeping every evidence URL and filling gaps."""
    winner_details = dict(winner.get("details") or {})
    loser_details = loser.get("details") or {}
    seen = {(e.get("source"), e.get("url")) for e in winner_details.get("evidence") or []}
    evidence = list(winner_details.get("evidence") or [])
    for entry in loser_details.get("evidence") or []:
        if (entry.get("source"), entry.get("url")) not in seen:
            evidence.append(entry)
    winner_details["evidence"] = evidence
    winner["details"] = winner_details

    if str(winner.get("category") or "") in {"", "Uncategorized", "Other"}:
        other = str(loser.get("category") or "")
        if other not in {"", "Uncategorized", "Other"}:
            winner["category"] = other
    winner_contact = dict(winner.get("contact") or {})
    for field, value in (loser.get("contact") or {}).items():
        if not winner_contact.get(field) and value:
            winner_contact[field] = value
    winner["contact"] = winner_contact
    for field in ("portfolio_proof", "city", "country", "relevant_service"):
        if not winner.get(field) and loser.get(field):
            winner[field] = loser[field]


def dedupe(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge records describing the same business, keeping every evidence URL.

    Merges on the business key first, then a second pass on the registrable
    domain so branch variants that normalise differently still collapse to one
    lead (seen in the Pakistan run: three 'M.R. Consultant' branch listings).
    """
    ordered = sorted(
        records,
        key=lambda r: r.get("weighted_score") or 0,
        reverse=True,
    )

    merged: dict[str, dict[str, Any]] = {}
    claimed: dict[int, str] = {}

    def take(record: dict[str, Any]) -> bool:
        """Attach `record` to an existing entry when an identity key matches."""
        for key in _identity_keys(record):
            current = merged.get(key)
            if current is None:
                continue
            _merge(current, record)
            return False
        for key in _identity_keys(record):
            merged.setdefault(key, record)
        claimed[id(record)] = list(_identity_keys(record))[0] if _identity_keys(record) else ""
        return True

    kept: list[dict[str, Any]] = []
    for record in ordered:
        if not record.get("business_key"):
            continue
        if take(record):
            kept.append(record)

    # A winner can acquire a domain key it did not hold when it was first seen;
    # fold any later record that now collides onto it.
    final: dict[str, dict[str, Any]] = {}
    for record in kept:
        keys = _identity_keys(record)
        clash = next((final[k] for k in keys if k in final), None)
        if clash is None:
            for k in keys:
                final[k] = record
        else:
            _merge(clash, record)

    unique = {id(r): r for r in final.values()}
    return sorted(unique.values(), key=lambda r: r.get("weighted_score") or 0, reverse=True)
