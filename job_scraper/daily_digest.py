"""Run the daily scrape and write Word documents of that day's new jobs.

The scheduled task (6, 8, 10, noon, 2, 4 and 6) calls this with no arguments. It runs `pipeline.py all`, then
writes:

    daily/YYYY-MM-DD.docx   jobs first found on that date
    daily/latest.docx       jobs found in the last 14 days

A job is written into one date file only. It is also added to latest.docx and
removed from there 14 days after the day it was found. The first days after a
posting goes up are when applications get read. A week is still worth acting
on. At 14 days the listing is past the usual first review, and that is also as
far back as the scraper looks.

Inside each file, high and medium jobs are grouped by position type. Low-fit
jobs are left out of the Word files (the intro says how many); they stay in
latest.json so a later run does not list them again. A list at the top jumps
to each heading, and the same headings show in Word's navigation pane. Each
card says why the grade is high or medium.

`--from-existing` skips the scrape and writes the file from today's
candidates.json. That is only for checking the layout.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent.parent
SCRAPER = ROOT / "job_scraper"
DAILY = ROOT / "daily"
PYTHON = Path(sys.executable)
BUN_DIR = Path(r"C:\Users\adamc\.bun\bin")
STORE_PATH = DAILY / "latest.json"
ORDER = {"high": 0, "medium": 1, "low": 2}
# Drop a job from latest.txt this many days after the day it was found.
WINDOW_DAYS = 14


def _rank_key(job):
    """Grade first, then the 0-100 fit score (best first), then newest, then title."""
    return (
        ORDER.get(job.get("fit"), 9),
        -(job.get("fit_score") or 0),
        _date_key(job.get("found_on")),
        job.get("title") or "",
    )


def _title_reason(title, skills=None):
    """Why the title score is high, medium, or low. Same order as pipeline.fit."""
    import pipeline

    t = title or ""
    if pipeline.INTERN_RE.search(t):
        return "The title says intern."
    if "2027" in t:
        return "The title says 2027."
    if re.search(
        r"\b(firmware|embedded|electrical|manufacturing|cyber security architect|scada|lidar|maritime autonomous)\b",
        t,
        re.I,
    ):
        return "The title is outside the React, TypeScript, and Python work."
    is_bridge = bool(pipeline.BRIDGE_RE.search(t))
    is_senior = bool(pipeline.SENIOR_RE.search(t)) and not re.search(
        r"\b(junior|entry|associate)\b", t, re.I
    )
    if is_senior and not is_bridge:
        return "The title says senior, lead, staff, principal, or director."
    if is_bridge:
        if re.search(r"\b(director|vp\b|head of|chief|vice president)\b", t, re.I):
            return "The title is a bridge role and it says director, VP, or head."
        if is_senior:
            return "The title is a bridge role and it says senior or architect, so it stays medium."
        return "The title is technical sales, a sales engineer, pre-sales, a solutions consultant, or implementation work, and it does not say senior or director."
    if pipeline.AI_RE.search(t) and not is_senior:
        if pipeline.ENTRY_RE.search(t) or re.search(
            r"\b(application|fullstack|full[- ]?stack|software)\b", t, re.I
        ):
            return "The title is AI engineering and it says an entry or software role."
        return "The title mentions AI and it does not say senior."
    if pipeline.ENTRY_RE.search(t) and pipeline.FS_RE.search(t):
        return "The title is entry-level engineering."
    if re.search(r"\b(full[- ]?stack)\b", t, re.I) and not is_senior:
        return "The title says full stack and it does not say senior."
    if pipeline.FS_RE.search(t) and not is_senior:
        return "The title is engineering and it does not say junior or senior, so it stays medium."
    if pipeline.QA_RE.search(t) and not is_senior:
        return "The title is QA and it does not say senior."
    sl = " ".join(skills or []).lower()
    if any(s in sl for s in ["react", "typescript", "next.js", "nextjs", "python", "fastapi", "langchain", "rag"]):
        return "The title is not a scored role. A React, TypeScript, or Python tag on the card makes it medium."
    return "The title does not match technical sales, a sales engineer, entry-level engineering, or junior QA."


_PAGE_CHROME_RE = re.compile(
    r"skip to content|see our open jobs|hacker news new \||just a moment|performing security verification",
    re.I,
)


def _clip_sentence(text):
    text = " ".join((text or "").split()).strip(" -")
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text


def _posting_quote(description, prefer_requirement=False):
    """One readable sentence. Skip a mid-word excerpt and site navigation."""
    import pipeline

    desc = " ".join((description or "").split())
    if not desc:
        return ""
    hn = re.search(r"on:\s*Ask HN: Who is hiring\?\s*\([^)]+\)\s*", desc, re.I)
    if hn:
        desc = desc[hn.end():]
    # Split on a sentence end. Leave "U.S." and other single-letter abbreviations intact.
    parts = [
        part.strip()
        for part in re.split(r"(?<=[a-z)][.!?])\s+(?=[A-Z0-9])", desc)
        if part.strip()
    ]

    def from_requirement():
        for part in parts:
            years = pipeline.YEARS_EXPERIENCE_RE.search(part)
            clearance = pipeline.CLEARANCE_RE.search(part)
            match = years or clearance
            if not match:
                continue
            text = part
            if text and text[0].islower():
                window_start = max(0, match.start() - 80)
                caps = list(re.finditer(r"\b[A-Z]", text[window_start:match.start()]))
                colon = text.rfind(": ", 0, match.start())
                if caps:
                    text = text[window_start + caps[-1].start() :]
                elif colon >= 0 and match.start() - colon < 90:
                    text = text[colon + 2 :]
                else:
                    text = text[match.start() :]
            if _PAGE_CHROME_RE.search(text):
                continue
            return _clip_sentence(text)
        return ""

    if prefer_requirement:
        found = from_requirement()
        if found:
            return found
    for part in parts:
        if len(part) < 24 or _PAGE_CHROME_RE.search(part):
            continue
        first = part.lstrip("\"'“”")
        if first and (first[0].isupper() or first[0].isdigit()):
            return _clip_sentence(part)
    # The saved excerpt can start mid-word. Use the first capitalized phrase.
    match = re.search(r"\b([A-Z][\w'’,-]*(?:\s+\S+){5,})", desc)
    if match and not _PAGE_CHROME_RE.search(match.group(1)):
        return _clip_sentence(match.group(1))
    return ""


def is_board_shell(description):
    """True when the saved page is an ATS error template, not the job text.

    Spark Hire leaves `{{position.name}}` and the misspelling "occured".
    UltiPro leaves `showAllLocations` and an unsupported-browser message.
    A real sentence that mentions open positions is not a shell by itself.
    """
    raw = description or ""
    low = raw.lower()
    if "{{position.name}}" in raw or "an error occured" in low:
        return True
    if "showalllocations" in low or "unsupported browser" in low:
        return True
    return False


def apply_board_shells(jobs):
    """A board error page is not a high grade and does not stay in the High section.

    A star stays only when its quote is still in the saved description.
    """
    for job in jobs:
        if not is_board_shell(job.get("description")):
            continue
        job["body_status"] = "shell"
        job["fit"] = "low"
        job["fit_reasons"] = []
        job["fit_blockers"] = ["The posting page did not render."]
        job.pop("fit_before_location", None)
        job.pop("location_block", None)
        notes = job.get("star_notes") or []
        desc = job.get("description") or ""
        kept = [note for note in notes if (note.get("quote") or "") in desc]
        if kept:
            job["star_notes"] = kept
        else:
            job.pop("star_notes", None)
    return jobs


def _body_sentence(job):
    """One sentence from the posting, or a line that says the page had no job text."""
    import pipeline

    if (job.get("body_status") or "") == "shell" or is_board_shell(job.get("description")):
        return "The posting page did not render."
    desc = job.get("description") or ""
    status = job.get("body_status") or ""
    stored = (job.get("fit") or "low").lower()
    title_fit = pipeline._fit_from_title(job.get("title") or "", job.get("skills") or [])
    prefer = ORDER.get(stored, 2) > ORDER.get(title_fit, 2)
    sentence = _posting_quote(desc, prefer_requirement=prefer)
    if status == "opened" and sentence:
        return f"Posting: {sentence}"
    if status == "unread":
        if sentence:
            return f"The full page did not load. Card text: {sentence}"
        return "The posting page did not include the job text."
    if status == "opened":
        return "The posting page did not include the job text."
    if sentence:
        return f"Card text: {sentence}"
    return "The posting body was not opened."


def _demotion_note(title, skills, description, stored):
    import pipeline

    title_fit = pipeline._fit_from_title(title, skills)
    if not description or stored == title_fit:
        return ""
    bits = []
    years = [int(n) for n in pipeline.YEARS_EXPERIENCE_RE.findall(description)]
    high_years = [y for y in years if y >= 5]
    if high_years:
        bits.append(f"the posting asks for {max(high_years)} years")
    if pipeline.CLEARANCE_RE.search(description):
        bits.append("the posting asks for a clearance")
    if not bits:
        return ""
    joined = " and ".join(bits)
    return f"The title score would be {title_fit}. {joined[0].upper()}{joined[1:]}, so the grade is {stored}."


def _why(job):
    """Grade, why that grade, then one line from the posting when we have it."""
    notes = (job.get("fit_blockers") or []) + (job.get("fit_reasons") or [])
    if notes and job.get("fit_before_location") != "high":
        score = job.get("fit_score")
        headline = f"{(job.get('fit') or 'low').capitalize()}" + (f", {score}/100." if score else ".")
        return f"{headline} {' '.join(notes)} {_body_sentence(job)}"
    title = job.get("title") or ""
    skills = job.get("skills") or []
    stored = (job.get("fit") or "low").lower()
    label = stored.capitalize()
    if job.get("fit_before_location") == "high" and stored != "high":
        block = job.get("location_block") or "the location is outside the requested list"
        reason = (
            f"The title score would be high. {block[0].upper()}{block[1:]}, "
            f"so the grade is {stored}."
        )
        if notes:
            reason = f"{reason} {' '.join(notes)}"
    else:
        demotion = _demotion_note(title, skills, job.get("description") or "", stored)
        reason = demotion or _title_reason(title, skills)
    return f"{label}. {reason} {_body_sentence(job)}"


def apply_link(job):
    """The employer's own apply page when we found it, otherwise the link the job came with."""
    return job.get("apply_url") or job.get("url") or ""


