# Constraint sheet: ATS forms, anti-bot limits, tailoring rules

Written 2026-10-28. Each fact carries its evidence: `[doc]` fetched this session from the vendor's documentation,
`[code]` read in this repo, `[log]` counted from `daily/applied_for.json` (82 entries), `[unverified]` stated from general
knowledge and not checked. Nothing marked `[unverified]` should drive a design decision until it is checked.

## 1. Where the pipeline stands (baseline)

- Test suite before any change: **440 passed, 2 skipped** under `.venv`, which has no Playwright, so every browser test was silently skipped. Under system `python -m pytest -q` the true baseline is **501 passed, 1 skipped** (~100 s). Use system Python. `[code]`
- The pipeline already exists: `apply_forms.py` (browser layer), `apply_answers.py` (field resolver, five outcomes),
  `apply_agent.py` (queue, Telegram approval, submit). Phase 1 is therefore an audit against the oracle, not a greenfield build.
- Hosts handled: `boards.greenhouse.io`, `job-boards.greenhouse.io`, `job-boards.eu.greenhouse.io`, `jobs.lever.co`,
  `jobs.eu.lever.co`, `jobs.ashbyhq.com`. `[code]`

## 2. Submit gate

| Rule | Evidence |
|---|---|
| Form fields are read first with nothing typed (`discover`) | `[code]` apply_forms.py |
| `--dry-run` fills and sets status `dry_run`, never clicks submit | `[code]` apply_agent.py lines 485-486 |
| Normal path: summary to Telegram with Submit / Skip / I'll do it; submit only after the Submit tap | `[code]` apply_agent.py module doc |
| Answers are hashed (`digest`); a form that changed after approval is handed back as Recommended | `[code]` |
| A submit click with no confirmation is logged Recommended, never Applied | `[code]` |
| Standing permission for the Telegram-approved submit dated 2026-10-28 | CLAUDE.md |

