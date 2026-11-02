"""Public interview reports for one company, then a labeled estimate if none turn up.

    python job_scraper/interview_intel.py "Acme" --role "Software Engineer"
    python job_scraper/interview_intel.py "Acme" --role "Software Engineer" --out documents/applications/acme_software_engineer/interview_intel.md

Searches DuckDuckGo, Hacker News, and Reddit once per company. Glassdoor, Indeed,
ZipRecruiter, and Blind pages are not opened: those sites block scripts, and this
repo does not scrape them. A search snippet from one of them is kept and labeled
snippet-only.

When nothing relevant comes back, the file says so and adds general interview
practice. That section always starts with "AI estimate. This is not a report of
what <company> does." Pass --ai to have a headless model write those bullets.
Without --ai the bullets are a fixed general practice, which /interview then
replaces in the conversation. Either way the section is not a company fact.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (personal-job-search; contact: adam)"
MAX_BYTES = 400_000
STALE_DAYS = 365 * 3
MAX_FINDINGS = 8
MAX_PAGES = 3

NOT_OPENED = (
    "glassdoor.com",
    "indeed.com",
    "ziprecruiter.com",
    "teamblind.com",
)
GENERIC_COMPANY = {
    "inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "and",
    "software", "technologies", "technology", "tech", "solutions", "group",
    "labs", "lab", "systems", "system", "international", "services", "holdings",
    "global", "digital", "consulting",
}
GENERIC_ROLE = {
    "senior", "junior", "sr", "jr", "i", "ii", "iii", "iv", "the", "and", "of",
    "a", "an", "level",
}
# Rounds and hiring-process write-ups. A press "interview with the CEO" does not match.
SPECIFIC = re.compile(
    r"interview (experience|questions|prep|loop|rounds?)|hiring process|"
    r"phone screen|recruiter (screen|call|round)|hiring manager|"
    r"technical (screen|interview|phone|round)|coding (screen|interview)|live coding|"
    r"take[- ]home|coding challenge|homework assignment|system design|"
    r"on-?site|interview loop|hiring loop|panel interview|behavioral|behavioural|values interview",
    re.I,
)
STRONG = re.compile(
    r"phone screen|recruiter (screen|call|round)|hiring manager|technical (screen|interview|phone|round)"
    r"|coding (screen|interview)|live coding|take[- ]home|coding challenge|system design"
    r"|on-?site|interview loop|hiring loop|panel interview|behavioral|behavioural|values interview",
    re.I,
)
POSTING = re.compile(r"\b(apply now|we'?re hiring|we are hiring|job opening|now hiring)\b", re.I)
SIGNALS = (
    ("recruiter screen", re.compile(r"recruiter (screen|call|round)|recruiter phone", re.I)),
    ("hiring-manager conversation", re.compile(r"hiring manager", re.I)),
    ("technical screen", re.compile(r"technical (screen|interview|phone|round)|coding (screen|interview)|live coding", re.I)),
    ("take-home exercise", re.compile(r"take[- ]home|coding challenge|homework assignment", re.I)),
    ("system design round", re.compile(r"system design", re.I)),
    ("onsite or interview loop", re.compile(r"on-?site|interview loop|hiring loop|panel interview", re.I)),
    ("behavioral or values round", re.compile(r"behavioral|behavioural|values interview", re.I)),
)
BANNED_ESTIMATE = (
    "glassdoor", "teamblind", "blind.com", "candidates report", "candidates say",
    "people say", "reddit", "according to",
)


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    source: str
    when: date | None = None


@dataclass
class Finding:
    title: str
    url: str
    source: str
    detail: str
    note: str
    when: date | None
    role_named: bool
    stale: bool
    scan: str


@dataclass
class Report:
    company: str
    role: str
    queries: list
    findings: list
    notes: list
    sourced: bool
    markdown: str = ""
    extra: dict = field(default_factory=dict)


def lead_sentence(company):
    return f"AI estimate. This is not a report of what {company} does."


def company_tokens(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    specific = [w for w in words if w not in GENERIC_COMPANY and len(w) > 1]
    if specific:
        return specific
    skip = {"inc", "llc", "ltd", "corp", "co", "the", "and"}
    return [w for w in words if w not in skip and len(w) > 1]


def role_tokens(role):
    words = re.findall(r"[a-z0-9]+", (role or "").lower())
    return [w for w in words if w not in GENERIC_ROLE and len(w) > 1]


def has_tokens(text, tokens):
    low = (text or "").lower()
    return all(re.search(rf"\b{re.escape(token)}\b", low) for token in tokens)


def names_role(text, role):
    tokens = role_tokens(role)
    if not tokens:
        return True
    long = [token for token in tokens if len(token) >= 4] or tokens
    low = (text or "").lower()
    return any(re.search(rf"\b{re.escape(token)}\b", low) for token in long)


def _get(url, timeout=20, headers=None, data=None):
    base = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json"}
    if headers:
        base.update(headers)
    req = urllib.request.Request(url, data=data, headers=base)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, raw.decode(charset, errors="replace"), None
    except urllib.error.HTTPError as exc:
        return exc.code, "", f"HTTP {exc.code}"
    except Exception as exc:
        return 0, "", type(exc).__name__


def unwrap(url):
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if "duckduckgo.com" in host and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return url


def normalize(url):
    url = unwrap(url)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
            if not k.lower().startswith("utm_")]
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.hostname.lower(), path, "", urllib.parse.urlencode(kept), ""))


def public_http(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
        return False
    return True


def host_in(url, names):
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in names)


def host_blocked(url):
    return host_in(url, NOT_OPENED)


COMMENT_HOSTS = ("news.ycombinator.com", "reddit.com")


class _DdgParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hits = []
        self._mode = None
        self._depth = 0
        self._href = ""
        self._buf = []
        self._pending = None

    def handle_starttag(self, tag, attrs):
        attr = {key: value or "" for key, value in attrs}
        cls = attr.get("class", "")
        if self._mode:
            if tag not in {"br", "img", "hr", "meta", "link", "input", "wbr"}:
                self._depth += 1
            return
        if tag == "a" and ("result-link" in cls or "result__a" in cls):
            self._mode, self._depth, self._href, self._buf = "title", 1, attr.get("href", ""), []
        elif "result-snippet" in cls or "result__snippet" in cls:
            self._mode, self._depth, self._buf = "snippet", 1, []

    def handle_endtag(self, tag):
        if not self._mode:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if self._mode == "title" and text:
            self._pending = (self._href, text)
        elif self._mode == "snippet" and self._pending and text:
            href, title = self._pending
            self.hits.append(Hit(title=title, url=unwrap(href), snippet=text[:280], source="DuckDuckGo"))
            self._pending = None
        self._mode = None
        self._buf = []

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)


def parse_ddg(html):
    parser = _DdgParser()
    parser.feed(html or "")
    parser.close()
    return parser.hits


def ddg_blocked(html):
    low = (html or "").lower()
    if "result-link" in low or "result__a" in low:
        return False
    markers = ("anomaly", "captcha", "unfortunately", "prove you", "bot detection")
    return any(marker in low for marker in markers)


def search_ddg(query, get=_get):
    urls = (
        "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query}),
        "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query}),
    )
    saw_block = False
    saw_empty = False
    for url in urls:
        status, body, err = get(url)
        # A challenge page comes back as 202 with a body. 4xx/5xx are errors.
        if err or status == 0 or status >= 400:
            continue
        if ddg_blocked(body):
            saw_block = True
            continue
        hits = parse_ddg(body)
        if hits:
            return hits, None
        saw_empty = True
    if saw_empty:
        return [], None
    if saw_block:
        return [], "bot check"
    return [], "no response"


def _date_from_unix(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _date_from_iso(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def html_to_text(raw):
    cleaned = re.sub(r"cracking the coding interview", " ", raw or "", flags=re.I)
    cleaned = re.sub(r"(?is)<(script|style|noscript)\b.*?>.*?</\1>", " ", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    return unescape(re.sub(r"\s+", " ", cleaned)).strip()


def clip(text, limit=500):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;")
    return (cut or text[:limit].rstrip()) + "..."


def _near_company(sentence, tokens):
    """A round word counts only when this company is named beside it."""
    low = sentence.lower()
    for token in tokens:
        for match in re.finditer(rf"\b{re.escape(token)}\b", low):
            window = low[max(0, match.start() - 80):match.end() + 80]
            if SPECIFIC.search(window):
                return True
    return False


def _first_person_round(sentence):
    return re.search(
        r"\b(I|my)\b.{0,50}\b(interview\w*|on-?site|screen|round)\b"
        r"|\b(interview\w*|on-?site|screen|round)\b.{0,50}\b(I|my)\b",
        sentence,
        re.I,
    )


def process_sentences(text, tokens, title=""):
    """Sentences that describe this company's hiring process, not a nearby anecdote."""
    title_names = has_tokens(title, tokens)
    kept = []
    follow = False
    for sentence in re.split(r"(?<=[.!?])\s+", html_to_text(text)):
        sentence = re.sub(r"^(>\s*)+", "", sentence).strip()
        if not sentence:
            continue
        grounded = SPECIFIC.search(sentence) and (
            _near_company(sentence, tokens) or (title_names and _first_person_round(sentence))
        )
        if grounded:
            kept.append(sentence)
            follow = True
        elif follow and (has_tokens(sentence, tokens) or SPECIFIC.search(sentence)):
            kept.append(sentence)
        else:
            follow = False
    return clip(" ".join(kept))


