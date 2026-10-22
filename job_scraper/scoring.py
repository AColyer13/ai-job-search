"""Resume-aware job scoring.

A grade is earned from the posting text, not from the title alone. The title
only decides which kind of role this is. Whether Adam can realistically get it
depends on what the posting asks for:

    * years of experience, split into sales years and technical years
    * a computer-science / engineering degree with no alternative accepted
    * clearance, offshore or staffing-firm postings

Adam's background (see CLAUDE.md): B.A. Marketing, a full-stack bootcamp
certificate, about 5 years in B2B sales / business development, and about a
year of shipped full-stack projects and AI-evaluation contract work. No CS
degree and no engineering job title.

Rules in one line each:
    high    the role fits the background AND the posting text confirms it
    medium  plausible, but a requirement is borderline
    low     a requirement he does not meet, or not a role he can do

A posting whose text could not be read is low. It is retried on later runs while it
is inside the 14-day window, and graded properly once the text loads.

Stdlib only. `score(job)` returns a dict; `grade_job(job)` writes it onto the job.
"""
import re

# --- Adam's background, in whole years -------------------------------------
SALES_YEARS = 4          # stated minimum. A posting that REQUIRES more is left off the list.
TECH_YEARS = 1           # shipped projects + AI contracts, no engineering title
MAX_ENTRY_TECH_YEARS = 2  # engineering roles asking more than this are a stretch

_HIGH, _MED, _LOW = "high", "medium", "low"
_RANK = {_LOW: 0, _MED: 1, _HIGH: 2}


def _min_grade(a, b):
    return a if _RANK[a] <= _RANK[b] else b


# --- Title classification ---------------------------------------------------
SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|experienced|staff|principal|lead|director|vice president|head of|vp\b|chief |"
    r"ii|iii|iv)\b|(?:engineer|developer|analyst)\s*[2-9]\b",
    re.I,
)
BRIDGE_RE = re.compile(
    r"\b(solutions engineer|sales engineer|solutions consultant|solutions architect|"
    r"technical sales|pre[-\s]?sales|presales|technical account|customer engineer|"
    r"forward deployed|implementation (?:engineer|consultant|specialist|manager)|"
    r"onboarding (?:engineer|consultant|specialist)|technical consultant)\b",
    re.I,
)
ENGINEERING_RE = re.compile(
    r"\b(full[- ]?stack|software engineer|software developer|application developer|"
    r"web developer|front[- ]?end|back[- ]?end|developer|"
    r"(?:ai|ml|llm|machine learning)\b.{0,30}\b(?:engineer|developer)|"
    r"(?:engineer|developer)\b.{0,30}\b(?:ai|ml|llm|machine learning))\b",
    re.I,
)
QA_RE = re.compile(r"\b(qa|sdet|quality assurance|software tester)\b", re.I)
ENTRY_TITLE_RE = re.compile(
    r"\b(junior|jr\.?|entry[- ]level|(?<!senior )(?<!sr )(?<!sr\. )(?<!experienced )associate|trainee|new grad|early career|graduate|"
    r"apprentice|(?:engineer|developer)\s+(?:i|1))\b",
    re.I,
)
INTERN_RE = re.compile(r"\b(intern|internship|co-?op)\b|2027", re.I)
OFF_ROLE_RE = re.compile(
    r"\b(firmware|embedded|electrical|mechanical|manufacturing|scada|lidar|"
    r"cyber ?security|security engineer|devops|site reliability|sre|data engineer|"
    r"data scien|network engineer|systems engineer|hardware|blockchain|"
    r"machine learning scientist|research scientist|\.net|java\b|c\+\+|golang|rust|"
    r"ios|android|salesforce developer|sap\b|mainframe|cobol|technician|inspector|"
    r"(?:1st|2nd|3rd|first|second|third|night|weekend) shift|food|pharma\w*|laborator\w+|retail|coordinator)\b",
    re.I,
)
# Roles that need a clinical or licensed background, whatever the family.
CLINICAL_RE = re.compile(
    r"\b(clinical|nurse|nursing|radiolog\w+|patient care|medical (?:device|sales)|"
    r"healthcare sales|ultrasound|pacs)\b",
    re.I,
)