def _minimums_line(job):
    """One line: does the job meet the minimums the posting states (yes / stretch / no / not checked)."""
    state = job.get("minimums")
    note = (job.get("minimums_note") or "").strip()
    head = "Meets the posting's stated minimums:"
    if state == "yes":
        return f"{head} yes."
    if state == "stretch":
        return f"{head} stretch. {note}".strip()
    if state == "unchecked":
        return f"{head} not checked. {note}".strip()
    if state == "no":
        return f"{head} no. {note}".strip()
    return ""


def _where(job):
    loc = (job.get("location") or "").strip() or "Location not listed"
    wm = (job.get("work_mode") or "").strip()
    if wm and wm.lower() not in loc.lower():
        return f"{loc} (card says {wm})"
    return loc


def _pipeline_notes(log_text):
    notes = []
    for line in (log_text or "").splitlines():
        if line.startswith("TOTAL ") or line.startswith("SKIP ") or line.startswith("FAIL ") or line.startswith("DONE ") or line.startswith("HEALTH-WARN"):
            notes.append(line.strip())
    return notes


def _counts(jobs):
    return {k: sum(1 for j in jobs if j.get("fit") == k) for k in ("high", "medium", "low")}


def _date_key(value):
    """Sort key so a missing or newer date comes first inside a fit band."""
    try:
        return -date.fromisoformat(value).toordinal()
    except (TypeError, ValueError):
        return 0


def _read_store_doc():
    if not STORE_PATH.exists():
        return {}
    return json.loads(STORE_PATH.read_text(encoding="utf-8"))


def load_store():
    return _read_store_doc().get("jobs") or {}


def load_sources():
    """SKIP, FAIL, and TOTAL lines from the last scrape, kept on the store."""
    sources = _read_store_doc().get("sources") or []
    return [line for line in sources if isinstance(line, str)]


def save_store(jobs, sources=None):
    """Write the window. A missing sources argument keeps the lines already on disk."""
    if sources is None:
        sources = load_sources()
    DAILY.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(
            {"window_days": WINDOW_DAYS, "jobs": jobs, "sources": list(sources)},
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )


def _key(url):
    import pipeline
    return pipeline._url_key(url or "") or (url or "")


def pipeline_dedupe_key(job):
    import pipeline
    return pipeline.dedupe_key(job)


