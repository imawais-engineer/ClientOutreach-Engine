"""Source 10: review existing leads and recommend the next action.

Read-only. It writes a report file and prints it; it never sends a message and
never mutates the dashboard. Anything unverifiable becomes "human review".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE_STATUSES = {
    "contacted",
    "replied",
    "discovery_discussion",
    "proposal_or_pilot",
    "nurture",
}
STOP_STATUSES = {"do_not_contact", "lost", "disqualified", "won"}


def fetch_leads(limit: int = 200) -> list[dict[str, Any]]:
    base = os.environ.get("DASHBOARD_URL")
    secret = os.environ.get("DASHBOARD_API_SECRET")
    if not base or not secret:
        raise RuntimeError("DASHBOARD_URL and DASHBOARD_API_SECRET are required")
    request = urllib.request.Request(
        f"{base.rstrip('/')}/api/leads?status=all&limit={limit}",
        headers={"x-api-secret": secret},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.load(response)
    return [lead for lead in body.get("leads", []) if isinstance(lead, dict)]


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def eligibility(lead: Mapping[str, Any], now: datetime) -> tuple[bool, str]:
    """The seven checks from config/sources/10-followups.json."""
    if lead.get("opt_out"):
        return False, "opt_out"
    status = str(lead.get("status") or "")
    if status in STOP_STATUSES:
        return False, f"status_{status}"
    if status not in ELIGIBLE_STATUSES:
        return False, "not_in_follow_up_stage"
    last = _parse(lead.get("last_contact_at"))
    if last is None:
        return False, "contact_history_unverified"
    if lead.get("hidden"):
        return False, "hidden"
    due = _parse(lead.get("next_follow_up_at"))
    if due is not None and due > now:
        return False, "not_due_yet"
    return True, "eligible"


def review(leads: Iterable[Mapping[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for lead in leads:
        ok, reason = eligibility(lead, now)
        details = lead.get("details") if isinstance(lead.get("details"), dict) else {}
        reply = str(details.get("reply_classification") or "").lower()

        action = "no_contact"
        next_status = lead.get("status")
        review_needed = False

        if reason == "opt_out":
            action, next_status = "suppress", "do_not_contact"
        elif reply == "explicit_opt_out":
            action, next_status = "suppress", "do_not_contact"
        elif reply == "not_interested":
            action, next_status = "close", "lost"
        elif reason == "contact_history_unverified":
            action, review_needed = "review_record", True
        elif reply in {"interested", "requests_more_information", "requests_portfolio_or_examples", "requests_pricing", "requests_proposal", "refers_to_another_decision_maker"}:
            action, next_status = "respond", lead.get("status")
        elif ok:
            action = "follow_up"
        elif reason == "not_due_yet":
            action = "wait"

        rows.append(
            {
                "lead_id": lead.get("id"),
                "business": lead.get("business"),
                "status": lead.get("status"),
                "platform": lead.get("platform"),
                "last_contact_at": lead.get("last_contact_at"),
                "next_follow_up_at": lead.get("next_follow_up_at"),
                "reply_classification": reply or None,
                "weighted_score": lead.get("weighted_score"),
                "eligibility": reason,
                "recommended_action": action,
                "recommended_status": next_status,
                "human_review_required": review_needed,
                "suggested_message": None,
                "note": (
                    "Draft a message that references the last interaction; no automatic sending."
                    if action == "follow_up"
                    else "No outreach."
                ),
            }
        )
    order = {"suppress": 0, "close": 1, "respond": 2, "follow_up": 3, "review_record": 4, "wait": 5, "no_contact": 6}
    return sorted(rows, key=lambda r: order.get(str(r["recommended_action"]), 9))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", help="report path (default output/followups_<date>.json)")
    args = parser.parse_args(argv)

    try:
        leads = fetch_leads(args.limit)
    except Exception as exc:
        print(f"[followups] {exc}", file=sys.stderr)
        return 1

    rows = review(leads)
    target = Path(args.out) if args.out else ROOT / "output" / f"followups_{datetime.now(timezone.utc).date()}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["recommended_action"])] = counts.get(str(row["recommended_action"]), 0) + 1
    print(f"reviewed {len(rows)} leads | " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"report: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
