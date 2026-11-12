# Engine architecture and roadmap

What the engine does today, how the pieces fit, and what is still missing. The
README is the operator's guide; this file is the map of the machinery.

## Data flow

```
                    config.json            config/sources/*.json
                         |                          |
                         v                          v
  pipeline.run  ->  pipeline.spec  <----------  dimensions, geography,
       |                                          job families, queries
       |
       v
  pipeline.sources.gather(source_id)  ------------->  candidates
       |                                                  |
       |  google_maps -> pipeline.maps (Apify)            |
       |  job_boards  -> pipeline.discover (3 feeds)      |
       |  social      -> SocialCrawl HTTP                 |
       |  --raw file  -> pipeline.discover.load_raw       |
       v                                                  v
  qualify.build_record ---------------------------> canonical record
       |                                                  |
       v                                                  v
  score.score_record ---> weighted_score, classification   |
       |                                                  |
       v                                                  v
  qualify.dedupe (business_key, domain, post_url) -> one record per business
       |
       v
  qualify.admit -> drop or keep -> qualification_tier -> qualified | research_only
       |
       +--> output/<source>_<date>.json
       |
       +--> push.to_payload -> POST /api/leads (x-api-secret)
```

`pipeline.validate` sits beside the path rather than inside it: `check_fit` gates
job candidates during discovery, and the rest of its checks (`check_recency`,
`check_relevance`, `check_contact`, `check_remote`) are available for a stricter
pre-admission pass and are exercised by the tests.

## Why two qualification paths exist

The engine grew in steps, and the release keeps both:

- `pipeline/clean.py` is the earlier path: normalise business names, merge by
  fuzzy name match, pick an outreach angle from `profile/profile.json`, label
  recency. It is not called by `pipeline.run`; `tests/test_clean.py` covers it.
- `pipeline/qualify.py` + `score.py` is the current path used by `run.py`:
  exact identity keys, per-source weighted dimensions from the specs, explicit
  admission gates and a research/qualified split.

New work belongs in the current path. The older one is kept for its helpers
and its tests, not as a second opinion.

## Decisions worth knowing

**One business, one lead.** Identity is `business_key` (normalised name), then
the registrable domain of the website, then the post URL. Merging keeps every
evidence entry and fills only empty fields, so a business found by Maps and by a
job board has two evidence URLs and one score.

**The band does not decide qualification.** `classification` (priority /
potential / deprioritize) is a ranking label. Whether a record may be reached is
decided by `qualification_tier`, which reads the recorded evidence
classification. A high score over a hypothesis is still research-only.

**Geography is never inferred.** `google_maps` raises if `geography.targeting`
is not `explicit` or `active_markets` is empty. A run that searched the wrong
country silently is worse than a run that refused to start.

**Missing keys skip a layer.** No key means that layer logs a reason and returns
nothing. It never produces a placeholder candidate and never blocks the run.

**Pushes are best effort.** The output file is written before the push, and a
failed POST is counted and reported per business rather than aborting.

## Roadmap

Ordered by what unblocks the most:

1. **Live adapters for sources 04–09.** Each needs a permitted data path and a
   seed. Until then they are `--raw` only.
2. **Research worker.** Open the evidence page, extract a summary, set
   `evidence_classification` honestly. This is what separates a listing from a
   real opportunity. Requires a model key and a per-lead budget.
3. **Seed lists for websites and directories.** Both sources need to be told
   which businesses to look at; directory feeds or an existing-lead crawl are
   the candidates.
4. **Expiry sweep.** Re-check leads older than the freshness window and move
   expired ones to a stale state instead of leaving them in the active list.
5. **Channel adapters for follow-ups.** `followups.py` recommends; nothing
   sends. Sending needs an approval step first.

## Open questions

- Which model, at what budget, for the research worker. Ten leads can be forty
  page fetches.
- Whether sub-band leads should be stored as visible deprioritized records or
  dropped. Today they are dropped (`below_band`).
- Which channel the follow-up step should use, and whether approval lives in the
  dashboard or in the CLI.
- Whether the legacy `clean.py` path should be retired once nothing imports it.