def collapse_store(store):
    """Drop stored copies of a job that is also stored from another site.

    Keeps the employer's own link over an aggregator repost, otherwise the earliest
    found_on. The kept row also keeps the longer description. Returns the number removed.
    """
    import pipeline
    # A repost whose employer page is also stored: keep the employer's row.
    by_url = {_key(row.get("url")): k for k, row in store.items()}
    reposts = [
        k for k, row in store.items()
        if row.get("apply_url") and by_url.get(_key(row["apply_url"]), k) != k
    ]
    for k in reposts:
        del store[k]
    removed_reposts = len(reposts)
    best = {}
    for key, row in store.items():
        twin = pipeline.dedupe_key(row)
        if not twin:
            continue
        cur = best.get(twin)
        if cur is None:
            best[twin] = key
            continue
        cur_row = store[cur]
        incoming_employer = pipeline._is_aggregator(cur_row) and not pipeline._is_aggregator(row)
        current_employer = pipeline._is_aggregator(row) and not pipeline._is_aggregator(cur_row)
        if incoming_employer:
            pipeline._copy_longer_description(row, cur_row)
            pipeline._copy_earliest_found(row, cur_row)
            best[twin] = key
        elif current_employer:
            pipeline._copy_longer_description(cur_row, row)
            pipeline._copy_earliest_found(cur_row, row)
        elif (row.get("found_on") or "9999") < (cur_row.get("found_on") or "9999"):
            pipeline._copy_longer_description(row, cur_row)
            pipeline._copy_earliest_found(row, cur_row)
            best[twin] = key
        else:
            pipeline._copy_longer_description(cur_row, row)
            pipeline._copy_earliest_found(cur_row, row)
    keep = set(best.values()) | {k for k, r in store.items() if not pipeline.dedupe_key(r)}
    removed = [k for k in store if k not in keep]
    for k in removed:
        del store[k]
    return len(removed) + removed_reposts


def _row_needs_text(row):
    """True when a stored row was never read, or the saved page was a shell."""
    status = row.get("body_status") or ""
    desc = row.get("description") or ""
    blockers = " ".join(row.get("fit_blockers") or [])
    if status in ("shell", "unread"):
        return True
    if "could not be read" in blockers or "did not render" in blockers:
        return True
    if is_board_shell(desc):
        return True
    return not desc and status != "opened"


def _incoming_text_better(stored, job):
    """True when this run actually read a posting the store only had a shell for."""
    desc = job.get("description") or ""
    if not desc or is_board_shell(desc):
        return False
    if (job.get("body_status") or "") == "opened":
        return True
    return len(desc) > len(stored.get("description") or "")


def _refresh_stored_text(stored, job):
    """Replace the unread text and grade. Leave the day it was first found."""
    stored["description"] = (job.get("description") or "")[:8000]
    stored["body_status"] = job.get("body_status")
    if job.get("fit"):
        stored["fit"] = job.get("fit")
    stored["fit_reasons"] = job.get("fit_reasons") or []
    stored["fit_blockers"] = job.get("fit_blockers") or []
    stored["entry_evidence"] = bool(job.get("entry_evidence"))
    stored["disqualified"] = bool(job.get("disqualified"))
    if job.get("salary"):
        stored["salary"] = job.get("salary")


def merge_jobs(store, jobs, found_on):
    """Add jobs that are not already stored. Keep the original found_on."""
    added = 0
    have = {k for k in (pipeline_dedupe_key(row) for row in store.values()) if k}
    for job in jobs:
        url = job.get("url") or ""
        key = _key(url)
        if not key:
            continue
        if key in store:
            stored = store[key]
            if _row_needs_text(stored) and _incoming_text_better(stored, job):
                _refresh_stored_text(stored, job)
            continue
        twin = pipeline_dedupe_key(job)  # the same job already stored from another site
        if twin and twin in have:
            continue
        if twin:
            have.add(twin)
        store[key] = {
            "found_on": found_on,
            "found_at": datetime.now().strftime("%H:%M"),
            "date": (job.get("date") or "")[:10],
            "fit": job.get("fit"),
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "work_mode": job.get("work_mode"),
            "url": url,
            "description": (job.get("description") or "")[:8000],
            "skills": job.get("skills") or [],
            "salary": job.get("salary"),
            "body_status": job.get("body_status"),
            "fit_reasons": job.get("fit_reasons") or [],
            "fit_blockers": job.get("fit_blockers") or [],
            "fit_score": job.get("fit_score") or 0,
            "fit_parts": job.get("fit_parts"),
            "entry_evidence": bool(job.get("entry_evidence")),
            "disqualified": bool(job.get("disqualified")),
        }
        added += 1
    return added


def prune_jobs(store, today, window_days=WINDOW_DAYS):
    """Remove jobs found `window_days` or more before `today`."""
    cutoff = today - timedelta(days=window_days)
    kept = {}
    removed = 0
    for key, job in store.items():
        raw = job.get("found_on") or today.isoformat()
        try:
            found = date.fromisoformat(raw)
        except ValueError:
            found = today
        if found <= cutoff:
            removed += 1
            continue
        kept[key] = job
    return kept, removed


def jobs_found_on(store, day):
    """Jobs whose first-found date is exactly `day`. Other days stay out."""
    return [job for job in store.values() if job.get("found_on") == day]


def _ordered(jobs):
    return sorted(
        jobs,
        key=_rank_key,
    )


# Reading order for the morning file. Closest resume match first, then the
# paid bridge, then the engineering goal, then the longer and more senior
# sections. Fit scoring is unchanged.
TYPE_ORDER = (
    ("technical_sales", "Technical sales"),
    ("sales_engineer", "Sales engineer and solutions engineer"),
    ("engineering", "Engineering"),
    ("customer_engineer", "Customer engineer and implementation"),
    ("presales", "Pre-sales and solutions consultant"),
    ("qa", "QA"),
    ("solutions_architect", "Solutions architect"),
    ("other", "Other"),
)
_TECH_SALES_RE = re.compile(r"\btechnical sales\b", re.I)
# "Pre-Sales Engineer" contains the letters "sales engineer" after a hyphen.
# The lookbehind keeps that title in the pre-sales section.
_SE_RE = re.compile(
    r"(?<!pre-)(?<!pre )\b(?:sales|solutions) engineers?\b",
    re.I,
)
_FAE_RE = re.compile(r"\b(?:field )?applications? engineer\b", re.I)
_SA_RE = re.compile(r"\bsolutions? architect\b", re.I)
_PRESALES_ARCH_RE = re.compile(
    r"\bpre[-\s]?sales\b.{0,40}\barchitect\b|\barchitect\b.{0,40}\bpre[-\s]?sales\b",
    re.I,
)
_PRESALES_RE = re.compile(r"\b(?:pre[-\s]?sales|solutions? consultant)\b", re.I)
_IMPL_RE = re.compile(
    r"\b(?:technical account|customer engineer|forward deployed|"
    r"implementation engineer|implementation consultant|implementation specialist|"
    r"implementation manager|onboarding engineer)\b",
    re.I,
)
# Software QA only. "Quality Assurance Technician" and "Quality Engineer"
# stay in Other. Those titles are plant and lab quality, not software test.
_QA_TITLE_RE = re.compile(r"\b(?:qa|sdet|software tester|software quality assurance)\b", re.I)
_QA_ENGINEER_RE = re.compile(r"\bquality assurance(?!\s+technician)\b", re.I)
_DEV_RE = re.compile(r"\b(?:developers?|systems development engineers?)\b", re.I)
_SOFTWARE_ENGINEERS_RE = re.compile(r"\bsoftware engineers\b", re.I)
_AI_ENG_RE = re.compile(
    r"\b(?:ai|ml|llm|machine learning)\b.{0,30}\b(?:engineer|developer)\b|"
    r"\b(?:engineer|developer)\b.{0,30}\b(?:ai|ml|llm|machine learning)\b",
    re.I,
)


