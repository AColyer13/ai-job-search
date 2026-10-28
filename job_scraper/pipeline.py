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

`all` runs the engineering queries and the technical-sales queries. The printed
list is jobs not already in seen_jobs.json.

Stdlib only. Paths resolve relative to this file, so it works from any cwd.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
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
    r"\b(full[- ]?stack|software engineer|software developer|application developer|"
    r"web developer|frontend|front-end|backend|react|typescript|python)\b",
    re.I,
)
# Bridge titles cover the three technical-sales families the scrape keeps
# searching: TSR / technical sales consultant (quota), SE / solutions engineer
# (technical win), and pre-sales consultant / solutions architect (advisory).
# "architect" is also in SENIOR_RE, so a plain "Solutions Architect" scores
# medium. A bare "Software Architect" or "Cloud Architect" is not a bridge
# title and stays low.
BRIDGE_RE = re.compile(
    r"\b(solutions engineer|sales engineer|"
    r"technical sales (?:representative|consultant|rep|specialist|trainee)|"
    r"pre[-\s]?sales (?:consultant|engineer)|"
    r"solutions consultant|solutions architect|"
    r"technical account|customer engineer|"
    r"forward deployed|implementation engineer|implementation consultant|"
    r"implementation specialist|implementation manager|onboarding engineer)\b",
    re.I,
)
ENTRY_RE = re.compile(
    r"\b(junior|jr\.?|entry[- ]level|associate software|software engineer i\b|software engineer 1\b|new grad|early career|graduate)\b",
    re.I,
)
# Junior QA only. He has Playwright in CI and no QA job title, so a plain
# "QA Engineer" or "SDET" stays medium and a senior title stays low.
QA_RE = re.compile(
    r"\b(qa|sdet|quality assurance|software tester)\b",
    re.I,
)
# Broad title pre-filter for fetch-all HTTP sources (fit() does the real triage later).
TECH_TITLE_RE = re.compile(
    r"\b(engineer|developer|software|full[- ]?stack|frontend|front-end|backend|"
    r"ai\b|ml\b|llm|machine learning|data scien|devops|solutions|technical account|"
    r"technical sales|pre[-\s]?sales|implementation|"
    r"qa\b|quality assurance|software tester|sdet)\b",
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
        # A US state, abbreviated ("Austin, TX") or spelled out ("Austin, Texas",
        # "Remote, Texas"), is US remote. Only a non-US place with work_mode
        # remote is international.
        if _region_code(l):
            return "remote"
        if re.search(r"united states|, us\b|usa|us - remote|remote, us|us remote", l) or l in (
            "remote",
            "united states",
        ):
            return "remote"
        if wm == "remote" and not loc:
            return "remote"
        if wm == "remote":
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


_REMOTE_FILLER_RE = re.compile(
    r"\b(remote|united states of america|united states|u\.s\.a\.|u\.s\.|usa|us)\b",
    re.I,
)
_ELSEWHERE_RE = re.compile(
    r"(?:based on our campus in|based in|located in|requires relocation to(?: the area in)?|"
    r"must be located in|must reside in)\s+([^.]{0,160})",
    re.I,
)


def _generic_us_remote(loc):
    """True for a US-remote line that does not name another city or state."""
    low = (loc or "").lower().strip()
    if not low or not re.search(r"\b(remote|united states|usa|u\.s\.|us)\b", low):
        return False
    cleaned = _REMOTE_FILLER_RE.sub(" ", low)
    cleaned = re.sub(r"[,()/.\-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned == ""


def _card_in_requested_location(loc, work_mode=None):
    """Twin Cities list, Gulfport–Biloxi, or US remote with no other city."""
    low = (loc or "").lower()
    if re.search(r"\bdayton\b", low) and not re.search(r"\bminnesota\b|\bmn\b", low):
        return False
    tier = loc_tier(loc, work_mode)
    if tier in ("mn", "ms"):
        return True
    if _generic_us_remote(low):
        return True
    if (work_mode or "").lower() == "remote" and not low:
        return True
    return False


def _required_elsewhere(description):
    """City the posting itself requires, when that city is outside the list."""
    if not description:
        return ""
    for match in _ELSEWHERE_RE.finditer(description):
        phrase = re.split(r"\s+and\s+", match.group(1), maxsplit=1)[0].strip(" ,;:-")
        if not phrase:
            continue
        if re.search(r"\btime zones?\b", phrase, re.I):
            continue
        if _generic_us_remote(phrase) or _card_in_requested_location(phrase, "remote"):
            continue
        if not (_region_code(phrase.lower()) or re.search(r",\s*[A-Za-z]", phrase)):
            continue
        place = re.search(
            r"([A-Za-z][A-Za-z .'-]{1,40},\s*(?:[A-Z]{2}|[A-Za-z][a-z]+))",
            phrase,
        )
        return place.group(1).strip() if place else phrase[:80]
    return ""


def location_high_block(loc, work_mode=None, description=None, title=None):
    """Why this job cannot be high, or None when the location is on the list."""
    elsewhere = _required_elsewhere(description)
    if elsewhere:
        return f"the posting requires {elsewhere}"
    import scoring

    named = scoring.title_states(title)
    if named and not {"MN", "MS"} & set(named):
        return f"the title ties the seat to {', '.join(named)}, so you would relocate"
    limited_to = scoring.state_restriction(description)
    if limited_to and not {"MN", "MS"} & set(limited_to):
        return f"the posting is limited to {', '.join(limited_to)}, so you would relocate"
    if _card_in_requested_location(loc, work_mode):
        return None
    shown = (loc or "").strip() or "not listed"
    return f"the card location is {shown}"


def cap_high_for_location(job):
    """Drop high to medium when the job is outside the requested locations."""
    capped_before = job.get("fit_before_location") == "high"
    if (job.get("fit") or "").lower() != "high" and not capped_before:
        return job
    block = location_high_block(
        job.get("location"), job.get("work_mode"), job.get("description"), job.get("title")
    )
    if not block:
        if capped_before:
            job["fit"] = "high"
        job.pop("fit_before_location", None)
        job.pop("location_block", None)
        return job
    job["fit_before_location"] = "high"
    job["location_block"] = block
    job["fit"] = "medium"
    return job


# Description-level demotion signals. These only ever move a title-based
# verdict down a tier (high->medium->low) - they never promote, since a title
# match is a stronger, more deliberate signal than free text in a description.
YEARS_EXPERIENCE_RE = re.compile(
    r"(\d{1,2})\+?\s*years?(?:\s+of)?\s+(?:[\w/,&-]+\s+){0,4}experience", re.I
)
CLEARANCE_RE = re.compile(
    r"\b(security clearance|active clearance|top secret|ts/sci|polygraph)\b", re.I
)


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
    if QA_RE.search(t) and not is_senior:
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
    """Grade a title (and posting text when there is any) against Adam's resume.

    Delegates to scoring.score. Without readable posting text the grade is
    low: a title cannot tell "1 year, bootcamp welcome" from "5 years, CS
    degree required". A non-role title with a React/Python skill tag and
    readable text that passes the checks is medium.
    """
    import scoring

    out = scoring.score({"title": title, "description": description or ""})
    grade = out["grade"]
    if not out["verified"]:
        # scoring.grade_job keeps an unread target title as medium for the daily list.
        # A title-only call has no text at all, so it stays low.
        return "low"
    if (
        grade == "low"
        and out["verified"]
        and not out["disqualified"]
        and out["family"] == "other"
        and skills
        and not scoring.SENIOR_RE.search(title or "")
    ):
        sl = " ".join(skills).lower()
        if any(k in sl for k in ["react", "typescript", "next.js", "nextjs", "python", "fastapi", "langchain", "rag"]):
            return "medium"
    return grade


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

# posting_text.enrich skips a refetch once the saved text is 1,500 characters,
# so keep the API body up to the same cap the fetcher uses (8,000).
DESC_MAX = 8000


def _get(url, headers=None, timeout=HTTP_TIMEOUT, data=None):
    """GET (POST when `data` is bytes) with two retries on 429/502/503/504, waiting out Retry-After (capped at 30 s)."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            retry_after = e.headers.get("Retry-After", "") if e.headers else ""
            time.sleep(min(30, int(retry_after)) if retry_after.isdigit() else 5 * 2 ** attempt)


def _get_json(url, headers=None, timeout=HTTP_TIMEOUT, data=None):
    return json.loads(_get(url, headers=headers, timeout=timeout, data=data))


def _strip_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(text or ""))).strip()


NON_ENGLISH_TITLE_RE = re.compile(
    r"\b(desarrollador|entwickler|entwicklerin|développeur|developpeur|programador|"
    r"ingeniero|ingénieur|m/w/d|f/m/d)\b",
    re.I,
)


# Sites that repost another employer's job. When the same job is also on the
# employer's own page or ATS, the employer's link wins.
AGGREGATOR_HOSTS = (
    "linkedin.com", "himalayas.app", "jobs.workable.com", "jooble.org", "themuse.com", "simplify.jobs", "adzuna.com", "whatjobs.com", "startup.jobs",
    "echojobs.io", "wellfound.com", "builtin.com", "indeed.com", "ziprecruiter.com",
    "glassdoor.com", "google.com/search", "governmentjobs.com", "arbeitnow.com",
    "remoteok.com", "weworkremotely.com", "workingnomads.com", "jobicy.com", "remotive.com",
)


def _norm_words(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_COMPANY_NOISE_RE = re.compile(
    r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|careers?|jobs|hiring|"
    r"linkedin|board|boards|us|usa)(?=\b|$)"
)


# One employer posting under its short name. Seen on 10 HPE pairs in the store.
_COMPANY_ALIASES = {"hpe": "hewlett packard enterprise"}


def company_key(name):
    """Company name without legal suffixes or board labels, so "Rithum" and "Rithum LinkedIn Board" match.

    A trailing "International" goes too ("Flextronics International" is "Flextronics"), but not a
    leading one ("International Paper" keeps its name).
    """
    words = _norm_words(name)
    stripped = " ".join(_COMPANY_NOISE_RE.sub(" ", words).split())
    stripped = re.sub(r"(?<=\s)international$", "", stripped).strip()
    key = stripped or words
    return _COMPANY_ALIASES.get(key, key)


def dedupe_key(job):
    """Same company, same title, same city (or both remote) is one job."""
    company, title = company_key(job.get("company")), _norm_words(job.get("title"))
    if not company or not title:
        return None
    loc = (job.get("location") or "").lower()
    place = "remote" if "remote" in loc or job.get("work_mode") == "remote" else _norm_words(loc.split(",")[0])
    return (company, title, place)


def _is_aggregator(job):
    url = (job.get("url") or "").lower()
    return any(h in url for h in AGGREGATOR_HOSTS)


def _copy_longer_description(dest, src):
    """Keep the longer posting text when two rows are the same job."""
    src_text = src.get("description") or ""
    if len(src_text) > len(dest.get("description") or ""):
        dest["description"] = src_text
        if src.get("body_status"):
            dest["body_status"] = src.get("body_status")


def _copy_earliest_found(dest, src):
    """Keep the earlier found_on when collapsing stored copies."""
    if (src.get("found_on") or "9999") < (dest.get("found_on") or "9999"):
        dest["found_on"] = src.get("found_on")
        if src.get("found_at"):
            dest["found_at"] = src.get("found_at")


# Better places first: Minnesota, then a remote seat, then the Gulf Coast. Anything else ranks last.
_TIER_RANK = {"mn": 0, "remote": 1, "ms": 2}


def _text_fingerprint(job):
    """Start of the posting text, or '' when there is too little of it to tell postings apart."""
    text = _norm_words(job.get("description"))
    return text[:300] if len(text) >= 150 else ""


def collapse_duplicates(jobs):
    """One row per job across sites. Keeps the employer's own link over a repost.

    Same company, title and place is one job. So is the same company and title with the same
    posting text under two place labels (one posting listed for Chicago and for Minneapolis); the
    row with the better place survives and the other place goes in `also_places`.

    Returns (kept, dropped_count). Each kept job lists the other portals that
    carried it in `also_on`.
    """
    best, loose, order = {}, {}, []
    for job in jobs:
        key = dedupe_key(job)
        if key is None:
            order.append(job)
            continue
        fp = _text_fingerprint(job)
        loose_key = (key[0], key[1], fp) if fp else None
        cur = best.get(key)
        if cur is None and loose_key:
            cur = loose.get(loose_key)
        if cur is None:
            best[key] = job
            if loose_key:
                loose[loose_key] = job
            order.append(job)
            continue
        cur_key = dedupe_key(cur)
        employer_wins = _is_aggregator(cur) and not _is_aggregator(job)
        if cur_key == key:
            job_wins = employer_wins
        else:
            gap = _TIER_RANK.get(cur.get("tier"), 9) - _TIER_RANK.get(job.get("tier"), 9)
            job_wins = gap > 0 or (gap == 0 and employer_wins)
        winner, loser = (job, cur) if job_wins else (cur, job)
        _copy_longer_description(winner, loser)
        _copy_earliest_found(winner, loser)
        winner.setdefault("also_on", [])
        if job_wins:
            winner["also_on"].extend(loser.get("also_on", []) + [loser.get("portal")])
            order[order.index(cur)] = job
        else:
            winner["also_on"].append(loser.get("portal"))
        if cur_key != key:
            winner.setdefault("also_places", []).extend(loser.get("also_places", []) + [loser.get("location")])
        best[key] = best[cur_key] = winner
        if loose_key:
            loose[loose_key] = winner
    return order, len(jobs) - len(order)


class SkipSource(Exception):
    """Raised by a source that is not configured (e.g. missing API key)."""


def _keep_title(title):
    t = title or ""
    return bool(TECH_TITLE_RE.search(t)) and not NON_ENGLISH_TITLE_RE.search(t)


REMOTEOK_TAGS = ("", "sales", "customer-support", "full-stack", "junior", "engineer")


def _fail_if_all_failed(name, errors, attempts):
    """A source that makes several requests must not report success when every one failed.

    Swallowing each error and returning [] looks like "no new jobs" and lets the
    search be marked finished, which shrinks the next run's posting window.
    """
    if attempts and len(errors) >= attempts:
        raise RuntimeError(f"all {attempts} {name} request(s) failed: {errors[-1]}")


def src_remoteok():
    rows, ids, errors = [], set(), []
    for tag in REMOTEOK_TAGS:
        try:
            batch = _get_json("https://remoteok.com/api" + (f"?tag={tag}" if tag else ""))
        except Exception as e:
            errors.append(e)
            continue
        for j in batch:
            if isinstance(j, dict) and "position" in j and str(j.get("id")) not in ids:
                ids.add(str(j.get("id")))
                rows.append(j)
    _fail_if_all_failed("RemoteOK", errors, len(REMOTEOK_TAGS))
    out = []
    for j in rows:
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
            "description": _strip_html(j.get("description"))[:DESC_MAX],
        })
    return out


# Remotive asks for at most 4 fetches a day and blocks more than that, so it runs on
# these four hours of the hourly schedule and skips the rest.
REMOTIVE_HOURS = (6, 10, 13, 16)


def src_remotive():
    if datetime.now().hour not in REMOTIVE_HOURS:
        raise SkipSource("Remotive allows 4 fetches a day; runs at 6, 10, 13 and 16")
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
            "description": _strip_html(j.get("description"))[:DESC_MAX],
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
            raw_loc = (j.get("location") or "").strip()
            # The feed is EU-heavy. A bare "Remote" would look like US remote.
            # Only an explicit US location is tagged remote-US; everything else
            # keeps its country so loc_tier can exclude it.
            us = bool(re.search(r"\b(united states|u\.s\.a?\.?|usa)\b", raw_loc, re.I))
            if us:
                location, work_mode = f"Remote ({raw_loc})", "remote"
            elif raw_loc:
                location, work_mode = f"Remote ({raw_loc})", "remote"
            else:
                location, work_mode = "", None
            out.append({
                "title": j.get("title"),
                "company": j.get("company_name"),
                "location": location,
                "date": datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat() if created else "",
                "url": j.get("url"),
                "id": j.get("slug") or "",
                "portal": "arbeitnow",
                "work_mode": work_mode,
                "skills": j.get("tags") or [],
                "description": _strip_html(j.get("description"))[:DESC_MAX],
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
            "description": _strip_html(j.get("jobDescription"))[:DESC_MAX],
        })
    return out


HIMALAYAS_QUERIES = (
    "solutions engineer", "sales engineer", "solutions consultant", "implementation",
    "technical account manager", "pre-sales", "forward deployed", "customer engineer",
    "junior software engineer", "associate software engineer", "full stack", "junior developer",
)


def src_himalayas():
    """Himalayas search API: remote-only, US, newest first, with the full posting text."""
    out, seen = [], set()
    for q in HIMALAYAS_QUERIES:
        for page in (1, 2):
            try:
                data = _get_json(
                    "https://himalayas.app/jobs/api/search?sort=recent&country=US"
                    f"&page={page}&q={urllib.parse.quote(q)}"
                )
            except Exception:
                break
            jobs = data.get("jobs") or []
            for j in jobs:
                url = j.get("guid") or j.get("applicationLink")
                if not url or url in seen or not _keep_title(j.get("title")):
                    continue
                seen.add(url)
                raw_date = j.get("pubDate")
                date = ""
                if raw_date not in (None, "", 0):
                    try:
                        date = datetime.fromtimestamp(int(raw_date)).date().isoformat()
                    except (TypeError, ValueError, OSError):
                        date = ""
                out.append({
                    "title": j.get("title"),
                    "company": j.get("companyName"),
                    "location": "Remote (United States)",
                    "date": date,
                    "url": url,
                    "id": url.rsplit("/", 1)[-1],
                    "portal": "himalayas",
                    "work_mode": "remote",
                    "skills": [],
                    "description": _strip_html(j.get("description"))[:DESC_MAX],
                })
            if len(jobs) < 20:
                break
            time.sleep(0.5)
    return out


WWR_CATEGORIES = (
    "remote-programming-jobs", "remote-full-stack-programming-jobs", "remote-front-end-programming-jobs",
    "remote-back-end-programming-jobs", "remote-sales-and-marketing-jobs", "remote-customer-support-jobs",
    "remote-devops-sysadmin-jobs",
)


def src_weworkremotely():
    out, seen, errors = [], set(), []
    for category in WWR_CATEGORIES:
        try:
            raw = _get(f"https://weworkremotely.com/categories/{category}.rss")
            root = ET.fromstring(raw)
        except Exception as e:
            errors.append(e)
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            # WWR titles look like "Company: Role"
            company, _, role = title.partition(":")
            role = role.strip() or title
            link = (item.findtext("link") or "").strip()
            if not _keep_title(role) or link in seen:
                continue
            seen.add(link)
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
                "description": _strip_html(item.findtext("description"))[:DESC_MAX],
            })
    _fail_if_all_failed("We Work Remotely", errors, len(WWR_CATEGORIES))
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
            "description": _strip_html(j.get("description"))[:DESC_MAX],
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
            "description": text[:DESC_MAX],
        })
        if len(out) >= 100:
            break
    return out


# --- Key-required sources (skipped silently when env vars are absent) --------

def src_jooble():
    """Jooble's aggregator API (many US boards). Every query runs for every location."""
    key = os.environ.get("JOOBLE_API_KEY")
    if not key:
        raise SkipSource("set JOOBLE_API_KEY to enable - free key from jooble.org/api/about")
    cfg = json.loads(QUERIES_PATH.read_text(encoding="utf-8")).get("jooble") or {}
    since = (date.today() - timedelta(days=int(cfg.get("days", 3)))).isoformat()
    out, seen, errors = [], set(), []
    combos = [(q, loc) for q in cfg.get("queries") or [] for loc in cfg.get("locations") or [""]]
    for q, loc in combos:
        for page in range(1, int(cfg.get("pages", 1)) + 1):
            body = json.dumps({"keywords": q, "location": loc, "page": page, "datecreatedfrom": since}).encode()
            try:
                data = _get_json(f"https://jooble.org/api/{key}", headers={"Content-Type": "application/json"}, data=body)
            except Exception as e:
                errors.append(e)
                break
            for j in data.get("jobs") or []:
                jid = str(j.get("id") or "")
                if not jid or jid in seen or not _keep_title(j.get("title")):
                    continue
                seen.add(jid)
                place = j.get("location") or ""
                remote = loc == "remote" or "remote" in place.lower()
                out.append({
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "location": f"Remote ({place})" if remote and place else ("Remote (United States)" if remote else place),
                    "date": (j.get("updated") or "")[:10],
                    "url": j.get("link"),
                    "id": jid,
                    "portal": "jooble",
                    "work_mode": "remote" if remote else None,
                    "skills": [],
                    "description": _strip_html(j.get("snippet"))[:2000],
                })
    _fail_if_all_failed("Jooble", errors, len(combos))
    return out


def _load_jsearch_budget(path, month):
    """Return the month's budget, a fresh month, or None when the file is corrupt.

    A missing file is a first run. A new month starts at zero. A corrupt file
    is not treated as zero — that would spend past the free-plan cap.
    """
    if not path.exists():
        return {"month": month, "requests": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("requests"), int) or raw["requests"] < 0:
        return None
    if not isinstance(raw.get("month"), str):
        return None
    if raw.get("month") != month:
        return {"month": month, "requests": 0}
    return raw


def _save_jsearch_budget(path, budget):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(budget), encoding="utf-8")
    os.replace(tmp, path)


def _jsearch_query_text(query):
    """Don't append a country when the query already names a place or remote."""
    if re.search(r"\b(remote|minneapolis|gulfport|minnesota|mississippi|united states)\b", query, re.I):
        return query
    return query + " in United States"


def src_jsearch():
    """Google-for-Jobs results (Indeed, ZipRecruiter, Glassdoor and others) through the JSearch API.

    Those sites do not allow scraping, so this goes through a licensed aggregator. It
    needs a JSearch key; the plan's monthly request count is the cap, so see
    `jsearch` in queries.json.

    Each successful HTTP call is charged `pages` requests (JSearch bills per page).
    Failed calls are not charged, and a run that lands nothing does not use up
    the day's run. A corrupt budget file skips the source instead of resetting.
    """
    key = os.environ.get("JSEARCH_API_KEY")
    rapid_key = os.environ.get("RAPIDAPI_KEY")
    if not (key or rapid_key):
        raise SkipSource("set JSEARCH_API_KEY to enable - JSearch on openwebninja.com (covers Indeed, ZipRecruiter, Glassdoor)")
    cfg = json.loads(QUERIES_PATH.read_text(encoding="utf-8")).get("jsearch") or {}
    queries = cfg.get("queries") or ["solutions engineer", "sales engineer", "junior software engineer"]
    # Every run sends every query; `monthly_limit` stops it below the plan's request count.
    budget_path = SCRAPER_DIR / "jsearch_runs.json"
    month = TODAY[:7]
    budget = _load_jsearch_budget(budget_path, month)
    if budget is None:
        raise SkipSource(f"jsearch budget file is unreadable ({budget_path.name}); not spending requests")
    runs_per_day = cfg.get("runs_per_day")  # unset: the monthly limit is the only cap
    if runs_per_day and budget.get("date") == TODAY and budget.get("count", 0) >= int(runs_per_day):
        raise SkipSource(f"already ran {budget['count']} time(s) today (jsearch.runs_per_day in queries.json)")
    pages = max(1, int(cfg.get("pages", 1)))
    limit = int(cfg.get("monthly_limit", 190))
    planned = len(queries) * pages
    if budget["requests"] + planned > limit:
        raise SkipSource(f"monthly request budget used ({budget['requests']} of {limit})")
    if key:  # OpenWebNinja, where JSearch is now hosted
        base, headers = "https://api.openwebninja.com/jsearch/search", {"x-api-key": key}
    else:
        base = "https://jsearch.p.rapidapi.com/search"
        headers = {"X-RapidAPI-Key": rapid_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    out = []
    succeeded = 0
    for q in queries:
        remote_only = bool(re.search(r"\bremote\b", q, re.I))
        url = (
            f"{base}"
            f"?query={urllib.parse.quote(_jsearch_query_text(q))}&page=1&num_pages={pages}"
            f"&date_posted={cfg.get('date_posted', 'week')}&country=us"
        )
        if remote_only:
            url += "&remote_jobs_only=true"
        try:
            data = _get_json(url, headers=headers, timeout=90).get("data") or []  # a live search takes 20-30 s
        except Exception as exc:
            print(f"jsearch query {q!r} failed: {exc}", flush=True)
            continue
        succeeded += pages
        for j in data:
            city = ", ".join(p for p in (j.get("job_city"), j.get("job_state")) if p)
            out.append({
                "title": j.get("job_title"),
                "company": j.get("employer_name"),
                "location": "Remote (United States)" if j.get("job_is_remote") else (city or "United States"),
                "date": (j.get("job_posted_at_datetime_utc") or "")[:10],
                "url": j.get("job_apply_link") or j.get("job_google_link"),
                "id": str(j.get("job_id") or ""),
                "portal": "jsearch",
                "work_mode": "remote" if j.get("job_is_remote") else None,
                "skills": [],
                "description": (j.get("job_description") or "")[:DESC_MAX],
            })
    if succeeded:
        runs_today = budget.get("count", 0) if budget.get("date") == TODAY else 0
        budget.update(month=month, date=TODAY, count=runs_today + 1, requests=budget["requests"] + succeeded)
        _save_jsearch_budget(budget_path, budget)
    elif queries:
        print("jsearch: no request succeeded; today's run was not counted", flush=True)
    return out


def _recent(iso_date, days):
    """True when an ISO date is within `days` of today. A missing date counts as recent."""
    if not iso_date:
        return True
    return iso_date[:10] >= (date.today() - timedelta(days=days)).isoformat()


WORKABLE_QUERIES = (
    "solutions engineer", "sales engineer", "implementation", "technical account manager",
    "customer engineer", "junior software engineer", "associate software engineer", "full stack developer",
)


def src_workable():
    """Workable's public job search (jobs.workable.com): every company on Workable, keyless."""
    out, seen, errors = [], set(), []
    for q in WORKABLE_QUERIES:
        token = None
        for _page in range(4):
            url = f"https://jobs.workable.com/api/v1/jobs?query={urllib.parse.quote(q)}&location=United%20States"
            if token:
                url += "&pageToken=" + urllib.parse.quote(token)
            try:
                data = _get_json(url)
            except Exception as e:
                if _page == 0:
                    errors.append(e)
                break
            for j in data.get("jobs") or []:
                loc = j.get("location") or {}
                link = j.get("url")
                if not link or link in seen or loc.get("countryName") not in (None, "United States"):
                    continue
                created = (j.get("created") or "")[:10]
                if not _recent(created, 21) or not _keep_title(j.get("title")):
                    continue
                seen.add(link)
                place = ", ".join(p for p in (loc.get("city"), loc.get("subregion")) if p)
                remote = j.get("workplace") == "remote"
                out.append({
                    "title": j.get("title"),
                    "company": (j.get("company") or {}).get("title"),
                    "location": "Remote (United States)" if remote else (place or "United States"),
                    "date": created,
                    "url": link,
                    "id": str(j.get("id") or ""),
                    "portal": "workable",
                    "work_mode": "remote" if remote else None,
                    "skills": [],
                    "description": _strip_html(
                        (j.get("description") or "") + " " + (j.get("requirementsSection") or "")
                    )[:8000],
                })
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(0.3)
    _fail_if_all_failed("Workable", errors, len(WORKABLE_QUERIES))
    return out


SIMPLIFY_LISTINGS = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
)


def src_simplify():
    """SimplifyJobs' community list of new-grad and entry-level tech roles (a public GitHub file)."""
    out = []
    for j in json.loads(_get(SIMPLIFY_LISTINGS, timeout=120)):
        if not (j.get("active") and j.get("is_visible", True)):
            continue
        try:
            posted = datetime.fromtimestamp(int(j.get("date_posted") or 0)).date().isoformat()
        except (TypeError, ValueError, OSError):
            continue
        if not _recent(posted, 21) or not _keep_title(j.get("title")):
            continue
        places = [p for p in (j.get("locations") or []) if p]
        loc = "; ".join(places)
        out.append({
            "title": j.get("title"),
            "company": j.get("company_name"),
            "location": loc or "United States",
            "date": posted,
            "url": j.get("url"),
            "id": str(j.get("id") or ""),
            "portal": "simplify",
            "work_mode": "remote" if "remote" in loc.lower() else None,
            "skills": [],
            "description": "",  # the posting itself is opened later by the text reader
        })
    return out


MUSE_CATEGORIES = ("Sales", "Software Engineering", "Customer Service", "Data and Analytics")
MUSE_LOCATIONS = ("Flexible / Remote", "Minneapolis, MN")


def src_muse():
    """The Muse public API: curated listings, entry and mid level, remote and Minneapolis."""
    out, seen, errors = [], set(), []
    headers = {"User-Agent": "Mozilla/5.0 (personal-job-search)"}
    for cat in MUSE_CATEGORIES:
        for loc in MUSE_LOCATIONS:
            for page in (1, 2):
                url = (
                    "https://www.themuse.com/api/public/jobs?page=%d&category=%s&location=%s"
                    "&level=Entry%%20Level&level=Mid%%20Level"
                    % (page, urllib.parse.quote(cat), urllib.parse.quote(loc))
                )
                try:
                    data = _get_json(url, headers=headers)
                except Exception as e:
                    if page == 1:
                        errors.append(e)
                    break
                for j in data.get("results") or []:
                    link = (j.get("refs") or {}).get("landing_page")
                    if not link or link in seen or not _keep_title(j.get("name")):
                        continue
                    seen.add(link)
                    places = [l.get("name") for l in j.get("locations") or [] if l.get("name")]
                    remote = any("remote" in p.lower() or "flexible" in p.lower() for p in places)
                    out.append({
                        "title": j.get("name"),
                        "company": (j.get("company") or {}).get("name"),
                        "location": "Remote (United States)" if remote else "; ".join(places),
                        "date": (j.get("publication_date") or "")[:10],
                        "url": link,
                        "id": str(j.get("id") or ""),
                        "portal": "muse",
                        "work_mode": "remote" if remote else None,
                        "skills": [],
                        "description": _strip_html(j.get("contents"))[:8000],
                    })
                if page >= (data.get("page_count") or 1):
                    break
    _fail_if_all_failed("The Muse", errors, len(MUSE_CATEGORIES) * len(MUSE_LOCATIONS))
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
                "description": _strip_html(j.get("UserArea", {}).get("Details", {}).get("JobSummary"))[:DESC_MAX],
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
            "description": _strip_html(j.get("content"))[:DESC_MAX],
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
            "description": _strip_html(j.get("descriptionPlain") or j.get("description"))[:DESC_MAX],
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
            "description": _strip_html(j.get("descriptionPlain") or "")[:DESC_MAX],
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
    "himalayas": src_himalayas,
    "workable": src_workable,
    "simplify": src_simplify,
    "muse": src_muse,
    "weworkremotely": src_weworkremotely,
    "workingnomads": src_workingnomads,
    "hn_hiring": src_hn_hiring,
    "jooble": src_jooble,
    "jsearch": src_jsearch,
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

LAST_RUN_PATH = SCRAPER_DIR / "last_run.json"
# LinkedIn's f_TPR control only accepts these posted-within windows.
LINKEDIN_JOBAGE_DAYS = (1, 7, 14, 30)


def portal_jobage(portal, days):
    """Days value to pass as `--jobage`.

    LinkedIn rejects any other window, so snap up to the next valid bucket
    (a 5-day gap still covers those five days). Other portals keep the raw count.
    """
    days = int(days)
    if portal != "linkedin-search":
        return str(days)
    for bucket in LINKEDIN_JOBAGE_DAYS:
        if days <= bucket:
            return str(bucket)
    return str(LINKEDIN_JOBAGE_DAYS[-1])
DEFAULT_SEARCH_SETTINGS = {
    "min_days": 1,
    "max_days": 14,
    "max_pages": {"linkedin-search": 4, "freehire-search": 4},
    "page_size": {"linkedin-search": 10, "freehire-search": 50},
    "page_delay_seconds": 1.0,
}


def search_settings(cfg):
    out = json.loads(json.dumps(DEFAULT_SEARCH_SETTINGS))
    for key, value in (cfg.get("search_settings") or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def search_window_days(settings, now=None):
    """Days of postings to ask the portals for.

    Based on how long ago the last complete search finished, rounded up to whole
    days (the portals filter by day), with a day of overlap once a run has been
    missed. Runs a few hours apart ask for one day. A computer that was off for
    three days asks for five. Every page of those results is read. With no
    record of a last run it uses the maximum.
    """
    now = now or datetime.now()
    try:
        last = datetime.fromisoformat(json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))["search_finished"])
    except Exception:
        return int(settings["max_days"])
    elapsed_days = max((now - last).total_seconds(), 0) / 86400
    days = math.ceil(elapsed_days) + (1 if elapsed_days >= 1 else 0)
    return max(int(settings["min_days"]), min(int(settings["max_days"]), days))