def parse_reddit(data):
    hits = []
    for child in ((data or {}).get("data") or {}).get("children") or []:
        post = child.get("data") or {}
        title = re.sub(r"\s+", " ", (post.get("title") or "")).strip()
        permalink = post.get("permalink") or ""
        if not title or not permalink:
            continue
        url = permalink if permalink.startswith("http") else "https://www.reddit.com" + permalink
        body = html_to_text(post.get("selftext") or "")
        sub = post.get("subreddit") or "?"
        hits.append(Hit(
            title=title, url=url, snippet=(body or title)[:2000],
            source=f"Reddit r/{sub}", when=_date_from_unix(post.get("created_utc")),
        ))
    return hits


def search_reddit(query, get=_get):
    url = "https://www.reddit.com/search.json?" + urllib.parse.urlencode({
        "q": query, "limit": "10", "sort": "relevance", "type": "link",
    })
    status, body, err = get(url, headers={"User-Agent": "personal-job-search interview-intel"})
    if err or status != 200:
        return [], err or f"HTTP {status}"
    try:
        data = json.loads(body)
    except ValueError:
        return [], "response was not JSON"
    return parse_reddit(data), None


def parse_hn(data):
    hits = []
    for item in (data or {}).get("hits") or []:
        title = re.sub(r"\s+", " ", (item.get("title") or item.get("story_title") or "")).strip()
        text = html_to_text(item.get("comment_text") or item.get("story_text") or "")
        url = item.get("url") or ""
        if not url and item.get("objectID"):
            url = f"https://news.ycombinator.com/item?id={item['objectID']}"
        if not url or not (title or text):
            continue
        hits.append(Hit(
            title=title or text[:80], url=url, snippet=(text or title)[:2000],
            source="Hacker News", when=_date_from_iso(item.get("created_at")),
        ))
    return hits


