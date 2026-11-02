"""Public employee reviews for one company. No invented rating.

    python job_scraper/company_reviews.py "Acme"
    python job_scraper/company_reviews.py "Acme" --out documents/applications/acme_role/company_reviews.md

Searches DuckDuckGo, Hacker News, and Reddit. Glassdoor, Indeed, ZipRecruiter,
and Blind pages are not opened. A search snippet from those sites is labeled
snippet-only. A blank result is not a star rating and not a culture summary.
"""
import argparse
import re
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import interview_intel

REVIEW = re.compile(
    r"work[- ]life|\bwlb\b|on-?call|burnout|hours (were|are|was)|long hours|working hours|"
    r"\b(manager|management|boss)\b|"
    r"\b(toxic|layoffs?|laid off|pip|fired|resign\w*|quit)\b|"
    r"great place|loved working|hated working|would not recommend|would recommend|"
    r"\b(salary|compensation|underpaid|overpaid|paid well|low pay|high pay|pay band|equity)\b|\bpay\b(?! attention)|"
    r"return to office|\brto\b|remote policy|"
    r"culture (is|was|feels|felt|at)|benefits (are|were|package)",
    re.I,
)
THEMES = (
    ("pay", re.compile(r"salary|compensation|underpaid|overpaid|paid well|low pay|high pay|pay band|equity|\bpay\b(?! attention)", re.I)),
    ("hours or work-life", re.compile(r"work[- ]life|\bwlb\b|on-?call|burnout|hours (were|are|was)|long hours|working hours|weekend", re.I)),
    ("management", re.compile(r"\b(manager|management|boss)\b", re.I)),
    ("layoffs or job security", re.compile(r"layoffs?|laid off|\bpip\b|\bfired\b", re.I)),
    ("culture", re.compile(r"\btoxic\b|loved working|hated working|culture (is|was|feels|felt|at)|great place|would not recommend|would recommend", re.I)),
    ("remote or office policy", re.compile(r"return to office|\brto\b|remote policy|\bhybrid\b", re.I)),
)
WORKED_AT = re.compile(r"\b(I|we)\s+worked at\b", re.I)
JUDGMENT = re.compile(
    r"exemplary|intense|terrible|awful|amazing|loved|hated|burnout|toxic|recommend|"
    r"hours (were|are|was)|long hours|working hours|manager|culture|layoffs?|salary|compensation|\bpay\b(?! attention)|on-?call|\bwlb\b|work[- ]life",
    re.I,
)
FIRST_PERSON = re.compile(
    r"\b(I|my|we|our)\b.{0,60}\b(worked|working|manager|team|hours|pay|salary|quit|left|culture|layoff|comp)\b"
    r"|\b(worked|working|manager|hours|pay|salary|quit|left|culture|layoff)\b.{0,60}\b(I|my|we|our)\b",
    re.I,
)
SPEAKER = re.compile(r"\b(I|me|my|we|our|us)\b", re.I)
OWN_JOB = re.compile(
    r"\b(I|we)\s+worked\b|\bI've worked\b|\bI have worked\b|\bworked at\b|\bworking at\b|"
    r"caught up in|like me|my team|when I was|"
    r"someone who(?:'s| has) worked",
    re.I,
)
EVALUATION = re.compile(
    r"work[- ]life|\bwlb\b|manager|hours (were|are|was)|long hours|working hours|"
    r"culture (is|was|at)|loved working|hated working|\btoxic\b|salary|compensation|"
    r"\bpay\b(?! attention)|burnout|on-?call|would not recommend|would recommend|underpaid|overpaid",
    re.I,
)
POSTING = interview_intel.POSTING


@dataclass
class Review:
    title: str
    url: str
    source: str
    detail: str
    note: str
    when: date | None
    stale: bool
    scan: str


def _near(sentence, tokens):
    low = sentence.lower()
    for token in tokens:
        for match in re.finditer(rf"\b{re.escape(token)}\b", low):
            window = low[max(0, match.start() - 80):match.end() + 80]
            if REVIEW.search(window):
                return True
    return False


def _sentences(text):
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", interview_intel.html_to_text(text)):
        sentence = re.sub(r"^(>\s*)+", "", sentence).strip()
        if sentence and not sentence.endswith("?"):
            out.append(sentence)
    return out


