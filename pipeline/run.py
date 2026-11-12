"""Run one source family: discover -> qualify -> score -> output -> push.

Examples:
    python -m pipeline.run --source google_maps --dry-run
    python -m pipeline.run --source job_boards
    python -m pipeline.run --source websites --raw raw_leads/provider_export.json

There is no lead cap. Volume is bounded by the admission gate in
config/brain.json, which drops anything without a citable signal.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import spec as spec_mod
from .env import load_env
from .push import dashboard_configured, push_leads
from .qualify import admit, build_record, dedupe, qualification_tier
from .sources import gather

ROOT = Path(__file__).resolve().parents[1]


def run(
    source_id: str,
    days: int = 7,
    raw_path: str | Path | None = None,
    now: datetime | None = None,
    out_path: str | Path | None = None,
    quiet: bool = False,
    push: bool = True,
) -> dict[str, Any]:
    load_env()  # a raw import never touches a provider, so load keys explicitly
    now = now or datetime.now(timezone.utc)
    spec = spec_mod.source(source_id)
    brain = spec_mod.brain()

    candidates = gather(source_id, raw_path=str(raw_path) if raw_path else None)
    records: list[dict[str, Any]] = []
    drops: Counter[str] = Counter()
    tiers: Counter[str] = Counter()

    known_sources = set(spec_mod.source_ids())
    built: list[dict[str, Any]] = []
    for candidate in candidates:
        item_source = str(candidate.get("source") or source_id)
        if item_source not in known_sources:
            item_source = source_id
        try:
            built.append(build_record(candidate, item_source, now))
        except Exception as exc:  # a malformed candidate must not kill the run
            drops["invalid_record"] += 1
            if not quiet:
                print(f"[skip] {candidate.get('business')}: {exc}", file=sys.stderr)

    # Merge before the gate: a weak entry can still contribute its source URL to a
    # stronger record for the same business.
    deduped_all = dedupe(built)
    for record in deduped_all:
        ok, reason = admit(record, str(record.get("source") or source_id))
        if not ok:
            drops[reason] += 1
            continue
        tier = qualification_tier(record)
        details = record.setdefault("details", {})
        if tier == "research_only":
            # Stored in the research queue, never reachable, never a confirmed need.
            record["status"] = "researching"
            details["human_review_required"] = True
            details["qualification_blocked"] = (
                f"research_only: evidence_classification="
                f"{details.get('evidence_classification') or 'unset'}"
            )
            tiers["research_only"] += 1
        else:
            details["qualification_tier"] = "qualified"
            tiers["qualified"] += 1
        records.append(record)

    deduped = records
    day = now.date().isoformat()
    target = Path(out_path) if out_path else ROOT / "output" / f"{source_id}_{day}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(deduped, indent=2, ensure_ascii=False), encoding="utf-8")

    pushed: dict[str, Any] | None = None
    push_error: str | None = None
    if push and dashboard_configured() and deduped:
        try:
            pushed = push_leads(deduped)
        except Exception as exc:  # never fail the run because the dashboard is down
            push_error = str(exc)

    stats = {
        "source": source_id,
        "source_title": spec.get("title"),
        "discovered": len(candidates),
        "admitted": len(records),
        "final": len(deduped),
        "merged_in_run": len(built) - len(deduped_all),
        "dropped": dict(drops),
        "qualification_tiers": dict(tiers),
        "output_file": str(target),
        "push": pushed,
        "cap": spec_mod.config().get("batch", {}).get("max_leads_per_run"),
        "lifecycle": brain.get("lifecycle", {}).get("states"),
    }

    if not quiet:
        print(
            f"{source_id}: discovered {stats['discovered']} | admitted {stats['admitted']} "
            f"| final {stats['final']} | merged {stats['merged_in_run']}"
        )
        if drops:
            print("dropped: " + ", ".join(f"{k} {v}" for k, v in sorted(drops.items())))
        print(f"out: {target}")
        if pushed:
            print(
                f"dashboard: {pushed['created']} created, {pushed['merged']} merged, "
                f"{pushed['failed']} failed -> {pushed['endpoint']}"
            )
            for error in pushed["errors"]:
                print(f"[push] {error}", file=sys.stderr)
        elif push_error:
            print(f"[push] skipped: {push_error}", file=sys.stderr)
        elif push and not dashboard_configured():
            print("[push] DASHBOARD_URL / DASHBOARD_API_SECRET not set, skipped", file=sys.stderr)

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=None,
        help=f"one source family: {', '.join(spec_mod.source_ids())}",
    )
    parser.add_argument("--days", type=int, default=None, help="recency window in days")
    parser.add_argument("--raw", help="import a provider export instead of fetching")
    parser.add_argument("--out", help="output path (default output/<source>_<date>.json)")
    parser.add_argument("--dry-run", action="store_true", help="write the file, skip the push")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        source_id = args.source or spec_mod.active_sources()[0]
        if source_id not in spec_mod.source_ids():
            parser.error(
                f"unknown source '{source_id}'; choose from {', '.join(spec_mod.source_ids())}"
            )
        days = args.days or int(spec_mod.config().get("recency_days", 7))
    except spec_mod.SpecError as exc:
        parser.error(str(exc))

    run(
        source_id=source_id,
        days=days,
        raw_path=args.raw,
        out_path=args.out,
        quiet=args.quiet,
        push=not args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