def search_hn(query, get=_get):
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({
        "query": query, "hitsPerPage": "15", "tags": "(story,comment)",
    })
    status, body, err = get(url)
    if err or status != 200:
        return [], err or f"HTTP {status}"
    try:
        data = json.loads(body)
    except ValueError:
        return [], "response was not JSON"
    return parse_hn(data), None


def live_search(query):
    hits, errors = [], []
    for label, fn in (("DuckDuckGo", search_ddg), ("Hacker News", search_hn), ("Reddit", search_reddit)):
        try:
            batch, err = fn(query)
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}")
            continue
        hits.extend(batch)
        if err:
            errors.append(f"{label}: {err}")
    return hits, errors


def relevant(hits, company):
    tokens = company_tokens(company)
    if not tokens:
        return []
    chosen, seen = [], set()
    for hit in hits:
        full = html_to_text(f"{hit.title} {hit.snippet}")
        if not has_tokens(full, tokens):
            continue
        if re.search(r"who is hiring", hit.title or "", re.I):
            continue
        detail = process_sentences(hit.snippet, tokens, hit.title)
        if not detail:
            continue
        if POSTING.search(full) and not STRONG.search(detail):
            continue
        url = normalize(hit.url)
        if not url or not public_http(url) or url in seen:
            continue
        seen.add(url)
        chosen.append(replace(hit, url=url, snippet=detail))
        if len(chosen) >= MAX_FINDINGS:
            break
    return chosen


def page_excerpts(html):
    text = html_to_text(html)
    head = text[:2500].lower()
    if any(marker in head for marker in ("just a moment", "captcha", "sign in to", "log in to continue")):
        return "", "login wall or bot check"
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if not 50 <= len(sentence) <= 220:
            continue
        if re.search(r"\b(cookie|subscribe|sign in|privacy policy)\b", sentence, re.I):
            continue
        if STRONG.search(sentence) or re.search(r"\b(round|screen|onsite|interview)\b", sentence, re.I):
            kept.append(sentence)
        if len(kept) == 2:
            break
    return " ".join(kept), None


def default_open_page(url):
    status, body, err = _get(url, timeout=15)
    if err or status != 200:
        return "", err or f"HTTP {status}"
    return page_excerpts(body)


