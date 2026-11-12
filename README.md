# ClientOutreach engine

A lead-discovery engine for a one-person automation studio. It searches a set of
sources, decides which hits are real opportunities, merges the ones that describe
the same business, and pushes the survivors to a dashboard over HTTP.

The engine is a plain Python 3.10+ package with **no third-party dependencies**.
The dashboard it talks to is a separate application and is not part of this
repository.

```
discover -> validate -> qualify -> score -> dedupe -> rank -> push
                              \-> review-only queue
```

The rule the whole design follows: **evidence first, volume never**. A candidate
becomes a lead only when it carries a specific, citable signal with a URL. There
is no lead cap and no filler; a run that finds nothing returns nothing.

## Install

```bash
git clone <this-repo> && cd ClientOutreach
python3 -m venv .venv && source .venv/bin/activate

cp .env.example .env      # then fill in only the layers you will run
python3 -m unittest discover -s tests
```

There is nothing to `pip install`. `requirements.txt` is intentionally empty of
packages: the engine uses `urllib`, `json` and `xml.etree` from the standard
library.

## Run

```bash
# One source family per pass. Writes output/<source>_<date>.json.
python3 -m pipeline.run --source google_maps --dry-run
python3 -m pipeline.run --source job_boards
python3 -m pipeline.run --source social

# Any source without a live adapter: import a provider export instead.
python3 -m pipeline.run --source freelance --raw raw_leads/export.json

# Shorter recency window for one pass.
python3 -m pipeline.run --source job_boards --days 3

# Source 10: review leads already in the dashboard. Writes a report, sends nothing.
python3 -m pipeline.followups
```

`--dry-run` writes the output file and skips the push. Without `--dry-run` the
engine also POSTs every admitted record to the dashboard, and reports how many
were created, merged or failed.

## Layers

`config/sources/*.json` is the specification for each source: what to search,
which scoring dimensions apply, and which geography is in scope. `config.json`
lists which sources are active and their stage order. `config/brain.json` holds
the evidence model, the scoring maths, the lifecycle and the quality gate.

| # | Source id | Live adapter | What it looks for |
|---|---|---|---|
| 01 | `google_maps` | Apify actor, needs `APIFY_TOKEN` | Local businesses in explicitly named markets, from category keywords |
| 02 | `job_boards` | RemoteOK, Remotive, We Work Remotely (keyless) | Employers hiring, filtered to the spec's job families and the fit gate |
| 03 | `social` | SocialCrawl search, needs `SOCIALCRAWL_API_KEY` | Public posts where someone asks for automation work **and** names their business |
| 04 | `websites` | none — `--raw` export | Business sites and contact pages |
| 05 | `directories` | none — `--raw` export | Directory and professional-network listings |
| 06 | `referrals` | none — `--raw` export | Referrals, communities, partnerships |
| 07 | `freelance` | none — `--raw` export | Freelance project requests |
| 08 | `startups` | none — `--raw` export | Startup and founder networks |
| 09 | `competitors` | none — `--raw` export | Competitor, agency and technology signals |
| 10 | `followups` | reads the dashboard | Existing leads and re-engagement. Discoveries nothing |

Sources 04–09 are config-complete. `pipeline.gather()` logs
`no live adapter configured yet` and returns no candidates unless you pass
`--raw`; the qualification layer is identical either way, so a raw import never
needs its own rules.

### Enabling and disabling a layer

Edit `config.json`:

```json
"sources": { "active": ["google_maps", "job_boards", "social"] }
```

Only `active` sources can be discovered, and each pass takes one source family
(`--source <id>`). All of them write to the same table in the dashboard and are
merged by business key, so one business stays one lead across sources. To
disable a layer, remove it from `active`, or simply never pass `--source` for
it. A layer whose key is missing is skipped and reported, never faked.

### Market scoping

`google_maps` refuses to infer a location. The spec's `geography.targeting` must
be `"explicit"` and `geography.active_markets` must be non-empty, or the layer
raises instead of searching "wherever it searched last time". Results whose
`countryCode` falls outside those markets are discarded.