def role_family(title):
    t = title or ""
    if QA_RE.search(t):
        return "qa"
    if BRIDGE_RE.search(t):
        return "bridge"
    if ENGINEERING_RE.search(t):
        return "engineering"
    return "other"


# --- Company filters --------------------------------------------------------
OFFSHORE_COMPANY_RE = re.compile(
    r"\b(private limited|pvt\.?|\(opc\)|india|infotech|technologies pvt)\b", re.I
)
STAFFING_COMPANY_RE = re.compile(
    r"\b(staffing|recruit(?:ing|ment|ers)?|talent|placement|pavago|programmers\.io|"
    r"jobgether|lensa|snatch up|dice|hays|robert half|insight global|teksystems|randstad|"
    r"kforce|apex systems|cybercoders)\b",
    re.I,
)


# --- Description signals ----------------------------------------------------
_SALES_CTX = re.compile(
    r"\b(sales|selling|business development|account (?:executive|management|manager)|"
    r"customer[- ]facing|client[- ]facing|pre[- ]?sales|presales|quota|saas sales|"
    r"customer success|customer[- ]support|consulting|implementation|onboarding|"
    r"project management|professional services|technical sales|solutions)\b",
    re.I,
)
_TECH_CTX = re.compile(
    r"\b(software|engineering|engineer|development|developer|programming|coding|"
    r"devops|full[- ]?stack|front[- ]?end|back[- ]?end|python|java|javascript|typescript|"
    r"react|node|\.net|c\+\+|cloud|aws|azure|gcp|kubernetes|sql|api|apis|networking|"
    r"infrastructure|machine learning|ai\b|technical)\b",
    re.I,
)
# "3+ years", "3-5 years", "at least 3 years", "minimum of 3 years"
_YEARS_RE = re.compile(
    r"(?:at least|minimum(?: of)?|min\.?)?\s*(\d{1,2})\s*(?:\+|-|–|to)?\s*(\d{1,2})?\s*\+?\s*"
    r"(?:years?|yrs?\.?)(?:['’]s?)?(?:\s+of)?\s+([^.;\n]{0,90})",
    re.I,
)
_PREFERRED_RE = re.compile(
    r"\b(preferred|nice to have|a plus|bonus|ideal(?:ly)?|desired|helpful|advantageous)\b",
    re.I,
)
_ENTRY_TEXT_RE = re.compile(
    r"\b(entry[- ]level|early[- ]career|new grad(?:uate)?s?|recent graduates?|"
    r"no (?:prior |previous )?(?:professional )?(?:experience|degree)(?: is)? (?:required|necessary|needed)|"
    r"0\s*(?:-|–|to)\s*[1-3]\s*years?|"
    r"will train|training provided|career changers?|career[- ]switchers?|"
    r"boot ?camps?|coding camps?)\b",
    re.I,
)
CLEARANCE_RE = re.compile(
    r"\b(security clearance|active clearance|top secret|ts/sci|polygraph|"
    r"u\.?s\.? citizen(?:ship)? (?:is )?required)\b",
    re.I,
)
_SENIOR_TEXT_RE = re.compile(
    r"\b(senior[- ]level|seasoned|proven track record leading|lead a team of engineers|"
    r"mentor (?:junior|other) engineers)\b",
    re.I,
)