def _article(label):
    return "an" if label[:1].lower() in "aeiou" else "a"


def expect_lines(findings, role=""):
    lines = []
    for label, pattern in SIGNALS:
        hits = [item for item in findings if pattern.search(item.scan)]
        if not hits:
            continue
        example = next((item for item in hits if item.role_named and not item.stale), None)
        example = example or next((item for item in hits if item.role_named), None)
        example = example or next((item for item in hits if not item.stale), hits[0])
        where = f"{example.title} ({example.url})"
        if len(hits) == 1:
            line = f"- One report mentions {_article(label)} {label}: {where}. One write-up is one person's experience."
        else:
            line = f"- {len(hits)} reports mention {_article(label)} {label}. Example: {where}."
        if example.stale and example.when:
            line += f" The example is from {example.when.year}; the process may have changed."
        if role and not example.role_named:
            line += f" That report does not name the {role} role."
        lines.append(line)
    if not lines:
        lines.append(
            "- The reports name an interview at this company but do not describe the rounds. "
            "Do not assume a phone screen, a take-home, or an onsite."
        )
    return lines


def _role_phrase(role):
    if not role:
        return "this kind of interview"
    article = "an" if role[:1].lower() in "aeiou" else "a"
    return f"{article} {role} interview"


def static_estimate(company, role):
    return (
        f"{lead_sentence(company)}\n\n"
        f"No public write-up turned up, so the bullets below are general practice for {_role_phrase(role)}.\n"
        "- A first conversation is often about the resume, why you want the work, and when you can start.\n"
        "- Later conversations usually ask for specific examples of work you have done. "
        "A technical role often adds a walk-through of a project or a practical exercise.\n"
        "- Use STAR stories that match the resume you submitted. Do not add experience that resume does not show.\n"
        "- Prepare a few questions about the team, the work in the first six months, and how they will judge success.\n"
        "- Do not assume a phone screen, a take-home, a system-design round, or an onsite unless the company tells you. "
        "Processes range from one conversation to several rounds.\n"
    )


def ai_prompt(company, role):
    role_bit = role or "the role being discussed"
    return (
        f"Start with this exact sentence and nothing before it:\n{lead_sentence(company)}\n\n"
        f"Then give 5 to 8 short bullets of general interview practice for a candidate preparing for {role_bit}.\n"
        "After that first sentence, do not use the company name.\n"
        "Do not mention Glassdoor, Blind, Reddit, or what candidates report.\n"
        "Do not invent rounds, questions, products, or culture for any named company.\n"
        "Do not say according to anyone. Under 180 words.\n"
    )


def accept_estimate(text, company):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines or lines[0] != lead_sentence(company):
        return False
    if company.lower() in "\n".join(lines[1:]).lower():
        return False
    low = text.lower()
    return not any(phrase in low for phrase in BANNED_ESTIMATE)


def maybe_ai(company, role, ai):
    if ai is None:
        return static_estimate(company, role), "No model was called. This is general practice, not a report of the company."
    try:
        result = ai(ai_prompt(company, role))
    except Exception as exc:
        return static_estimate(company, role), f"The model failed ({type(exc).__name__}). General practice is used instead."
    text = (getattr(result, "text", "") or "") if getattr(result, "ok", False) else ""
    if accept_estimate(text, company):
        who = getattr(result, "provider", "") or "a model"
        return text.strip(), f"Written by {who}. This is still not a report of the company."
    return static_estimate(company, role), "The model reply was not usable. General practice is used instead."


def _queries(company, role):
    if role:
        return [f'"{company}" "{role}" interview', f'"{company}" interview process']
    return [f'"{company}" interview process']


def _note_list(errors):
    if not errors:
        return []
    if isinstance(errors, str):
        return [errors]
    return [str(item) for item in errors if item]


def _gather(company, role, search):
    queries = _queries(company, role)
    raw, notes = [], []
    used = [queries[0]]
    batch, errors = search(queries[0])
    raw.extend(batch or [])
    notes.extend(_note_list(errors))
    # A role query often matches one stale comment. The company-level query fills that in.
    if role and len(relevant(list(raw), company)) < 3:
        used.append(queries[1])
        batch, errors = search(queries[1])
        raw.extend(batch or [])
        notes.extend(_note_list(errors))
    unique_notes = []
    for note in notes:
        if note not in unique_notes:
            unique_notes.append(note)
    return used, relevant(raw, company), unique_notes


