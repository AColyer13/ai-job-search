"""Fetch the full text of a job posting so the scorer can read its requirements.

The search APIs return a title and a short snippet. A grade built on that is a
guess. This module opens the posting URL and pulls the job text out of it:

    1. schema.org JobPosting JSON-LD (most ATS pages and LinkedIn guest pages)
    2. Greenhouse / Lever / Ashby public JSON endpoints
    3. visible page text, scripts and styles removed

`body_status` is the outcome:
    opened   readable job text was found
    shell    an ATS error template or JavaScript-only page came back
    unread   the request failed

Stdlib only. One request per job, small timeout, failures never raise.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

USER_AGENT = "Mozilla/5.0 (personal-job-search; contact: adam)"
TIMEOUT = 20
MAX_CHARS = 8000

_SHELL_RE = re.compile(
    r"\{\{position\.name\}\}|an error occured|showalllocations|unsupported browser|"
    r"enable javascript|just a moment|performing security verification",
    re.I,
)
_CLOSED_RE = re.compile(
    r"no longer (?:accepting applications|available|open)|(?:position|job|role|posting) (?:has been|is|was) "
    r"(?:filled|closed|removed|expired)|this (?:job|position|posting) (?:has )?expired|"
    r"job (?:is )?no longer exists|page you.re looking for (?:doesn.t|does not) exist",
    re.I,
)
_JSONLD_RE = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.I | re.S
)


def _get(url, accept="text/html,application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def html_to_text(html):
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?i)</?(?:p|div|br|li|ul|ol|h[1-6]|tr|section)\b[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace(" ", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _jsonld_description(html):
    for raw in _JSONLD_RE.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and "@graph" in node:
                nodes = list(nodes) + list(node["@graph"])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            raw_type = node.get("@type") or ""
            if isinstance(raw_type, list):
                type_names = [str(item).lower() for item in raw_type]
            else:
                type_names = [str(raw_type).lower()]
            if "jobposting" in type_names:
                desc = node.get("description") or ""
                if desc:
                    return html_to_text(desc)
    return ""


def _ats_api_url(url):
    """Public JSON endpoint for Greenhouse, Lever, and Workday postings, or ''."""
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if m:
        return f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}"
    m = re.search(r"jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})", url)
    if m:
        return f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}"
    m = re.match(
        r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/(job/[^?#]+)", url
    )
    if m:
        tenant, wd, site, path = m.groups()
        return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/{path}"
    return ""


def _from_ats_json(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(data, dict) and isinstance(data.get("jobPostingInfo"), dict):  # Workday
        return html_to_text(unescape(data["jobPostingInfo"].get("jobDescription") or ""))
    if isinstance(data, dict):
        body = data.get("content") or data.get("descriptionPlain") or data.get("description") or ""
        if data.get("lists"):
            body += " ".join(
                f"{item.get('text', '')} {item.get('content', '')}" for item in data["lists"]
            )
        return html_to_text(unescape(body))
    return ""


def _himalayas_slug(url):
    m = re.match(r"https?://(?:www\.)?himalayas\.app/companies/[\w-]+/jobs/([\w-]+)", url or "")
    return m.group(1) if m else ""


def fetch_himalayas(job):
    """(status, text) for a himalayas.app posting, read from its search API by URL slug.

    The posting page itself refuses a plain request; the search API (the one src_himalayas
    uses) carries the full text. Searches the title, then title and company, three pages each.
    """
    slug = _himalayas_slug(job.get("url"))
    if not slug:
        return "unread", ""
    for q in (job.get("title") or "", f"{job.get('title') or ''} {job.get('company') or ''}".strip()):
        if not q:
            continue
        for page in (1, 2, 3):
            try:
                data = json.loads(_get(
                    "https://himalayas.app/jobs/api/search?sort=recent&country=US"
                    f"&page={page}&q={urllib.parse.quote(q)}", "application/json"))
            except Exception:
                break
            hits = data.get("jobs") or []
            for hit in hits:
                if _himalayas_slug(hit.get("guid") or hit.get("applicationLink")) == slug:
                    text = html_to_text(hit.get("description") or "")
                    return ("opened", text[:MAX_CHARS]) if len(text) >= 200 else ("shell", text)
            if len(hits) < 20:
                break
            time.sleep(0.4)
    return "unread", ""


_GH_JID_RE = re.compile(r"[?&]gh_jid=(\d+)")


def _greenhouse_by_jid(url, company):
    """Text of a Greenhouse job embedded on the employer's own site (a ?gh_jid=123 link), or ''."""
    m = _GH_JID_RE.search(url or "")
    if not m or not company:
        return ""
    for slug in board_slugs({"company": company, "url": url})[:4]:
        try:
            text = _from_ats_json(_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{m.group(1)}", "application/json"))
        except Exception:
            continue
        if len(text) >= 200:
            return text
    return ""


def greenhouse_form_url(url, company, fetch=None):
    """The hosted Greenhouse form for a ?gh_jid=123 link on an employer's own site, or ''.

    The link names the job but not the board. A board name counts only when the public jobs API
    (boards-api.greenhouse.io) lists that job id under it, so a wrong guess never becomes a form link.
    """
    parts = urllib.parse.urlparse(url or "")
    m = _GH_JID_RE.search(url or "")
    host = parts.netloc.lower()
    if not m or "greenhouse.io" in host:
        return ""
    jid = m.group(1)
    labels = host.removeprefix("www.").split(".")
    guesses = [labels[0]] if labels and labels[0] else []
    guesses += board_slugs({"company": company, "url": url})
    fetch = fetch or _get
    for slug in dict.fromkeys(g for g in guesses if len(g) >= 3):
        try:
            data = json.loads(fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}", "application/json"))
        except Exception:  # noqa: BLE001 - a 404 or a bad reply just means this guess is wrong
            continue
        if isinstance(data, dict) and str(data.get("id")) == jid:
            # The hosted /<board>/jobs/<id> page sends an embedded board back to the employer's site; the embed
            # endpoint serves the same application form on Greenhouse's own host.
            return f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={jid}"
    return ""


def fetch_text(url, company=""):
    """Return (status, text) for one posting URL."""
    if not url:
        return "unread", ""
    html = None
    embedded = _greenhouse_by_jid(url, company)
    if embedded:
        return "opened", embedded[:MAX_CHARS]
    api = _ats_api_url(url)
    api_blocked = False
    for attempt in range(2):
        try:
            if api and not api_blocked:
                try:
                    text = _from_ats_json(_get(api, "application/json"))
                    if len(text) >= 200:
                        return "opened", text[:MAX_CHARS]
                except urllib.error.HTTPError as e:
                    # A dead JSON endpoint still has an HTML page worth reading.
                    if e.code in (400, 401, 403, 404, 410):
                        api_blocked = True
                    else:
                        raise
            html = _get(url)
            break
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):  # the posting was taken down
                return "gone", ""
            if e.code in (400, 401, 403):  # blocked or malformed: retrying will not help
                return "unread", ""
        except Exception:
            pass
        time.sleep(2)
    if html is None:
        return "unread", ""
    text = _jsonld_description(html) or html_to_text(html)
    if _CLOSED_RE.search(text[:3000]):
        return "gone", ""
    if _SHELL_RE.search(text[:3000]) and len(text) < 1500:
        return "shell", text[:MAX_CHARS]
    if len(text) < 200:
        return "shell", text
    return "opened", text[:MAX_CHARS]