# Engineering titles stay on the list only at entry level: junior, trainee,
# associate, early career, new grad, or level I. Level II and a bare
# "Software Engineer" are left off the list.
_ENGINEERING_ABOVE_ENTRY_RE = re.compile(
    r"\b(?:ii|iii|iv|senior|sr\.?|staff|principal|lead)\b|"
    r"\b(?:engineer|developer)\s+[2-9]\b",
    re.I,
)
_ENGINEERING_ENTRY_RE = re.compile(
    r"\b(?:junior|jr\.?|entry(?:[- ]level)?|trainee|associate|new grad|early career|"
    r"graduate|intern(?:ship)?|co-?op|campus)\b|"
    r"\b(?:engineer|developer)\s+(?:i|1)\b",
    re.I,
)
def position_type(title):
    """Which section a title belongs in. One title, one section."""
    import pipeline

    t = title or ""
    if _QA_TITLE_RE.search(t) or _QA_ENGINEER_RE.search(t):
        return "qa"
    if _TECH_SALES_RE.search(t):
        return "technical_sales"
    if _SE_RE.search(t) or _FAE_RE.search(t):
        return "sales_engineer"
    if _SA_RE.search(t) or _PRESALES_ARCH_RE.search(t):
        return "solutions_architect"
    if _PRESALES_RE.search(t):
        return "presales"
    if _IMPL_RE.search(t):
        return "customer_engineer"
    if pipeline.FS_RE.search(t) or _DEV_RE.search(t) or _SOFTWARE_ENGINEERS_RE.search(t) or _AI_ENG_RE.search(t):
        return "engineering"
    return "other"


# Education the posting can name that is already on the resume: B.A. Marketing,
# Coding Temple full-stack certificate, HBS Online Business Analytics.
_BUSINESS_DEGREE_RE = re.compile(
    r"\b(?:bachelor(?:'?s)?(?:\s+degree)?|b\.?\s?a\.?\b|undergraduate|college degree|degree)"
    r"\s+(?:in|of)\s+(?:[\w,/+&-]+\s+){0,8}?(?:business administration|business|marketing)\b|"
    r"\b(?:business administration|marketing|business)\s+degree\b|"
    r"\bdegree\s+in\s+(?:[\w,/+&-]+\s+){0,8}?(?:business administration|business|marketing)\b|"
    r"\bbba\b",
    re.I,
)
_BOOTCAMP_RE = re.compile(
    r"\b(?:boot\s*camps?|bootcamps?|coding camps?|code camps?)\b|"
    r"\b(?:coding|software development|full[- ]stack|web development)\s+certificate\b|"
    r"\bcertificate\s+(?:program\s+)?in\s+(?:software|full[- ]stack|web development|coding)\b",
    re.I,
)
_ANALYTICS_CERT_RE = re.compile(
    r"\bbusiness analytics\b.{0,40}\b(?:certificate|certification|course)\b|"
    r"\b(?:certificate|certification|course)\b.{0,40}\bbusiness analytics\b",
    re.I,
)


def _plain_chunks(text):
    plain = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", text or "")
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = plain.replace("\u00a0", " ")
    return [ " ".join(chunk.split()) for chunk in re.split(r"[\n\r]+|(?<=[.!?])\s+", plain) ]


def resume_hits(text):
    """Qualifications the posting names that are already on the resume.

    Each hit is a short label and the sentence it came from. A business-development
    mention with no degree, and a generic certificate, are not hits.
    """
    found = []
    seen = set()
    rules = (
        ("business or marketing degree", _BUSINESS_DEGREE_RE),
        ("bootcamp or coding certificate", _BOOTCAMP_RE),
        ("business analytics certificate", _ANALYTICS_CERT_RE),
    )
    for line in _plain_chunks(text):
        if not line:
            continue
        for label, pattern in rules:
            if label in seen or not pattern.search(line):
                continue
            seen.add(label)
            found.append({"label": label, "quote": line[:240]})
    return found


def engineering_level_blocked(title):
    """True when an engineering title is above entry, or has no entry wording."""
    if position_type(title) != "engineering":
        return False
    if _ENGINEERING_ABOVE_ENTRY_RE.search(title or ""):
        return True
    return not _ENGINEERING_ENTRY_RE.search(title or "")


def exclusive_engineering_degree(text):
    """Sentence that requires an engineering or computer-science degree, or ''.

    Same rule as scoring.exclusive_degree. "Or related field" is not an
    alternative. A listed "business" or "marketing" degree is.
    """
    import scoring

    return scoring.exclusive_degree(text)


def omit_reason(job):
    """Why a stored job stays out of the Word file, or None to list it.

    Engineering is listed only at entry level. Any role is left out when the
    saved qualifications require a degree in engineering or computer science
    and offer no other degree or equivalent experience. Empty qualifications
    are not treated as a degree requirement.
    """
    if job.get("omit") == "degree":
        return "degree"
    if job.get("disqualified"):
        return "not qualified"
    if engineering_level_blocked(job.get("title") or "") and not job.get("entry_evidence"):
        return "engineering level"
    if exclusive_engineering_degree(job.get("description") or ""):
        return "degree"
    return None


_ORACLE_REQ_RE = re.compile(r"/job/(\d+)\b", re.I)


def requisition_key(job):
    """Oracle Candidate Experience requisition, or '' when the URL has none."""
    match = _ORACLE_REQ_RE.search(job.get("url") or "")
    if match:
        return "oracle:" + match.group(1)
    return ""


