# Search Queries for Job Scraper

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Adam uses `linkedin-search` and `freehire-search` (both US-relevant); the Danish portal demos (Jobindex/Jobbank/Jobdanmark/Jobnet) do not apply to his market and can be ignored. You do **not** need a matching `site:` line below for those CLIs to run.

The `site:` query templates in this file are the **WebSearch fallback** — for portals without a CLI, company career pages, or when a CLI fails.

## Search Sites

Primary:
- **linkedin.com/jobs** - filter: United States / Minneapolis-St. Paul metro / Remote; also covered by `linkedin-search` CLI
- **freehire.me** - also covered by `freehire-search` CLI
- **Remote aggregators** - RemoteOK, Remotive, Arbeitnow, Jobicy, WeWorkRemotely, Working Nomads, and the HN "Who is hiring" thread are all covered by `job_scraper/pipeline.py` (keyless HTTP sources; see `job_scraper/SOURCES.md`). Adzuna and USAJobs activate once their free API keys are set as env vars. No WebSearch fallback needed for these.
- **Target-company boards** - the `boards` source polls Greenhouse/Lever/Ashby directly for companies listed in `job_scraper/queries.json`'s `company_boards` (verify a slug with `tools/probe_company_board.py` before adding it - see `job_scraper/SOURCES.md` Tier 5).

Secondary (company career pages via Google):
- Direct Google searches with `site:` filters for known target companies (none specified yet - open to suggestions)

## Query Categories

Adam is a career-changer (full-stack bootcamp grad, Feb 2026, transitioning from 5 years of B2B technical sales) targeting **entry-level** full-stack and AI-engineering roles with roughly equal weight. Each query should be combined with location terms per the Location Filter below where the site supports it.

### Priority 1: Entry-Level Full-Stack Software Engineer

His strongest and most desired career direction.

```
site:linkedin.com/jobs "junior software engineer" React
site:linkedin.com/jobs "entry level software engineer" TypeScript
site:linkedin.com/jobs "full stack engineer" "React" "Python" entry level
```

### Priority 2: AI/ML Engineering (entry-level)

Matches his AI/LLM integration focus - equal priority to Priority 1.

```
site:linkedin.com/jobs "AI engineer" junior OR "entry level" RAG OR LangChain
site:linkedin.com/jobs "LLM engineer" OR "AI application engineer" junior
site:linkedin.com/jobs "prompt engineer" OR "AI agent" developer entry level
```

### Priority 3: Adjacent roles - sales-to-engineering bridge

Roles that value both his technical sales background and coding skills.

```
site:linkedin.com/jobs "solutions engineer" React OR Python
site:linkedin.com/jobs "sales engineer" technical demo software
site:linkedin.com/jobs "technical account manager" full stack
site:linkedin.com/jobs "implementation engineer" OR "implementation consultant" software
```

### Priority 4: Broader Technical

Wider net for general junior technical roles.

```
site:linkedin.com/jobs "junior developer" React OR Next.js
site:linkedin.com/jobs "associate software engineer"
site:linkedin.com/jobs "software engineer I" full stack
site:linkedin.com/jobs "frontend developer" OR "web developer" junior React
```

## Location Filter

When evaluating results, verify the job location matches one of these tiers:
- **Ideal:** Fully remote (any location)
- **Acceptable:** Local commute from Edina spanning **Rogers, MN** (north) to **Chanhassen, MN** (south): Edina, Eden Prairie, Minnetonka, Hopkins, St. Louis Park, Golden Valley, Plymouth, Maple Grove, Osseo, Brooklyn Park, Rogers, Dayton, Medina, Wayzata, Orono, Chaska, Victoria, Bloomington, Richfield. Garmin (Chanhassen) is in range. Hybrid with occasional Minneapolis days is OK; skip 5-day downtown-only offices when possible.
- **Acceptable (relocation):** Mississippi, preferably Gulfport and Biloxi (and nearby Gulf Coast: Long Beach, D'Iberville, Ocean Springs, Pass Christian)
- **Borderline:** Other US locations requiring relocation
- **Too far / exclude:** St. Paul or anywhere east of Minneapolis, unless the role is fully remote; Mississippi roles well inland from the Gulf Coast

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- "/scrape [focus_area]" -> relevant category queries + custom focus-specific queries
