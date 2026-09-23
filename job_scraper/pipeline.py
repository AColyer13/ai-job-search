"""Consolidated job-scrape pipeline. Replaces the per-run _run_*/ scripts.

Usage (from repo root):
    python job_scraper/pipeline.py all                 # search + process + update-seen
    python job_scraper/pipeline.py search              # run queries, write raw JSON to _run_<today>/
    python job_scraper/pipeline.py process             # dedup + location tiers + fit -> candidates.json
    python job_scraper/pipeline.py update-seen         # merge run results into seen_jobs.json
    python job_scraper/pipeline.py expire [--days 21]  # mark stale 'new' entries as 'expired'

Options:
    --run-dir PATH   use an existing run folder instead of _run_<today>
    --only a,b,c     run only these query/source names
    --skip a,b,c     skip these query/source names
    --workers N      parallel workers for CLI queries (default 4)

Stdlib only. Paths resolve relative to this file, so it works from any cwd.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRAPER_DIR = ROOT / "job_scraper"
SEEN_PATH = SCRAPER_DIR / "seen_jobs.json"
QUERIES_PATH = SCRAPER_DIR / "queries.json"
TODAY = date.today().isoformat()

USER_AGENT = "Mozilla/5.0 (personal-job-search; contact: adam)"
HTTP_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Location tiers and fit scoring (ported from the _run_2026-09-14 process.py)
# ---------------------------------------------------------------------------

MN_OK = {
    "edina", "eden prairie", "minnetonka", "hopkins", "st. louis park", "st louis park",
    "golden valley", "plymouth", "maple grove", "osseo", "brooklyn park", "rogers",
    "dayton", "medina", "wayzata", "orono", "chaska", "victoria", "bloomington",
    "richfield", "chanhassen", "minneapolis", "shakopee", "new hope", "crystal",
    "brooklyn center", "excelsior", "long lake", "hamel", "maple plain",
}
MS_OK = {
    "gulfport", "biloxi", "long beach", "d'iberville", "diberville",
    "ocean springs", "pass christian", "kiln",
}
EXCLUDE_EAST = {
    "st. paul", "st paul", "saint paul", "woodbury", "maplewood", "oakdale", "eagan",
    "inver grove", "south st paul", "roseville", "white bear", "stillwater", "hastings",
}

# Full US state names, mapped to their 2-letter code. Postings routinely spell
# the state out ("Crystal City, Virginia") instead of abbreviating it, and the
# abbreviation-only regexes below would silently miss those.
US_STATE_NAMES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne",
    "nevada": "nv", "new hampshire": "nh", "new jersey": "nj",
    "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
}
US_STATE_ABBR_RE = re.compile(
    r",\s*(al|ak|az|ar|ca|co|ct|dc|de|fl|ga|hi|id|il|ia|ks|ky|la|ma|md|me|mi|mn|"
    r"mo|ms|mt|nc|nd|ne|nh|nj|nm|nv|ny|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|va|vt|wa|"
    r"wi|wv|wy)\b"
)
CA_PROVINCE_RE = re.compile(
    r"\b(bc|ab|on|qc|mb|sk|ns|nb|pe|nl|yt|nt|nu|british columbia|ontario|quebec|"
    r"alberta|manitoba|saskatchewan|nova scotia|new brunswick)\b",
    re.I,
)
NON_US = [
    "canada", "ontario", "quebec", "british columbia", "alberta", "brazil", "brasil",
    "spain", "united kingdom", "scotland", "finland", "germany", "france", "india",
    "poland", "portugal", "netherlands", "sweden", "ireland", "australia", "mexico",
    "italy", "japan", "korea", "singapore", "philippines", "romania", "ukraine",
    "israel", "uae", "dubai", "toronto", "manaus", "helsinki", "madrid", "edinburgh",
    "calgary", "porto alegre", "london", "berlin", "paris", "bangalore", "bengaluru",
    "hyderabad", "warsaw", "lisbon", "sao paulo", "são paulo", "vancouver", "montreal",
    "ottawa", "dublin", "amsterdam", "zurich", "munich", "barcelona", "colombia",
    "peru", "hamburg", "south africa", "europe", "norway", "gauteng", "medell",
    "antioquia", "durban", "latam", "worldwide", "lviv",
]

SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|vice president|head of|architect|vp\b|chief |ciso|cto)\b",
    re.I,
)
INTERN_RE = re.compile(r"\b(intern|internship|co-op|coop)\b", re.I)
AI_RE = re.compile(r"\b(ai|ml|llm|genai|machine learning|prompt|rag|langchain|agent)\b", re.I)
FS_RE = re.compile(
    r"\b(full[- ]?stack|software engineer|software developer|web developer|"
    r"frontend|front-end|backend|react|typescript|python)\b",
    re.I,
)
BRIDGE_RE = re.compile(
    r"\b(solutions engineer|sales engineer|technical account|customer engineer|"
    r"forward deployed|implementation engineer|implementation consultant|"
    r"implementation specialist)\b",
    re.I,
)
ENTRY_RE = re.compile(
    r"\b(junior|jr\.?|entry[- ]level|associate software|software engineer i\b|software engineer 1\b|new grad|early career|graduate)\b",
    re.I,
)
# Broad title pre-filter for fetch-all HTTP sources (fit() does the real triage later).
TECH_TITLE_RE = re.compile(
    r"\b(engineer|developer|software|full[- ]?stack|frontend|front-end|backend|"
    r"ai\b|ml\b|llm|machine learning|data scien|devops|solutions|technical account)\b",
    re.I,
)


def _region_code(l):
    """Return the 2-letter US state code found in `l` (via abbreviation after a
    comma, or a spelled-out state name anywhere), or None if none is found.
    Used to tell apart same-named cities in different states/provinces, e.g.
    "Crystal City, Virginia" vs. Crystal, MN, or "Victoria, BC" vs. Victoria, MN.
    """
    m = US_STATE_ABBR_RE.search(l)
    if m:
        return m.group(1)
    for name, code in US_STATE_NAMES.items():
        if re.search(r"\b" + re.escape(name) + r"\b", l):
            return code
    return None


def loc_tier(loc, work_mode=None):
    loc = (loc or "").strip()
    l = loc.lower()
    wm = (work_mode or "").lower() if work_mode else ""
    if l in ("ua", "uk", "de", "in", "br", "ca", "pl", "pt", "es", "fr", "nl", "eu"):
        return "exclude-intl"
    if re.search(r"dayton,?\s*oh|\bohio\b|dayton office", l):
        return "unknown"
    if any(x in l for x in EXCLUDE_EAST):
        return "exclude-east"
    if any(x in l for x in NON_US) and not any(
        x in l for x in ["united states", "usa", "minnesota", ", mn", ", ms"]
    ):
        return "exclude-intl"
    if CA_PROVINCE_RE.search(l) and not any(
        x in l for x in ["united states", "usa", ", us"]
    ):
        return "exclude-intl"
    for c in MN_OK:
        if re.search(r"\b" + re.escape(c) + r"\b", l):
            if re.search(r"\boh\b|ohio", l):
                return "unknown"
            region = _region_code(l)
            if region and region != "mn":
                continue  # same-named place in another state (e.g. "Plymouth, MA")
            return "mn"
    for c in MS_OK:
        if re.search(r"\b" + re.escape(c) + r"\b", l):
            region = _region_code(l)
            if region and region != "ms":
                continue  # same-named place in another state (e.g. "Long Beach, CA")
            return "ms"
    if "mississippi" in l or re.search(r",\s*ms\b", l):
        return "ms-inland"
    if "remote" in l or wm == "remote":
        if re.search(r"united states|, us\b|usa|us - remote|remote, us|us remote", l) or l in (
            "remote",
            "united states",
        ):
            return "remote"
        if wm == "remote" and not loc:
            return "remote"
        if wm == "remote":
            if re.search(r", (al|ak|az|ar|ca|co|ct|dc|fl|ga|hi|id|il|ia|ks|ky|la|ma|md|me|mi|mo|mt|nc|nd|ne|nh|nj|nm|nv|ny|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|va|vt|wa|wi|wv|wy)\b", l) or "united states" in l or ", us" in l:
                return "remote"
            return "exclude-intl"
        return "remote"
    if "minnesota" in l or re.search(r",\s*mn\b", l):
        return "mn-other"
    if "united states" in l or l.endswith(", us") or ", usa" in l:
        return "us-reloc"
    if _region_code(l):
        return "us-reloc"
    if not loc:
        return "unknown"
    return "unknown"


# Description-level demotion signals. These only ever move a title-based
# verdict down a tier (high->medium->low) - they never promote, since a title
# match is a stronger, more deliberate signal than free text in a description.
YEARS_EXPERIENCE_RE = re.compile(
    r"(\d{1,2})\+?\s*years?(?:\s+of)?\s+(?:[\w/,&-]+\s+){0,4}experience", re.I
)
CLEARANCE_RE = re.compile(
    r"\b(security clearance|active clearance|top secret|ts/sci|polygraph)\b", re.I
)
_DEMOTE = {"high": "medium", "medium": "low", "low": "low"}


def _fit_from_title(title, skills=None):
    t = title or ""
    if INTERN_RE.search(t) or "2027" in t:
        return "low"
    if re.search(
        r"\b(firmware|embedded|electrical|manufacturing|cyber security architect|scada|lidar|maritime autonomous)\b",
        t,
        re.I,
    ):
        return "low"
    is_bridge = bool(BRIDGE_RE.search(t))
    is_senior = bool(SENIOR_RE.search(t)) and not re.search(
        r"\b(junior|entry|associate)\b", t, re.I
    )
    if is_senior and not is_bridge:
        return "low"
    if is_bridge:
        if re.search(r"\b(director|vp\b|head of|chief|vice president)\b", t, re.I):
            return "low"
        if is_senior:
            return "medium"
        return "high"
    if AI_RE.search(t) and not is_senior:
        if ENTRY_RE.search(t) or re.search(
            r"\b(application|fullstack|full[- ]?stack|software)\b", t, re.I
        ):
            return "high"
        return "medium"
    if ENTRY_RE.search(t) and FS_RE.search(t):
        return "high"
    if re.search(r"\b(full[- ]?stack)\b", t, re.I) and not is_senior:
        return "high"
    if FS_RE.search(t) and not is_senior:
        return "medium"
    skills = skills or []
    sl = " ".join(skills).lower()
    core = any(
        s in sl
        for s in ["react", "typescript", "next.js", "nextjs", "python", "fastapi", "langchain", "rag"]
    )
    if core and not is_senior:
        return "medium"
    return "low"


def fit(title, skills=None, description=None):
    """Title/skills-based verdict, demoted using the posting description when
    available. A title like "Software Engineer" can't tell "3 years, React"
    apart from "8 years, Java + clearance required" - the description can.
    """
    f = _fit_from_title(title, skills)
    if description and f != "low":
        years = [int(n) for n in YEARS_EXPERIENCE_RE.findall(description)]
        if any(y >= 5 for y in years):
            f = _DEMOTE[f]
        if CLEARANCE_RE.search(description):
            f = _DEMOTE[f]
    return f


SALARY_RE = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3})?(?:\s?[kK])?"
    r"(?:\s*(?:-|–|to)\s*\$?\s?\d{2,3}(?:,\d{3})?(?:\s?[kK])?)?"
)


def extract_salary(description):
    """Best-effort salary figure pulled from a posting description, so
    candidates.json can surface pay without a second fetch. Returns None if
    no dollar figure is found - this is a convenience hint, not a parsed,
    validated compensation field."""
    if not description:
        return None
    m = SALARY_RE.search(description)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# HTTP job sources (keyless). Each returns a list of normalized job dicts:
# title, company, location, date, url, id, portal, work_mode, skills, description
# ---------------------------------------------------------------------------

def _get(url, headers=None, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _get_json(url, headers=None):
    return json.loads(_get(url, headers=headers))


def _strip_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(text or ""))).strip()


NON_ENGLISH_TITLE_RE = re.compile(
    r"\b(desarrollador|entwickler|entwicklerin|développeur|developpeur|programador|"
    r"ingeniero|ingénieur|m/w/d|f/m/d)\b",
    re.I,
)


class SkipSource(Exception):
    """Raised by a source that is not configured (e.g. missing API key)."""


def _keep_title(title):
    t = title or ""
    return bool(TECH_TITLE_RE.search(t)) and not NON_ENGLISH_TITLE_RE.search(t)


def src_remoteok():
    data = _get_json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is a legal notice
        if not _keep_title(j.get("position")):
            continue
        jid = str(j.get("id") or j.get("slug") or "")
        out.append({
            "title": j.get("position"),
            "company": j.get("company"),
            "location": j.get("location") or "Remote",
            "date": (j.get("date") or "")[:10],
            "url": j.get("url") or f"https://remoteok.com/remote-jobs/{jid}",
            "id": jid,
            "portal": "remoteok",
            "work_mode": "remote",
            "skills": j.get("tags") or [],
            "description": _strip_html(j.get("description"))[:2000],
        })
    return out


def src_remotive():
    data = _get_json("https://remotive.com/api/remote-jobs?limit=200")
    out = []
    for j in data.get("jobs") or []:
        if not _keep_title(j.get("title")):
            continue
        loc = j.get("candidate_required_location") or "Anywhere"
        out.append({
            "title": j.get("title"),
            "company": j.get("company_name"),
            "location": f"Remote ({loc})",
            "date": (j.get("publication_date") or "")[:10],
            "url": j.get("url"),
            "id": str(j.get("id") or ""),
            "portal": "remotive",
            "work_mode": "remote",
            "skills": j.get("tags") or [],
            "description": _strip_html(j.get("description"))[:2000],
        })
    return out


def src_arbeitnow():
    out = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    for _ in range(2):  # first two pages only
        data = _get_json(url)
        for j in data.get("data") or []:
            if not j.get("remote"):
                continue
            if not _keep_title(j.get("title")):
                continue
            created = j.get("created_at")
            out.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "location": "Remote",
                "date": datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat() if created else "",
                "url": j.get("url"),
                "id": j.get("slug") or "",
                "portal": "arbeitnow",
                "work_mode": "remote",
                "skills": j.get("tags") or [],
                "description": _strip_html(j.get("description"))[:2000],
            })
        url = (data.get("links") or {}).get("next")
        if not url:
            break
    return out


def src_jobicy():
    data = _get_json("https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa")
    out = []
    for j in data.get("jobs") or []:
        if not _keep_title(j.get("jobTitle")):
            continue
        out.append({
            "title": j.get("jobTitle"),
            "company": j.get("companyName"),
            "location": f"Remote ({j.get('jobGeo') or 'USA'})",
            "date": (j.get("pubDate") or "")[:10],
            "url": j.get("url"),
            "id": str(j.get("id") or ""),
            "portal": "jobicy",
            "work_mode": "remote",
            "skills": j.get("jobIndustry") or [],
            "description": _strip_html(j.get("jobDescription"))[:2000],
        })
    return out


def src_weworkremotely():
    raw = _get("https://weworkremotely.com/categories/remote-programming-jobs.rss")
    out = []
    root = ET.fromstring(raw)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        # WWR titles look like "Company: Role"
        company, _, role = title.partition(":")
        role = role.strip() or title
        if not _keep_title(role):
            continue
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
        except ValueError:
            dt = ""
        out.append({
            "title": role,
            "company": company.strip(),
            "location": "Remote",
            "date": dt,
            "url": link,
            "id": link.rsplit("-", 1)[-1],
            "portal": "weworkremotely",
            "work_mode": "remote",
            "skills": [],
            "description": _strip_html(item.findtext("description"))[:2000],
        })
    return out


def src_workingnomads():
    data = _get_json("https://www.workingnomads.com/api/exposed_jobs/")
    out = []
    for j in data:
        if not isinstance(j, dict) or not _keep_title(j.get("title")):
            continue
        out.append({
            "title": j.get("title"),
            "company": j.get("company_name"),
            "location": j.get("location") or "Remote",
            "date": (j.get("pub_date") or "")[:10],
            "url": j.get("url"),
            "id": str(j.get("id") or ""),
            "portal": "workingnomads",
            "work_mode": "remote",
            "skills": (j.get("tags") or "").split(",") if isinstance(j.get("tags"), str) else (j.get("tags") or []),
            "description": _strip_html(j.get("description"))[:2000],
        })
    return out


def src_hn_hiring():
    search = _get_json(
        "https://hn.algolia.com/api/v1/search_by_date?"
        "query=%22Ask%20HN%3A%20Who%20is%20hiring%3F%22&tags=story&hitsPerPage=1"
    )
    hits = search.get("hits") or []
    if not hits:
        return []
    thread_id = hits[0]["objectID"]
    thread = _get_json(f"https://hn.algolia.com/api/v1/items/{thread_id}")
    out = []
    for c in thread.get("children") or []:
        text = _strip_html(c.get("text"))
        if not text or "remote" not in text.lower():
            continue
        first_line = text.split("  ")[0][:300]
        parts = [p.strip() for p in first_line.split("|")]
        company = parts[0] if parts else "Unknown"
        title = parts[1] if len(parts) > 1 else "See HN comment"
        loc = next((p for p in parts[1:] if "remote" in p.lower()), "Remote")
        out.append({
            "title": title,
            "company": company,
            "location": loc,
            "date": (c.get("created_at") or "")[:10],
            "url": f"https://news.ycombinator.com/item?id={c.get('id')}",
            "id": str(c.get("id") or ""),
            "portal": "hn_hiring",
            "work_mode": "remote",
            "skills": [],
            "description": text[:2000],
        })
        if len(out) >= 100:
            break
    return out


# --- Key-required sources (skipped silently when env vars are absent) --------

def src_adzuna():
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise SkipSource("set ADZUNA_APP_ID and ADZUNA_APP_KEY to enable - free at developer.adzuna.com")
    out = []
    for q in ["junior software engineer", "AI engineer", "full stack developer"]:
        url = (
            "https://api.adzuna.com/v1/api/jobs/us/search/1"
            f"?app_id={app_id}&app_key={app_key}&results_per_page=50"
            f"&what={urllib.parse.quote(q)}&max_days_old=14&content-type=application/json"
        )
        for j in _get_json(url).get("results") or []:
            out.append({
                "title": _strip_html(j.get("title")),
                "company": (j.get("company") or {}).get("display_name"),
                "location": (j.get("location") or {}).get("display_name"),
                "date": (j.get("created") or "")[:10],
                "url": j.get("redirect_url"),
                "id": str(j.get("id") or ""),
                "portal": "adzuna",
                "work_mode": "remote" if "remote" in (j.get("title") or "").lower() else None,
                "skills": [],
                "description": _strip_html(j.get("description"))[:2000],
            })
    return out


def src_usajobs():
    key = os.environ.get("USAJOBS_API_KEY")
    email = os.environ.get("USAJOBS_EMAIL")
    if not (key and email):
        raise SkipSource("set USAJOBS_API_KEY and USAJOBS_EMAIL to enable - free at developer.usajobs.gov")
    out = []
    headers = {"Authorization-Key": key, "User-Agent": email}
    for kw in ["software engineer", "artificial intelligence"]:
        url = (
            "https://data.usajobs.gov/api/search"
            f"?Keyword={urllib.parse.quote(kw)}&ResultsPerPage=50&DatePosted=14"
        )
        for item in _get_json(url, headers=headers).get("SearchResult", {}).get("SearchResultItems") or []:
            j = item.get("MatchedObjectDescriptor") or {}
            locs = [l.get("LocationName") for l in j.get("PositionLocation") or []]
            out.append({
                "title": j.get("PositionTitle"),
                "company": j.get("OrganizationName") or j.get("DepartmentName"),
                "location": "; ".join(x for x in locs if x),
                "date": (j.get("PublicationStartDate") or "")[:10],
                "url": j.get("PositionURI"),
                "id": str(j.get("PositionID") or ""),
                "portal": "usajobs",
                "work_mode": "remote" if j.get("TeleworkEligible") else None,
                "skills": [],
                "description": _strip_html(j.get("UserArea", {}).get("Details", {}).get("JobSummary"))[:2000],
            })
    return out


# --- Target-company board APIs (Tier 5 in SOURCES.md) -----------------------
# Greenhouse/Lever/Ashby all expose keyless public JSON per company. Slugs are
# guessed easily but wrong just as easily (a wrong slug 404s and is silently
# skipped, per-company, by the try/except below) - verify a slug with
# `tools/probe_company_board.py <platform> <slug>` before adding it to
# queries.json's "company_boards" list.

def _gh_board_jobs(slug):
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs") or []:
        loc = (j.get("location") or {}).get("name") or ""
        out.append({
            "title": j.get("title"),
            "location": loc,
            "date": (j.get("updated_at") or "")[:10],
            "url": j.get("absolute_url"),
            "id": str(j.get("id") or ""),
            "work_mode": "remote" if "remote" in loc.lower() else None,
            "skills": [],
            "description": _strip_html(j.get("content"))[:2000],
        })
    return out


def _lever_board_jobs(slug):
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        out.append({
            "title": j.get("text"),
            "location": loc,
            "date": (str(j.get("createdAt") or "")[:10]),
            "url": j.get("hostedUrl"),
            "id": str(j.get("id") or ""),
            "work_mode": "remote" if "remote" in loc.lower() else None,
            "skills": [],
            "description": _strip_html(j.get("descriptionPlain") or j.get("description"))[:2000],
        })
    return out


def _ashby_board_jobs(slug):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for j in data.get("jobs") or []:
        loc = j.get("location") or ""
        out.append({
            "title": j.get("title"),
            "location": loc,
            "date": (j.get("publishedAt") or "")[:10],
            "url": j.get("jobUrl"),
            "id": str(j.get("id") or ""),
            "work_mode": "remote" if j.get("isRemote") else None,
            "skills": [],
            "description": _strip_html(j.get("descriptionPlain") or "")[:2000],
        })
    return out


_BOARD_PLATFORMS = {
    "greenhouse": _gh_board_jobs,
    "lever": _lever_board_jobs,
    "ashby": _ashby_board_jobs,
}


def src_boards():
    """Poll every company in queries.json's "company_boards" list. One
    company's failure (bad slug, board closed) never aborts the others."""
    cfg = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    companies = cfg.get("company_boards") or []
    if not companies:
        raise SkipSource("no entries in queries.json 'company_boards' - see SOURCES.md Tier 5")
    out = []
    for c in companies:
        name, platform, slug = c.get("name"), c.get("platform"), c.get("slug")
        fn = _BOARD_PLATFORMS.get(platform)
        if not fn:
            print(f"    SKIP company_boards.{name}: unknown platform {platform!r}", flush=True)
            continue
        try:
            for j in fn(slug):
                if not _keep_title(j.get("title")):
                    continue
                out.append({**j, "company": name, "portal": f"boards-{platform}"})
        except Exception as e:
            print(f"    SKIP company_boards.{name} ({platform}/{slug}): {type(e).__name__}: {e}", flush=True)
    return out