# A degree line is exclusive when it names CS/engineering and offers no way out.
_DEGREE_FIELD_RE = re.compile(
    r"\b(?:bachelor(?:'?s)?|b\.?\s?s\.?\b|m\.?\s?s\.?\b|master(?:'?s)?|undergraduate|college|degree)\b"
    r".{0,60}\b(?:in|of)\s+(?:computer science|computer engineering|software engineering|"
    r"electrical engineering|mechanical engineering|engineering|information (?:technology|systems)|"
    r"mathematics|cs\b)|"
    r"\b(?:bs|ms|b\.s\.?|m\.s\.?)\b(?:\s*/\s*(?:bs|ms|b\.s\.?|m\.s\.?))?\s+(?:in\s+)?"
    r"(?:computer science|computer engineering|software engineering|engineering|cs)\b|"
    r"\b(?:engineering|computer science|computer engineering)\s+degree\b",
    re.I,
)
_DEGREE_ESCAPE_RE = re.compile(
    r"\b(?:equivalent|business|marketing|any discipline|any field|any degree|"
    r"similar experience|preferred|nice to have|ideally|bonus|a plus|"
    r"boot ?camp|or experience|or work experience|or practical|or relevant|or comparable|"
    r"in lieu|self[- ]taught|demonstrated|portfolio)\b",
    re.I,
)


def _chunks(text):
    plain = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", text or "")
    plain = re.sub(r"<[^>]+>", " ", plain).replace(" ", " ")
    return [" ".join(c.split()) for c in re.split(r"[\n\r]+|(?<=[.!?])\s+", plain) if c.strip()]


_PREF_HEADER_RE = re.compile(
    r"\b(preferred|nice[- ]to[- ]haves?|bonus|a plus|pluses|desired|ideal candidate|"
    r"extra credit|good to have|would be great|not required|optional)\b",
    re.I,
)
_REQ_HEADER_RE = re.compile(
    r"\b(requirements?|required|qualifications?|must[- ]haves?|minimum|basic|"
    r"what you(?:'|’)?ll need|what you need|what you bring|who you are|about you)\b",
    re.I,
)
# Words that are headings only when the line ends with a colon; "Experience with
# Salesforce" on its own is a bullet, not a heading.
_REQ_COLON_HEADER_RE = re.compile(r"\b(you have|skills|experience|background)\b", re.I)
_NEUTRAL_HEADER_RE = re.compile(
    r"\b(responsibilit|what you(?:'|’)?ll do|the role|about us|about the|benefits|"
    r"perks|compensation|salary|why join|who we are|our (?:team|mission)|overview)\b",
    re.I,
)


def _is_header(line):
    """A short line that names a section. Bullets with digits or sentences are not."""
    if line.endswith(":"):
        return len(line) <= 90
    return (
        len(line.split()) <= 6
        and not re.search(r"\d", line)
        and not line.endswith((".", ",", ";"))
    )


def sections(text):
    """(line, preferred) for each line of the posting.

    A heading such as "Preferred Qualifications" marks every line under it as
    preferred until the next heading. "Required" / "Minimum" / "Requirements"
    headings switch back. A sentence that says preferred or nice to have is
    preferred on its own, wherever it sits.
    """
    mode_pref = False
    out = []
    for line in _chunks(text):
        if _is_header(line):
            colon = line.endswith(":")
            if _PREF_HEADER_RE.search(line):
                mode_pref = True
                out.append((line, True))
                continue
            if (
                _REQ_HEADER_RE.search(line)
                or _NEUTRAL_HEADER_RE.search(line)
                or (colon and _REQ_COLON_HEADER_RE.search(line))
            ):
                mode_pref = False
                out.append((line, False))
                continue
        out.append((line, mode_pref or bool(_PREFERRED_RE.search(line))))
    return out


_TECH_FIELD_DEGREE_RE = re.compile(
    r"\b(?:bachelor|degree|b\.?s\.?)\b.{0,50}\b(?:technical|stem|quantitative|scientific)\s+"
    r"(?:field|discipline|subject)",
    re.I,
)
_ADVANCED_DEGREE_RE = re.compile(
    r"\b(?:master(?:'?s)?|m\.?s\.?|ph\.?d\.?|doctorate|doctoral)\b.{0,30}\b(?:degree|required|in)\b|"
    r"\b(?:ph\.?d\.?|doctorate)\b",
    re.I,
)