def record_search_finished(now=None):
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    LAST_RUN_PATH.write_text(json.dumps({"search_finished": stamp}), encoding="utf-8")


def _set_flag(args_list, flag, value):
    """args_list with `flag value` set, replacing any existing one."""
    out, skip = [], False
    for i, arg in enumerate(args_list):
        if skip:
            skip = False
            continue
        if arg == flag:
            skip = True
            continue
        out.append(arg)
    return out + [flag, str(value)]


def _cli_page(portal, args_list):
    cli = ROOT / ".agents" / "skills" / portal / "cli" / "src" / "cli.ts"
    cmd = ["bun", "run", str(cli), *args_list]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout or "", p.stderr or ""


def _result_key(r):
    return r.get("url") or r.get("id") or json.dumps(r, sort_keys=True)[:200]


def _run_cli_query(name, portal, args_list, out_dir, settings=None, jobage_days=None):
    """Run one portal query, reading every page up to the portal's page cap.

    A portal returns one page per call (10 LinkedIn cards, up to 50 FreeHire
    rows). Reading only page 1 hid everything past the first page. Pages are
    read until one comes back short, the portal's total is reached, or the cap.
    """
    import time

    settings = settings or DEFAULT_SEARCH_SETTINGS
    page_size = int(settings["page_size"].get(portal, 20))
    max_pages = int(settings["max_pages"].get(portal, 1))
    delay = float(settings.get("page_delay_seconds", 0))
    base = _set_flag(args_list, "--limit", page_size)
    if jobage_days:
        base = _set_flag(base, "--jobage", portal_jobage(portal, jobage_days))
    print(f"START {name}", flush=True)

    results, seen_keys, meta, stderr_all, rc = [], set(), {}, [], 0
    pages_read = 0
    later_failed = False
    for page in range(1, max_pages + 1):
        rc, out, err = _cli_page(portal, _set_flag(base, "--page", page))
        if err:
            stderr_all.append(err)
        try:
            data = json.loads(out)
        except Exception as e:
            if page == 1:
                (out_dir / f"{name}.json").write_text(out, encoding="utf-8")
                if stderr_all:
                    (out_dir / f"{name}.err").write_text("\n".join(stderr_all), encoding="utf-8")
                print(f"FAIL {name} exit={rc} parse={e} stderr={err[:300]}", flush=True)
                return rc or 1
            later_failed = True
            break  # later page failed: keep what was read
        rows = data.get("results") or []
        meta = data.get("meta") or meta
        pages_read += 1
        fresh = [r for r in rows if _result_key(r) not in seen_keys]
        for r in fresh:
            seen_keys.add(_result_key(r))
        results.extend(fresh)
        total = meta.get("total")
        if len(rows) < page_size or not fresh:
            break
        if total is not None and len(results) >= int(total):
            break
        if page < max_pages and delay:
            time.sleep(delay)

    meta = {**meta, "pages_read": pages_read, "count": len(results)}
    (out_dir / f"{name}.json").write_text(
        json.dumps({"meta": meta, "results": results}, ensure_ascii=False), encoding="utf-8")
    if stderr_all:
        (out_dir / f"{name}.err").write_text("\n".join(stderr_all), encoding="utf-8")
    capped = pages_read == max_pages and len(results) >= page_size * max_pages
    note = " CAPPED" if capped else ""
    print(f"OK {name} n={len(results)} pages={pages_read} exit={rc}{note}", flush=True)
    # A later page failing must not throw away the pages already saved, and must
    # not block last_run.json. The first page failing still counts as a failure.
    if later_failed:
        return 0 if results else (rc or 1)
    return rc