HTTP_SOURCES = {
    "remoteok": src_remoteok,
    "remotive": src_remotive,
    "arbeitnow": src_arbeitnow,
    "jobicy": src_jobicy,
    "weworkremotely": src_weworkremotely,
    "workingnomads": src_workingnomads,
    "hn_hiring": src_hn_hiring,
    "adzuna": src_adzuna,
    "usajobs": src_usajobs,
    "boards": src_boards,
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_seen():
    if SEEN_PATH.exists():
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}}


def save_seen(doc):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def run_dir(args):
    if args.run_dir:
        return Path(args.run_dir)
    return SCRAPER_DIR / f"_run_{TODAY}"


def selected(names, args):
    only = set(args.only.split(",")) if args.only else None
    skip = set(args.skip.split(",")) if args.skip else set()
    return [n for n in names if (only is None or n in only) and n not in skip]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _run_cli_query(name, portal, args_list, out_dir):
    cli = ROOT / ".agents" / "skills" / portal / "cli" / "src" / "cli.ts"
    cmd = ["bun", "run", str(cli), *args_list]
    print(f"START {name}", flush=True)
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    (out_dir / f"{name}.json").write_text(p.stdout or "", encoding="utf-8")
    if p.stderr:
        (out_dir / f"{name}.err").write_text(p.stderr, encoding="utf-8")
    try:
        data = json.loads(p.stdout or "")
        n = len(data.get("results") or [])
        print(f"OK {name} n={n} exit={p.returncode}", flush=True)
    except Exception as e:
        print(f"FAIL {name} exit={p.returncode} parse={e} stderr={(p.stderr or '')[:300]}", flush=True)
    return p.returncode