def exclusive_degree(text):
    """The sentence that REQUIRES a CS/engineering/technical degree or a graduate degree
    with no experience alternative, or ''. Marketing B.A., bootcamp, and equivalent
    experience are alternatives; "or related field" is not.
    """
    for line, preferred in sections(text):
        if preferred or _DEGREE_ESCAPE_RE.search(line):
            continue
        if _DEGREE_FIELD_RE.search(line) or _TECH_FIELD_DEGREE_RE.search(line):
            return line[:240]
        if _ADVANCED_DEGREE_RE.search(line):
            return line[:240]
    return ""


# Languages and tools he has not worked in. A required line naming one of these
# with none of his own stack beside it ("Java or Python") is a real requirement.
_LACKING_SKILL_RE = re.compile(
    r"\b(java|c#|\.net|c\+\+|golang|rust|kotlin|swift|scala|ruby|php|kubernetes|k8s|"
    r"terraform|ansible|hadoop|spark|snowflake|databricks|apex|sap|oracle|mainframe|"
    r"cobol|perl|matlab|unity|unreal|pytorch model training|cuda)\b",
    re.I,
)
_OWN_SKILL_RE = re.compile(
    r"\b(python|javascript|typescript|react|node|next\.?js|fastapi|flask|sql|html|css|"
    r"llm|rag|prompt)\b",
    re.I,
)
_SKILL_REQUIRED_RE = re.compile(
    r"\b(experience|proficien\w*|knowledge|expertise|skilled|hands[- ]on|fluen\w*|"
    r"strong|solid|working|command)\b",
    re.I,
)
_CREDENTIAL_RE = re.compile(
    r"\b(cissp|cism|cisa|ceh|security\+|pmp|cpa|cfa|cfp|series\s*(?:7|63|65|66)|"
    r"aws certified|azure certified|certified (?:solutions|developer|cloud|kubernetes|scrum)|"
    r"cka\b|ccna|ccnp|salesforce certified|nursing licen\w+|rn\b|"
    r"(?:state|professional|valid) licen[sc]e)\b",
    re.I,
)


_NOT_A_REQUIREMENT_RE = re.compile(
    r"^\s*(professional development|learning|growth|benefits|perks)\b|"
    r"\b(opportunit\w+ to|you(?:'ll| will) (?:learn|grow|get to))\b",
    re.I,
)


def missing_skill(text):
    """First required line naming a language or tool you lack, or ''."""
    for line, preferred in sections(text):
        if preferred or len(line) > 400 or _NOT_A_REQUIREMENT_RE.search(line):
            continue
        if (
            _LACKING_SKILL_RE.search(line)
            and not _OWN_SKILL_RE.search(line)
            and _SKILL_REQUIRED_RE.search(line)
        ):
            return line[:240]
    return ""


def missing_credential(text):
    """First required line naming a certification or license you lack, or ''."""
    for line, preferred in sections(text):
        if preferred or _DEGREE_ESCAPE_RE.search(line):
            continue
        if _CREDENTIAL_RE.search(line):
            return line[:240]
    return ""


def years_required(text):
    """Largest required years of (sales, technical, unknown) experience.

    Only required years count. Anything under a Preferred / Nice to have heading,
    or in a sentence that says preferred, is returned separately as the fourth
    value so the card can note it. A range uses its lower bound, since the
    lower bound is the stated minimum ("3-5 years" asks for 3).
    """
    sales = tech = other = pref = 0
    for line, preferred in sections(text):
        for m in _YEARS_RE.finditer(line):
            if preferred:
                n = int(m.group(1))
                if 0 < n <= 20:
                    pref = max(pref, n)
                continue
            n = int(m.group(1))
            if not 0 < n <= 20:
                continue
            ctx = m.group(3) or ""
            # Look at the clause after the number plus the start of the sentence.
            window = ctx + " " + line[:80]
            is_tech = bool(_TECH_CTX.search(ctx))
            is_sales = bool(_SALES_CTX.search(ctx))
            if is_tech and not is_sales:
                tech = max(tech, n)
            elif is_sales and not is_tech:
                sales = max(sales, n)
            elif is_tech and is_sales:
                other = max(other, n)
            elif _TECH_CTX.search(window) and not _SALES_CTX.search(window):
                tech = max(tech, n)
            else:
                other = max(other, n)
    return sales, tech, other, pref


