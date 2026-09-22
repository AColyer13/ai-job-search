# Job Sources

Everything `pipeline.py` can pull from, plus sources that need a key or were
considered and rejected. Queries for the CLI portals live in `queries.json`;
HTTP sources are enabled/disabled via the `http_sources` list there (or
`--only` / `--skip` on the command line).

## Tier 1 — CLI portals (`.agents/skills/`, run via `bun`)

| Portal | Coverage | Notes |
|---|---|---|
| `linkedin-search` | US / MSP / remote | Public guest API. ToS-sensitive — keep volume low. |
| `freehire-search` | US tech, multi-market | Aggregates ~50 ATS platforms. Self-hostable backend. |

## Tier 2 — Keyless HTTP sources (implemented in `pipeline.py`)

| Source | Endpoint | Coverage |
|---|---|---|
| RemoteOK | `remoteok.com/api` | Remote tech, worldwide |
| Remotive | `remotive.com/api/remote-jobs` | Remote tech; `candidate_required_location` used for US filtering |
| Arbeitnow | `arbeitnow.com/api/job-board-api` | EU-heavy; only `remote: true` jobs kept |
| Jobicy | `jobicy.com/api/v2/remote-jobs?geo=usa` | Remote, geo-filtered to USA |
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

## Tier 4 — Free key, not yet implemented

Worth adding if coverage gaps appear (follow the `src_adzuna` pattern in
`pipeline.py`):

- **The Muse** — https://www.themuse.com/developers/api/v2 (free key, US)
- **Jooble** — https://jooble.org/api/about (free key, aggregates US boards)
- **Careerjet** — https://www.careerjet.com/partners/api/ (free affiliate key)
- **CareerOneStop** — https://www.careeronestop.org/Developers/WebAPI/ (US Dept. of Labor, free key)

## Tier 5 — Target-company board APIs

Greenhouse (`boards-api.greenhouse.io/v1/boards/<company>/jobs`), Lever
(`api.lever.co/v0/postings/<company>`), and Ashby (`api.ashbyhq.com/posting-api/job-board/<company>`)
all expose keyless public JSON per company. If a target-company list emerges
(`search-queries.md` currently has none), a `boards` source can poll them directly.

## Rejected

- **Indeed / Wellfound / Glassdoor** — no public API, scraping violates ToS.
- **Google Jobs via SerpApi** — paid only.
- **Reed.co.uk** — UK-only, not this market.