def cmd_search(args):
    out_dir = run_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))

    queries = [q for q in cfg["cli_queries"] if q["name"] in selected([q["name"] for q in cfg["cli_queries"]], args)]
    fails = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_run_cli_query, q["name"], q["portal"], q["args"], out_dir): q["name"] for q in queries}
        for fut in as_completed(futs):
            try:
                rc = fut.result()
            except Exception as e:
                print(f"EXC {futs[fut]} {e}", flush=True)
                rc = 1
            if rc != 0:
                fails += 1

    for name in selected(cfg.get("http_sources") or list(HTTP_SOURCES), args):
        fn = HTTP_SOURCES.get(name)
        if not fn:
            print(f"SKIP {name} (unknown source)", flush=True)
            continue
        print(f"START {name}", flush=True)
        try:
            results = fn()
            (out_dir / f"{name}.json").write_text(
                json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
            print(f"OK {name} n={len(results)}", flush=True)
        except SkipSource as e:
            print(f"SKIP {name} ({e})", flush=True)
        except Exception as e:
            print(f"FAIL {name} {type(e).__name__}: {e}", flush=True)
            fails += 1

    print(f"DONE fails={fails}")
    return 0


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------

# Query params that are just tracking/wrapper noise (utm_*, ref codes) and can
# be stripped when comparing two URLs for "is this the same posting". Anything
# else in the query string is assumed to be meaningful (e.g. HN comment links
# use ?id=<n> as the *only* thing distinguishing one posting from another -
# stripping the whole query string there made every HN link after the first
# look like a duplicate of it).
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "se", "v", "ref", "referrer", "source", "gh_src",
}