def cmd_search(args):
    out_dir = run_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))

    queries = [q for q in cfg["cli_queries"] if q["name"] in selected([q["name"] for q in cfg["cli_queries"]], args)]
    fails = 0
    settings = search_settings(cfg)
    jobage_days = getattr(args, "window_days", None) or search_window_days(settings)
    print(f"search window: last {jobage_days} day(s)", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(_run_cli_query, q["name"], q["portal"], q["args"], out_dir, settings, jobage_days): q["name"]
            for q in queries
        }
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

    if fails == 0 and not args.only and not getattr(args, "window_days", None):
        record_search_finished()
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


# A posting id is only the same job when it is the same id on the same host.
# A bare 8-digit number shows up in unrelated URLs and must not match globally.
_POSTING_ID_RE = re.compile(r"(?:gh_jid=|/jobs/(?:view/)?)(\d{6,})", re.I)


def _posting_id(url):
    if not url:
        return None
    match = _POSTING_ID_RE.search(url)
    if not match:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    return (host, match.group(1))


def _company_title_place(job):
    """Company + title + city, using the same word normalization as dedupe_key.

    An empty place is not a match. Two openings with no city, or the same title
    in two cities, are different jobs.
    """
    company, title = _norm_words(job.get("company")), _norm_words(job.get("title"))
    if not company or not title:
        return None
    loc = (job.get("location") or "").lower()
    if "remote" in loc or (job.get("work_mode") or "").lower() == "remote":
        place = "remote"
    else:
        place = _norm_words((job.get("location") or "").split(",")[0])
    if not place:
        return None
    return (company, title, place)