# Board feeds and aggregators cut their text at 2,000 characters, which loses the requirements
# section of a long posting. Text that long and never opened is a snippet, not the posting.
TRUNCATED_AT = 1900
TRUNCATED_MAX = 2000  # the cut length; longer text came from a source that keeps more


def enrich(jobs, workers=8, only=None):
    """Fetch posting text for each job and set description + body_status in place.

    `only` is an optional predicate; jobs it rejects are skipped. A job that
    already has long readable text is not fetched again. A shell or unread
    page is fetched again even when the saved error text is long.
    """
    def _needs_fetch(job):
        status = job.get("body_status")
        if status in ("opened", "gone"):
            return False
        if status in ("shell", "unread"):
            return True
        size = len(job.get("description") or "")
        return size < 1500 or TRUNCATED_AT <= size <= TRUNCATED_MAX

    todo = [j for j in jobs if (only is None or only(j)) and _needs_fetch(j)]

    def work(job):
        if _himalayas_slug(job.get("url")):
            status, text = fetch_himalayas(job)
        else:
            status, text = fetch_text(job.get("url"), job.get("company"))
        if status == "opened" or not job.get("description"):
            job["description"] = text or job.get("description") or ""
        # A failed re-fetch of a long snippet must not turn "read the snippet" into "could not read it".
        if status in ("opened", "gone") or len(job.get("description") or "") < 1500:
            job["body_status"] = status
        return status

    with ThreadPoolExecutor(max_workers=workers) as ex:
        statuses = list(ex.map(work, todo))
    return {s: statuses.count(s) for s in set(statuses)}