def _url_key(url):
    """Dedup key for a URL: strip known tracking-wrapper query params, keep
    everything else (including params that identify the specific posting)."""
    if not url or "?" not in url:
        return url
    base, qs = url.split("?", 1)
    kept = [p for p in qs.split("&") if p.split("=", 1)[0] not in _TRACKING_PARAMS]
    return base + ("?" + "&".join(kept) if kept else "")


def _seen_indexes(seen):
    seen_urls = set(seen.keys()) | {v.get("url", "") for v in seen.values()}
    seen_url_keys = {_url_key(u) for u in seen_urls if u}
    seen_ids = set()
    seen_ct = set()
    for k, v in seen.items():
        u = v.get("url") or k
        m = re.search(r"(\d{8,})", u)
        if m:
            seen_ids.add(m.group(1))
        m2 = re.search(r"(?:gh_jid=|/jobs/)(\d{6,})", u)
        if m2:
            seen_ids.add(m2.group(1))
        seen_ct.add(((v.get("company") or "").strip().lower(),
                     (v.get("title") or "").strip().lower()))
    return seen_urls, seen_url_keys, seen_ids, seen_ct


def _already(j, seen_urls, seen_url_keys, seen_ids, seen_ct):
    url = j.get("url") or ""
    if url and (url in seen_urls or _url_key(url) in seen_url_keys):
        return True
    m = re.search(r"(\d{8,})", url) or re.search(r"(\d{8,})", str(j.get("id") or ""))
    if m and m.group(1) in seen_ids:
        return True
    m2 = re.search(r"(?:gh_jid=|/jobs/)(\d{6,})", url)
    if m2 and m2.group(1) in seen_ids:
        return True
    ct = ((j.get("company") or "").strip().lower(), (j.get("title") or "").strip().lower())
    return ct in seen_ct and bool(ct[0]) and bool(ct[1])


