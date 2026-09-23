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

### Fixed
- **HN "Who is hiring" dedup collision** - comparing dedup keys by stripped
  "base URL" made every HN comment after the first look like a duplicate,
  because all of them share the path `news.ycombinator.com/item` and differ
  only by `?id=<n>`. Dedup now strips known tracking params (`utm_*`, `se`,
  `v`, `ref`, `source`, `gh_src`, ...) instead of the whole query string, so
  distinct postings on the same path stay distinct while tracking-param
  variants of the same posting still collapse.
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

### Added
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