def _seen_indexes(seen):
    seen_urls = set(seen.keys()) | {v.get("url", "") for v in seen.values()}
    seen_url_keys = {_url_key(u) for u in seen_urls if u}
    seen_ids = set()
    seen_ct = set()
    for k, v in seen.items():
        u = v.get("url") or k
        pid = _posting_id(u)
        if pid:
            seen_ids.add(pid)
        ct = _company_title_place(v)
        if ct:
            seen_ct.add(ct)
    return seen_urls, seen_url_keys, seen_ids, seen_ct


def _already(j, seen_urls, seen_url_keys, seen_ids, seen_ct):
    url = j.get("url") or ""
    if url and (url in seen_urls or _url_key(url) in seen_url_keys):
        return True
    pid = _posting_id(url)
    if pid and pid in seen_ids:
        return True
    ct = _company_title_place(j)
    return bool(ct) and ct in seen_ct


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
        key = _url_key(j.get("url") or "") or j.get("id")
        if key and key not in uniq:
            uniq[key] = j
    return list(uniq.values()), health


def cmd_process(args):
    rd = run_dir(args)
    seen = load_seen()["seen"]
    seen_urls, seen_url_keys, seen_ids, seen_ct = _seen_indexes(seen)
    jobs, health = _load_run_jobs(rd)

    import posting_text
    import scoring

    survivors = []
    skipped_seen = skipped_loc = skipped_lang = 0
    for j in jobs:
        if NON_ENGLISH_TITLE_RE.search(j.get("title") or ""):
            skipped_lang += 1
            continue
        if _already(j, seen_urls, seen_url_keys, seen_ids, seen_ct):
            skipped_seen += 1
            continue
        j["tier"] = loc_tier(j.get("location"), j.get("work_mode"))
        survivors.append(j)
    survivors, duplicates = collapse_duplicates(survivors)
    print(f"duplicates across sites: {duplicates}")

    # Read the posting for every job that is in a wanted location and has a
    # role family he could do. The grade needs the requirements, not the title.
    def worth_reading(j):
        if j["tier"] not in ("mn", "remote", "ms"):
            return False
        return scoring.worth_reading(j)

    opened = posting_text.enrich(survivors, only=worth_reading)
    print(f"posting text: {opened}")

    new, all_rows = [], []
    for j in survivors:
        scoring.grade_job(j)
        cap_high_for_location(j)
        all_rows.append({"url": j.get("url"), "fit": j["fit"], "tier": j["tier"]})
        if j["tier"] not in ("mn", "remote", "ms"):
            skipped_loc += 1
            continue
        new.append(j)

    order = {"high": 0, "medium": 1, "low": 2}
    new.sort(key=lambda j: (order.get(j["fit"], 9), -(j.get("fit_score") or 0), str(j.get("date") or ""), j.get("title") or ""))

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
        "description": (j.get("description") or "")[:DESC_MAX],
        "body_status": j.get("body_status"),
        "fit_reasons": j.get("fit_reasons") or [],
        "fit_blockers": j.get("fit_blockers") or [],
        "entry_evidence": bool(j.get("entry_evidence")),
        "disqualified": bool(j.get("disqualified")),
    } for j in new]

    with open(rd / "candidates.json", "w", encoding="utf-8") as f:
        json.dump({"new": out, "all": all_rows, "all_count": len(jobs),
                   "seen": skipped_seen, "loc_skip": skipped_loc,
                   "health": health}, f, indent=2, ensure_ascii=False)

    import daily_digest

    presentable, left_out = daily_digest.presentable_split(new)
    print(f"\n=== PRESENTABLE ({len(presentable)}) ===")
    for i, j in enumerate(presentable, 1):
        print(f"{i:2}. [{j['fit']}/{j['tier']}] {j.get('title')}")
        salary_suffix = f" | {j['salary']}" if j.get("salary") else ""
        print(f"    {j.get('company')} | {j.get('location')} | {j.get('date')}{salary_suffix}")
        print(f"    {j.get('url')}")
    if left_out:
        print(f"\n=== LEFT OUT ({len(left_out)}) ===")
        for j, reason in left_out:
            print(f"  {reason} | {j.get('title')} | {j.get('company')}")
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
    # A posting whose text failed to load is not settled: leave it unseen so the
    # next run reads it again instead of locking in a low grade.
    retry_urls = {
        j["url"] for j in cand["new"]
        if j.get("body_status") in ("shell", "unread")
        or any(
            "could not be read" in b or "did not render" in b
            for b in (j.get("fit_blockers") or [])
        )
    }
    fit_by_url = {r["url"]: r["fit"] for r in cand.get("all", [])}

    doc = load_seen()
    seen = doc["seen"]
    seen_keys = {_url_key(k) for k in seen}
    jobs, _ = _load_run_jobs(rd)

    added = 0
    for j in jobs:
        url = j.get("url") or ""
        key = _url_key(url)
        if not url or url in seen or key in seen_keys or url in retry_urls:
            continue
        f = fit_by_url.get(url) or fit(j.get("title"), j.get("skills"), j.get("description"))
        status = "new" if url in new_urls and f in ("high", "medium") else "skipped"
        seen[url] = {
            "title": j.get("title"),
            "company": j.get("company"),
            "location": j.get("location"),
            "work_mode": j.get("work_mode"),
            "url": url,
            "first_seen": TODAY,
            "fit": f,
            "status": status,
            "portal": j.get("portal"),
        }
        seen_keys.add(key)
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
    """List the 14-day Word window, not every status=new row in seen_jobs.json."""
    import daily_digest

    store = daily_digest.load_store()
    jobs = list(store.values())
    daily_digest.apply_location_caps(jobs)
    rows = daily_digest.listable(jobs)
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda v: (
        order.get((v.get("fit") or "").lower(), 9),
        v.get("found_on") or "",
        v.get("title") or "",
    ))
    print(f"ACTIONABLE ({len(rows)} in the {daily_digest.WINDOW_DAYS}-day list):")
    for i, v in enumerate(rows, 1):
        print(f"{i:2}. [{v.get('fit')}] {v.get('title')} - {v.get('company')}")
        print(f"    found_on={v.get('found_on')} {v.get('location') or ''}")
        print(f"    {v.get('url')}")
    return 0


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "command",
        nargs="?",
        choices=["search", "process", "update-seen", "expire", "present", "all"],
    )
    ap.add_argument("--run-dir")
    ap.add_argument("--only")
    ap.add_argument("--skip")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--window-days", type=int, help="search the last N days instead of the time since the last run")
    ap.add_argument("--days", type=int, default=21, help="expire threshold in days")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.command:
        print("Write today's list with:")
        print("    python job_scraper/daily_digest.py")
        print("The Word file is daily/latest.docx.")
        print("The page is reports/application-dashboard.html.")
        return 2

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
