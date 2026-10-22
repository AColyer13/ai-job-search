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
            if isinstance(node, dict) and str(node.get("@type", "")).lower() == "jobposting":
                desc = node.get("description") or ""
                if desc:
                    return html_to_text(desc)
    return ""


def _ats_api_url(url):
    """Public JSON endpoint for Greenhouse, Lever, and Ashby postings, or ''."""
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if m:
        return f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}"
    m = re.search(r"jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})", url)
    if m:
        return f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}"
    return ""


def _from_ats_json(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(data, dict):
        body = data.get("content") or data.get("descriptionPlain") or data.get("description") or ""
        if data.get("lists"):
            body += " ".join(
                f"{item.get('text', '')} {item.get('content', '')}" for item in data["lists"]
            )
        return html_to_text(unescape(body))
    return ""


def fetch_text(url):
    """Return (status, text) for one posting URL."""
    if not url:
        return "unread", ""
    try:
        api = _ats_api_url(url)
        if api:
            text = _from_ats_json(_get(api, "application/json"))
            if len(text) >= 200:
                return "opened", text[:MAX_CHARS]
        html = _get(url)
    except Exception:
        return "unread", ""
    text = _jsonld_description(html) or html_to_text(html)
    if _SHELL_RE.search(text[:3000]) and len(text) < 1500:
        return "shell", text[:MAX_CHARS]
    if len(text) < 200:
        return "shell", text
    return "opened", text[:MAX_CHARS]


def enrich(jobs, workers=8, only=None):
    """Fetch posting text for each job and set description + body_status in place.

    `only` is an optional predicate; jobs it rejects are skipped. A job that
    already has long text (an earlier fetch) is not fetched again.
    """
    todo = [
        j for j in jobs
        if (only is None or only(j))
        and j.get("body_status") != "opened"
        and len(j.get("description") or "") < 1500
    ]

    def work(job):
        status, text = fetch_text(job.get("url"))
        if status == "opened" or not job.get("description"):
            job["description"] = text or job.get("description") or ""
        job["body_status"] = status
        return status

    with ThreadPoolExecutor(max_workers=workers) as ex:
        statuses = list(ex.map(work, todo))
    return {s: statuses.count(s) for s in set(statuses)}
