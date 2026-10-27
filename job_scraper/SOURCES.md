# Job Sources

Everything `pipeline.py` can pull from, plus sources that need a key or were
considered and rejected. Queries for the CLI portals live in `queries.json`;
HTTP sources are enabled/disabled via the `http_sources` list there (or
`--only` / `--skip` on the command line).

## Run schedule

The Windows task "Adam daily job search" runs `run_daily.cmd` hourly from 6 AM to 5 PM, and every run searches every source. The daily Word files are rewritten on each run from the jobs found so far.

## Tier 1 — CLI portals (`.agents/skills/`, run via `bun`)

| Portal | Coverage | Notes |
|---|---|---|
| `linkedin-search` | US / MSP / remote | Public guest API. ToS-sensitive — keep volume low. |
| `freehire-search` | US tech, multi-market | Aggregates ~50 ATS platforms. Self-hostable backend. |

## Tier 2 — Keyless HTTP sources (implemented in `pipeline.py`)

| Source | Endpoint | Coverage |
|---|---|---|
| RemoteOK | `remoteok.com/api` | Remote tech, worldwide |
| Remotive | `remotive.com/api/remote-jobs` | Remote tech (the public API now returns about 10 jobs per call); `candidate_required_location` used for US filtering |
| Arbeitnow | `arbeitnow.com/api/job-board-api` | EU-heavy; only `remote: true` jobs kept |
| Jobicy | `jobicy.com/api/v2/remote-jobs?geo=usa` | Remote, geo-filtered to USA |
| Workable | `jobs.workable.com/api/v1/jobs` (8 queries, 4 pages, US, 21 days) | Every company on Workable; full text. |
| SimplifyJobs | public `listings.json` on GitHub | Entry-level tech roles, active and posted in 21 days; no text, so the reader opens each. |
| The Muse | `themuse.com/api/public/jobs` (4 categories, remote and Minneapolis) | Curated; may rate-limit, failures are skipped. |
| Himalayas | `himalayas.app/jobs/api/search` (12 queries, 2 pages) | Remote, US; returns the full posting text. Its job pages return 403 to scripts, so this API is the only way to read them. |
| WeWorkRemotely | RSS (`/categories/remote-programming-jobs.rss`) | Remote programming |
| Working Nomads | `workingnomads.com/api/exposed_jobs/` | Remote tech |
| HN "Who is hiring" | Algolia API (`hn.algolia.com`) | Monthly thread; comments mentioning "remote", capped at 100 |

All are fetched with a descriptive User-Agent, one request per source (two for
Arbeitnow pagination), and failures are isolated — one source dying does not
abort the run.

## Tier 3 — Free API key required (implemented, gated on env vars)

These are skipped silently until the env vars are set:

| Source | Env vars | Get a key |
|---|---|---|
| Adzuna (US) | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | https://developer.adzuna.com/ |
| USAJobs (federal) | `USAJOBS_API_KEY`, `USAJOBS_EMAIL` | https://developer.usajobs.gov/ |
| JSearch (OpenWebNinja) | `JSEARCH_API_KEY` (a RapidAPI key in `RAPIDAPI_KEY` also works) | https://www.openwebninja.com/api/jsearch. Google for Jobs results: Indeed, ZipRecruiter, Glassdoor and others, through a licensed API. The plan allows 10,000 requests a month. Each run sends every query (5), so 12 runs a day is about 1,800 requests a month, and it stops at `monthly_limit` (9,500). The count is kept in `job_scraper/jsearch_runs.json`. Queries are in `queries.json` under `jsearch`. |

## Tier 4 — Free key, not yet implemented

Worth adding if coverage gaps appear (follow the `src_adzuna` pattern in
`pipeline.py`):

- **The Muse** — https://www.themuse.com/developers/api/v2 (free key, US)
- **Jooble** — https://jooble.org/api/about (free key, aggregates US boards)
- **Careerjet** — https://www.careerjet.com/partners/api/ (free affiliate key)
- **CareerOneStop** — https://www.careeronestop.org/Developers/WebAPI/ (US Dept. of Labor, free key)

## Tier 5 — Target-company board APIs (implemented as the `boards` source)

Greenhouse (`boards-api.greenhouse.io/v1/boards/<company>/jobs`), Lever
(`api.lever.co/v0/postings/<company>`), and Ashby (`api.ashbyhq.com/posting-api/job-board/<company>`)
all expose keyless public JSON per company. `pipeline.py`'s `src_boards()`
polls every entry in `queries.json`'s `company_boards` list; one company
failing (bad slug, board closed) never aborts the others.

**Verify a slug before adding it** — guessed slugs 404 far more often than
they hit:

```bash
python tools/probe_company_board.py greenhouse <slug>
python tools/probe_company_board.py lever <slug>
python tools/probe_company_board.py ashby <slug>
```

Currently seeded (both confirmed live): Jamf and Sezzle, both on Greenhouse.
Several other Twin Cities employers were tried (SPS Commerce, Arctic Wolf,
Calabrio, Workiva, Seeq, Garmin under various slug guesses) and all 404'd —
their real slugs, if they use one of these three platforms at all, aren't
guessable from the company name.

## Rejected

- **Scraping Indeed / ZipRecruiter / Glassdoor directly** — no public API, scraping violates their terms and they block bots. Use JSearch (Tier 3) instead. Wellfound, Built In, Adzuna, WhatJobs and startup.jobs arrive through FreeHire.

## Duplicates

The same job often appears on LinkedIn, an aggregator and the employer page. `collapse_duplicates` (run in `pipeline.py process`) treats same company + title + city (or both remote) as one job and keeps the employer's own link. `daily_digest.py` also skips a job already stored from another site and cleans old copies out of the store.
- **Google Jobs via SerpApi** — paid only.
- **Reed.co.uk** — UK-only, not this market.