def collapse_same_requisition(jobs):
    """One card per Oracle requisition. Keep the earliest found_on.

    The store still holds every URL. Only the list the client sees collapses.
    """
    ranked = sorted(
        enumerate(jobs),
        key=lambda pair: (
            pair[1].get("found_on") or "9999-99-99",
            pair[1].get("url") or "",
            pair[0],
        ),
    )
    drop = set()
    seen = set()
    for index, job in ranked:
        key = requisition_key(job)
        if not key:
            continue
        if key in seen:
            drop.add(index)
            continue
        seen.add(key)
    return [job for index, job in enumerate(jobs) if index not in drop]


def listable(jobs):
    return collapse_same_requisition([job for job in jobs if omit_reason(job) is None])


# Job boards that give only a ~275-character snippet and block a plain request for the page.
# A row from one of them whose text was never read cannot be graded, so it is listed apart.
SNIPPET_ONLY_HOSTS = ("jooble.org",)


def is_unchecked(job):
    """True for a snippet-only board row whose posting text was never read.

    The grade on such a row comes from the title alone. Hand checks of 16 of them found no Fit and
    5 Stretch, so they are kept out of the lists and counted as left out. The store keeps them.
    """
    if (job.get("fit") or "").lower() not in ("high", "medium"):
        return False
    if job.get("minimums") != "unchecked":
        return False
    return any(host in (job.get("url") or "") for host in SNIPPET_ONLY_HOSTS)


def presentable_split(jobs):
    """High and medium jobs the Word file lists, and the ones it leaves out.

    A location cap stays in the shown list at its capped grade. Engineering
    level and an exclusive degree requirement go to the left-out list. Rows
    that were never read (see is_unchecked) are in neither list.
    """
    shown = []
    left_out = []
    for job in jobs:
        reason = omit_reason(job)
        fit = (job.get("fit") or "").lower()
        if reason:
            if fit in ("high", "medium"):
                left_out.append((job, reason))
            continue
        if fit in ("high", "medium") and not is_unchecked(job):
            shown.append(job)
    return collapse_same_requisition(shown), left_out


def type_sections(jobs):
    """Jobs split by position type, in TYPE_ORDER. Empty types are left out."""
    buckets = {key: [] for key, _label in TYPE_ORDER}
    for job in jobs:
        buckets[position_type(job.get("title") or "")].append(job)
    sections = []
    for key, label in TYPE_ORDER:
        group = _ordered(buckets[key])
        if group:
            sections.append({"key": key, "label": label, "jobs": group})
    return sections


def _type_heading(label, jobs):
    counts = _counts(jobs)
    parts = []
    for key, name in (("high", "High"), ("medium", "Medium")):
        if counts[key]:
            parts.append(f"{name} {counts[key]}")
    detail = ", ".join(parts) if parts else "0"
    return f"{label} ({len(jobs)}). {detail}"


def fit_jobs(jobs):
    """Jobs worth showing: listable, read, and graded high or medium. Low fits and snippet-only rows stay out."""
    return [job for job in listable(jobs) if (job.get("fit") or "low") != "low" and not is_unchecked(job)]


def left_out_count(jobs):
    """How many listable jobs the Word files skip: graded low, or a snippet-only row that was never read."""
    return len(listable(jobs)) - len(fit_jobs(jobs))


def _p(text, bold=False, size=22, space_after=80, color=None):
    """One Word paragraph. size is half-points (22 = 11pt)."""
    run = (
        f'<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        + ("<w:b/>" if bold else "")
        + (f'<w:color w:val="{color}"/>' if color else "")
        + f"</w:rPr><w:t xml:space=\"preserve\">{xml_escape(text)}</w:t></w:r>"
    )
    return (
        f'<w:p><w:pPr><w:spacing w:after="{space_after}" w:line="276" w:lineRule="auto"/></w:pPr>'
        f"{run}</w:p>"
    )


def _link_p(rel_id, url):
    label = xml_escape(url)
    return (
        "<w:p><w:pPr><w:spacing w:after=\"200\" w:line=\"276\" w:lineRule=\"auto\"/></w:pPr>"
        f'<w:hyperlink r:id="{rel_id}" w:history="1">'
        '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/>'
        '<w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr>'
        f'<w:t xml:space="preserve">{label}</w:t></w:r></w:hyperlink></w:p>'
    )


def _anchor_p(anchor, text):
    """A clickable jump to a heading bookmark in this same document."""
    label = xml_escape(text)
    return (
        "<w:p><w:pPr><w:spacing w:after=\"40\" w:line=\"276\" w:lineRule=\"auto\"/></w:pPr>"
        f'<w:hyperlink w:anchor="{anchor}" w:history="1">'
        '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/>'
        '<w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr>'
        f'<w:t xml:space="preserve">{label}</w:t></w:r></w:hyperlink></w:p>'
    )


def _section_heading(bookmark_id, anchor, text):
    """Heading 1 plus a bookmark so the index and the navigation pane can jump here."""
    label = xml_escape(text)
    return (
        "<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/>"
        '<w:spacing w:before="360" w:after="80"/></w:pPr>'
        f'<w:bookmarkStart w:id="{bookmark_id}" w:name="{anchor}"/>'
        '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:b/><w:sz w:val="28"/><w:szCs w:val="28"/>'
        '<w:color w:val="1F4E79"/></w:rPr>'
        f'<w:t xml:space="preserve">{label}</w:t></w:r>'
        f'<w:bookmarkEnd w:id="{bookmark_id}"/></w:p>'
    )


APPLIED_LOG_PATH = DAILY / "applied_for.json"
GREEN, RED = "00A050", "C00000"


def result_entries():
    """The applied log's entries (daily/applied_for.json), or {} when there is none or it cannot be read."""
    try:
        return json.loads(APPLIED_LOG_PATH.read_text(encoding="utf-8")).get("entries") or {}
    except (OSError, ValueError):
        return {}


def result_mark(job, entries):
    """(mark, text) for a job the applied log knows: a green check when it was applied for, a red X when the AI could not.
    None for a job nobody has tried yet."""
    entry = entries.get(_key(apply_link(job))) or entries.get(_key(job.get("url")))
    if entry is None:
        want = pipeline_dedupe_key(job)
        if want is not None:
            for other in entries.values():
                probe = {k: other.get(k) for k in ("title", "company", "location", "work_mode")}
                if pipeline_dedupe_key(probe) == want:
                    entry = other
                    break
    if entry is None:
        return None
    if entry.get("status") == "applied":
        who = entry.get("applied_by") or "AI"
        return "✅", f"{who} applied" + (f" on {entry['date_applied']}" if entry.get("date_applied") else "")
    why = (entry.get("reason") or "").strip()
    kind = not_applied_kind(why)
    head = {"blocked": "AI didn't apply, blocked", "gap": "AI didn't apply, not built yet", "closed": "Not applied"}[kind]
    return "❌", head + (f": {_clip(why, 140)}" if why else "")


