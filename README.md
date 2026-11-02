# AI Job Search

The goal is a fully autonomous job search with no human in the loop. Search, grading, answers, and submission run on their own. Nobody is asked to approve a job, answer a form, or press Submit.

Finds and grades jobs seven times a day. Grok is the default AI for drafts and questions, with Antigravity as the fallback. The apply agent submits eligible jobs under the standing instructions in [CLAUDE.md](CLAUDE.md). Candidate profile and agent rules are in that file and [AGENTS.md](AGENTS.md).

## Finding jobs

Run `python jobs.py` to see up to five saved matches with usable posting links. Unattempted jobs come first, ranked by fit; previously handled jobs appear afterward. Missing or invalid links are counted and excluded. This reads the saved shortlist without starting a search or applying. The full list is in `daily/latest.docx`.

The Windows scheduled task "Adam daily job search" runs `job_scraper/run_daily.cmd` at 6 AM, 8 AM, 10 AM, noon, 2 PM, 4 PM and 6 PM. After each search the apply agent works the new jobs and logs anything it cannot answer (see `job_scraper/apply_agent.py`). Each run searches every source in [job_scraper/SOURCES.md](job_scraper/SOURCES.md): LinkedIn and FreeHire through the CLI tools in `.agents/skills/`, plus public feeds, company boards and JSearch (a paid plan of 10,000 requests a month). Queries live in `job_scraper/queries.json`.

## Grading

`job_scraper/scoring.py` gives each posting a 0 to 100 fit score: career fit 30, technical fit 30, experience 25, work style 15. High is 70 and up, Medium is 50 to 69, below 50 is Low. It also reads the posting's stated minimums, sets duplicates aside and applies a Java rule. Postings cut off at 2,000 characters are re-fetched in full.

## Output

Every run rewrites `daily/<date>.docx` and `daily/latest.docx`. They list High and Medium jobs only. Low-fit jobs stay in `daily/latest.json` so they are not listed again.

## Applying

The apply agent submits on its own. It answers each required question from the profile or by writing the best answer it can, cleans that wording, and saves it for the next time. It skips a robot check, a required GPA, a role that requires already living outside Minnesota, Texas, Mississippi, Louisiana, Alabama, Florida, Georgia, or South Carolina, a closed posting, a rejected email code, or a missing confirmation. It skips jobs already logged and records outcomes with `job_scraper/applied_log.py`. The reports show AI applied, Adam applied, or why the job was not applied for.

If Submit has no confirmed outcome, the agent keeps it unconfirmed and blocks duplicate attempts, including the same employer and role found at another URL. The email fallback checks the employer and role; a security-code email is not a submission receipt. A missing email does not prove the application failed. The tracking audit keeps these cases visible until confirmation evidence resolves them.

## Tracking

`job_search_tracker.csv` feeds `/rank`, `/scrape` and `/gmail-sync`. `/outcome` records results and `/interview` prepares for interviews.

## Tests

```bash
python -m pytest -q
```
