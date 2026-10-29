"""Check a drafted answer or cover letter for claims about Adam that his profile does not support.

Two rules, both deterministic:
  1. Banned claims: wording the profile contradicts or never states (see BANNED_CLAIMS).
  2. Numbers: every dollar amount, "N+" count, percentage or "N years" in the draft must appear in the source
     text (the resume plus application_facts.md). Posting text is never a source.
A draft that fails is not sent to Adam as written; the agent asks him instead.
"""
import re

BANNED_CLAIMS = (
    (r"model[- ]generated code", "Activus involved no coding (Adam, 2026-10-28)"),
    (r"\b(five|5)\s*\+\s*years", "the resume says nearly five years of sales"),
    (r"\bquota\b", "no quota attainment is on file"),
    (r"\btest[- ]driven\b|\bTDD\b", "TDD is not a documented practice"),
    (r"\bstaff\s+(ai|engineer|research)", "never imply a staff title"),
    (r"\bsecured\b[^.]*\bRec Room\b", "use 'helped source and close'"),
    (r"AI-?901[^.]*\b(certified|passed|earned)\b", "AI-901 is in progress, not passed"),
)

NUMBER = re.compile(
    r"\$\d[\d,]*(?:\.\d+)?[KMB]?\+?"          # $500K+  $1.1M  $2M
    r"|\b\d[\d,]*\+"                            # 100+
    r"|\b\d+(?:\.\d+)?\s?%"                     # 40%
    r"|\b\d+\+?\s?(?:years?|yrs?)\b",           # 7 years  5+ years
    re.I)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower())


def check(text, source):
    """Problems found in `text`, as short strings. Empty means the draft makes no unsupported claim."""
    found = []
    for pattern, why in BANNED_CLAIMS:
        m = re.search(pattern, text or "", re.I)
        if m:
            found.append(f"'{m.group(0)}': {why}")
    haystack = _norm(source)
    for m in NUMBER.finditer(text or ""):
        token = _norm(m.group(0)).rstrip("+").strip()
        if token not in haystack:
            problem = f"number not in the profile: {m.group(0).strip()}"
            if not any(m.group(0).strip() in f for f in found):
                found.append(problem)
    return found
