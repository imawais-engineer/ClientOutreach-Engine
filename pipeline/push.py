"""Push canonical records to the dashboard.

The API upserts on business_key: a business discovered by three sources becomes
one lead with three evidence entries.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterable, Mapping

TIMEOUT_SECONDS = 20


class PushError(RuntimeError):
    """Raised when the dashboard rejects a record."""


def dashboard_configured() -> bool:
    return bool(os.environ.get("DASHBOARD_URL") and os.environ.get("DASHBOARD_API_SECRET"))


def to_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Map a canonical record onto the dashboard's POST /api/leads contract."""
    details = dict(record.get("details") or {})
    return {
        "business": record.get("business"),
        "business_key": record.get("business_key"),
        "category": record.get("category"),
        "city": record.get("city"),
        "country": record.get("country"),
        "platform": record.get("platform"),
        "source": record.get("source"),
        "need": record.get("need"),
        "why_suits": record.get("why_suits"),
        "relevant_service": record.get("relevant_service"),
        "contact": record.get("contact") or {},
        "posted_at": record.get("posted_at"),
        "remote_eligible_pak": bool(record.get("remote_eligible_pak")),
        "weighted_score": record.get("weighted_score"),
        "classification": record.get("classification"),
        "evidence_level": record.get("evidence_level"),
        "pitch_angle": record.get("outreach_angle") or record.get("pitch_angle"),
        "case_study": record.get("portfolio_proof") or record.get("case_study"),
        "status": record.get("status") or "discovered",
        "details": details,
    }


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    base = os.environ["DASHBOARD_URL"].rstrip("/")
    secret = os.environ["DASHBOARD_API_SECRET"]
    request = urllib.request.Request(
        f"{base}/api/leads",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "x-api-secret": secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        raise PushError(f"HTTP {exc.code}: {body}") from exc
    except Exception as exc:  # network, DNS, timeout
        raise PushError(str(exc)) from exc


def push_leads(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Push every record, counting created / merged / failed."""
    created = merged = failed = 0
    errors: list[str] = []
    for record in records:
        payload = to_payload(record)
        try:
            result = _post(payload)
        except PushError as exc:
            failed += 1
            errors.append(f"{payload.get('business')}: {exc}")
            continue
        if result.get("action") == "merged":
            merged += 1
        else:
            created += 1

    return {
        "created": created,
        "merged": merged,
        "duplicates": merged,
        "failed": failed,
        "errors": errors,
        "endpoint": f"{os.environ.get('DASHBOARD_URL', '').rstrip('/')}/api/leads",
    }