def _load_run_jobs(rd):
    jobs, health = [], {}
    for fp in sorted(rd.glob("*.json")):
        name = fp.name
        if name in ("candidates.json", "presentable.json"):
            continue
        try:
            with open(fp, encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict) or "results" not in data:
            continue
        portal = name.rsplit(".", 1)[0]
        if portal.startswith("li_"):
            portal = "linkedin-search"
        elif portal.startswith("fh_"):
            portal = "freehire-search"
        results = data.get("results") or []
        h = health.setdefault(portal, {"n": 0, "empty_co": 0, "empty_ti": 0, "files": 0, "zero_files": 0})
        h["files"] += 1
        if not results:
            h["zero_files"] += 1
        for r in results:
            h["n"] += 1
            if not r.get("company"):
                h["empty_co"] += 1
            if not r.get("title"):
                h["empty_ti"] += 1
            r["date"] = str(r.get("date") or "")[:10]
            jobs.append({**r, "portal": r.get("portal") or portal, "src": name})
    uniq = {}
    for j in jobs:
        key = j.get("url") or j.get("id")
        if key and key not in uniq:
            uniq[key] = j
    return list(uniq.values()), health


def cmd_process(args):
    rd = run_dir(args)
    seen = load_seen()["seen"]
    seen_urls, seen_url_keys, seen_ids, seen_ct = _seen_indexes(seen)
    jobs, health = _load_run_jobs(rd)

    new, all_rows = [], []
    skipped_seen = skipped_loc = skipped_lang = 0
    for j in jobs:
        if NON_ENGLISH_TITLE_RE.search(j.get("title") or ""):
            skipped_lang += 1
            continue
        if _already(j, seen_urls, seen_url_keys, seen_ids, seen_ct):
            skipped_seen += 1
            continue
        tier = loc_tier(j.get("location"), j.get("work_mode"))
        f = fit(j.get("title"), j.get("skills"), j.get("description"))
        j["tier"] = tier
        j["fit"] = f
        all_rows.append({"url": j.get("url"), "fit": f, "tier": tier})
        if tier not in ("mn", "remote", "ms"):
            skipped_loc += 1
            continue
        new.append(j)

    order = {"high": 0, "medium": 1, "low": 2}
    new.sort(key=lambda j: (order.get(j["fit"], 9), str(j.get("date") or ""), j.get("title") or ""))

    from collections import Counter
    print(f"TOTAL unique={len(jobs)} new={len(new)} seen={skipped_seen} loc_skip={skipped_loc} lang_skip={skipped_lang}")
    print("tiers", dict(Counter(j["tier"] for j in new)))
    print("fit", dict(Counter(j["fit"] for j in new)))
    print("portals", dict(Counter(j["portal"] for j in new)))
    for portal, h in health.items():
        if h["n"] == 0 or h["empty_co"] == h["n"] or h["empty_ti"] == h["n"]:
            print(f"HEALTH-WARN {portal}: {h}")

    out = [{
        "fit": j["fit"], "tier": j["tier"], "title": j.get("title"),
        "company": j.get("company"), "location": j.get("location"),
        "date": j.get("date"), "url": j.get("url"), "id": j.get("id"),
        "portal": j["portal"], "work_mode": j.get("work_mode"),
        "src": j["src"], "skills": j.get("skills") or [],
        "salary": extract_salary(j.get("description")),
        "description": (j.get("description") or "")[:400],
    } for j in new]

    with open(rd / "candidates.json", "w", encoding="utf-8") as f:
        json.dump({"new": out, "all": all_rows, "all_count": len(jobs),
                   "seen": skipped_seen, "loc_skip": skipped_loc,
                   "health": health}, f, indent=2, ensure_ascii=False)

    presentable = [j for j in new if j["fit"] in ("high", "medium")]
    print(f"\n=== PRESENTABLE ({len(presentable)}) ===")
    for i, j in enumerate(presentable, 1):
        print(f"{i:2}. [{j['fit']}/{j['tier']}] {j.get('title')}")
        salary_suffix = f" | {j['salary']}" if j.get("salary") else ""
        print(f"    {j.get('company')} | {j.get('location')} | {j.get('date')}{salary_suffix}")
        print(f"    {j.get('url')}")
    return 0


