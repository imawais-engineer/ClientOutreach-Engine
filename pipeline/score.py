"""Scoring: five source-specific dimensions -> weighted total -> band.

Dimension names differ per source (see config/sources/*.json), so scoring works
from the dimension name rather than assuming a fixed list.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import spec as spec_mod

# Dimension families: the *name* of a dimension tells us what evidence to look at.
_EVIDENCE = ("evidence", "signal", "requirements", "request_clarity", "opportunity")
_FIT = ("service_fit", "current_service_fit", "implementation")
_VERIFY = ("verif", "legitimacy")
_CONTACT = ("contact", "decision_maker", "relationship", "application_feasibility")
_COMMERCIAL = ("commercial", "suitability", "attractiveness", "contractor", "partnership")
_PROGRESS = ("progress", "recency", "next_action")
_INTENT = ("buying_intent", "purchase_intent", "demand", "hiring", "intent")
_GEO = ("geographic", "geo_relevance", "location_relevance", "market_relevance")

# Evidence classification -> need_evidence score out of 5. These classes are
# written by the research step; the scoring layer never promotes one on its own.
EVIDENCE_CLASS_SCORE: dict[str, int] = {
    "confirmed_requirement": 5,
    "observed_opportunity_signal": 4,
    "hypothesis_only": 1,
    "no_relevant_opportunity": 0,
}

# need_evidence points contributed by each internal score, for the 0.25 weight
# used by Source 01: score/5 * 0.25 * 100. A score of 2 clears the 10-point gate.
NEED_EVIDENCE_GATE = 10

CAPABILITY_TERMS = (
    "n8n", "make.com", "zapier", "python", "api", "webhook", "ai agent", "mcp",
    "crm", "hubspot", "gohighlevel", "zoho", "lead", "qualification", "routing",
    "whatsapp", "messaging", "chatbot", "support", "triage", "inbox", "email",
    "rag", "knowledge base", "retrieval", "automation", "workflow", "integration",
    "website", "landing page", "backend", "data entry", "reporting", "scheduling",
)


def _dimension_kind(name: str) -> str:
    key = name.lower()
    for family, members in (
        ("intent", _INTENT),
        ("geo", _GEO),
        ("fit", _FIT),
        ("verify", _VERIFY),
        ("contact", _CONTACT),
        ("commercial", _COMMERCIAL),
        ("progress", _PROGRESS),
        ("evidence", _EVIDENCE),
    ):
        if any(token in key for token in members):
            return family
    return "evidence"


def weighted_total(scores: Mapping[str, Any], dims: Mapping[str, Mapping[str, Any]]) -> int:
    """Weighted 0-100 total; unknown dimensions contribute nothing."""
    total = 0.0
    for name, dim in dims.items():
        entry = scores.get(name) or {}
        raw = entry.get("score") if isinstance(entry, Mapping) else entry
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        value = max(0.0, min(5.0, value))
        total += (value / 5.0) * float(dim.get("weight", 0)) * 100.0
    return int(round(total))


def classify(total: int) -> str:
    bands = spec_mod.classification_bands()
    priority = bands.get("priority_prospect", {}).get("min", 80)
    potential = bands.get("potential_prospect", {}).get("min", 60)
    if total >= priority:
        return "priority"
    if total >= potential:
        return "potential"
    return "deprioritize"


def _contact_paths(contact: Mapping[str, Any]) -> list[str]:
    return [k for k, v in contact.items() if isinstance(v, str) and v.strip()]


def _text(record: Mapping[str, Any]) -> str:
    parts = [
        record.get("need"),
        record.get("why_suits"),
        record.get("business"),
        record.get("category"),
        " ".join(record.get("tags") or []),
    ]
    details = record.get("details") or {}
    parts.append(details.get("observed_evidence") if isinstance(details, Mapping) else None)
    return " ".join(str(p) for p in parts if p).lower()


def _capability_hits(record: Mapping[str, Any]) -> int:
    text = _text(record)
    return sum(1 for term in CAPABILITY_TERMS if term in text)


def heuristic_scores(
    record: Mapping[str, Any], source_id: str
) -> dict[str, dict[str, Any]]:
    """Evidence-based fallback scores when a research step provided none."""
    dims = spec_mod.dimensions(source_id)
    contact = record.get("contact") or {}
    details = record.get("details") or {}
    evidence = details.get("evidence") if isinstance(details, Mapping) else None
    evidence = evidence or []
    paths = _contact_paths(contact)
    hits = _capability_hits(record)
    tier = str(record.get("evidence_tier") or record.get("evidence_level") or "").lower()
    confirmed = str(details.get("confirmed_requirement") or "").lower()

    # --- need_evidence -----------------------------------------------------
    # Driven by the recorded evidence classification, never by how many URLs a
    # record happens to carry. An unclassified record gets a conservative
    # fallback that cannot by itself clear the qualification gate.
    ev_cls = str(details.get("evidence_classification") or "").lower()
    if confirmed in {"yes", "true"}:
        evidence_score = 5
    elif ev_cls in EVIDENCE_CLASS_SCORE:
        evidence_score = EVIDENCE_CLASS_SCORE[ev_cls]
    else:
        fallback = 0
        if any(e.get("url") for e in evidence):
            fallback += 1
        if str(details.get("observed_evidence") or "").strip():
            fallback += 1
        if len(evidence) >= 2:
            fallback += 1
        if tier in {"a", "1", "direct"}:
            fallback += 1
        evidence_score = max(1, min(3, fallback)) if fallback else 0

    # --- buying_intent -----------------------------------------------------
    # Zero unless evidence supports it. A plausible opportunity is not demand.
    intent_score = 5 if confirmed in {"yes", "true"} else 0
    if intent_score == 0:
        signal = str(
            details.get("buying_intent_signal")
            or details.get("confirmed_buying_intent")
            or ""
        ).lower()
        if signal in {"yes", "true", "confirmed", "hiring", "active_request", "explicit_request"}:
            intent_score = 4
        elif signal not in {"", "unknown", "none", "none_observed", "not_observed"}:
            intent_score = 2
        if ev_cls == "confirmed_requirement":
            intent_score = max(intent_score, 4)

    # --- geographic relevance ---------------------------------------------
    # Only an explicitly configured, verified market earns points. A missing
    # city or country never raises the total.
    geo_spec = spec_mod.source(source_id).get("geography") or {}
    geo_markets = geo_spec.get("markets") or {}
    geo_active = {str(c).upper() for c in geo_spec.get("active_markets") or [] if str(c).strip()}
    geo_score = 0
    geo_code = str(record.get("country") or "").upper()
    if geo_code and geo_active and geo_code in geo_active:
        geo_score = 3
        geo_city = str(record.get("city") or "").strip().lower()
        if geo_city:
            geo_score = 4
            market = geo_markets.get(geo_code) or {}
            if geo_city in {str(c).strip().lower() for c in market.get("cities") or []}:
                geo_score = 5

    verify_score = 1 if record.get("business") else 0
    verify_score += 1 if contact.get("website") else 0
    verify_score += 1 if (contact.get("phone") or contact.get("email")) else 0
    verify_score += 1 if record.get("city") else 0
    verify_score += 1 if str(details.get("verification_status") or "").lower() == "verified" else 0
    verify_score = max(0, min(5, verify_score))

    fit_score = max(0, min(5, 1 + min(4, hits)))
    contact_score = max(0, min(5, len(paths)))

    commercial = 1
    if record.get("category"):
        commercial += 1
    if record.get("relevant_service"):
        commercial += 1
    if details.get("commercial_suitability") in {"high", "medium"}:
        commercial += 2
    elif details.get("engagement_type") or details.get("opportunity_type"):
        commercial += 1
    if record.get("source") in {"freelance", "referrals", "competitors"}:
        commercial += 1
    commercial = max(0, min(5, commercial))

    progress = 1
    status = str(record.get("status") or "discovered")
    order = {state: i for i, state in enumerate(spec_mod.lifecycle())}
    progress += min(3, order.get(status, 0) // 3)
    if record.get("last_contact_at"):
        progress += 1
    progress = max(0, min(5, progress))

    values = {
        "evidence": evidence_score,
        "fit": fit_score,
        "verify": verify_score,
        "contact": contact_score,
        "commercial": commercial,
        "progress": progress,
        "intent": max(0, min(5, intent_score)),
        "geo": geo_score,
    }
    why = {
        "evidence": (
            f"classification {ev_cls or 'unset'}"
            if ev_cls
            else f"unclassified: {len(evidence)} evidence entries, tier {tier or 'unset'} (capped below gate)"
        ),
        "fit": f"{hits} capability keyword hits",
        "verify": f"{verify_score}/5 verification signals",
        "contact": f"{len(paths)} contact paths",
        "commercial": "category and engagement cues",
        "progress": f"lifecycle {status}",
        "intent": "no citable demand signal" if not intent_score else f"signal: {details.get('buying_intent_signal') or details.get('confirmed_buying_intent')}",
        "geo": (
            f"{geo_code} not in active markets {sorted(geo_active)}"
            if geo_code and geo_active and geo_code not in geo_active
            else ("location not verified" if not geo_score else f"in-market: {geo_code} city={'set' if geo_score >= 4 else 'unset'}")
        ),
    }

    scores: dict[str, dict[str, Any]] = {}
    for name, dim in dims.items():
        kind = _dimension_kind(name)
        scores[name] = {
            "score": values[kind],
            "weight": float(dim.get("weight", 0)),
            "why": why[kind],
        }
    return scores


def score_record(record: dict[str, Any], source_id: str) -> dict[str, Any]:
    """Attach scores, weighted total and classification to a record in place."""
    details = record.setdefault("details", {})
    provided = details.get("scores") if isinstance(details.get("scores"), dict) else None
    scores = provided or heuristic_scores(record, source_id)
    dims = spec_mod.dimensions(source_id)
    total = weighted_total(scores, dims)
    details["scores"] = scores
    record["weighted_score"] = total
    record["classification"] = classify(total)
    record["score"] = max(0, min(10, int(round(total / 10))))
    return record