# --- the employer's own apply page ---------------------------------------------------------
# A job that reached us through an aggregator (Himalayas, startup.jobs, WhatJobs) has a link to the
# repost, often a page that returns 403 to scripts. Many employers publish the same job on a public
# ATS board API (Greenhouse, Lever, Ashby, Workable). When the company's board lists a job with the
# same title, that listing is the real apply page and carries the full text.
BOARD_TIMEOUT = 10
_BOARD_CACHE = {}
_NAME_NOISE_RE = re.compile(
    r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|group|holdings|careers?|jobs|hiring|us|usa)\b"
)


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def board_slugs(job):
    """Likely ATS board names for the job's company, best first."""
    slugs = []
    m = re.search(r"himalayas\.app/companies/([\w-]+)/", job.get("url") or "")
    if m:
        slugs.append(m.group(1))
    base = " ".join(_NAME_NOISE_RE.sub(" ", _words(job.get("company"))).split())
    if base:
        slugs.extend([base.replace(" ", ""), base.replace(" ", "-"), base.split()[0]])
    if m:  # "net-at-work-llc" -> "net-at-work"
        slugs.append(re.sub(r"-(?:inc|llc|ltd|corp|co)$", "", m.group(1)))
    seen, out = set(), []
    for slug in slugs:
        if len(slug) >= 3 and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out[:4]


def _get_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=BOARD_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _board_jobs(ats, slug):
    """[{title, url, text, location}] for one company board, cached for the run. [] when there is none."""
    key = (ats, slug)
    if key in _BOARD_CACHE:
        return _BOARD_CACHE[key]
    rows = []
    if ats == "greenhouse":
        data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        for j in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
            rows.append({"title": j.get("title"), "url": j.get("absolute_url"),
                         "text": html_to_text(unescape(j.get("content") or "")),
                         "location": (j.get("location") or {}).get("name", "")})
    elif ats == "lever":
        data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in data if isinstance(data, list) else []:
            lists = " ".join(f"{i.get('text', '')} {html_to_text(i.get('content') or '')}" for i in j.get("lists") or [])
            rows.append({"title": j.get("text"), "url": j.get("hostedUrl"),
                         "text": (j.get("descriptionPlain") or "") + "\n" + lists,
                         "location": (j.get("categories") or {}).get("location", "")})
    elif ats == "ashby":
        data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        for j in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
            rows.append({"title": j.get("title"), "url": j.get("jobUrl") or j.get("applyUrl"),
                         "text": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
                         "location": ("Remote " if j.get("isRemote") else "") + str(j.get("location") or "")})
    elif ats == "workable":
        data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        for j in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
            rows.append({"title": j.get("title"), "url": j.get("url") or j.get("shortlink"),
                         "text": html_to_text(unescape(j.get("description") or "")),
                         "location": " ".join(str(j.get(k) or "") for k in ("city", "state", "country"))})
    rows = [r for r in rows if r["title"] and r["url"]]
    _BOARD_CACHE[key] = rows
    return rows


_REMOTE_LOC_RE = re.compile(r"remote|united states|usa|\bus\b|anywhere", re.I)


def _pick_listing(rows, job):
    """The board listing with this job's exact title, preferring one in the job's own place."""
    title = _words(job.get("title"))
    same = [r for r in rows if _words(r["title"]) == title]
    if not same:
        return None
    loc = (job.get("location") or "").lower()
    city = loc.split(",")[0].strip()
    for r in same:
        where = (r.get("location") or "").lower()
        if (city and city in where) or (_REMOTE_LOC_RE.search(loc) and _REMOTE_LOC_RE.search(where)):
            return r
    return same[0] if len(same) == 1 else None


def resolve_apply(job):
    """(apply_url, text, ats) from the company's own public job board, or ("", "", "")."""
    for slug in board_slugs(job):
        for ats in ("greenhouse", "lever", "ashby", "workable"):
            listing = _pick_listing(_board_jobs(ats, slug), job)
            if listing:
                return listing["url"], (listing["text"] or "")[:MAX_CHARS], ats
    return "", "", ""


def resolve_apply_links(jobs, only=None, today=None, workers=8, retry_days=3):
    """Replace aggregator links with the employer's own apply page where its board lists the job.

    Sets apply_url, apply_source and apply_checked on each job tried. A found listing also supplies the
    posting text when the aggregator page could not be read. A job tried in the last `retry_days` days
    is skipped. Returns {"found": n, "tried": n}.
    """
    import pipeline
    from datetime import date, timedelta

    today = today or date.today()
    recent = (today - timedelta(days=retry_days)).isoformat()
    todo = [
        j for j in jobs
        if pipeline._is_aggregator(j)
        and not j.get("apply_url")
        and (j.get("apply_checked") or "") <= recent
        and (only is None or only(j))
    ]

    def work(job):
        url, text, ats = resolve_apply(job)
        job["apply_checked"] = today.isoformat()
        if not url:
            return False
        job["apply_url"], job["apply_source"] = url, ats
        if text and (job.get("body_status") != "opened" or len(job.get("description") or "") < 1500):
            job["description"] = text
            job["body_status"] = "opened"
        return True

    with ThreadPoolExecutor(max_workers=workers) as ex:
        found = sum(ex.map(work, todo))
    return {"found": found, "tried": len(todo)}