## How a candidate is qualified

1. **Build** (`pipeline/qualify.py:build_record`). A source candidate is mapped
   onto the canonical record: `business`, `business_key`, `need`, `why_suits`,
   `contact`, `category`, `posted_at`, plus one `details.evidence` entry
   carrying the source URL, the observed text, a classification and a
   verification flag.
2. **Score** (`pipeline/score.py`). Each source defines five weighted
   dimensions in its spec; names differ per source, so the scorer works from the
   dimension name. Each dimension scores 0–5, `weighted_total` gives 0–100, and
   `classify` maps it to a band (60+ priority, 40–59 potential, below 40
   deprioritize). Scores supplied by a research step are used as-is; otherwise
   `heuristic_scores` derives them from the recorded evidence.
3. **Admit** (`pipeline/qualify.py:admit`). Six hard gates, each with its own
   drop reason:

   | Gate | Rejects as |
   |---|---|
   | Business identity and a stated need | `no_business` / `no_opportunity` |
   | At least one citable evidence URL | `no_citable_evidence` |
   | Service fit not recorded as poor, and fit ≥ 2/5 | `capability` / `no_service_match` |
   | Evidence not classified as "no relevant opportunity" | `no_opportunity` |
   | Not opted out or marked do-not-contact | `do_not_contact` |
   | A weak tier must carry a confirmed requirement or an observed signal | `contextual_only` |
   | Band is not "deprioritize" | `below_band` |

4. **Tier** (`qualification_tier`). Surviving records split into `qualified` and
   `research_only`. The split is made on the recorded evidence classification,
   never on the blended score: a `hypothesis_only` or `no_relevant_opportunity`
   record goes to the research queue as `status: researching` with
   `human_review_required`, and is never handed to outreach.

The run report counts every drop by reason, so a thin pass is explainable.

## Duplicates and updates

`business_key` is the stable identity: lowercased, `&` expanded to `and`,
punctuation removed, and trailing legal suffixes (`ltd`, `llc`, `inc`,
`pvt ltd`, …) stripped, then slugged and truncated to 120 characters. Two
listings of "M.R. Consultants (Pvt) Ltd" reduce to one key.

Merging happens twice:

- **In the run** (`pipeline/qualify.py:dedupe`). Records are sorted by weighted
  score, then merged on the first matching identity key: `business_key`, then
  the registrable domain of `contact.website`, then `contact.post_url`. A
  second pass folds records that only acquired a domain key after merging. The
  winner keeps every evidence entry — duplicates are dropped, evidence is not.
  Merging fills empty fields (`category`, `contact.*`, `city`, `country`,
  `portfolio_proof`, `relevant_service`) and never overwrites a populated one.
- **At the dashboard** (`pipeline/push.py`). `POST /api/leads` upserts on
  `business_key`: new evidence is appended, empty fields are filled, and a
  stronger existing source is not overwritten. The response's `action` is
  `created` or `merged`, and the run reports both counts.

So a business found by four sources is one lead with four evidence entries —
never four leads.

## Recency and expiry

- `config.json:recency_days` (default 7) is the freshness window; `--days`
  overrides it per pass.
- `pipeline/validate.py:check_recency` rejects a candidate with no verifiable
  date (`no verifiable date`), a future timestamp, or one older than the window
  (`older than N days`). Rejection happens **before** admission, so a stale hit
  never becomes a lead.
- A Google Maps listing has no publish date. It carries
  `recency_source: "observed_live"` instead: the business was verified open on
  the crawl date. That is treated as weak freshness and scores lower than a
  fresh post.
- Ranking is `weighted_score DESC NULLS LAST, then recency DESC`, so the newest
  strong evidence is first.

Expiry is a gate, not a sweep. The engine does not revisit leads already in the
dashboard to archive them later — see *Known gaps*. `pipeline/followups.py`
is the only module that looks at existing leads, and it recommends
`no_contact` / `review_record` rather than mutating anything.