BLOCK_MARKER = re.compile(r"too many unique questions", re.I)
GAP_MARKER = re.compile(r"no answer yet for", re.I)
CLOSED_WORDS = re.compile(r"closed|no longer|closed or gone|not found|404|expired|position (has been )?filled|gpa|move on|skipped|i'll do it|apply by hand", re.I)
BLOCKED_WORDS = re.compile(r"robot|captcha|cloudflare|\bbot\b|log ?in|sign[- ]in|account|verification code|email code|security code|assessment|video|"
                           r"human step|refused the click|blocked|too many", re.I)


def not_applied_kind(reason):
    """Why a job was not applied for: 'blocked' (bot check, login, too many unique questions), 'gap' (the project does not
    handle something a form asked or did yet) or 'closed' (nothing to fix: the posting is gone, a rule says move on)."""
    text = reason or ""
    if GAP_MARKER.search(text):
        return "gap"
    if BLOCK_MARKER.search(text):
        return "blocked"
    if CLOSED_WORDS.search(text):
        return "closed"
    return "blocked" if BLOCKED_WORDS.search(text) else "gap"


def _clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _job_blocks(job, rel_id, number, entries=None):
    """Paragraphs for one posting. rel_id is None when the card has no URL."""
    fit = (job.get("fit") or "low").upper()
    hits = job.get("star_notes") or resume_hits(job.get("description") or "")
    star = "★ " if hits else ""
    heading = f"{star}{number}. {fit} | {job.get('title') or 'Untitled'}"
    blocks = [
        _p(heading, bold=True, size=24, space_after=40),
        _p(f"{job.get('company') or 'Company not listed'} | {_where(job)}", size=22, space_after=40),
    ]
    mark = result_mark(job, entries) if entries else None
    if mark:
        blocks.append(_p(f"{mark[0]} {mark[1]}", bold=True, size=22, space_after=40, color=GREEN if mark[0] == "✅" else RED))
    if hits:
        labels = ", ".join(hit["label"] for hit in hits)
        blocks.append(_p(f"★ Posting lists: {labels}", size=22, space_after=40))
        for hit in hits:
            quote = (hit.get("quote") or "").strip()
            if quote:
                blocks.append(_p(quote, size=22, space_after=40))
    if job.get("found_on"):
        when = f"{job['found_on']} {job['found_at']}" if job.get("found_at") else job["found_on"]
        blocks.append(_p(f"Found: {when}", size=22, space_after=40))
    if job.get("salary"):
        blocks.append(_p(f"Pay on the card: {job['salary']}", size=22, space_after=40))
    blocks.append(_p(_why(job), size=22, space_after=40))
    minimums = _minimums_line(job)
    if minimums:
        blocks.append(_p(minimums, size=22, space_after=40))
    url = apply_link(job)
    if url and rel_id:
        blocks.append(_link_p(rel_id, url))
    else:
        blocks.append(_p("No link on the card.", size=22, space_after=200))
    return blocks


def write_docx(path, title, intro, jobs):
    """Write a US Letter .docx grouped by position type.

    Each posting link is an external hyperlink. Each type heading is a
    bookmark, and the list under the intro jumps to those bookmarks.
    """
    apply_location_caps(jobs)
    rels = []
    body = [_p(title, bold=True, size=32, space_after=120)]
    for line in intro:
        body.append(_p(line, size=22, space_after=60))
    upper = type_sections(fit_jobs(jobs))
    entries = result_entries()
    link_n = 10
    bookmark_id = 1

    def _emit(jobs_here, anchor, heading):
        nonlocal link_n, bookmark_id
        body.append(_section_heading(bookmark_id, anchor, heading))
        bookmark_id += 1
        for number, job in enumerate(jobs_here, 1):
            url = apply_link(job)
            rel_id = None
            if url:
                rel_id = f"rId{link_n}"
                link_n += 1
                rels.append((rel_id, url))
            body.extend(_job_blocks(job, rel_id, number, entries))

    if not upper:
        body.append(_p("No high or medium jobs in this file.", size=22))
    else:
        body.append(_p("High and medium by type", bold=True, size=24, space_after=60))
        for section in upper:
            anchor = "type_" + section["key"]
            body.append(_anchor_p(anchor, _type_heading(section["label"], section["jobs"])))
    for section in upper:
        _emit(section["jobs"], "type_" + section["key"], _type_heading(section["label"], section["jobs"]))
    return write_docx_package(path, body, rels)


def write_docx_package(path, body, rels):
    """Zip `body` (a list of w:p / w:tbl strings) and its hyperlink `rels` into a US Letter .docx.

    rels is a list of (relationship id, url). Returns the path, or None when the file is open elsewhere.
    """
    sect = (
        "<w:sectPr>"
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" '
        'w:header="720" w:footer="720" w:gutter="0"/>'
        "</w:sectPr>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{''.join(body)}{sect}</w:body></w:document>"
    )
    rel_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>',
    ]
    for rel_id, url in rels:
        rel_xml.append(
            f'<Relationship Id="{rel_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{xml_escape(url).replace(chr(34), "&quot;")}" TargetMode="External"/>'
        )
    rel_xml.append("</Relationships>")
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/>'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/>'
        '<w:basedOn w:val="Normal"/>'
        '<w:uiPriority w:val="9"/>'
        '<w:qFormat/>'
        '<w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr>'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:b/><w:color w:val="1F4E79"/>'
        '<w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
        "</w:styles>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", package_rels)
            zf.writestr("word/document.xml", document)
            zf.writestr("word/styles.xml", styles)
            zf.writestr("word/_rels/document.xml.rels", "".join(rel_xml))
        os.replace(tmp, path)
    except PermissionError:
        if tmp.exists():
            tmp.unlink()
        print(f"Skipped {path}. The file is open in another program.")
        return None
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return path


def run_pipeline(extra=()):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if BUN_DIR.is_dir():
        env["PATH"] = str(BUN_DIR) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [str(PYTHON), str(SCRAPER / "pipeline.py"), "all", *extra],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    DAILY.mkdir(parents=True, exist_ok=True)
    with (DAILY / "run.log").open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== {date.today().isoformat()} exit={proc.returncode} =====\n")
        fh.write(log)
        if not log.endswith("\n"):
            fh.write("\n")
    return proc.returncode, log


def _stored_text_readable(job):
    """True when the saved text is a real posting, not a shell or an empty card."""
    import scoring

    status = job.get("body_status") or ""
    if status in ("shell", "unread"):
        return False
    text = job.get("description") or ""
    if is_board_shell(text):
        return False
    return scoring.has_text(text)


