"""The apply log: daily/applied_for.docx, kept month by month.

Each day Adam asks the agent to apply to jobs from the daily list. The agent
opens each posting and sorts it:

    applied      an application was submitted (applied_by says who: AI or Adam)
    recommended  a good fit the agent could not submit, so Adam applies by hand
                 (an account, login, CAPTCHA, assessment or an answer only he can give)
    unsent       the apply agent filled the form and asked on Telegram, but Adam never tapped Submit.
                 The AI could have applied. Whoever applies later (a lump-sum run by another AI, or
                 a Telegram request) logs it `applied`, and that moves it out of this bucket.
    attempted    the apply agent opened the form, filled it and tried to submit, but the site stopped it (a robot
                 check, an email code it could not read, a form quirk). No person was pulled in: the reason is
                 logged so the cause can be fixed, and the job waits here until someone applies and logs it `applied`.

Every job the agent touches goes in through this script, so the Word file and
daily/applied_for.json never drift apart:

    python job_scraper/applied_log.py add --url URL --status applied --method "Greenhouse form"
    python job_scraper/applied_log.py add --url URL --status recommended --reason "Needs an account"
    python job_scraper/applied_log.py add --url URL --status unsent --reason "No Submit tap in 4.5 hours"
    python job_scraper/applied_log.py add --url URL --status attempted --reason "Lever robot check refused the click"
    python job_scraper/applied_log.py add --url URL --status applied --by Adam   # he did a recommended one
    python job_scraper/applied_log.py list [--open]    # what is already logged
    python job_scraper/applied_log.py render           # rewrite the Word file
    python job_scraper/applied_log.py status --company Axis --role "Sales Engineer" --to rejected \\
        --reply-date 2026-10-14 --note "Rejection email, subject ..."   # where an applied job stands now

Title, company, location and fit come from daily/latest.json when the URL is
there; pass --title and --company for a job that is not. The same job under a
different link (matched on company, title and place) updates the same entry,
so a job is never logged or applied to twice. An applied entry is never moved
back to recommended.

Applied jobs also record what yield_report.py groups by: the fit score, the scoring.py revision, the board
the job was found on, who submitted it and the resume version. `migrate-tracker` adds those columns to an older
tracker.

Applied jobs (by the AI or by Adam) also get a row in job_search_tracker.csv, the
file /rank, /scrape and /gmail-sync read as the record of applications. Recommended
jobs do not, so they stay visible to the next search. Pass --no-tracker to skip it.

The Word file groups entries by month, newest month first. A job sits in the
month it was applied for, or the month it was recommended while still open.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_digest  # noqa: E402
from daily_digest import _anchor_p, _link_p, _p, _section_heading, write_docx_package  # noqa: E402

LOG_PATH = daily_digest.DAILY / "applied_for.json"
DOC_PATH = daily_digest.DAILY / "applied_for.docx"
TRACKER_PATH = daily_digest.ROOT / "job_search_tracker.csv"
TRACKER_HEADER = [
    "date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
    "fit_rating", "notes", "cv_file", "cover_letter_file", "source",
    # Yield columns: what yield_report.py groups by. Blank means unknown, never a guess.
    "score", "score_version", "discovery_source", "apply_mode", "resume_version", "first_response_date",
]
ROLE_TYPES = {
    "engineering": "software engineering",
    "sales_engineer": "sales engineering",
    "technical_sales": "technical sales",
    "presales": "presales",
    "solutions_architect": "solutions architecture",
    "customer_engineer": "customer engineering",
    "qa": "qa",
}
STATUSES = ("applied", "recommended", "unsent", "attempted")
SLOTS = ("applied", "attempted", "unsent", "recommended")
SLOT_DATE = {"applied": "date_applied", "attempted": "date_attempted", "unsent": "date_unsent", "recommended": "date_recommended"}
FIT_ORDER = {"high": 0, "medium": 1, "low": 2}
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class LogError(Exception):
    pass


def load_log(path=None):
    path = path or LOG_PATH
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Do not start a fresh log over a damaged one: that would erase the history.
        raise LogError(f"{path} is not valid JSON ({exc}). Fix or restore it before logging more.") from exc
    return doc.get("entries") or {}


def save_log(entries, path=None):
    path = path or LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"entries": entries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def month_key(value):
    """'2026-10-27' -> '2026-10'. Raises on anything that is not an ISO date."""
    return date.fromisoformat(value).strftime("%Y-%m")


def month_label(key):
    year, month = key.split("-")
    return f"{MONTH_NAMES[int(month) - 1]} {year}"


def _lookup_job(url, store):
    """The daily-list card for this URL (its own link or the employer apply link), or None."""
    want = daily_digest._key(url)
    for job in store.values():
        if want in (daily_digest._key(job.get("url")), daily_digest._key(daily_digest.apply_link(job))):
            return job
    return None


def _find_existing(entries, url, card):
    key = daily_digest._key(url)
    if key in entries:
        return key
    probe = {k: card.get(k) for k in ("title", "company", "location", "work_mode")}
    if not (probe["title"] and probe["company"]):
        return None
    want = daily_digest.pipeline_dedupe_key(probe)
    if want is None:
        return None
    for other_key, other in entries.items():
        other_probe = {k: other.get(k) for k in ("title", "company", "location", "work_mode")}
        if daily_digest.pipeline_dedupe_key(other_probe) == want:
            return other_key
    return None


def add_entry(entries, *, url, status, day, by="AI", store=None, **fields):
    """Add or update one entry in `entries`. Returns (key, message). Raises LogError on bad input."""
    if status not in STATUSES:
        raise LogError(f"status must be one of {', '.join(STATUSES)}")
    if not url:
        raise LogError("a URL is required")
    month_key(day)  # fail early on a bad date
    job = _lookup_job(url, store or {}) or {}
    card = {
        "title": fields.get("title") or job.get("title"),
        "company": fields.get("company") or job.get("company"),
        "location": fields.get("location") or job.get("location") or "",
        "work_mode": job.get("work_mode") or "",
        "fit": fields.get("fit") or job.get("fit") or "",
        "fit_score": fields.get("fit_score") if fields.get("fit_score") is not None else job.get("fit_score"),
    }
    key = _find_existing(entries, url, card)
    if key is None and not (card["title"] and card["company"]):
        raise LogError("this URL is not on the daily list; pass --title and --company")
    entry = entries.get(key) if key else None
    is_new = entry is None
    if is_new:
        key = daily_digest._key(url)
        entry = {k: v for k, v in card.items() if k != "work_mode"}
        entry["url"] = daily_digest.apply_link(job) if job else url
        entry["work_mode"] = card["work_mode"]
        entry["discovery_source"] = discovery_source(job)
        entry["status"] = "recommended"  # the branches below move it on to `status`
        entry["date_recommended"] = ""
        entry["date_unsent"] = ""
        entry["date_attempted"] = ""
        entry["date_applied"] = ""
        entry["applied_by"] = ""
        entries[key] = entry
    if status == "applied":
        if entry["status"] == "applied":
            message = f"already logged as applied on {entry['date_applied']} by {entry['applied_by']}; left as is"
            _merge_notes(entry, fields)
            return key, message
        entry["status"] = "applied"
        entry["score_version"] = _git_rev("job_scraper/scoring.py")
        entry["applied_by"] = by
        entry["date_applied"] = day
        entry["reason"] = ""
        message = f"logged as applied by {by} ({month_label(month_key(day))})"
    elif status == "attempted":
        if entry["status"] == "applied":
            return key, f"already applied on {entry['date_applied']} by {entry['applied_by']}; not moved back to attempted"
        entry["status"] = "attempted"
        entry["date_attempted"] = entry.get("date_attempted") or day
        if fields.get("reason"):
            entry["reason"] = fields["reason"]
        message = f"logged as AI tried to apply, could not finish ({month_label(month_key(entry['date_attempted']))})"
    elif status == "unsent":
        if entry["status"] == "applied":
            return key, f"already applied on {entry['date_applied']} by {entry['applied_by']}; not moved back to unsent"
        if entry["status"] == "attempted":
            return key, "already logged as AI tried to apply, could not finish; left as is"
        entry["status"] = "unsent"
        entry["date_unsent"] = entry.get("date_unsent") or day
        if fields.get("reason"):
            entry["reason"] = fields["reason"]
        message = f"logged as AI could have applied, not sent ({month_label(month_key(entry['date_unsent']))})"
    else:
        if entry["status"] == "applied":
            return key, f"already applied on {entry['date_applied']} by {entry['applied_by']}; not moved back to recommended"
        if entry["status"] == "attempted":
            return key, "already logged as AI tried to apply, could not finish; left as is"
        if entry["status"] == "unsent":
            return key, "already logged as AI could have applied, not sent; left as is"
        entry["date_recommended"] = entry.get("date_recommended") or day
        if fields.get("reason"):
            entry["reason"] = fields["reason"]
        message = f"logged as recommended, you apply ({month_label(month_key(entry['date_recommended']))})"
    _merge_notes(entry, fields)
    if not is_new:
        message = "updated: " + message
    return key, message


def _git_rev(relative_path):
    """Short hash of the last commit that touched this file, plus '+edited' when it has uncommitted changes.
    Empty when git is unavailable, so a missing git never blocks a log entry."""

    def run(*args):
        result = subprocess.run(
            ["git", *args], cwd=daily_digest.ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout.strip()

    try:
        rev = run("log", "-1", "--format=%h", "--", relative_path)
        if rev and run("status", "--porcelain", "--", relative_path):
            rev += "+edited"
        return rev
    except (OSError, subprocess.SubprocessError):
        return ""


def discovery_source(job):
    """Where the search found the job: the host of the card's own link ('himalayas.app', 'linkedin.com',
    'job-boards.greenhouse.io'), not the employer page it was applied on. Empty when the job is not on the list."""
    host = urlparse((job or {}).get("url") or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def resume_version(resume, hash_file=True):
    """File name without folder or extension, plus the first 7 characters of a hash of the file's bytes, so a
    rebuilt PDF under the same name counts as a new version. documents/cv is gitignored, so git cannot say.
    The hash is left off when the file cannot be read or when hash_file is False (old rows: the file on disk
    may no longer be the one that was submitted)."""
    if not resume:
        return ""
    path = Path(str(resume).replace("\\", "/"))
    if hash_file:
        try:
            return f"{path.stem}@{hashlib.sha256(path.read_bytes()).hexdigest()[:7]}"
        except OSError:
            pass
    return path.stem


def _applied_by_note(notes):
    for who, mode in (("AI", "ai"), ("Adam", "hand")):
        if f"Applied by {who} " in (notes or ""):
            return mode
    return ""


def _tracker_extras(entry, hash_resume=True):
    """Values for the yield columns. The score stays blank when the job was not on the list (None); a 0 is a
    real grade (a Low gate), so it is kept."""
    score = entry.get("fit_score")
    return [
        "" if score is None else score,
        entry.get("score_version") or "",
        entry.get("discovery_source") or "",
        {"AI": "ai", "Adam": "hand"}.get(entry.get("applied_by"), ""),
        entry.get("resume_version") or resume_version(entry.get("resume"), hash_file=hash_resume),
        "",
    ]


def migrate_tracker(path=None, entries=None, store=None):
    """Bring job_search_tracker.csv up to TRACKER_HEADER: append the yield columns, pad every row, fill what the
    apply log and the daily list know. Existing cells are never changed. Safe to run again. Returns the number of rows brought up
    to date."""
    path = path or TRACKER_PATH
    raw = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
    rows = list(csv.reader(io.StringIO(raw))) if raw.strip() else []
    if not rows or rows[0] == TRACKER_HEADER:
        return 0
    old = rows[0]
    if old != TRACKER_HEADER[:len(old)]:
        raise LogError(f"{path} has columns this script does not know ({','.join(old)}); not migrating")
    by_url = {daily_digest._key(e.get("url")): e for e in (entries or {}).values() if e.get("url")}
    out = [TRACKER_HEADER]
    for row in rows[1:]:
        row = row + [""] * (len(old) - len(row))
        cells = dict(zip(old, row))
        entry = by_url.get(daily_digest._key(cells.get("source")), {})
        card = _lookup_job(cells.get("source") or "", store or {}) or {}
        extras = _tracker_extras({
            "discovery_source": discovery_source(card), **entry,
            "resume": entry.get("resume") or cells.get("cv_file"),
        }, hash_resume=False)
        if not entry:  # not in the apply log: who applied is only known when the note says so
            extras[3] = _applied_by_note(cells.get("notes"))
        out.append(row + extras)
    eol = "\r\n" if "\r\n" in raw else "\n"
    buf = io.StringIO()
    csv.writer(buf, lineterminator=eol).writerows(out)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(buf.getvalue().encode("utf-8"))
    os.replace(tmp, path)
    return len(rows) - 1


def fill_tracker_blanks(entry, path=None):
    """Fill the CV, cover letter and resume_version cells of a job's existing tracker row when they are blank.

    A confirmation email (or another AI) can log a job before the agent that sent it records which resume went out.
    The resume then arrives on a later call, and the row that is already there should pick it up. Only blank cells
    change; nothing else in the file is touched. Returns a short description.
    """
    import pipeline

    path = path or TRACKER_PATH
    if not path.exists():
        return "no tracker"
    raw = path.read_bytes().decode("utf-8")
    bom = raw.startswith("﻿")
    raw = raw.lstrip("﻿")
    eol = "\r\n" if "\r\n" in raw else "\n"
    rows = list(csv.reader(io.StringIO(raw)))
    header = rows[0]
    if any(name not in header for name in ("cv_file", "cover_letter_file", "resume_version", "source", "company", "role")):
        return "tracker columns missing"
    col = {name: header.index(name) for name in header}
    want = (pipeline.company_key(entry.get("company")), pipeline._norm_words(entry.get("title")))
    values = {"cv_file": entry.get("resume") or "", "cover_letter_file": entry.get("cover_letter") or "",
              "resume_version": entry.get("resume_version") or resume_version(entry.get("resume"))}
    changed = []
    for row in rows[1:]:
        row.extend([""] * (len(header) - len(row)))
        same = (pipeline.company_key(row[col["company"]]), pipeline._norm_words(row[col["role"]])) == want
        if not (same or (entry.get("url") and row[col["source"]] == entry["url"])):
            continue
        for name, value in values.items():
            if value and not row[col[name]].strip():
                row[col[name]] = value
                changed.append(name)
        break
    if not changed:
        return "nothing to fill"
    buf = io.StringIO()
    csv.writer(buf, lineterminator=eol).writerows(rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((("﻿" if bom else "") + buf.getvalue()).encode("utf-8"))
    os.replace(tmp, path)
    return "filled " + ", ".join(changed)


def append_tracker_row(entry, path=None):
    """Add an applied entry to job_search_tracker.csv, the file /rank, /scrape and /gmail-sync read as the
    record of applications. Returns "added", or "already in the tracker" when the company and role are there.

    Append only: existing rows and the file's line endings are left alone. Columns the log cannot know
    (sector, contact, CV and cover letter files) stay blank rather than guessed.
    """
    import pipeline

    path = path or TRACKER_PATH
    migrate_tracker(path, load_log() if path == TRACKER_PATH else None)
    raw = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
    if raw.strip():
        rows = list(csv.DictReader(io.StringIO(raw)))
    else:
        rows = []
    want = (pipeline.company_key(entry.get("company")), pipeline._norm_words(entry.get("title")))
    for row in rows:
        if (pipeline.company_key(row.get("company")), pipeline._norm_words(row.get("role"))) == want:
            return "already in the tracker"
        if entry.get("url") and row.get("source") == entry["url"]:
            return "already in the tracker"
    eol = "\r\n" if "\r\n" in raw or not raw else "\n"
    method = entry.get("method") or ""
    note = f"Applied by {entry['applied_by']} on {entry['date_applied']}"
    note += f" via {method}." if method else "."
    missing = [label for label, key in (("CV", "resume"), ("cover letter", "cover_letter")) if not entry.get(key)]
    note += " Logged by applied_log.py; sector" + "".join(f", {label}" for label in missing) + " not recorded."
    out = io.StringIO()
    writer = csv.writer(out, lineterminator=eol)
    if not raw.strip():
        writer.writerow(TRACKER_HEADER)
    writer.writerow([
        entry["date_applied"], entry.get("company") or "", "", entry.get("title") or "",
        ROLE_TYPES.get(daily_digest.position_type(entry.get("title")), "other"),
        method.lower() or "unknown", "applied", "", entry.get("fit") or "", note,
        entry.get("resume") or "", entry.get("cover_letter") or "", entry.get("url") or "",
        *_tracker_extras(entry),
    ])
    prefix = "" if (not raw or raw.endswith("\n")) else eol
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        handle.write((prefix + out.getvalue()).encode("utf-8"))
    return "added"


# What an email can move a tracker row to. `hired` and `offer declined` are Adam's call, so they are not here.
EMAIL_STATUSES = ("applied", "interview", "offer", "rejected", "no response", "withdrawn")
# A row in one of these is closed: an email does not reopen it.
CLOSED_STATUSES = ("hired", "rejected", "offer declined", "withdrawn")
# A reply from the employer (what yield_report.py counts as a response).
REPLY_STATUSES = ("interview", "offer", "rejected")


def set_tracker_status(*, company=None, role=None, url=None, to, reply_date=None, note=None, today=None, path=None):
    """Move one tracker row to `to`, fill first_response_date if it is empty and append a dated note.

    The row is found by `url` (the source column) or by company plus role words; more or fewer than one match
    is an error, so a status never lands on the wrong job. Returns a one-line description of what changed.
    Never overwrites first_response_date, never touches another row, never reopens a closed row.
    """
    import pipeline

    path = path or TRACKER_PATH
    if to not in EMAIL_STATUSES:
        raise LogError(f"status must be one of {', '.join(EMAIL_STATUSES)} (hired and offer declined are Adam's to record)")
    if reply_date:
        date.fromisoformat(reply_date)
    raw = path.read_bytes().decode("utf-8")
    bom = raw.startswith("﻿")
    raw = raw.lstrip("﻿")
    eol = "\r\n" if "\r\n" in raw else "\n"
    rows = list(csv.reader(io.StringIO(raw)))
    header = rows[0]
    col = {name: header.index(name) for name in ("company", "role", "status", "notes", "source", "first_response_date")}

    def cell(row, name):
        return row[col[name]] if col[name] < len(row) else ""

    matches = []
    for i, row in enumerate(rows[1:], 1):
        if url and cell(row, "source") == url:
            matches.append(i)
        elif company and pipeline.company_key(cell(row, "company")) == pipeline.company_key(company):
            if role is None or pipeline._norm_words(role) == pipeline._norm_words(cell(row, "role")) \
                    or pipeline._norm_words(role) in pipeline._norm_words(cell(row, "role")):
                matches.append(i)
    if len(matches) != 1:
        found = ", ".join(f"{cell(rows[i], 'company')} / {cell(rows[i], 'role')}" for i in matches) or "none"
        raise LogError(f"expected exactly one tracker row for that job, found {len(matches)} ({found}); be more specific")
    row = rows[matches[0]]
    row.extend([""] * (len(header) - len(row)))
    name = f"{row[col['company']]} / {row[col['role']]}"
    current = row[col["status"]].strip().lower()
    if current in CLOSED_STATUSES and current != to:
        raise LogError(f"{name} is already {current}; an email does not reopen it. Use /outcome if that is wrong.")
    changes = []
    if current != to:
        row[col["status"]] = to
        changes.append(f"status {current or 'blank'} -> {to}")
    if reply_date and to in REPLY_STATUSES and not row[col["first_response_date"]].strip():
        row[col["first_response_date"]] = reply_date
        changes.append(f"first_response_date {reply_date}")
    if note:
        stamp = f"{reply_date or today or date.today().isoformat()}: {note}"
        if stamp not in row[col["notes"]]:
            row[col["notes"]] = (row[col["notes"]].rstrip() + "; " if row[col["notes"]].strip() else "") + stamp
            changes.append("note added")
    if not changes:
        return f"{name}: already {to}, nothing to change"
    buf = io.StringIO()
    csv.writer(buf, lineterminator=eol).writerows(rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((("﻿" if bom else "") + buf.getvalue()).encode("utf-8"))
    os.replace(tmp, path)
    return f"{name}: " + ", ".join(changes)


def _merge_notes(entry, fields):
    for name in ("method", "note", "reason", "resume", "cover_letter"):
        if fields.get(name):
            entry[name] = fields[name]


def _sort_key(entry, date_field):
    """Best fit first, then highest score, then most recent, then title."""
    return (
        FIT_ORDER.get(entry.get("fit"), 9),
        -(entry.get("fit_score") or 0),
        -date.fromisoformat(entry[date_field]).toordinal(),
        entry.get("title") or "",
    )


def months(entries):
    """{month key: {'applied': [...], 'attempted': [...], 'unsent': [...], 'recommended': [...]}} for every month with an entry."""
    grouped = {}
    for entry in entries.values():
        slot = entry["status"] if entry["status"] in SLOTS else "recommended"
        grouped.setdefault(month_key(entry[SLOT_DATE[slot]]), {name: [] for name in SLOTS})[slot].append(entry)
    for bucket in grouped.values():
        for slot in SLOTS:
            bucket[slot].sort(key=lambda e, f=SLOT_DATE[slot]: _sort_key(e, f))
    return dict(sorted(grouped.items(), reverse=True))


def _table(rows, widths):
    """A bordered Word table. Each cell is already a paragraph string."""
    border = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    out = [
        f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>',
        "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>",
    ]
    for cells in rows:
        out.append("<w:tr>")
        for width, cell in zip(widths, cells):
            out.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>{cell}</w:tc>')
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def _anchor(key):
    return "month_" + key.replace("-", "_")


def _entry_blocks(entry, number, rel_id):
    fit = (entry.get("fit") or "").upper()
    score = entry.get("fit_score")
    head = " ".join(part for part in (f"{number}.", fit, str(score) if score is not None else "") if part)
    blocks = [
        _p(f"{head} | {entry.get('title') or 'Untitled'}", bold=True, size=24, space_after=40),
        _p(f"{entry.get('company') or 'Company not listed'} | {entry.get('location') or 'Location not listed'}", space_after=40),
    ]
    if entry["status"] == "applied":
        line = f"Applied by {entry['applied_by']} on {entry['date_applied']}"
        if entry.get("method"):
            line += f" via {entry['method']}"
        blocks.append(_p(line + ".", space_after=40))
        for label, key in (("Resume", "resume"), ("Cover letter", "cover_letter")):
            if entry.get(key):
                blocks.append(_p(f"{label} submitted: {entry[key]}", space_after=40))
        if entry.get("date_recommended"):
            blocks.append(_p(f"Recommended on {entry['date_recommended']}.", space_after=40))
    elif entry["status"] == "attempted":
        blocks.append(_p(f"AI tried to apply on {entry['date_attempted']} and could not finish. Nobody was asked; "
                         "the cause is in the error log so it can be fixed.", space_after=40))
        blocks.append(_p(f"What stopped it: {entry.get('reason') or 'not recorded'}", space_after=40))
        blocks.append(_p("To finish: apply by hand or let the next fix retry it, then log it applied. "
                         "That moves it to Applied automatically.", space_after=40))
    elif entry["status"] == "unsent":
        blocks.append(_p(f"AI prepared this on {entry['date_unsent']} and asked you on Telegram. No Submit tap came, "
                         "so it was not sent. The AI could have applied.", space_after=40))
        blocks.append(_p(f"Detail: {entry.get('reason') or 'not recorded'}", space_after=40))
        blocks.append(_p("To finish: ask the bot to apply, or have an AI apply to it, then log it applied. "
                         "That moves it to Applied automatically.", space_after=40))
    else:
        blocks.append(_p(f"Recommended on {entry['date_recommended']}. You apply.", space_after=40))
        blocks.append(_p(f"Why by hand: {entry.get('reason') or 'not recorded'}", space_after=40))
    if entry.get("note"):
        blocks.append(_p(f"Note: {entry['note']}", space_after=40))
    if rel_id:
        blocks.append(_link_p(rel_id, entry["url"]))
    else:
        blocks.append(_p("No link on the card.", space_after=200))
    return blocks


def write_doc(entries, path=None, today=None):
    path = path or DOC_PATH
    today = today or date.today().isoformat()
    grouped = months(entries)
    applied_total = sum(len(b["applied"]) for b in grouped.values())
    open_total = sum(len(b["recommended"]) for b in grouped.values())
    unsent_total = sum(len(b["unsent"]) for b in grouped.values())
    tried_total = sum(len(b["attempted"]) for b in grouped.values())
    body = [
        _p("Applied for", bold=True, size=32, space_after=120),
        _p(f"Updated {today}. {applied_total} applied, {open_total} for you to apply to by hand, "
           f"{tried_total} the AI tried and could not finish, {unsent_total} the AI prepared but you did not send.", space_after=60),
        _p("Applied: an application was submitted, and the line says who submitted it. "
           "Recommended, you apply: a good fit the agent cannot submit (an account, login, CAPTCHA, assessment or an answer only you can give). "
           "AI tried to apply, could not finish: the agent submitted the form and the site stopped it (a robot check, an email code, a form quirk); "
           "no one was pulled in, the cause is logged to be fixed, and it moves to Applied once someone applies. "
           "AI could have applied, not sent: the agent filled the form and asked on Telegram, but no Submit tap came; "
           "send it from Telegram or have another AI apply, and it moves to Applied. Newest month first.", space_after=120),
    ]
    rels = []
    link_n = 10
    if not grouped:
        body.append(_p("Nothing logged yet.", space_after=80))
    else:
        rows = [[_p(text, bold=True, space_after=0) for text in ("Month", "Applied by AI", "Applied by you", "AI tried, not finished", "AI prepared, not sent", "You apply by hand")]]
        for key, bucket in grouped.items():
            by_ai = sum(1 for e in bucket["applied"] if e["applied_by"] == "AI")
            by_you = len(bucket["applied"]) - by_ai
            rows.append([
                _anchor_p(_anchor(key), month_label(key)),
                _p(str(by_ai), space_after=0),
                _p(str(by_you), space_after=0),
                _p(str(len(bucket["attempted"])), space_after=0),
                _p(str(len(bucket["unsent"])), space_after=0),
                _p(str(len(bucket["recommended"])), space_after=0),
            ])
        body.append(_table(rows, [2000, 1400, 1400, 1600, 1600, 1600]))
    for bookmark_id, (key, bucket) in enumerate(grouped.items(), 1):
        body.append(_section_heading(bookmark_id, _anchor(key), month_label(key)))
        for slot, heading in (("applied", "Applied"), ("attempted", "AI tried to apply, could not finish"),
                              ("unsent", "AI could have applied, not sent"),
                              ("recommended", "Recommended, you apply by hand")):
            body.append(_p(f"{heading} ({len(bucket[slot])})", bold=True, size=26, space_after=60))
            if not bucket[slot]:
                body.append(_p("None.", space_after=120))
            for number, entry in enumerate(bucket[slot], 1):
                rel_id = None
                if entry.get("url"):
                    rel_id = f"rId{link_n}"
                    link_n += 1
                    rels.append((rel_id, entry["url"]))
                body.extend(_entry_blocks(entry, number, rel_id))
    return write_docx_package(path, body, rels)


def _print_entries(entries, only_open=False):
    grouped = months(entries)
    for key, bucket in grouped.items():
        for slot in SLOTS:
            if only_open and slot == "applied":
                continue
            for e in bucket[slot]:
                who = {"applied": f"by {e.get('applied_by')}", "unsent": "unsent", "attempted": "AI tried"}.get(slot, "you apply")
                print(f"{key} {slot:<11} {who:<9} {e.get('fit') or '-':<6} {e.get('title')} | {e.get('company')} | {e.get('url')}")
    if not grouped:
        print("nothing logged yet")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="log one job as applied or recommended")
    add.add_argument("--url", required=True)
    add.add_argument("--status", required=True, choices=STATUSES)
    add.add_argument("--by", default="AI", choices=("AI", "Adam"), help="who submitted it (applied only)")
    add.add_argument("--date", help="ISO date, default today")
    add.add_argument("--title")
    add.add_argument("--company")
    add.add_argument("--location")
    add.add_argument("--fit", choices=tuple(FIT_ORDER))
    add.add_argument("--score", type=int)
    add.add_argument("--method", help='how it was submitted, e.g. "Greenhouse form"')
    add.add_argument("--reason", help="recommended, unsent or attempted: why Adam has to apply by hand, why it was not sent, or what stopped the AI")
    add.add_argument("--resume", help="applied only: path of the resume file that was submitted (goes in the tracker's cv_file)")
    add.add_argument("--cover-letter", dest="cover_letter", help="applied only: path of the cover letter that was submitted")
    add.add_argument("--note")
    add.add_argument("--no-tracker", action="store_true", help="do not add an applied job to job_search_tracker.csv")
    st = sub.add_parser("status", help="record where an applied job stands now (from an interview, assessment, offer or rejection email)")
    st.add_argument("--company")
    st.add_argument("--role", help="words from the role title; needed when the company has more than one row")
    st.add_argument("--url", help="the tracker row's source URL, instead of company and role")
    st.add_argument("--to", required=True, choices=EMAIL_STATUSES)
    st.add_argument("--reply-date", dest="reply_date", help="ISO date on the employer's email (Central time); also fills first_response_date when empty")
    st.add_argument("--note", help="what the email said and its subject line")
    sub.add_parser("migrate-tracker", help="add the yield columns to job_search_tracker.csv (safe to repeat)")
    lst = sub.add_parser("list", help="print what is logged")
    lst.add_argument("--open", action="store_true", help="only jobs not yet applied to (recommended, unsent and attempted)")
    sub.add_parser("render", help="rewrite the Word file from the log")
    args = ap.parse_args(argv)

    try:
        entries = load_log()
        if args.cmd == "add":
            day = args.date or date.today().isoformat()
            key, message = add_entry(
                entries, url=args.url, status=args.status, day=day, by=args.by,
                store=daily_digest.load_store(),
                title=args.title, company=args.company, location=args.location,
                fit=args.fit, fit_score=args.score, method=args.method,
                reason=args.reason, note=args.note, resume=args.resume, cover_letter=args.cover_letter,
            )
            save_log(entries)
            e = entries[key]
            print(f"{e['title']} | {e['company']}: {message}")
            if e["status"] == "applied" and not args.no_tracker:
                result = append_tracker_row(e)
                if result == "already in the tracker" and (e.get("resume") or e.get("cover_letter")):
                    result += f"; {fill_tracker_blanks(e)}"
                print(f"job_search_tracker.csv: {result}")
        elif args.cmd == "status":
            if not (args.url or args.company):
                raise LogError("give --company (and --role) or --url")
            print("job_search_tracker.csv: " + set_tracker_status(
                company=args.company, role=args.role, url=args.url, to=args.to,
                reply_date=args.reply_date, note=args.note))
            return 0
        elif args.cmd == "migrate-tracker":
            print(f"job_search_tracker.csv: {migrate_tracker(entries=entries, store=daily_digest.load_store())} rows brought up to date")
            return 0
        elif args.cmd == "list":
            _print_entries(entries, only_open=args.open)
            return 0
    except (LogError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    written = write_doc(entries)
    if written:
        print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