def _finding(hit, company, role, excerpt, note, today):
    detail = (excerpt or hit.snippet or "").strip()
    # The story title is not evidence. "Please stop the coding challenges" is the thread, not the company's process.
    scan = f"{hit.snippet} {excerpt or ''}"
    when = hit.when
    return Finding(
        title=hit.title.strip() or hit.url,
        url=hit.url,
        source=hit.source,
        detail=detail[:500],
        note=note,
        when=when,
        role_named=names_role(scan, role),
        stale=bool(when and (today - when).days > STALE_DAYS),
        scan=scan,
    )


def render(company, role, queries, findings, notes, estimate, estimate_note):
    lines = [f"# Interview intel: {company}" + (f" - {role}" if role else ""), ""]
    if findings:
        lines.append(f"**Evidence:** {len(findings)} public report" + ("s" if len(findings) != 1 else ""))
    else:
        lines.append("**Evidence:** no public interview reports found")
    lines.append("**Searched:** " + "; ".join(queries))
    if notes:
        lines.append("**Search notes:** " + "; ".join(notes))
    lines.append(
        "**Not opened:** Glassdoor, Indeed, ZipRecruiter, and Blind. "
        "A snippet from those sites is labeled snippet-only."
    )
    lines.append("")
    if not findings:
        lines.append(
            f"No public interview write-up named {company} in this search. "
            f"Nothing below is what {company} has been reported to do."
        )
        lines.append("")
        lines.append("## AI estimate")
        lines.append("")
        lines.append(estimate_note)
        lines.append("")
        lines.append(estimate.rstrip())
        lines.append("")
        return "\n".join(lines)

    lines.append("## What people reported")
    lines.append("")
    for index, item in enumerate(findings, start=1):
        when = f", {item.when.year}" if item.when else ""
        stale = " (older than three years; the process may have changed)" if item.stale else ""
        role_note = " Does not name this role." if role and not item.role_named else ""
        lines.append(f"{index}. **{item.title}** ({item.source}{when}){stale}{role_note}")
        lines.append(f"   {item.url}")
        lines.append(f"   {item.note}")
        if item.detail:
            lines.append(f"   {item.detail}")
        lines.append("")
    lines.append("## What to expect")
    lines.append("")
    lines.append("These points come from the reports above. They are what those write-ups say, not a guarantee of your interview.")
    if role and findings and not any(item.role_named for item in findings):
        lines.append(f"None of these reports name the {role} role. They describe interviews at the company more broadly.")
    lines.append("")
    lines.extend(expect_lines(findings, role))
    lines.append("")
    return "\n".join(lines)


def research(company, role="", ai=None, search=None, open_page=None, today=None):
    company = re.sub(r"\s+", " ", (company or "").strip())[:120]
    role = re.sub(r"\s+", " ", (role or "").strip())[:120]
    if not company:
        raise ValueError("company is required")
    if not company_tokens(company):
        raise ValueError("company name has no searchable word")
    today = today or date.today()
    live = search is None
    search = search or live_search
    if open_page is None:
        open_page = default_open_page if live else (lambda url: ("", "page not opened"))
    queries, hits, notes = _gather(company, role, search)
    findings = []
    opened = 0
    for hit in hits:
        excerpt, note = "", ""
        if host_in(hit.url, COMMENT_HOSTS):
            note = "From the comment."
        elif host_blocked(hit.url):
            note = "Snippet only (this site is not opened)."
        elif opened >= MAX_PAGES:
            note = "Snippet only (further pages were not opened)."
        else:
            opened += 1
            try:
                excerpt, err = open_page(hit.url)
            except Exception as exc:
                excerpt, err = "", type(exc).__name__
            if excerpt:
                note = "From the page."
            elif err:
                note = f"Snippet only; page not read ({err})."
            else:
                note = "Snippet only; the page had no clear interview detail."
        findings.append(_finding(hit, company, role, excerpt or "", note, today))
    estimate, estimate_note = ("", "")
    if not findings:
        estimate, estimate_note = maybe_ai(company, role, ai)
    markdown = render(company, role, queries, findings, notes, estimate, estimate_note)
    return Report(company, role, queries, findings, notes, bool(findings), markdown)


def load_ai():
    from agent_runner import run_agent
    return run_agent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Public interview reports for one company.")
    parser.add_argument("company")
    parser.add_argument("--role", default="")
    parser.add_argument("--out")
    parser.add_argument("--ai", action="store_true", help="ask a headless model for the estimate when no reports turn up")
    args = parser.parse_args(argv)
    try:
        report = research(args.company, args.role, ai=load_ai() if args.ai else None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.markdown, encoding="utf-8")
    print(report.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