def regrade_store(jobs):
    """Read any posting still missing its text, then grade every stored job.

    A shell page and an empty card are read again when the title is worth it.
    A long posting that already opened is not fetched again. Grades come from
    the resume-aware scorer, so an older title-only grade is replaced. The
    location cap runs after this.
    """
    import posting_text
    import scoring

    def worth_reading(job):
        if _stored_text_readable(job):
            return False
        return scoring.worth_reading(job)

    try:
        found = posting_text.resolve_apply_links(jobs, only=scoring.worth_reading)
        print(f"Employer apply pages found for aggregator links: {found['found']} of {found['tried']}")
    except Exception as exc:
        print(f"Employer apply pages were not looked up: {exc}")
    try:
        posting_text.enrich(jobs, only=worth_reading)
    except Exception as exc:
        print(f"Posting text was not fetched: {exc}")
    for job in jobs:
        job.pop("fit_before_location", None)
        job.pop("location_block", None)
        scoring.grade_job(job)
    return jobs


def apply_location_caps(jobs):
    """A high grade requires a requested location. Other places drop to medium."""
    import pipeline

    for job in jobs:
        pipeline.cap_high_for_location(job)
    return jobs


def _rule_lines():
    """Sentences shared by the date file and latest.docx."""
    return [
        "Each job gets a fit score out of 100 from its posting text, on the four dimensions in the job evaluation framework: career alignment 30, technical match 30, experience asked 25, work style 15. High is 70 or more, medium is 50 to 69, and low is under 50. Inside each section the best score is listed first. The scale and its reasons are in job_scraper/FIT_SCALE.md.",
        "Target roles: entry full-stack and AI engineering, technical sales, sales and solutions engineering, pre-sales and solutions architecture. Technical account management, technical implementation, QA, AI evaluation, developer relations, support and integration engineering, Salesforce and RevOps seats, and technical writing sit next to those and top out at medium. Forward-deployed engineering and architect seats can be high when the posting shows they are open to your background. Implementation, onboarding and customer-success service seats are left out.",
        "High means nothing in the posting asks for more than you have. A stated shortfall (two or more years of engineering, three or more years of pre-sales, five or more years of sales) keeps a job at medium. A job whose text could not be read is medium when the title fits a target role, and the card says the requirements are unchecked. The exception is a Jooble job: Jooble gives only a short snippet, so a Jooble job that was never read is not listed and counts as left out. A snippet that names a security clearance, an on-site seat, a fixed territory or a staffing or training firm is low and left out.",
        "A posting is left out when its qualifications require a degree in engineering or computer science and do not accept another degree or equivalent experience.",
        "A star means the posting names a business or marketing degree, a bootcamp or coding certificate, or a business analytics certificate. The sentence from the posting is on the card.",
        "High and medium jobs are grouped by position type. Low-fit jobs (managers, senior levels, another region or language, hardware or trade engineering sales, jobs that require a degree or years you do not have) are left out.",
        "Each card says why the grade is high or medium. The posting sentence is on the card when the page could be read.",
        "Each card also says whether you meet the posting's stated minimums: yes, stretch (it asks for some years, tools or credentials you are short of), not checked (the text could not be read), or no.",
        "When a job came from an aggregator such as Himalayas, the link is the employer's own apply page whenever it could be found on Greenhouse, Lever, Ashby or Workable. The grade is then based on that page's text.",
        "A high grade is only for the Twin Cities area, Gulfport–Biloxi, or a US remote role that does not name another city. Any other location stays medium or low.",
    ]


def _source_lines(sources):
    if not sources:
        return []
    return ["Latest search:"] + list(sources)


def _publish(day, store, log_text):
    """Write the date file and latest.docx. The date file is one day only."""
    regrade_store(list(store.values()))
    apply_location_caps(list(store.values()))
    apply_board_shells(list(store.values()))
    fresh = _pipeline_notes(log_text)
    sources = fresh if fresh else load_sources()
    save_store(store, sources)
    day_all = jobs_found_on(store, day)
    day_jobs = fit_jobs(day_all)
    day_intro = [
        f"Date: {day}    Updated: {datetime.now():%H:%M} (an older time means a newer write was skipped because the file was open)",
        f"New jobs: {len(day_jobs)}    High: {_counts(day_jobs)['high']}    Medium: {_counts(day_jobs)['medium']}    Jobs left out (low fit or never read): {left_out_count(day_all)}",
        "Each job is written into one date file only. The same posting is not repeated on a later day.",
        f"Each job also stays in latest.docx for {WINDOW_DAYS} days from the day it was found, then it is removed.",
    ]
    day_intro.extend(_rule_lines())
    day_intro.extend(_source_lines(sources))
    for line in (log_text or "").splitlines():
        if line.startswith("The scrape") or line.startswith("No candidates"):
            day_intro.append(line.strip())
    dated = write_docx(DAILY / f"{day}.docx", "Adam Colyer job search", day_intro, day_jobs)
    latest = write_latest(day, store, sources)
    return dated, latest


def write_latest(day, store, sources=None):
    """Write latest.docx from the store. Also called when the applied log changes, so the marks stay current."""
    sources = sources if sources is not None else load_sources()
    latest_all = list(store.values())
    latest_jobs = fit_jobs(latest_all)
    latest_intro = [
        f"Latest jobs as of {day}    Updated: {datetime.now():%H:%M} (an older time means a newer write was skipped because the file was open)",
        f"Jobs: {len(latest_jobs)}    High: {_counts(latest_jobs)['high']}    Medium: {_counts(latest_jobs)['medium']}    Jobs left out (low fit or never read): {left_out_count(latest_all)}",
        f"A job stays in this file for {WINDOW_DAYS} days from the day it was found, then it is removed.",
        "The first days after a posting goes up are when applications get read. A week is still worth acting on. At 14 days the listing is past the usual first review, and that is also as far back as the scraper looks.",
    ]
    latest_intro.append("Result: a green check means the job was applied for (by the AI, or by Adam, found in his email). "
                        "A red X means not applied: \"AI didn't apply, blocked\" (bot check, login or too many unique questions), \"AI didn't apply, not built yet\" (the project does not handle something the form asked) or \"Not applied\" (closed or skipped). Adam applies to those himself; the X turns into a green check, \"Adam applied\", once his email shows he did. "
                        "No mark means nobody has tried it yet.")
    latest_intro.extend(_rule_lines())
    latest_intro.extend(_source_lines(sources))
    return write_docx(DAILY / "latest.docx", "Adam Colyer job search", latest_intro, latest_jobs)