def review_sentences(text, tokens, title=""):
    """Sentences about working at this company, not a product blurb or a one-line credential."""
    title_names = interview_intel.has_tokens(title, tokens)
    sentences = _sentences(text)
    keep = set()
    for index, sentence in enumerate(sentences):
        window = " ".join(sentences[index:index + 3])
        named = _as_employer(sentence, tokens)
        worked = WORKED_AT.search(sentence) and named and JUDGMENT.search(window)
        grounded = REVIEW.search(sentence) and named and (
            _near(sentence, tokens) or (title_names and FIRST_PERSON.search(sentence))
        ) and (SPEAKER.search(sentence) or EVALUATION.search(sentence))
        if grounded and re.search(r"layoffs?|laid off", sentence, re.I) and not EVALUATION.search(sentence):
            grounded = bool(OWN_JOB.search(sentence))
        if not grounded and not worked:
            continue
        keep.add(index)
        if worked or grounded:
            for follow in range(index + 1, min(index + 3, len(sentences))):
                nxt = sentences[follow]
                if REVIEW.search(nxt) or JUDGMENT.search(nxt) or interview_intel.has_tokens(nxt, tokens):
                    keep.add(follow)
                else:
                    break
    ordered = [sentences[i] for i in sorted(keep)]
    return interview_intel.clip(" ".join(ordered))


def _company_in(sentence, tokens):
    low = sentence.lower()
    return all(re.search(rf"\b{re.escape(token)}\b", low) for token in tokens)


# A one-word employer ("BILL", "Affirm", "absolute") is also an ordinary English word.
# "the layoff bill", "I can affirm", and "absolute size" are not that employer.
_INTENSIFIER = (
    "size|worst|least|best|most|very|all|once|nothing|zero|minimum|maximum|bottom|top|truth"
)


def _as_employer(sentence, tokens):
    if not _company_in(sentence, tokens):
        return False
    if len(tokens) != 1:
        return True
    token = re.escape(tokens[0])
    return bool(re.search(
        rf"(?:worked|working|work|employed|employee|employees|joined|joining|hired|hire)\s+"
        rf"(?:at|for|by)\s+{token}\b|"
        rf"\bat\s+{token}\b(?!\s+(?:{_INTENSIFIER})\b)|"
        rf"\b{token}'s\b|"
        rf"\b{token}\s+(?:employees|engineers|staff|layoffs|culture)\b",
        sentence,
        re.I,
    ))


def _queries(company):
    # Separate queries. A parenthetical OR on Hacker News does not search each term.
    return [
        f'"worked at" "{company}"',
        f'"{company}" layoff',
        f'"{company}" "work life"',
    ]


def relevant(hits, company):
    tokens = interview_intel.company_tokens(company)
    if not tokens:
        return []
    chosen, seen = [], set()
    for hit in hits:
        full = interview_intel.html_to_text(f"{hit.title} {hit.snippet}")
        if not interview_intel.has_tokens(full, tokens):
            continue
        if re.search(r"who is hiring", hit.title or "", re.I):
            continue
        if POSTING.search(full):
            continue
        detail = review_sentences(hit.snippet, tokens, hit.title)
        if not detail:
            continue
        url = interview_intel.normalize(hit.url)
        if not url or not interview_intel.public_http(url) or url in seen:
            continue
        seen.add(url)
        chosen.append(replace(hit, url=url, snippet=detail))
        if len(chosen) >= interview_intel.MAX_FINDINGS:
            break
    return chosen


def _note_list(errors):
    if not errors:
        return []
    if isinstance(errors, str):
        return [errors]
    return [str(item) for item in errors if item]


def _gather(company, search):
    raw, notes, used = [], [], []
    for query in _queries(company):
        used.append(query)
        batch, errors = search(query)
        raw.extend(batch or [])
        notes.extend(_note_list(errors))
    unique = []
    for note in notes:
        if note not in unique:
            unique.append(note)
    return used, relevant(raw, company), unique


def _open(hit, open_page, opened, tokens):
    if interview_intel.host_in(hit.url, interview_intel.COMMENT_HOSTS):
        return "", "From the comment.", opened
    if interview_intel.host_blocked(hit.url):
        return "", "Snippet only (this site is not opened).", opened
    if opened >= interview_intel.MAX_PAGES:
        return "", "Snippet only (further pages were not opened).", opened
    opened += 1
    try:
        excerpt, err = open_page(hit.url)
    except Exception as exc:
        excerpt, err = "", type(exc).__name__
    excerpt = review_sentences(excerpt or "", tokens, hit.title)
    if excerpt:
        return excerpt, "From the page.", opened
    if err:
        return "", f"Snippet only; page not read ({err}).", opened
    return "", "Snippet only; the page had no clear review.", opened