This is a human review gate, but it is not the literal "halt at the review screen" rule in the kickoff. The two agree in
effect (no application leaves without Adam's tap). Decision recorded in `master_profile.md` section 4, item 5.

## 3. ATS facts

### Greenhouse
- Public read: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?questions=true` returns
  `questions`, `location_questions`, `compliance`, and `demographic_questions` for orgs on Greenhouse Inclusion. No auth. `[doc]`
- Submit: `POST` to the same path needs HTTP Basic Auth with the **employer's** API key. We have none, so API submission is
  closed to us; only the hosted form is available. `[doc]`
- The API does not validate required fields; the client must. Names and phone must not contain a URL; first name, last name
  and email are required; files must be pdf, doc, docx, txt or rtf and non-empty. `[doc]`
- Greenhouse docs mention no CAPTCHA on the API; the hosted form has its own spam protection. The repo already detects a
  "security code" step by text. `[doc]` `[code]`
- Two hosted layouts exist in the repo's host list (`boards.` and `job-boards.`). The DOM reader treats them identically. `[code]`
- Implication: the public `questions=true` endpoint gives a DOM-free oracle for the field list. It could be used to cross-check
  `read_form` in tests. Not yet used. Not verified against a live board this session (no Greenhouse job was in today's list).

### Lever
- Public read: `GET https://api.lever.co/v0/postings/{SITE}/{POSTING-ID}` (EU: `api.eu.lever.co`). `[doc]`
- Submit: `POST /v0/postings/SITE/POSTING-ID?key=APIKEY`; the key comes from the employer's Super Admin. Resume must be
  multipart. Name and email required. 2 application POSTs per second per site, then HTTP 429. Lever calls its hosted form the
  best practice. `[doc]`
- Lever's docs do not mention hCaptcha, but the applied log has a case where Lever "rejects submits from the assistant
  browser after the hCaptcha". One observation (n=1). `[log]`
- A saved live Lever form (`tests/fixtures/ats/lever.html`, 2026-10-28) contains an empty `.h-captcha` div and a hidden
  `h-captcha-response` input. The widget appears when Submit is pressed, so `read_form` reports no blocker before filling.
  The log has 0 Lever successes. `[code]` `[log]`
- The form lives at `<posting>/apply`; `application_url` appends it. `[code]`

### Ashby
- Public read: `GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}?includeCompensation=true`. Returns title,
  location, workplace type, description, `applyUrl`, and compensation. It does **not** describe application form fields or
  accept applications. `[doc]`
- Form fields therefore come only from the DOM. The form lives at `<posting>/application`. `[code]`
- Ashby has been the second most successful host for the agent (9 applied). `[log]`

### Workday, iCIMS, Taleo, ADP, Paycom and others
- Require an account or login. Repo rule forbids creating accounts or logging in. These go to Recommended. `[code]` CLAUDE.md
- Of 29 Recommended entries, 6 name Workday and 7 are `himalayas.app` links. `[log]`

## 4. What the log says about outcomes (82 entries)

| Method | Applied |
|---|---|
| Greenhouse form | 15 |
| Ashby form | 9 |
| Manatal careers-page form | 7 |
| BambooHR, Workable | 3 each |
| TriNet Hire, Dover, Breezy | 2 each |
| Workday (Adam), LinkedIn Easy Apply (Adam) | 2 each |
| Rippling, Email, Recruitee, JazzHR | 1 each |
| **Still Recommended** | **29** |

Top Recommended reasons (of 29; reasons can overlap): off-supported host 19 (7 of them himalayas.app), account or Workday
named 8 (Workday 6), one Lever hCaptcha rejection, one unanswered Telegram summary (9.4 hours), one needs a personal essay. `[log]`

Hypothesis, not yet checked: a Himalayas listing links out to the employer's own ATS, and resolving that link would move some
of the 7 into the supported set. Needs a read of 2 or 3 of those links before it counts as a fact.

## 5. Anti-bot boundaries (repo policy)

- No CAPTCHA solving or bypass; no browser-fingerprint tricks. `EXECUTION_BRIEF.md` already rejects a browser-style
  User-Agent for 403 hosts. `[code]`
- Blockers detected by selector (reCAPTCHA, hCaptcha, Cloudflare Turnstile, visible password field) and by page text
  (security code, sign-in). `[code]`
- Jooble returns 403 to scripted reads; Himalayas has a usable API. `[code]` EXECUTION_BRIEF.md

## 6. File upload and PDF text layer

- Resume upload path: `profile["resume_pdf"]` via `set_input_files`. `[code]`
- ATS file types: Greenhouse API accepts pdf, doc, docx, txt, rtf. `[doc]` Hosted-form limits per ATS: `[unverified]`.
- CLAUDE.md checklist requires `pdftotext -layout` on the PDF with email and phone as literal text. Whether
  `Adam_Colyer_Resume_2026-10.pdf` passes was **not** run this session. `[unverified]`

## 7. Truth-grounded tailoring rules

1. Source of truth is `master_profile.md` sections 3 and 4. Posting text supplies keywords only.
2. A claim is allowed if its subject (title, number, employer, tool) appears in section 3. Otherwise the draft must come back
   `NEEDS_ADAM` (this is already how `TRUTH_RULES` in `apply_answers.py` works).
3. Enforcement: prompt instruction (`TRUTH_RULES`) plus, since 2026-10-28, `job_scraper/claim_linter.py`. `draft()` refuses
   any draft with a banned claim or a dollar amount, "N+" count, percentage or "N years" that is not in the resume or
   `application_facts.md`; the agent then asks Adam instead. Limits: it checks numbers and listed phrases only, so an
   invented title, employer or skill in plain words still reaches Adam's review. `[code]`
4. Keyword coverage: report genuine gaps, never stuff (CLAUDE.md checklist).
5. Known unsafe seed, fixed 2026-10-28: the base cover letter said Adam "evaluated model-generated code" at Activus. Adam
   confirmed no coding was involved; `application_facts.md` now matches the resume. Letters already sent from the base
   text since commit 9138271 may have carried the false line; `daily/apply_answer_log.json` does not exist, so I cannot
   list which. Banned phrases for the claim linter: "model-generated code", "code review" or "evaluated code" about Activus.

## 8. Drift between profile copies

Adam's facts are stored in at least six places: `CLAUDE.md`, `SKILL/01-candidate-profile.md`, `resume.md`,
`adam_colyer_resume.txt`, `application_facts.md`, `daily/apply_profile.json`, plus `DEFAULT_PROFILE` in `apply_answers.py`.
Drift already exists (Mercor start date, Activus description, Java/SQL status, Legal Eagle link). No test compares them. `[code]`
