# /interview - Prepare for an Interview on a Tracked Application

You are preparing the user for a real, scheduled interview on one of their applications. The frameworks for this already exist - `07-interview-prep.md` (STAR examples, tough questions, questions to ask, roleplay protocol) and the Company Research Checklist in `04-job-evaluation.md` - and the `/outcome` archive records which stage the user is at and what earlier stages surfaced. This command wires them together into a stage-specific prep pack and an optional mock interview.

`/interview` optimizes what the company hears. The interviewer has read the submitted CV, so everything prepared here must match what it claims.

Follow these steps **in order**.

---

## Step 0: Parse Input

`$ARGUMENTS` may contain a company name (optionally with a role), e.g. `/interview acme`.

- **With an argument:** match against `job_search_tracker.csv` rows (case-insensitive on company, then role). One match → proceed. Several → list and ask. None → this application isn't tracked; suggest `/outcome <company>` to register it first, or accept the posting and role details directly if the user wants to prep anyway.
- **Without an argument:** list tracker rows whose status suggests a live process (`interview`, `offer`, or recently `applied`) and ask which one. If the tracker is empty, ask for the company, role, and posting.

v1 preps for a **specific application**. Generic no-target practice is out of scope - if asked, prep against a real tracked application instead.

---

## Step 1: Load the Application Context

1. **The archive** (maintained by `/outcome`): `documents/applications/<company>_<role>/`
   - `job_posting.md` - the exact posting the user applied to
   - The archived resume - what was actually submitted. **This is what the interviewer read**; every talking point must be consistent with its claims.
   - `outcome.md` - the stage reached so far and any recorded feedback from earlier stages. Feedback from stage N is the highest-value input for stage N+1 prep.
2. **Fallbacks** (the application may predate `/outcome`): posting via WebFetch on the tracker row's `source` URL, or ask the user to paste it; the resume via the tracker row's `cv_file`. State plainly which context is missing rather than guessing - and suggest `/outcome <company>` to build the archive for next time.
3. **Ask the user what this interview is** (skip anything `outcome.md` already records): stage (phone screen / technical / case / final round), date, format (phone, video, onsite), and who is interviewing (names and titles, if known).
4. **Read the frameworks once** - do not re-read them in later steps:
   - `.claude/skills/job-application-assistant/07-interview-prep.md`
   - `.claude/skills/job-application-assistant/01-candidate-profile.md`
   - `.claude/skills/job-application-assistant/02-behavioral-profile.md`
   - `.claude/skills/job-application-assistant/04-job-evaluation.md`

---

## Step 2: Research the Company (Interview-Focused)

Execute the Company Research Checklist that `04-job-evaluation.md` defines: company website (mission, values, recent news), review sites, LinkedIn (team size, recent hires), and media coverage (growth, restructuring, workplace issues).

Additions for interview purposes:

- **Interviewer angle:** if interviewer names are known (from Step 1 or the tracker's `contact_person`), look up their public professional profile. A hiring manager probes team fit and motivation; a senior engineer probes technical depth; HR probes the CV timeline. Note the likely angle per interviewer - do not speculate beyond public information.
- **Conversation hooks:** 2-3 recent, verifiable company specifics (a product launch, a stated strategic priority) the user can reference naturally in answers and in the "why this company" moment.

**Verify before using:** every company claim that will appear in the prep pack must be independently confirmed via WebFetch/WebSearch - same rule the repo applies to cover-letter claims. An unverified "fact" delivered confidently in an interview is worse than no fact.

### Company interview reports

Run this before writing the prep pack. Do not pass `--ai`. If no public reports turn up, you write the role-specific preparation in this conversation.

```
python job_scraper/interview_intel.py "<company>" --role "<role>" --out "documents/applications/<company>_<role>/interview_intel.md"
```

The tool searches DuckDuckGo, Hacker News, and Reddit for write-ups of how this company interviews. It does not open Glassdoor, Indeed, ZipRecruiter, or Blind. A snippet from those sites is labeled snippet-only.

- **Reports found:** copy "What people reported" and "What to expect" into the prep pack with their links. Do not add rounds, questions, or timelines the reports do not mention. One write-up is one person's experience. A report older than three years is flagged; say the process may have changed.
- **Nothing found, or the search was blocked:** the first sentence of this section must say that no public interview report turned up, and it must include any search note (a bot check, a blocked forum). Then write preparation under the exact lead `AI estimate. This is not a report of what <company> does.` After that sentence, do not use the company name, do not cite Glassdoor or "candidates say," and do not describe a process as if this company uses it. Question types for this kind of role, STAR stories from the submitted resume, and questions to ask are in bounds. Replace the file's generic bullets with that tighter version and keep the lead sentence.
- The AI estimate is not a verified company fact. Do not reuse it as a "why this company" hook.

### Company reviews

Run this with the interview search:

```
python job_scraper/company_reviews.py "<company>" --out "documents/applications/<company>_<role>/company_reviews.md"
```

The tool uses the same public sources and the same closed sites (Glassdoor, Indeed, ZipRecruiter, Blind).

- **Reviews found:** copy "What people wrote" and "What those reviews mention" into the prep pack with their links. Do not add pay, hours, culture, or a score the reviews do not state. One review is one person's account. A review older than three years is flagged.
- **Nothing found, or the search was blocked:** say that, and include the search note. Do not write a culture summary or a rating to fill the gap. A blank result is not a score.

---

## Step 3: Build the Prep Pack

Assemble a stage-appropriate prep document with these sections:

### 1. What to expect at this company
The interview-intel result from Step 2, kept in its own words. Sourced bullets stay sourced. If the tool found nothing, this section starts by saying so and the only process advice under it is the labeled AI estimate.

### 2. What employees have written
The company-reviews result from Step 2, kept in its own words. If none were found, this section says so and stops. It does not add a rating.

### 3. Likely questions
Derive from four sources, in priority order:
1. **Recorded feedback from earlier stages** (`outcome.md`) - anything flagged, doubted, or left unresolved will come back
2. **The fit evaluation's gaps** - the requirements where the profile is weakest are the likeliest probes. For each, prepare an honest bridge answer per `07`'s "You don't have [X]" pattern: acknowledge, connect adjacent experience, show the learning path. **Never prepare an answer that invents experience.**
3. **The posting's stated requirements** - competency by competency
4. **The stage type** - phone screens get motivation and timeline questions; technical rounds get the posting's stack; final rounds get values, salary, and "any reservations" questions

### 4. STAR answer mapping
Match the ready-made STAR examples in `07-interview-prep.md` to the likely questions using their "Use for" tags. Then:
- For likely questions **no existing STAR example covers**, draft a new STAR answer grounded strictly in facts from `01-candidate-profile.md` - profile facts arranged into S/T/A/R, not embellished. Include these drafts in the prep pack; offer to append them to `07-interview-prep.md` only if the user explicitly approves.
- If incomplete STAR stubs are relevant to this role, surface them: the user should fill in the details before the interview.

### 5. Consistency brief
A short list of the specific claims the submitted CV makes (achievements, numbers, skills emphasized) that the interviewer is most likely to probe. The rule stated plainly: **no claim in the room that isn't on the paper, and every claim on the paper must be defensible in depth.**

### 6. Tough questions, customized
The relevant entries from `07`'s tough-question list with per-application answers - "Why this company specifically?" must use the verified hooks from Step 2, never a generic line.

### 7. Questions to ask
Pick 4-6 from `07`'s categories, customized to the research and the stage: role and team questions at screens, tech and growth questions at technical rounds, culture and leadership questions by the final round (that is the last chance to detect a deal-breaker). Cut any question the research already answers publicly - asking it signals you didn't look.

### 8. Logistics
The phone/video tips from `07` when the format calls for them, plus date and interviewer names as a header.

Save the tool output with `--out` to `documents/applications/<company>_<role>/interview_intel.md` and `company_reviews.md`. Save the pack to `interview_prep_<stage>.md` in that same folder (create the folder if this application predates `/outcome`). The folder is gitignored, so the pack stays personal; one file per stage, so earlier packs remain as history. Present the pack in chat as well - the file is the artifact, the conversation is the delivery. Lead with what the public reports say, or with the sentence that none were found. Do the same for employee reviews.

---

## Step 4: Offer a Mock Interview

Ask if the user wants to practice. If yes, run the roleplay **in this conversation** following the Roleplay Guidelines in `07-interview-prep.md` exactly: warm-up first, then role-specific technical questions, 1-2 behavioral questions tied to the posting's competencies, and one tough question or curveball. After each answer, give brief feedback - what worked, what to sharpen, and which STAR example from the pack would have served better.

Calibrate feedback against `02-behavioral-profile.md`: coach toward the user's natural register, not a generic ideal.

---

## Step 5: Close the Loop

End with:

> Good luck. After the interview, run `/outcome <company>` to log the stage and any feedback - it sharpens the prep for the next round.

If Step 3 drafted new STAR answers the user approved for keeps, remind them those were appended to `07-interview-prep.md` (or offer again if they deferred).

---

## Important Rules

1. **Consistency with the submitted documents.** The interviewer read the archived CV; prep must never contradict them or coach claims beyond them.
2. **Honesty on gaps.** Weak matches get bridge answers (acknowledge → adjacent experience → learning path), never invented experience. Same rule as everywhere else in this repo.
3. **Verified research only.** Company specifics go in the pack only after independent confirmation. Interviewer notes stick to public professional information.
4. **Stage-appropriate prep.** A phone screen pack and a final-round pack are different documents; recorded feedback from earlier stages takes priority over generic question lists.
5. **Write only to the application archive.** The prep pack lands in `documents/applications/<company>_<role>/`; framework and profile files are never edited, except appending user-approved STAR examples to `07-interview-prep.md` on explicit request.
6. **Sourced process vs AI estimate.** What to expect comes from `job_scraper/interview_intel.py`. Public reports are cited. No reports, or a blocked search, is said in the first sentence. Anything after that about the process is labeled `AI estimate. This is not a report of what <company> does.` and is not used as a company fact.
7. **Sourced reviews.** Employee reviews come from `job_scraper/company_reviews.py`. Quote them. No reviews, or a blocked search, is said plainly. Do not invent a rating, a culture summary, or a Glassdoor score.