## Channels

The record carries one `contact` object, and each field is a separate path:

| Field | Used for |
|---|---|
| `email` | Direct email. Set only when the source page published it |
| `phone` | Voice call, and WhatsApp once normalised to E.164 |
| `profile_url` | Social profile, for a DM |
| `dm_link` | Explicit message link when the platform provides one |
| `application_url` | Applying to a posting. A real application, never a sales message |
| `website` | The business site |
| `post_url` | The evidence post itself |

`pipeline/validate.py:contact_paths` counts these and the contact gate requires
at least one. The `contactability` scoring dimension rewards having more of
them. The engine never invents a channel: a channel that was not published on
the source page stays `null`. It does not send anything — the dashboard owns
outreach.

## Pushing to the dashboard

```bash
DASHBOARD_URL=https://your-dashboard.example.com
DASHBOARD_API_SECRET=change-me        # sent as the x-api-secret header
```

`POST {DASHBOARD_URL}/api/leads` with `x-api-secret`, one record per request:

```jsonc
{
  "business": "…", "business_key": "…", "category": "…",
  "city": "…", "country": "…", "platform": "…", "source": "google_maps",
  "need": "…", "why_suits": "…", "relevant_service": "…",
  "contact": { "email": null, "phone": "…", "profile_url": null,
               "dm_link": null, "application_url": null,
               "website": "…", "post_url": "…" },
  "posted_at": "2026-01-01T00:00:00+00:00",
  "remote_eligible_pak": false,
  "weighted_score": 0, "classification": "priority",
  "evidence_level": "direct", "status": "discovered",
  "pitch_angle": "…", "case_study": "…",
  "details": { "evidence": [ /* {source, url, observed_at, summary, classification, verification} */ ],
               "scores": { /* dimension -> {score, weight, why} */ } }
}
```

The full mapping is `pipeline/push.py:to_payload`. A failed push never fails the
run: the output file is already written and the failure is reported per business.

Package layout:

```
pipeline/
  spec.py     # the only reader of config/
  env.py      # stdlib .env loader
  discover.py # keyless feeds: RemoteOK, Remotive, We Work Remotely
  maps.py     # Google Maps via Apify
  sources.py  # per-source adapters -> candidates, and gather()
  validate.py # eligibility, recency, relevance, fit, contact gates
  score.py    # weighted dimensions -> 0-100 -> band
  qualify.py  # record building, admission, tiers, dedupe/merge
  clean.py    # legacy cleaning, angle selection, recency labels
  push.py     # POST /api/leads
  followups.py# Source 10, read-only review
  run.py      # CLI
config/       # source specs, brain, runtime config
prompts/      # research prompts, kept as product config
tests/        # 89 unit tests, stdlib unittest
```

## What collaborators must never commit

- `.env`, `.env.local`, `.env.bak*` — anything real. Only `.env.example` is
  tracked, and it holds placeholders.
- API keys, tokens, cookies, session strings, connection strings, dashboard
  secrets.
- `output/*.json` and `raw_leads/*.json` — provider exports and run results
  contain real businesses and contact details.
- `profile/raw_html/` — crawled pages.
- Private keys and credential files (`*.pem`, `*.key`, `credentials*`).

Check before you push:

```bash
git status --short          # nothing unexpected staged
git diff --cached | grep -iE "api[_-]?key|token|secret|password|postgres" 
```

## Known gaps

- No live adapter for sources 04–09. They accept `--raw` exports only.
- No LLM research worker: evidence summaries come from the source listing, not
  from reading the business's site. Adding one requires a model key.
- No archival sweep. Stale candidates are refused at admission; leads already
  in the dashboard are not re-checked or auto-archived.
- `pipeline/clean.py` is the earlier cleaning path, superseded by
  `qualify.py`/`score.py` and kept because the angle and recency helpers are
  still used by tests.
- `hunters/` holds placeholder modules for an earlier per-platform design. They
  are not imported by the pipeline.
- `followups.py` drafts nothing and sends nothing.