def refresh_latest(day=None):
    """Rebuild latest.docx so its marks match the applied log. False when Word has the file open or there is no store."""
    store = load_store()
    if not store:
        return False
    try:
        write_latest(day or datetime.now().strftime("%Y-%m-%d"), store)
        return True
    except OSError:
        return False


def write_backfill_files(store):
    """Write a past day's file only when that file is not already on disk.

    A backfill must not replace a morning file with the pruned store. Jobs
    older than the window are already gone, so rewriting the old file would
    drop cards that were on it.
    """
    written = []
    for past in sorted({j["found_on"] for j in store.values() if j.get("backfilled")}):
        path = DAILY / f"{past}.docx"
        if path.exists():
            continue
        past_all = jobs_found_on(store, past)
        past_jobs = fit_jobs(past_all)
        result = write_docx(path, "Adam Colyer job search", [
            f"Date: {past}",
            f"Jobs: {len(past_jobs)}    High: {_counts(past_jobs)['high']}    Medium: {_counts(past_jobs)['medium']}    Jobs left out (low fit or never read): {left_out_count(past_all)}",
            "Rebuilt by a backfill search: includes jobs posted this day that the daily runs had missed.",
        ] + _rule_lines(), past_jobs)
        if result:
            written.append(result)
    return written


def backfill_found_on(store, today, days):
    """File jobs found today under the day they were posted, for the last `days` days.

    A backfill search finds postings that went up on earlier days. Putting them in
    their own day's file shows what that day would have held. Returns {day: count}.
    """
    moved = {}
    oldest = (today - timedelta(days=days)).isoformat()
    for job in store.values():
        posted = (job.get("date") or "")[:10]
        if job.get("found_on") == today.isoformat() and oldest <= posted < today.isoformat():
            job["found_on"] = posted
            job["backfilled"] = True
            moved[posted] = moved.get(posted, 0) + 1
    return moved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", type=int, metavar="DAYS", help="also search the last DAYS days and file each job under the day it was posted")
    ap.add_argument("--from-existing", action="store_true", help="write the Word files from today's candidates.json and do not scrape")
    args = ap.parse_args()
    today = date.today()
    day = today.isoformat()
    cand_path = SCRAPER / f"_run_{day}" / "candidates.json"
    store = load_store()
    # Keep jobs already on disk for today before a new scrape overwrites them.
    if not store and cand_path.exists():
        prior = json.loads(cand_path.read_text(encoding="utf-8"))
        merge_jobs(store, prior.get("new") or [], day)
    log_text = ""
    if not args.from_existing:
        code, log_text = run_pipeline(["--window-days", str(args.backfill + 1)] if args.backfill else [])
        if code != 0 and not cand_path.exists():
            dated, latest = _publish(day, store, log_text + f"\nThe scrape exited {code}. See daily/run.log.")
            print(dated)
            print(latest)
            return code
    if cand_path.exists():
        doc = json.loads(cand_path.read_text(encoding="utf-8"))
        merge_jobs(store, doc.get("new") or [], day)
    elif not store:
        dated, latest = _publish(day, store, log_text + f"\nNo candidates file at {cand_path}.")
        print(dated)
        print(latest)
        return 1
    store, _removed = prune_jobs(store, today)
    print(f"duplicate jobs removed from the store: {collapse_store(store)}")
    if args.backfill:
        if cand_path.exists():  # older store rows did not keep the posting date
            for job in json.loads(cand_path.read_text(encoding="utf-8")).get("new") or []:
                row = store.get(_key(job.get("url")))
                if row is not None and not row.get("date"):
                    row["date"] = (job.get("date") or "")[:10]
        moved = backfill_found_on(store, today, args.backfill)
        print("backfilled by posting date:", dict(sorted(moved.items())))
    dated, latest = _publish(day, store, log_text)
    if args.backfill:
        write_backfill_files(store)
    print(dated)
    print(latest)
    _notify_telegram(store, day)
    _start_apply_agent()
    return 0


def _notify_telegram(store, day):
    """Message Adam the new fit jobs. Never fails the run: Telegram is a convenience."""
    try:
        import telegram_notify
        sent = telegram_notify.notify_new_jobs(store, day)
        if sent:
            print(f"telegram: sent {sent} job(s)")
    except Exception as exc:  # noqa: BLE001 - a notification problem must not lose the day's files
        print(f"telegram: skipped ({type(exc).__name__}: {exc})")


APPLY_MINUTES = 80  # the scheduled task stops after 2 hours; the search itself takes the rest


LISTENER_STALE_SECONDS = 1800  # same as the agent's lock: a lock a second agent could not take is a live listener


def _listener_running():
    """True while the always-on apply listener holds a fresh lock ("listen <pid>" in daily/apply_agent.lock)."""
    lock = DAILY / "apply_agent.lock"
    try:
        return (lock.read_text(encoding="utf-8").startswith("listen")
                and time.time() - lock.stat().st_mtime < LISTENER_STALE_SECONDS)
    except OSError:
        return False


def _start_apply_agent():
    """Run the apply agent after the search: it works today's fit jobs and asks Adam on Telegram.

    It runs here, inside the scheduled task, so Task Scheduler cannot end it as an orphan. It stops itself
    after APPLY_MINUTES, or sooner when nothing is waiting. Off when APPLY_AGENT=0 in .env, when Telegram is
    not set up, or under pytest. Its own lock stops a second copy. Never fails the run.
    """
    try:
        import telegram_notify
        env = telegram_notify.read_env()
        if os.environ.get("PYTEST_CURRENT_TEST") or env.get("APPLY_AGENT", "1") == "0":
            return
        if not env.get("TELEGRAM_BOT_TOKEN") or not env.get("TELEGRAM_CHAT_ID"):
            return
        if _listener_running():  # the always-on listener owns the state file; hand it today's jobs and finish
            (DAILY / "apply_queue_request.json").write_text(json.dumps({"day": date.today().isoformat()}), encoding="utf-8")
            print("apply agent: listener is running, asked it to queue today's jobs")
            return
        print("apply agent: starting")
        with open(DAILY / "apply_agent.log", "a", encoding="utf-8") as log:
            done = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("apply_agent.py")), "run", "--for", str(APPLY_MINUTES)],
                cwd=str(ROOT), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=(APPLY_MINUTES + 15) * 60)
        print(f"apply agent: finished (exit {done.returncode})")
    except subprocess.TimeoutExpired:
        print("apply agent: stopped at its time limit")
    except Exception as exc:  # noqa: BLE001
        print(f"apply agent: not run ({type(exc).__name__}: {exc})")


if __name__ == "__main__":
    raise SystemExit(main())
