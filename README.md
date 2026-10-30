# AI Job Search

Finds and grades jobs on its own every hour. Grok is the default AI for drafts and questions, with Claude and Antigravity as fallbacks. It applies only when asked, then logs each job. Candidate profile and agent rules are in [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md).

## Finding jobs

The Windows scheduled task "Adam daily job search" runs `job_scraper/run_daily.cmd` at 6 AM, 8 AM, 10 AM, noon, 2 PM, 4 PM and 6 PM. After each search the apply agent works the new jobs and asks you on Telegram for anything it needs (see `job_scraper/apply_agent.py`). Each run searches every source in [job_scraper/SOURCES.md](job_scraper/SOURCES.md): LinkedIn and FreeHire through the CLI tools in `.agents/skills/`, plus public feeds, company boards and JSearch (a paid plan of 10,000 requests a month). Queries live in `job_scraper/queries.json`.

## Grading

`job_scraper/scoring.py` gives each posting a 0 to 100 fit score: career fit 30, technical fit 30, experience 25, work style 15. High is 70 and up, Medium is 50 to 69, below 50 is Low. It also reads the posting's stated minimums, sets duplicates aside and applies a Java rule. Postings cut off at 2,000 characters are re-fetched in full.

## Output

Every run rewrites `daily/<date>.docx` and `daily/latest.docx`. They list High and Medium jobs only. Low-fit jobs stay in `daily/latest.json` so they are not listed again.

## Applying

Nothing submits automatically. Ask an agent to apply from the daily list. [CLAUDE.md](CLAUDE.md) tells it to skip anything already logged, submit a job only if no account, login or captcha is needed, and log it with `job_scraper/applied_log.py`. Jobs only Adam can do are logged as recommended. His own applications are logged as applied by him.

## Tracking

`job_search_tracker.csv` feeds `/rank`, `/scrape` and `/gmail-sync`. `/outcome` records results and `/interview` prepares for interviews.

## Tests

```bash
python -m unittest discover -s tests -t .
```