def has_text(description):
    """True when there is enough posting text to judge requirements."""
    return len(" ".join((description or "").split())) >= 300


# --- The scorer -------------------------------------------------------------
def score(job):
    """Grade one job. Returns {grade, reasons, blockers, verified, entry_evidence}.

    `reasons` are the things that support the grade, `blockers` are the things
    that held it down. Both are short plain sentences for the daily list.
    """
    title = job.get("title") or ""
    company = job.get("company") or ""
    desc = job.get("description") or ""
    reasons, blockers = [], []
    verified = has_text(desc) and job.get("body_status") not in ("shell", "unread")
    family = role_family(title)

    def result(grade, entry=False, disqualified=False):
        if grade == _LOW:
            del reasons[:]
        return {
            "disqualified": disqualified,
            "grade": grade,
            "reasons": reasons,
            "blockers": blockers,
            "verified": verified,
            "entry_evidence": entry,
            "family": family,
        }

    # -- hard no's from the title / company ---------------------------------
    if INTERN_RE.search(title):
        blockers.append("The title says intern or co-op.")
        return result(_LOW)
    if OFF_ROLE_RE.search(title) and family != "bridge":
        blockers.append("The title names a stack or field outside React, TypeScript, and Python.")
        return result(_LOW)
    if family == "other":
        blockers.append("The title is not a sales-engineering, technical-sales, or entry engineering role.")
        return result(_LOW)
    if CLINICAL_RE.search(title):
        blockers.append("The title needs a clinical or licensed background.")
        return result(_LOW)
    if OFFSHORE_COMPANY_RE.search(company):
        blockers.append(f"{company} looks like an offshore or outsourcing firm.")
        return result(_LOW)

    senior = (
        bool(SENIOR_RE.search(title))
        or (family in ("engineering", "qa") and bool(re.search(r"\bmanager\b", title, re.I)))
    ) and not ENTRY_TITLE_RE.search(title)
    if senior:
        blockers.append("The title is senior, lead, manager, director,.")
        return result(_LOW)

    entry_title = bool(ENTRY_TITLE_RE.search(title))
    # What the title alone supports.
    if family == "bridge":
        grade = _HIGH
        reasons.append("A sales-engineering role: your sales background is the match.")
    elif family == "engineering":
        if entry_title:
            grade = _HIGH
            reasons.append("The title is entry-level engineering.")
        else:
            grade = _MED
            blockers.append("The title does not say junior or entry, so the level is unknown.")
    else:  # qa
        grade = _HIGH if entry_title else _MED
        (reasons if entry_title else blockers).append(
            "The title is junior QA." if entry_title else "The title is QA with no level."
        )

    technical_bridge = family == "bridge" and bool(
        re.search(r"architect|forward deployed|technical consultant|implementation manager", title, re.I)
    )
    if technical_bridge:
        grade = _min_grade(grade, _MED)
        blockers.append("This title is mostly technical delivery; it needs proof it is open to sales backgrounds.")

    if STAFFING_COMPANY_RE.search(company):
        grade = _min_grade(grade, _MED)
        blockers.append(f"{company} looks like a staffing firm, not the hiring company.")

    # -- posting text -------------------------------------------------------
    entry_evidence = False
    if not verified:
        blockers.append("The posting text could not be read, so the requirements are unchecked.")
        return result(_LOW)

    degree = exclusive_degree(desc)
    if degree:
        blockers.append(f"It requires a degree you do not have, with no experience alternative: \"{degree[:140]}\"")
        return result(_LOW, disqualified=True)

    if CLEARANCE_RE.search(desc):
        blockers.append("It requires a security clearance or U.S. citizenship.")
        return result(_LOW, disqualified=True)

    credential = missing_credential(desc)
    if credential:
        blockers.append(f"It requires a certification or license you do not have: \"{credential[:140]}\"")
        return result(_LOW, disqualified=True)

    skill = missing_skill(desc)
    if skill:
        blockers.append(f"It requires a language or tool you have not worked in: \"{skill[:140]}\"")
        return result(_LOW, disqualified=True)

    sales_y, tech_y, other_y, pref_y = years_required(desc)
    need = 0
    if family == "engineering" or family == "qa":
        need = max(tech_y, other_y)
        entry_text = bool(_ENTRY_TEXT_RE.search(desc))
        if need > MAX_ENTRY_TECH_YEARS + 1:   # 4+
            blockers.append(f"It requires {need}+ years of engineering experience; you have about {TECH_YEARS}.")
            return result(_LOW, disqualified=True)
        if need == MAX_ENTRY_TECH_YEARS + 1:  # 3
            blockers.append(f"It requires {need}+ years of engineering experience; you have about {TECH_YEARS}.")
            if not entry_text:
                return result(_LOW, disqualified=True)
            grade = _min_grade(grade, _MED)
        elif need == MAX_ENTRY_TECH_YEARS:   # 2
            blockers.append("It asks for 2 years of engineering experience, one more than your projects.")
            grade = _min_grade(grade, _MED)
        elif need:
            reasons.append(f"It asks for {need} year of experience or less.")
    else:  # bridge
        if tech_y >= 4:
            blockers.append(f"It requires {tech_y}+ years in a technical or engineering role.")
            return result(_LOW, disqualified=True)
        if tech_y == 3:
            blockers.append("It requires 3+ years in a technical role; you have about 1.")
            return result(_LOW, disqualified=True)
        sales_need = max(sales_y, other_y)
        if sales_need > SALES_YEARS:         # 5+ required: not qualified
            blockers.append(f"It requires {sales_need}+ years of experience; you have 4 in sales.")
            return result(_LOW, disqualified=True)
        if sales_need:
            reasons.append(f"It asks for {sales_need} year{'s' if sales_need != 1 else ''}, inside your sales history.")

    have = SALES_YEARS if family == "bridge" else TECH_YEARS
    if pref_y > have:
        reasons.append(f"{pref_y}+ years is listed as preferred, not required.")

    if _SENIOR_TEXT_RE.search(desc):
        blockers.append("The posting describes a senior-level role.")
        return result(_LOW, disqualified=True)

    if _ENTRY_TEXT_RE.search(desc):
        entry_evidence = True
        reasons.append("The posting welcomes entry-level, career-change, bootcamp, or equivalent experience.")

    # A bare engineering title becomes high only when the text proves it is entry-friendly.
    if family == "engineering" and not entry_title:
        if entry_evidence and not blockers_hold(blockers):
            grade = _HIGH
        elif need in (1, MAX_ENTRY_TECH_YEARS):
            grade = _min_grade(grade, _MED)
        else:
            blockers.append("Nothing in the title or posting says this is open to entry-level or career-change candidates.")
            grade = _LOW

    if technical_bridge and entry_evidence and not blockers_hold(blockers, ("mostly technical delivery",)):
        grade = _HIGH
        blockers[:] = [x for x in blockers if "mostly technical delivery" not in x]
    if grade == _HIGH and not reasons:
        reasons.append("Nothing in the posting conflicts with your background.")
    return result(grade, entry_evidence)


def worth_reading(job):
    """True when only the missing posting text keeps this title from a grade.

    A title that is senior, off-role, an intern post or an offshore firm is low
    no matter what the text says, so there is no reason to open it.
    """
    result = score({"title": job.get("title"), "company": job.get("company")})
    return result["grade"] != _LOW or any("could not be read" in b for b in result["blockers"])


def blockers_hold(blockers, ignore=("does not say junior or entry",)):
    """True when a blocker other than the ignorable notes is present."""
    return any(not any(i in b for i in ignore) for b in blockers)


def grade_job(job):
    """Score `job` and write fit, fit_reasons, fit_blockers onto it."""
    out = score(job)
    job["fit"] = out["grade"]
    job["fit_reasons"] = out["reasons"]
    job["fit_blockers"] = out["blockers"]
    job["entry_evidence"] = out["entry_evidence"]
    job["verified"] = out["verified"]
    job["disqualified"] = out["disqualified"]
    return job
