# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are vetted checkpoints of `master`. If you maintain a personalized fork,
prefer updating to a tagged release over pulling raw `master` (see
[SETUP.md, section 8](SETUP.md#8-pulling-upstream-updates-into-your-fork)). The
`framework_version` markers on methodology files tell you which of your customized
files a release touched; `python3 tools/check_upstream_updates.py` lists them with
per-file diff commands.

## [Unreleased]

### Added
- JSearch sends every query on each run, against a 9,500 a month cap. The Windows scheduled task runs the search hourly from 6 AM to 5 PM; the code has no schedule of its own.
- More free sources: Workable global search, SimplifyJobs new-grad list, The Muse, all seven We Work Remotely categories, and six RemoteOK tags.
- Cross-site duplicate handling: one job on LinkedIn, an aggregator and the employer page is one row, with the employer link kept. JSearch source (needs `JSEARCH_API_KEY`) for Indeed, ZipRecruiter and Glassdoor results. FreeHire page cap is now 20.
- `daily_digest.py --backfill N` searches the last N days and files each job under the day it was posted, rewriting those days' Word files. `pipeline.py search --window-days N` sets the window by hand. Search caps are now 5 pages for LinkedIn and 10 for FreeHire.
- **Himalayas source** (`src_himalayas`), a remote US search API that returns full posting text. Workday postings are now read through their public JSON endpoint.
- **Full-depth search, three runs a day.** `pipeline.py search` now reads every page of each LinkedIn and FreeHire query (up to `search_settings.max_pages` in `queries.json`), not just the first 10 or 20 results, and asks for postings since the last finished search (1 to 14 days, tracked in `job_scraper/last_run.json`) so a missed run widens the next one. The scheduled task runs at 8:00 AM, 1:00 PM and 6:00 PM; each run adds to the same `daily/YYYY-MM-DD.docx`, and cards show the time they were found.
- **Resume-aware scorer** (`job_scraper/scoring.py`). Grades now come from the posting text, not the title. `posting_text.py` opens each candidate's posting and the scorer checks required years (sales vs. technical), CS or engineering degree requirements with no alternative, clearance, and offshore or staffing firms against the resume. A job whose text was not read cannot be high. On the 230 jobs in `latest.json` this cut high from 38 to 15.
- The morning list, the Word file, and `reports/application-dashboard.html` use one list. Unlabeled engineering titles are left out of the morning highlights, a board error page is not treated as a read posting, and the same Oracle requisition is one card.

### Fixed
- `cmd_process` never opened any posting: it asked the scorer whether a title was worth reading, and the scorer grades every job without text low. Now `scoring.worth_reading` decides, so about 280 postings per run are read instead of none. Postings that fail to load are no longer marked seen, so the next run retries them.
- The scorer no longer grades "Sr Associate" or "Experienced Associate" titles as entry level, and rejects clinical and retail-coordinator titles. Tracking-parameter URL variants of one job collapse to a single result.
- **HN "Who is hiring" dedup collision** - comparing dedup keys by stripped
  "base URL" made every HN comment after the first look like a duplicate,
  because all of them share the path `news.ycombinator.com/item` and differ
  only by `?id=<n>`. Dedup now strips known tracking params (`utm_*`, `se`,
  `v`, `ref`, `source`, `gh_src`, ...) instead of the whole query string, so
  distinct postings on the same path stay distinct while tracking-param
  variants of the same posting still collapse. `update-seen` now uses that
  same key. It was still dropping the whole query string, so a later run
  could show the same Hacker News comment again.
- **`loc_tier()` misclassified same-named cities in the wrong state/country**
  as local or relocatable Twin Cities/Gulf Coast postings - e.g. Long Beach,
  CA; Victoria, BC; Crystal City, Virginia; and "Kilnworks, TX" matching the
  MS_OK substring `"kiln"`. City matching now uses word-boundary regexes
  instead of substring checks, and the region-mismatch guard (comparing the
  detected US state, or Canadian province, against the expected MN/MS state)
  now applies to both the MN_OK and MS_OK checks and recognizes full state
  names, not just 2-letter abbreviations.
- **`job_search_tracker.csv` drift** - `/apply` drafts CV/cover-letter `.tex`
  files but never writes the tracker row (`/outcome` does, run by hand after
  submitting), so the tracker silently falls behind. Added
  `tools/check_tracker_drift.py` to detect drafted applications with no
  matching tracker row, without fabricating any data it can't safely infer
  from a filename.

### Fixed
- **The morning list leaves out engineering titles above entry level, and postings that require an engineering or computer-science degree.** Level II, senior, and unlabeled engineering titles such as a bare "Software Engineer" or "Full Stack Developer" are not listed. Entry, trainee, junior, associate, early career, new grad, and level I stay. A posting is also left out when its qualifications require a degree in engineering or computer science and do not accept another degree or equivalent experience. "Or business," "or related," and "or equivalent experience" stay listed. The fit score itself is unchanged.
- **Technical Sales Trainee scored low.** The technical-sales title list matched representative, consultant, rep, and specialist. "Trainee" fell through to the default low. "Technical Sales Trainee" now scores high, and a senior wording of that title stays medium.

### Added
- A high grade is only for the Twin Cities area, Gulfport–Biloxi, or a US remote role that does not name another city. A card tied to another city, or a posting that requires another city, drops from high to medium. Medium and low grades stay as they are.
- Each Word card says why the grade is high, medium, or low, then one sentence from the posting when the page could be read. High and medium jobs stay grouped by position type. Low jobs are grouped by type at the bottom of the file.
- A job in the Word list gets a star when the posting names a business or marketing degree, a bootcamp or coding certificate, or a business analytics certificate. The card quotes that sentence. A bare bachelor's degree, an MBA, and a business-development mention with no degree stay unstarred. The fit score is unchanged.
- Daily Word files group jobs under a heading for each position type, in this order: technical sales, sales engineer and solutions engineer, engineering, customer engineer and implementation, pre-sales and solutions consultant, QA, solutions architect, and other. The list at the top of the file jumps to a type. The same headings show in Word's navigation pane. Fit scoring is unchanged.
- A Windows task named "Adam daily job search" runs `job_scraper/run_daily.cmd` every day at 8:00 AM local time and writes `daily/YYYY-MM-DD.txt` plus `daily/latest.txt`. If the PC is asleep at 8:00, the task runs when it wakes. `latest.docx` keeps each job for 14 days from the day it was found, then removes it. The morning file and latest are Word documents. A job is written into one date file only.
- Search also covers the developer wordings of the same jobs (software, application, Python, full stack, web, and backend developer), plus customer engineer and implementation consultant. Junior QA, junior quality assurance, and junior software tester are searched on purpose. A non-senior QA title scores medium. Senior QA and SDET score low.
- Daily search now also covers Technical Sales Representative / Technical Sales Consultant, Pre-Sales Consultant / Solutions Consultant, and Solutions Architect (Minneapolis, US-remote, Gulfport, and US freehire), plus US-remote Sales Engineer. `BRIDGE_RE` scores those as bridge roles. A plain "Solutions Architect" stays medium because `architect` is already a seniority token. Bare software or cloud architect titles stay low.
- `fit()` now also considers the job description (when available), demoting
  (never promoting) a title-based fit score when the description requires
  5+ years of experience or an active security clearance.
- `extract_salary()` pulls a salary figure or range out of the description
  when present; surfaced in `candidates.json` and `present`'s output.
- Query matrix gained entries for "web developer" / frontend-developer and
  "implementation engineer/consultant" bridge-role titles, matching new
  `FS_RE`/`BRIDGE_RE` regex coverage in `fit()`.
- `expire` now runs automatically as the last step of the `all` pipeline
  command, instead of needing to be invoked separately.
- New **Tier 5 company-board source** (`boards` in `http_sources`): polls
  Greenhouse/Lever/Ashby's public JSON APIs for companies listed in
  `queries.json`'s `company_boards`. One company's failure never aborts the
  others. Seeded with two verified slugs (Jamf, Sezzle). Added
  `tools/probe_company_board.py` to verify a slug against the live API
  before adding it, since guessed slugs 404 far more often than they hit.

## [1.0.0] - 2026-07-22

First tagged release. This marks the framework as stable and gives forks a described
checkpoint to update against instead of a moving `master`. It is a baseline of what
already exists rather than a set of new changes; subsequent releases will document
what changed since the previous tag.

At this baseline the framework provides:

- **Application workflow** - a drafter/reviewer `/apply` pipeline (CV + cover letter),
  plus `/setup`, `/scrape`, `/rank`, `/interview`, `/outcome`, `/upskill`,
  `/expand`, `/html-report`, `/gmail-sync`, `/notion-sync`, `/add-portal`,
  `/add-template`, and `/reset`.
- **Portal search skills** - country-agnostic job-board CLIs (LinkedIn, freehire, and
  the Danish boards) in the portable Agent Skills format under `.agents/skills/`,
  discovered and orchestrated by `/scrape`, with an `enabled:` toggle for skipping
  portals.
- **Framework versioning** - `framework_version` markers on methodology files plus
  `tools/check_framework_version.py` (CI guard) and `tools/check_upstream_updates.py`
  (fork-side update preview).
- **Privacy and safety guards** - `.gitignore` protection for personal data, the
  `tools/security_guards.py` allowlist for `.gitignore` negations, and a CI policy of
  making no live portal requests.
- **Cross-runtime support** - a root `AGENTS.md` pointer so Codex and Antigravity can
  discover the portable portal skills, with Claude Code as the reference runtime.

[Unreleased]: https://github.com/MadsLorentzen/ai-job-search/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/MadsLorentzen/ai-job-search/releases/tag/v1.0.0
