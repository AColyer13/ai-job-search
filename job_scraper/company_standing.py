"""Public evidence about an employer, and how that evidence moves a fit grade.

The store is daily/company_standing.json. daily/*.json is gitignored, so the
file stays on this machine. Each record is a public page: a URL, a date, and
whether that page speaks positively, negatively, or calls the employer a fake.
The grade is computed from those records. Nothing here is a star rating, and a
company with no records is unknown. Unknown does not change the score.

Scoring reads the file only. It does not search the web. Refresh pulls
company_reviews for one company and stores the write-ups that actually judge
the employer. Glassdoor, Indeed, ZipRecruiter, and Blind are not opened.

    outstanding   2+ pages positive, none negative, within 3 years   +12, then the role ceiling
    bad           2+ pages negative, none positive, within 3 years   cap 49 (off the list)
    mixed         both sides, from at least 2 pages                  cap 64 (cannot be high)
    thin          one page                                           shown, score unchanged
    fake          a stored scam record, or a fee / private-chat hire hard low, score 0
    unknown       nothing stored, or only write-ups older than 3 years  no change
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "daily" / "company_standing.json"
STALE_DAYS = 365 * 3
OUTSTANDING_BONUS = 12
BAD_CAP = 49
MIXED_CAP = 64

_SUFFIX = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "incorporated"}
_STANCES = {"positive", "negative", "mixed", "fake"}
_NEG = re.compile(
    r"\blayoffs?\b|\blaid off\b|\btoxic\b|hated working|would not recommend|\bunderpaid\b|\bburnout\b|"
    r"worst (?:company|job|place)|do not work (?:here|there)",
    re.I,
)
_POS = re.compile(
    r"loved working|\bexemplary\b|would recommend|great place to work|"
    r"work[- ]life balance (?:is|was) (?:decent|good|great|excellent|amazing)|"
    r"best (?:company|job|place)|enjoyed working",
    re.I,
)
# A hiring scam in the posting itself. Product copy ("processing fees") is not this.
FAKE_HIRING_RE = re.compile(
    r"\b(?:"
    r"training fee|"
    r"(?:pay|paying|payment|fee|money) (?:is )?required to (?:start|apply|begin|join)|"
    r"(?:wire|send) (?:us )?(?:money|a fee|payment)|"
    r"purchase (?:a |the )?(?:starter|training) kit|"
    r"paid in gift cards|"
    r"interview(?:s|ing)? (?:will be |is |are )?(?:only |via |on |over )?whatsapp|"
    r"(?:apply|contact|message) (?:us |me )?(?:only )?on (?:whatsapp|telegram)"
    r")\b",
    re.I,
)


def key_of(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    kept = [word for word in words if word not in _SUFFIX]
    return " ".join(kept)


def fake_posting(text):
    return bool(FAKE_HIRING_RE.search(text or ""))


def stance_of(text):
    """positive, negative, mixed, or '' when the text does not judge the employer."""
    neg = bool(_NEG.search(text or ""))
    pos = bool(_POS.search(text or ""))
    if pos and neg:
        return "mixed"
    if pos:
        return "positive"
    if neg:
        return "negative"
    return ""


def telegram_mark(stood):
    """What Telegram should say about this employer. One page is not enough to call it good or bad."""
    kind = (stood or {}).get("standing") or "unknown"
    if kind == "outstanding":
        return "✅", "good"
    if kind in {"bad", "fake"}:
        return "❌", "not a company you want to work for"
    return "○", "undecided"


def mark_line(company, path=None):
    """One Telegram line from the stored pages. Does not search."""
    symbol, words = telegram_mark(lookup(company, path=path))
    return f"{symbol} <b>{words}</b>"


def _incoming_research(company):
    """The check that runs as a job arrives. Hacker News is the source that returns text here."""
    import company_reviews
    import interview_intel

    def search(query):
        hits, err = interview_intel.search_hn(query)
        return hits, ([f"Hacker News: {err}"] if err else [])

    return company_reviews.research(company, search=search, open_page=lambda url: ("", "not opened"))


def ensure(company, path=None, research=None):
    """Store a public check the first time a company shows up. Later calls only read the file."""
    name = re.sub(r"\s+", " ", (company or "").strip())[:120]
    key = key_of(name)
    if not key:
        return lookup(name, path=path)
    if key in load(path)["companies"]:
        return lookup(name, path=path)
    try:
        return refresh(name, path=path, research=research or _incoming_research)
    except Exception:
        return lookup(name, path=path)


def match_notices(company, notices):
    """Layoff-list rows whose company name is this employer, not a longer name that merely contains it."""
    key = key_of(company)
    if not key:
        return []
    found, seen = [], set()
    for notice in notices or []:
        if key_of(notice.get("company")) != key:
            continue
        url = (notice.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        found.append(_record(
            url,
            notice.get("source") or "layoff list",
            (notice.get("date") or "")[:10],
            False,
            "layoff",
            notice.get("note") or "Public layoff notice.",
        ))
    return found


def store_notices(companies, notices, path=None, today=None):
    """Save layoff notices for the companies that match. A notice does not change the grade."""
    today = today or date.today()
    path = Path(path) if path else DEFAULT_PATH
    data = load(path)
    matched = []
    for company in companies or []:
        name = re.sub(r"\s+", " ", (company or "").strip())[:120]
        key = key_of(name)
        incoming = match_notices(name, notices)
        if not key or not incoming:
            continue
        entry = data["companies"].get(key) or {"name": name, "records": []}
        entry["name"] = entry.get("name") or name
        entry["records"] = _merge(entry.get("records"), incoming)
        entry["updated"] = today.isoformat()
        data["companies"][key] = entry
        matched.append((name, incoming))
    if matched:
        _save(data, path)
    return matched


def normalize_todays_layoffs(payload):
    notices = []
    for event in (payload or {}).get("events") or []:
        company = ((event.get("company") or {}).get("name") or "").strip()
        sources = event.get("sources") or []
        url = ""
        if sources and isinstance(sources[0], dict):
            url = sources[0].get("url") or ""
        url = url or event.get("url") or ""
        count = (event.get("count") or {}).get("value")
        note = event.get("headline") or "Public layoff notice."
        if count:
            note = f"{note} ({count} jobs)."
        notices.append({
            "company": company,
            "url": url,
            "date": ((event.get("dates") or {}).get("announced") or "")[:10],
            "note": note,
            "source": "Today's Layoffs",
        })
    return notices


def normalize_fyi_csv(text):
    import csv
    from io import StringIO
    notices = []
    for row in csv.DictReader(StringIO(text or "")):
        count = (row.get("Laid_Off_Count") or "").strip()
        note = "Public layoff notice."
        if count:
            note = f"Public layoff notice: {count} jobs."
        notices.append({
            "company": (row.get("Company") or "").strip(),
            "url": (row.get("Source") or "").strip(),
            "date": (row.get("Date") or "")[:10],
            "note": note,
            "source": "layoffs.fyi",
        })
    return notices


def fetch_layoff_notices(get=None):
    """One download of the public layoff lists, then every company is matched locally."""
    import urllib.request
    sources = (
        ("https://todayslayoffs.com/api/v1/events.json", "json"),
        ("https://raw.githubusercontent.com/sthasangam/ML-Project/master/layoffs_data_fyi.csv", "csv"),
    )

    def default_get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "ai-job-search/1.0"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.read()

    get = get or default_get
    notices, errors = [], []
    for url, kind in sources:
        try:
            raw = get(url)
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if kind == "json":
                notices.extend(normalize_todays_layoffs(json.loads(text)))
            else:
                notices.extend(normalize_fyi_csv(text))
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}")
    return notices, errors


def effect(total, cap, standing):
    """Fit points after company evidence. Fake is handled before points exist."""
    if standing == "outstanding":
        return min(int(total) + OUTSTANDING_BONUS, cap)
    if standing == "bad":
        return min(int(total), BAD_CAP)
    if standing == "mixed":
        return min(int(total), MIXED_CAP)
    return total


def _is_stale(record, today):
    if record.get("stance") == "fake":
        return False
    if record.get("stale"):
        return True
    raw = str(record.get("date") or "")[:10]
    if not raw:
        return False
    try:
        when = date.fromisoformat(raw)
    except ValueError:
        return False
    return (today - when).days > STALE_DAYS


def judge(records, today=None):
    today = today or date.today()
    records = [r for r in (records or []) if isinstance(r, dict)]
    if any(r.get("stance") == "fake" for r in records):
        return "fake"
    pos, neg = set(), set()
    for record in records:
        if _is_stale(record, today):
            continue
        url = (record.get("url") or "").strip()
        stance = record.get("stance")
        if not url or stance not in {"positive", "negative", "mixed"}:
            continue
        if stance in {"positive", "mixed"}:
            pos.add(url)
        if stance in {"negative", "mixed"}:
            neg.add(url)
    if len(neg) >= 2 and not pos:
        return "bad"
    if len(pos) >= 2 and not neg:
        return "outstanding"
    if pos and neg and len(pos | neg) >= 2:
        return "mixed"
    if pos or neg:
        return "thin"
    return "unknown"


def load(path=None):
    path = Path(path) if path else DEFAULT_PATH
    if not path.is_file():
        return {"companies": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"companies": {}}
    if not isinstance(data, dict) or not isinstance(data.get("companies"), dict):
        return {"companies": {}}
    return data


def _save(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def lookup(company, path=None, today=None):
    today = today or date.today()
    data = load(path)
    key = key_of(company)
    entry = data["companies"].get(key) if key else None
    if not isinstance(entry, dict):
        return {"standing": "unknown", "records": [], "stale": 0, "name": company or ""}
    records = [r for r in (entry.get("records") or []) if isinstance(r, dict)]
    stale = sum(1 for r in records if _is_stale(r, today))
    standing = judge(records, today)
    if standing == "fake":
        shown = [r for r in records if r.get("stance") == "fake"]
    else:
        shown = [r for r in records if not _is_stale(r, today)]
    return {
        "standing": standing,
        "records": shown,
        "stale": stale,
        "name": entry.get("name") or company or "",
    }


def _url(stood, stances):
    for record in stood.get("records") or []:
        if record.get("stance") in stances and record.get("url"):
            return record["url"]
    return ""


def grade_note(company, stood):
    """('reason'|'blocker'|'', sentence) for the daily list. Empty when nothing is stored."""
    kind = (stood or {}).get("standing") or "unknown"
    name = company or (stood or {}).get("name") or "This company"
    if kind == "fake":
        url = _url(stood, {"fake"})
        where = f" ({url})" if url else ""
        return "blocker", (
            f"{name} is recorded as a fake or scam employer from a public page{where}."
        )
    if kind == "outstanding":
        url = _url(stood, {"positive", "mixed"})
        where = f" ({url})" if url else ""
        return "reason", (
            f"At least two public write-ups from different pages, within three years, "
            f"describe working at {name} positively{where}. That adds {OUTSTANDING_BONUS} points. "
            f"A ceiling on the role still applies."
        )
    if kind == "bad":
        url = _url(stood, {"negative", "mixed"})
        where = f" ({url})" if url else ""
        return "blocker", (
            f"At least two public write-ups from different pages, within three years, "
            f"describe working at {name} negatively, and none describe it positively{where}. "
            f"The score is capped at {BAD_CAP}, so it stays off the list."
        )
    if kind == "mixed":
        url = _url(stood, {"negative", "positive", "mixed"})
        where = f" ({url})" if url else ""
        return "blocker", (
            f"Public write-ups disagree about working at {name}{where}. "
            f"The score is capped at {MIXED_CAP}, so it cannot be high."
        )
    if kind == "thin":
        url = _url(stood, {"positive", "negative", "mixed"})
        where = f" ({url})" if url else ""
        return "blocker", (
            f"One public write-up mentions working at {name}{where}. "
            f"One person is not enough to move the grade."
        )
    if (stood or {}).get("stale"):
        return "reason", (
            f"Public write-ups stored for {name} are older than three years, so they do not move the grade."
        )
    return "", ""


def _record(url, source, when, stale, stance, note):
    return {
        "url": url,
        "source": source or "",
        "date": when.isoformat() if hasattr(when, "isoformat") else (str(when or "")[:10]),
        "stale": bool(stale),
        "stance": stance,
        "note": re.sub(r"\s+", " ", (note or "")).strip()[:300],
    }


def records_from_reviews(reviews):
    """Keep reviews that judge the employer. A description with no judgment is dropped."""
    out = []
    for item in reviews or []:
        url = (getattr(item, "url", "") or "").strip()
        if not url.startswith("http"):
            continue
        text = f"{getattr(item, 'detail', '')} {getattr(item, 'note', '')}"
        stance = stance_of(text)
        if not stance:
            continue
        out.append(_record(
            url,
            getattr(item, "source", ""),
            getattr(item, "when", None),
            getattr(item, "stale", False),
            stance,
            getattr(item, "detail", "") or getattr(item, "note", ""),
        ))
    return out


def _merge(existing, incoming):
    by_url = {}
    for record in existing or []:
        if isinstance(record, dict) and record.get("url"):
            by_url[record["url"]] = record
    for record in incoming:
        by_url[record["url"]] = record
    return list(by_url.values())


def refresh(company, path=None, research=None, today=None):
    """Pull public employee reviews for one company and store the judgments."""
    company = re.sub(r"\s+", " ", (company or "").strip())[:120]
    if not key_of(company):
        raise ValueError("company is required")
    if research is None:
        import company_reviews
        research = company_reviews.research
    report = research(company)
    incoming = records_from_reviews(getattr(report, "findings", None))
    path = Path(path) if path else DEFAULT_PATH
    data = load(path)
    key = key_of(company)
    entry = data["companies"].get(key) or {"name": company, "records": []}
    entry["name"] = company
    entry["updated"] = (today or date.today()).isoformat()
    entry["records"] = _merge(entry.get("records"), incoming)
    data["companies"][key] = entry
    _save(data, path)
    stood = lookup(company, path=path, today=today)
    stood["added"] = len(incoming)
    return stood


def add_note(company, stance, url, note, source="added by hand", when=None, path=None):
    """Store one page Adam (or a session) actually opened. A URL is required."""
    company = re.sub(r"\s+", " ", (company or "").strip())[:120]
    if not key_of(company):
        raise ValueError("company is required")
    if stance not in _STANCES:
        raise ValueError("stance must be positive, negative, mixed, or fake")
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("a public http(s) URL is required")
    when = when or date.today()
    path = Path(path) if path else DEFAULT_PATH
    data = load(path)
    key = key_of(company)
    entry = data["companies"].get(key) or {"name": company, "records": []}
    entry["name"] = entry.get("name") or company
    entry["updated"] = when.isoformat()
    entry["records"] = _merge(entry.get("records"), [
        _record(url, source, when, False, stance, note),
    ])
    data["companies"][key] = entry
    _save(data, path)
    return lookup(company, path=path, today=when)


_BAND_RANK = {
    "outstanding": 0,
    "thin-positive": 1,
    "unknown": 2,
    "thin-mixed": 3,
    "mixed": 4,
    "thin-negative": 5,
    "bad": 6,
    "fake": 7,
}


def band_of(stood):
    """Finer than standing, so one positive page ranks above silence and one negative page ranks below it."""
    kind = (stood or {}).get("standing") or "unknown"
    if kind != "thin":
        return kind
    stances = {r.get("stance") for r in (stood.get("records") or []) if r.get("stance")}
    if stances <= {"positive"}:
        return "thin-positive"
    if stances <= {"negative"}:
        return "thin-negative"
    return "thin-mixed"


def _counts(stood):
    pos = neg = 0
    for record in (stood or {}).get("records") or []:
        stance = record.get("stance")
        if stance in {"positive", "mixed"}:
            pos += 1
        if stance in {"negative", "mixed"}:
            neg += 1
    return pos, neg


def sort_key(name, stood):
    pos, neg = _counts(stood)
    return (_BAND_RANK.get(band_of(stood), 9), -(pos - neg), (name or "").lower())


def ranked(path=None, today=None):
    """Stored companies, best to worst. Unknown is silence, not a bad score, so it sits under a positive page and above a negative one."""
    data = load(path)
    rows = []
    for key, entry in data["companies"].items():
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or key
        rows.append((name, lookup(name, path=path, today=today)))
    rows.sort(key=lambda row: sort_key(row[0], row[1]))
    return rows


def _list_detail(stood):
    band = band_of(stood)
    pos, neg = _counts(stood)
    if band == "outstanding":
        return f"outstanding, {pos} positive pages"
    if band == "thin-positive":
        return "one positive page (one person does not move the grade)"
    if band == "thin-negative":
        return "one negative page (one person does not move the grade)"
    if band == "thin-mixed":
        return "one page says both positive and negative things (does not move the grade)"
    if band == "mixed":
        return f"mixed, {pos} positive and {neg} negative pages (cannot be high)"
    if band == "bad":
        return f"bad, {neg} negative pages (off the list)"
    if band == "fake":
        return "fake or scam (off the list)"
    if stood.get("stale"):
        detail = "only pages older than three years (does not move the grade)"
    else:
        detail = "no public judgment stored"
    layoffs = [r for r in (stood.get("records") or []) if r.get("stance") == "layoff"]
    if layoffs:
        detail += f" Public layoff notice on file ({len(layoffs)}), which does not change the mark."
    return detail


def render_list(path=None, today=None):
    rows = ranked(path, today)
    if not rows:
        return (
            "No companies are stored. The grade does not change until public write-ups are saved.\n"
        )
    lines = ["Companies, best to worst, from stored public pages.", ""]
    for index, (name, stood) in enumerate(rows, start=1):
        lines.append(f"{index}. {name} — {_list_detail(stood)}")
    lines.append("")
    return "\n".join(lines)


def render_show(company, stood):
    lines = [
        f"# {company}",
        "",
        f"Standing: {stood.get('standing') or 'unknown'}.",
        "This is computed from the pages below. It is not a rating from a review site.",
        "",
    ]
    records = stood.get("records") or []
    if not records and not stood.get("stale"):
        lines.append("No pages are stored. The grade does not change until public write-ups are saved.")
        lines.append("Run: python job_scraper/company_standing.py refresh \"COMPANY\"")
        lines.append("")
        return "\n".join(lines)
    if stood.get("stale") and stood.get("standing") == "unknown":
        lines.append(f"{stood['stale']} stored page(s) are older than three years and do not move the grade.")
        lines.append("")
    for index, record in enumerate(records, start=1):
        when = record.get("date") or "date unknown"
        lines.append(f"{index}. {record.get('stance')} ({record.get('source') or 'source unknown'}, {when})")
        lines.append(f"   {record.get('url')}")
        if record.get("note"):
            lines.append(f"   {record['note']}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Public employer evidence used by the fit grade.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show", help="Print the stored standing for one company.")
    show.add_argument("company")

    refresh_cmd = sub.add_parser("refresh", help="Pull public employee reviews and store judgments.")
    refresh_cmd.add_argument("company")

    note = sub.add_parser("note", help="Store one public page. A URL is required.")
    note.add_argument("company")
    note.add_argument("--stance", required=True, choices=sorted(_STANCES))
    note.add_argument("--url", required=True)
    note.add_argument("--note", default="")
    note.add_argument("--source", default="added by hand")

    listed = sub.add_parser("list", help="Print every stored company and its standing.")
    listed.add_argument("--path", default="")

    sweep_cmd = sub.add_parser("sweep", help="Match many companies at once against the public layoff lists.")
    sweep_cmd.add_argument("--applied", action="store_true", help="Use companies with an application on file.")
    sweep_cmd.add_argument("companies", nargs="*")

    for cmd in (show, refresh_cmd, note, sweep_cmd):
        cmd.add_argument("--path", default="")

    args = parser.parse_args(argv)
    path = args.path or None
    try:
        if args.cmd == "list":
            data = load(path)
            if not data["companies"]:
                print("No companies are stored. The grade does not change until public write-ups are saved.")
                return 0
            print(render_list(path))
            return 0
        if args.cmd == "show":
            print(render_show(args.company, lookup(args.company, path=path)))
            return 0
        if args.cmd == "refresh":
            stood = refresh(args.company, path=path)
            print(render_show(args.company, stood))
            print(f"Stored {stood.get('added', 0)} write-up(s) that judge the employer.")
            return 0
        if args.cmd == "sweep":
            names = list(args.companies)
            if args.applied:
                import applied_log
                seen = set()
                for entry in applied_log.load_log().values():
                    if entry.get("status") != "applied":
                        continue
                    name = re.sub(r"\s+", " ", (entry.get("company") or "").strip())[:120]
                    key = key_of(name)
                    if name and key and key not in seen:
                        seen.add(key)
                        names.append(name)
            notices, errors = fetch_layoff_notices()
            matched = store_notices(names, notices, path=path)
            print(f"Checked {len(names)} companies against {len(notices)} public layoff notices in one pass.")
            for error in errors:
                print(f"Source skipped: {error}")
            if not matched:
                print("None of those companies are on the lists. The marks stay undecided.")
                return 0
            print("A layoff notice is not an employee review, so the mark stays undecided.")
            for name, incoming in matched:
                print(f"{name}: {len(incoming)} notice(s)")
                for record in incoming:
                    print(f"  {record.get('date') or 'date unknown'} {record.get('url')}")
            return 0
        stood = add_note(args.company, args.stance, args.url, args.note, source=args.source, path=path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_show(args.company, stood))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