# ---------------------------------------------------------------------------
# update-seen
# ---------------------------------------------------------------------------

def cmd_update_seen(args):
    rd = run_dir(args)
    cand_path = rd / "candidates.json"
    if not cand_path.exists():
        print("candidates.json missing - run `process` first", file=sys.stderr)
        return 1
    cand = json.loads(cand_path.read_text(encoding="utf-8"))
    new_urls = {j["url"] for j in cand["new"]}
    fit_by_url = {r["url"]: r["fit"] for r in cand.get("all", [])}

    doc = load_seen()
    seen = doc["seen"]
    jobs, _ = _load_run_jobs(rd)

    added = 0
    for j in jobs:
        url = j.get("url") or ""
        if not url or url in seen:
            continue
        base = url.split("?")[0]
        if any(k.split("?")[0] == base for k in seen):
            continue
        f = fit_by_url.get(url) or fit(j.get("title"), j.get("skills"), j.get("description"))
        status = "new" if url in new_urls and f in ("high", "medium") else "skipped"
        seen[url] = {
            "title": j.get("title"),
            "company": j.get("company"),
            "url": url,
            "first_seen": TODAY,
            "fit": f,
            "status": status,
            "portal": j.get("portal"),
        }
        added += 1

    save_seen(doc)
    print(f"added {added}; total {len(seen)}")
    return 0