def _theme_near(text, tokens, pattern):
    low = text.lower()
    for token in tokens:
        for match in re.finditer(rf"\b{re.escape(token)}\b", low):
            window = low[max(0, match.start() - 80):match.end() + 80]
            if pattern.search(window):
                return True
    return False


def _themes(reviews, company):
    tokens = interview_intel.company_tokens(company)
    lines = []
    for label, pattern in THEMES:
        hits = [item for item in reviews if _theme_near(item.scan, tokens, pattern)]
        if not hits:
            continue
        example = next((item for item in hits if not item.stale), hits[0])
        where = f"{example.title} ({example.url})"
        if len(hits) == 1:
            line = f"- One review mentions {label}: {where}. One review is one person's account."
        else:
            line = f"- {len(hits)} reviews mention {label}. Example: {where}."
        if example.stale and example.when:
            line += f" The example is from {example.when.year}; it may be out of date."
        lines.append(line)
    if not lines:
        lines.append(
            "- The reviews name this company but do not describe pay, hours, management, layoffs, or culture. "
            "Do not fill that in."
        )
    return lines


def render(company, queries, reviews, notes):
    lines = [f"# Company reviews: {company}", ""]
    if reviews:
        lines.append(f"**Evidence:** {len(reviews)} public employee review" + ("s" if len(reviews) != 1 else ""))
    else:
        lines.append("**Evidence:** no public employee reviews found")
    lines.append("**Searched:** " + "; ".join(queries))
    if notes:
        lines.append("**Search notes:** " + "; ".join(notes))
    lines.append(
        "**Not opened:** Glassdoor, Indeed, ZipRecruiter, and Blind. "
        "Star ratings on those sites were not read. A blank result here is not a score."
    )
    lines.append("")
    if not reviews:
        lines.append(
            f"No public employee review named {company} in this search. "
            f"Nothing below is what people have written about working there. "
            f"This report does not add a rating or a culture summary."
        )
        lines.append("")
        return "\n".join(lines)
    lines.append("## What people wrote")
    lines.append("")
    for index, item in enumerate(reviews, start=1):
        when = f", {item.when.year}" if item.when else ""
        stale = " (older than three years; it may be out of date)" if item.stale else ""
        lines.append(f"{index}. **{item.title}** ({item.source}{when}){stale}")
        lines.append(f"   {item.url}")
        lines.append(f"   {item.note}")
        if item.detail:
            lines.append(f"   {item.detail}")
        lines.append("")
    lines.append("## What those reviews mention")
    lines.append("")
    lines.append("These points come from the reviews above. They are what those people wrote, not a company score.")
    lines.append("")
    lines.extend(_themes(reviews, company))
    lines.append("")
    return "\n".join(lines)


def research(company, search=None, open_page=None, today=None):
    company = re.sub(r"\s+", " ", (company or "").strip())[:120]
    if not company:
        raise ValueError("company is required")
    tokens = interview_intel.company_tokens(company)
    if not tokens:
        raise ValueError("company name has no searchable word")
    today = today or date.today()
    live = search is None
    search = search or interview_intel.live_search
    if open_page is None:
        open_page = interview_intel.default_open_page if live else (lambda url: ("", "page not opened"))
    queries, hits, notes = _gather(company, search)
    reviews = []
    opened = 0
    for hit in hits:
        excerpt, note, opened = _open(hit, open_page, opened, tokens)
        detail = (excerpt or hit.snippet or "").strip()
        when = hit.when
        reviews.append(Review(
            title=hit.title.strip() or hit.url,
            url=hit.url,
            source=hit.source,
            detail=detail[:500],
            note=note,
            when=when,
            stale=bool(when and (today - when).days > interview_intel.STALE_DAYS),
            scan=f"{hit.snippet} {excerpt or ''}",
        ))
    markdown = render(company, queries, reviews, notes)
    return interview_intel.Report(company, "", queries, reviews, notes, bool(reviews), markdown)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Public employee reviews for one company.")
    parser.add_argument("company")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        report = research(args.company)
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