# ---------------------------------------------------------------------------
# expire
# ---------------------------------------------------------------------------

def cmd_expire(args):
    doc = load_seen()
    seen = doc["seen"]
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    expired = 0
    for v in seen.values():
        if v.get("status") == "new" and (v.get("first_seen") or "9999") < cutoff:
            v["status"] = "expired"
            expired += 1
    if expired and not args.dry_run:
        save_seen(doc)
    verb = "would expire" if args.dry_run else "expired"
    print(f"{verb} {expired} entries (status=new, first_seen < {cutoff}); total {len(seen)}")
    return 0


# ---------------------------------------------------------------------------
# present - list everything currently actionable (status == "new")
# ---------------------------------------------------------------------------

def cmd_present(args):
    seen = load_seen()["seen"]
    rows = [v for v in seen.values() if v.get("status") == "new"]
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda v: (order.get(v.get("fit"), 9), v.get("first_seen") or ""))
    print(f"ACTIONABLE ({len(rows)}): ")
    for i, v in enumerate(rows, 1):
        print(f"{i:2}. [{v.get('fit')}] {v.get('title')} - {v.get('company')}")
        print(f"    first_seen={v.get('first_seen')} portal={v.get('portal')}")
        print(f"    {v.get('url')}")
    return 0


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["search", "process", "update-seen", "expire", "present", "all"])
    ap.add_argument("--run-dir")
    ap.add_argument("--only")
    ap.add_argument("--skip")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--days", type=int, default=21, help="expire threshold in days")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.command == "search":
        return cmd_search(args)
    if args.command == "process":
        return cmd_process(args)
    if args.command == "update-seen":
        return cmd_update_seen(args)
    if args.command == "expire":
        return cmd_expire(args)
    if args.command == "present":
        return cmd_present(args)
    # all
    rc = cmd_search(args)
    if rc != 0:
        return rc
    rc = cmd_process(args)
    if rc != 0:
        return rc
    rc = cmd_update_seen(args)
    if rc != 0:
        return rc
    return cmd_expire(args)


if __name__ == "__main__":
    sys.exit(main())
