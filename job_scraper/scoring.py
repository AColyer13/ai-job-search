"""Resume-aware job scoring.

A grade is earned from the posting text, not from the title alone. The title
only decides which kind of role this is. Whether Adam can realistically get it
depends on what the posting asks for:

    * years of experience, split into sales years and technical years
    * a computer-science / engineering degree with no alternative accepted
    * clearance, offshore or staffing-firm postings

Adam's background (see CLAUDE.md): B.A. Marketing, a full-stack bootcamp
certificate, just under 5 years in B2B sales / business development, and about a
year of shipped full-stack projects and AI-evaluation contract work. No CS
degree and no engineering job title.

The grade comes from a 0-100 fit score on the four dimensions of
.claude/skills/job-application-assistant/04-job-evaluation.md:

    career fit   30   what the seat is (role tier, <=22) and the product/market (<=8)
    technical    30   base for the seat plus overlap with the stack he has shipped
    experience   25   minus what the posting asks for beyond his history
    work style   15   customer-facing, end-to-end, mentorship; minus narrow frictions

    high    70+   nothing in the posting asks for more than he has
    medium  50-69
    low     under 50, or any hard gate below

Hard gates (always low): off-role stack, another region or language, clinical or
hardware/trade engineering, people managers, senior titles, exclusive degree,
clearance, a certification or language he lacks, years far past his history.

Ceilings: unread postings 64, QA 55, account-management / implementation /
onboarding / customer-success seats 59, factory or staffing-firm postings 64,
and engineering or technical-delivery seats with no sign they take a career
changer 69. The target roles (entry full-stack / AI engineering, technical sales,
sales / solutions engineering, pre-sales / solutions architecture) have no ceiling.

A posting whose text could not be read is medium when the title fits a target role
(the requirements are unchecked, not failed) and low when the title is adjacent. It is
retried on later runs while it is inside the 14-day window, and graded properly once
the text loads. The daily Word files leave low jobs out.

Every weight, threshold and assumption is written down in job_scraper/FIT_SCALE.md.

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
    r"avp|assistant vice president|mid[- ]?level|mid[- ]?senior|founding|ii|iii|iv|(?:l|ic)[ -]?[4-9]|level (?:iii|iv|[3-9]))\b|"
    r"(?:engineer|developer|analyst|consultant|specialist|architect)\s*[2-9]\b",
    re.I,
)
# A people-manager title. "Account Manager" (Technical Account Manager) is an individual seat.
MANAGER_RE = re.compile(r"\bmanager\b", re.I)
ACCOUNT_MANAGER_RE = re.compile(r"\baccount manager\b", re.I)
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
SOFTWARE_QA_RE = re.compile(r"\b(software|engineer|tester|testing|test|automation|sdet|trainee|analyst)\b", re.I)
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
    r"data scien|business intelligence|power bi|rpa|bubble|network engineer|systems engineer|hardware|blockchain|"
    r"machine learning scientist|research scientist|\.net|c\+\+|golang|rust|"
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


# Roles whose hardware, factory or trade expertise Adam does not have, whatever the title family.
INDUSTRIAL_TITLE_RE = re.compile(
    r"\b(nuclear|cnc|machine tools?|laser|welding|valves?|hvac|electrical|electronics|mechanical|"
    r"power engineering|aftermarket|vehicle components|packaging|pumps?|hydraulic|pneumatic|plc|"
    r"environmental|wastewater|oil (?:and|&) gas|chemical|steel|aerospace|construction|roofing|"
    r"bas|building automation|hvac controls|electrification|motion control|industrial automation)\b",
    re.I,
)
_INDUSTRIAL_DESC_RE = re.compile(
    r"\b(hvac|machin(?:e|es|ery|ing)|manufactur\w+|valves?|pumps?|mechanical|electrical|welding|cnc|plc|"
    r"fabricat\w+|oem|hydraulic|pneumatic|aerospace|nuclear|capital equipment|industrial|blueprints?|"
    r"schematics?|motors?|distributors?|contractors?)\b",
    re.I,
)
_SOFTWARE_DESC_RE = re.compile(
    r"\b(saas|software|platform|apis?|cloud|cybersecurity|integrations?|web|applications?|"
    r"machine learning|crm|erp|devops|sql|data)\b",
    re.I,
)
# A vertical he has no background in. The software is a side note; the domain is the job.
VERTICAL_TITLE_RE = re.compile(
    r"\b(payroll|ehr|emr|rcm|hcm|accounting|tax|workday|servicenow|netsuite|hubspot|paylocity|"
    r"viewpoint|pos)\b",
    re.I,
)
# Offensive-security work: a different field even when the title says Solutions Engineer.
PENTEST_TITLE_RE = re.compile(
    r"\b(?:penetration test\w*|pen[- ]?test\w*|red team\w*|offensive security|ethical hack\w*|vulnerability research\w*)\b",
    re.I,
)
# A remote seat tied to a sales territory: the hire has to live there. Midwest, central and the Southeast
# (his relocation tier is the Gulf Coast) are fine; Northeast, West, NM/CO and the like are not.
_REGION_RE = re.compile(
    r"\b(?:north ?east|east coast|west coast|west|east|south ?west|north ?west|pacific|mountain|"
    r"new england|mid[- ]atlantic|tri[- ]state|bay area)\b",
    re.I,
)
_REGION_OK_RE = re.compile(
    r"(?i:\b(?:mid ?west|central|great lakes|south ?east|south|gulf|minnesota|mississippi|national|nationwide|south ?west|texas|oklahoma|arkansas|louisiana|alabama|georgia|florida)\b)|\b(?:MN|MS|TX|OK|AR|LA|AL|GA|FL)\b"
)
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
_STATE_LIST_RE = re.compile(r"\b[A-Z]{2}(?:\s*[/,&]\s*[A-Z]{2})+\b")


# Home (Minnesota, Mississippi) and the states he has said he would relocate to.
HOME_STATES = frozenset({"MN", "MS"})
RELOCATION_STATES = frozenset({"TX", "OK", "AR", "LA", "AL", "GA", "FL"})


def territory_excludes_him(title):
    """True when the title names a sales territory that leaves out Minnesota and his relocation states."""
    t = title or ""
    named = title_states(t)
    if named and not (HOME_STATES | RELOCATION_STATES) & set(named):
        return True
    if _REGION_RE.search(t) and not _REGION_OK_RE.search(t):
        return True
    for m in _STATE_LIST_RE.finditer(t):
        codes = re.findall(r"[A-Z]{2}", m.group(0))
        if len(codes) >= 2 and all(c in _US_STATES for c in codes) and not (HOME_STATES | RELOCATION_STATES) & set(codes):
            return True
    return False


_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
_TITLE_STATE_RE = re.compile(
    r"(?:[,/|(–—-]|\bin)\s*(" + "|".join(sorted(_STATE_NAMES, key=len, reverse=True))
    + r")\b(?!\s+(?:tech|university|post|mutual|gas|health|hospital|bank|power|capital|life|state)\b)",
    re.I,
)


def title_states(title):
    """Two-letter codes for states a title names after a separator ("Solutions Engineer, Florida")."""
    return sorted({_STATE_NAMES[m.group(1).lower()] for m in _TITLE_STATE_RE.finditer(title or "")})


_STATE_TOKEN = r"(?:(?-i:[A-Z]{2})|" + "|".join(sorted(_STATE_NAMES, key=len, reverse=True)) + r")"
_STATE_LIST = _STATE_TOKEN + r"(?:\s*(?:/|,|&|and|or)\s*" + _STATE_TOKEN + r")*"
# "TX/OK/AR/LA only", "must reside in Texas, Oklahoma or Arkansas", "open to candidates located in ...".
_STATE_ONLY_RES = (
    re.compile(r"\b(" + _STATE_LIST + r")\s+(?:residents?\s+)?only\b", re.I),
    re.compile(
        r"(?:must|need to|required to|have to)\s+(?:currently\s+)?(?:reside|live|be (?:located|based|a resident))\s+(?:in|within)\s+(?:one of\s+)?(?:the\s+)?(?:following\s+)?(?:states?\s*(?:of\s+)?:?\s*)?("
        + _STATE_LIST + r")",
        re.I,
    ),
    re.compile(
        r"(?:open|available|restricted|limited|hiring)\s+(?:only\s+)?(?:to\s+(?:candidates|applicants)\s+)?(?:located\s+)?(?:in|to)\s+(?:the\s+)?(?:states? of\s+)?("
        + _STATE_LIST + r")\s*(?:only|\.|,|;|$)",
        re.I,
    ),
    re.compile(r"\bonly\s+(?:in|for|to)\s+(?:residents? of\s+)?(?:the\s+)?(?:states? of\s+)?(" + _STATE_LIST + r")", re.I),
)


def state_restriction(text):
    """Two-letter codes of the states a posting limits hires to ("TX/OK/AR/LA only"), or [].

    Only wording that restricts ("only", "must reside in", "restricted to") counts. A list of office
    states with no such cue is not a restriction.
    """
    for rx in _STATE_ONLY_RES:
        for m in rx.finditer(text or ""):
            codes = []
            for tok in re.split(r"\s*(?:/|,|&|\band\b|\bor\b)\s*", m.group(1).strip()):
                key = tok.lower()
                code = _STATE_NAMES.get(key) or (tok.upper() if len(tok) == 2 and tok.upper() in _US_STATES else "")
                if code and code not in codes:
                    codes.append(code)
            if codes:
                return codes
    return []


# Language or region he cannot cover.
FOREIGN_TITLE_RE = re.compile(
    r"\b(emea|apac|apj|latam|dach|anz|europe|uk|germany|german|french|spanish|japan\w*|korea\w*|"
    r"mandarin|cantonese|bilingual|multilingual|india)\b",
    re.I,
)
_FOREIGN_LOCATION_RE = re.compile(
    r"\b(emea|apac|latam|europe|united kingdom|uk|germany|france|india|canada|australia|singapore|"
    r"philippines|poland|brazil|mexico|ireland|netherlands|spain|israel|japan)\b",
    re.I,
)
_US_LOCATION_RE = re.compile(r"(?i:\b(?:united states|usa|u\.s\.|us)\b)|,\s*[A-Z]{2}\b")
INDUSTRIAL_COMPANY_RE = re.compile(
    r"\b(schneider electric|emerson|balluff|promach|envirogen|circor|rockwell|siemens|honeywell|"
    r"parker hannifin|bosch|eaton|abb|caterpillar|john deere|grainger|fastenal|carrier|trane|lennox|"
    r"johnson controls|nvent|hubbell|aaon)\b",
    re.I,
)
# A quota-carrying seat that sells complex products. His sales years carry over even when the product is hardware.
TECH_SALES_SEAT_RE = re.compile(
    r"\btechnical sales (?:representative|rep|trainee|specialist|executive|consultant|associate)\b",
    re.I,
)
# Not a full-time career seat, or not a selling or engineering seat.
NOT_A_CAREER_RE = re.compile(
    r"\b(part[- ]time|seasonal|temporary|1099|per hour|hourly|enablement)\b|/\s*hour",
    re.I,
)
# Roles outside the four he is aiming at. They are worth a look, never a top grade.
ADJACENT_RE = re.compile(
    r"\b(technical account|technical consultant|implementation|onboarding|customer success)\b",
    re.I,
)
# The technical versions of those seats can lead to pre-sales or engineering, so they stay on the list
# at the bottom of medium. The rest (implementation specialist, onboarding, customer success manager)
# are service roles he is not aiming at.
ADJACENT_TECH_RE = re.compile(
    r"\b(technical account|technical consultant|technical implementation|implementation (?:engineer|consultant|architect)|"
    r"customer success engineer|solutions? delivery)\b",
    re.I,
)


def industrial_domain(desc):
    """True when the posting is about factory or trade equipment, not software."""
    industrial = len(_INDUSTRIAL_DESC_RE.findall(desc or ""))
    software = len(_SOFTWARE_DESC_RE.findall(desc or ""))
    return industrial >= 6 and industrial > software


def foreign_only(location):
    """True when the card location names another country and no US place."""
    loc = location or ""
    return bool(_FOREIGN_LOCATION_RE.search(loc)) and not _US_LOCATION_RE.search(loc)


# Seats next to his targets that career changers move through: AI evaluation and training (his Mercor and
# Activus work), developer relations, technical support, integration, CRM/sales-ops tooling and API writing.
# They are worth a look, never a top grade, and only when the posting text can be read.
LANE_RE = re.compile(
    r"\b(?:ai|llm|search|model|content) (?:trainer|tutor|rater|evaluator|quality (?:rater|evaluator|analyst|specialist))|"
    r"\bai (?:training|evaluation|quality|data)\b.{0,20}\b(?:specialist|expert|analyst|contributor)|"
    r"\b(?:llm|ai|model) evaluat\w+|search quality (?:rater|evaluator|analyst)|"
    r"\bdata annotation (?:expert|specialist|analyst)|\bprompt (?:engineer|writer|specialist)|\bai generalist|"
    r"\bdeveloper (?:advocate|relations|evangelist)|\bdevrel\b|\btechnical evangelist|\bdeveloper experience engineer|"
    r"\b(?:technical|application|product|customer) support engineer|\bsupport engineer|"
    r"\bintegrations? (?:engineer|specialist|consultant|developer)|"
    r"\bsalesforce (?:admin\w*|engineer)|\bcrm admin\w*|\b(?:sales|revenue|go[- ]to[- ]market|gtm) (?:operations|ops) (?:analyst|specialist|engineer)|\brevops\b|"
    r"\btechnical (?:writer|documentation)|\bdocumentation engineer|\bapi (?:writer|documentation)",
    re.I,
)
# Crowd-work platforms and per-task pay: not a career seat even when the title says AI.
AI_WORK_FARM_RE = re.compile(
    r"\b(outlier|dataannotation|data annotation tech|remotasks?|appen|lionbridge|telus (?:digital|international)|"
    r"transperfect|welocalize|cloudfactory|clickworker|toloka|prolific|uber ai solutions)\b",
    re.I,
)
PIECE_RATE_RE = re.compile(r"\b(?:paid per (?:task|hit|annotation)|per[- ]task (?:pay|basis)|piece[- ]rate|work as much or as little)\b", re.I)


def role_family(title):
    t = title or ""
    if QA_RE.search(t):
        return "qa"
    if BRIDGE_RE.search(t):
        return "bridge"
    if LANE_RE.search(t):
        return "lane"
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
    r"kforce|apex systems|cybercoders|higher-people|supersourcing|true talent|my recruiter|"
    r"tds global|human resource|wfhquillen|dev10|geniusee)\b",
    re.I,
)
# Posting text that rules a job out, however well the title fits.
UNPAID_RE = re.compile(
    r"/\s*unpaid\b|\bunpaid\s+(?:trainee|training|intern\w*|position|role|program|opportunity|basis)\b|"
    r"\bvolunteer (?:position|role|basis)\b|\bno (?:pay|compensation)\b",
    re.I,
)
COMMISSION_ONLY_RE = re.compile(
    r"\b(?:commission[- ]only|100\s?% commission|commission[- ]based only|straight commission|"
    r"no base (?:salary|pay)|without a base (?:salary|pay)|unlimited earning potential,? no base)\b",
    re.I,
)
_LANGS = (
    r"turkish|spanish|french|german|portuguese|italian|dutch|japanese|korean|mandarin|cantonese|chinese|"
    r"hindi|arabic|russian|ukrainian|polish|hebrew"
)
_LANG_RE = re.compile(
    rf"\b(?:fluen(?:t|cy)|native|proficien(?:t|cy)|bilingual)\b[^.\n]{{0,40}}\b(?:{_LANGS})\b|"
    rf"\b(?:{_LANGS})\b[^.\n]{{0,30}}\b(?:fluen(?:t|cy)|native speaker|required)\b",
    re.I,
)
_LANG_SOFT_RE = re.compile(r"\b(plus|asset|bonus|preferred|nice to have|advantage|desirable|e-verify|eeo)\b", re.I)


class _LanguageRequired:
    """A language other than English is required, not 'a plus' or equal-opportunity boilerplate."""

    @staticmethod
    def search(text):
        text = text or ""
        for m in _LANG_RE.finditer(text):
            if not _LANG_SOFT_RE.search(text[max(0, m.start() - 80):m.end() + 80]):
                return m
        return None


LANGUAGE_REQUIRED_RE = _LanguageRequired()
_NON_LATIN_RE = re.compile(r"[Ѐ-ӿ֐-ۿ぀-ヿ一-鿿가-힯]")
# Resume mills, pay-to-train schemes and shells that post as employers.
MILL_COMPANY_RE = re.compile(
    r"\b(lifelancer|skillhat|info resume edge|ace it careers|sedorh|inoutsource|work force nexus|"
    r"silicon valley insight|synergisticit)\b",
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
    r"(?:years?|yrs?\.?)(?!ly)(?:['’]s?)?(?:\s+of)?\s*(?=([^.;\n]{0,90}))",
    re.I,
)
_NUMBER_WORDS = {
    w: i for i, w in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split(), 1)
}
_WORD = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_SPELLED_PAREN_RE = re.compile(rf"\b(?:{_WORD})\b\s*\((\d{{1,2}})\)", re.I)  # "Six (6) years"
_SPELLED_RANGE_RE = re.compile(
    rf"\b({_WORD})\b\s*(?:-|–|to)\s*\b({_WORD})\b(?=\s*\+?\s*(?:years?|yrs?)\b)", re.I)  # "three to five years"
_SPELLED_YEARS_RE = re.compile(rf"\b({_WORD})\b(?=\s*\+?\s*(?:years?|yrs?)\b)", re.I)  # "six years", "six+ years"


def _numerals(text):
    """`text` with spelled-out year counts as digits, so _YEARS_RE reads them.

    "Six (6) years" -> "6 years", "three to five years" -> "3-5 years", "five+ years" -> "5+ years".
    Number words that are not followed by years are left alone.
    """
    text = _SPELLED_PAREN_RE.sub(r"\1", text or "")
    text = _SPELLED_RANGE_RE.sub(lambda m: f"{_NUMBER_WORDS[m.group(1).lower()]}-{_NUMBER_WORDS[m.group(2).lower()]}", text)
    return _SPELLED_YEARS_RE.sub(lambda m: str(_NUMBER_WORDS[m.group(1).lower()]), text)


_PREFERRED_RE = re.compile(
    r"\b(preferred|nice to have|a plus|bonus|ideal(?:ly)?|desired|helpful|advantageous)\b",
    re.I,
)
_IDEAL_DOMAIN_RE = re.compile(r"\bideal(?:ly)?\s+(?:with|in|within|from|using|at|across|on|for)\b", re.I)
# Equal-opportunity boilerplate ("candidates from non-traditional backgrounds are encouraged to apply")
# says nothing about the level of the role, so those sentences are ignored for entry wording.
_EEO_SENTENCE_RE = re.compile(
    r"[^.!?\n]*\b(?:underrepresented|equal[- ]opportunity|diverse|diversity|inclusi\w+|encourage\w*[^.!?\n]{0,40}\bapply)\b[^.!?\n]*[.!?]?",
    re.I,
)
_ENTRY_TEXT_RE = re.compile(
    r"\b(entry[- ]level|early[- ]career|new grad(?:uate)?s?|recent graduates?|"
    r"no (?:prior |previous )?(?:professional )?(?:experience|degree)(?: is)? (?:required|necessary|needed)|"
    r"0\s*(?:-|–|to)\s*[1-3]\s*years?|"
    r"will train|training provided|career changers?|career[- ]switchers?|"
    r"non[- ]traditional (?:background|path|candidate|applicant)s?|"
    r"self[- ]taught (?:engineers?|developers?|programmers?|coders?|candidates?)|(?:hire|hiring) for potential|"
    r"returnships?|(?:sdrs?|bdrs?) (?:to|into) (?:se|sales engineer\w*|solutions engineer\w*)|"
    r"(?:sales|solutions) engineering (?:development|apprenticeship|rotation|trainee) program|rotational program|"
    r"no degree (?:is )?required|no minimum (?:years|experience)|"
    r"boot ?camps?|coding camps?)\b",
    re.I,
)
CLEARANCE_RE = re.compile(
    r"\b(security clearance|active clearance|top secret|ts/sci|polygraph|"
    r"u\.?s\.? citizen(?:ship)? (?:is )?required)\b",
    re.I,
)

# --- Snippet gates ------------------------------------------------------------
# A posting that could not be read still leaves a snippet of a sentence or two (Jooble gives about
# 275 characters). These patterns fire only on plain wording in that snippet, and each one is
# skipped when the snippet also says remote or names a home or relocation place.
_SNIPPET_CLEARANCE_RE = re.compile(r"\b(?:secret|public trust|dod)\s+(?:security\s+)?clearance\b", re.I)
_REMOTE_WORD_RE = re.compile(r"\b(remote|work from home|wfh|telecommut\w*|hybrid|distributed team|anywhere in the (?:us|u\.s\.|united states))\b", re.I)
_ONSITE_RE = re.compile(
    r"/\s*on-?site|"  # no \b: Jooble glues the label to the next sentence ("/On-siteAs a ...")
    r"\bon-?site\s+(?:role|position|only|required|full[- ]time|five days|5 days)\b|"
    r"\b(?:this|the)\s+(?:role|position|job)\s+is\s+(?:fully\s+)?(?:on-?site|in[- ]office|in[- ]person)\b|"
    r"\bmust\s+(?:work|be)\s+(?:on-?site|in[- ]office|in the office)\b|"
    r"\b(?:5|five) days (?:a|per) week (?:on-?site|in (?:the )?office)\b",
    re.I,
)
# "in the Toledo/Detroit area", "the New York-New Jersey-Connecticut tri-state area".
_AREA_RE = re.compile(
    r"\bthe\s+((?:[A-Z][\w.'-]*(?:/|\s+)){1,5}?)(?:tri-state\s+)?area\b"
)
_AREA_NOT_A_PLACE_RE = re.compile(r"\b(?:Greater|Customer|Product|Technical|Service|Support|Success|Sales|Security|Cloud|Data|Same|Local|Global|Public|Private)\b")
_HOME_PLACE_RE = re.compile(
    r"\b(?:minnesota|mn|twin cities|minneapolis|st\.? paul|saint paul|edina|eden prairie|minnetonka|"
    r"bloomington|plymouth|maple grove|rogers|chanhassen|wayzata|hopkins|richfield|mississippi|gulfport|biloxi|"
    r"dallas|houston|austin|san antonio|fort worth|atlanta|miami|tampa|orlando|jacksonville|"
    r"new orleans|baton rouge|birmingham|huntsville|tulsa|oklahoma city|little rock|nashville)\b",
    re.I,
)
_TRAINING_FIRM_RE = re.compile(
    r"\b(?:positions?|roles?|opportunities)\s+with\s+(?:our\s+)?clients\b|"
    r"\bour\s+client\s+network\b|"
    r"\b(?:job\s+)?placement\s+(?:program|assistance|services|team)\b|"
    r"\bskill[- ]enhancement\s+program\b|"
    r"\bpay\s+(?:us\s+)?after\s+(?:you\s+)?(?:get\s+)?(?:placed|hired)\b",
    re.I,
)


def snippet_blocker(company, text):
    """One plain sentence for why an unread posting's snippet rules the job out, or ''."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    if CLEARANCE_RE.search(text) or _SNIPPET_CLEARANCE_RE.search(text):
        return "The snippet says the job needs a security clearance, which you do not hold."
    if _TRAINING_FIRM_RE.search(text) or MILL_COMPANY_RE.search(company or ""):
        return f"{company or 'The poster'} reads like a training or placement firm that hires for clients, not the employer."
    remote = _REMOTE_WORD_RE.search(text)
    home = _HOME_PLACE_RE.search(text)
    if not remote and not home:
        if _ONSITE_RE.search(text):
            return "The snippet says the job is on-site, and the card gives no remote or hybrid option."
        for m in _AREA_RE.finditer(text):
            place = m.group(1).strip()
            if not _AREA_NOT_A_PLACE_RE.search(place):
                return f"The snippet ties the job to the {place} area with no remote option, so a hire would have to live there."
    return ""


_SENIOR_TEXT_RE = re.compile(
    r"\b(senior[- ]level|(?:a|over a|more than a) decade of|half a decade|senior individual contributor|this is a senior (?:\w+ ){0,2}role|seasoned|proven track record leading|lead a team of engineers|"
    r"mentor (?:junior|other) engineers)\b",
    re.I,
)

# A degree line is exclusive when it names CS/engineering and offers no way out.
_DEGREE_FIELD_RE = re.compile(
    r"\b(?:bachelor(?:'?s)?|b\.?\s?s\.?\b|m\.?\s?s\.?\b|master(?:'?s)?|undergraduate|college|degree)\b"
    r".{0,60}\b(?:in|of)\s+(?:an?\s+)?(?:computer science|computer engineering|(?:[\w-]+\s+){0,2}engineering|"
    r"software development|computer information systems|information (?:technology|systems)|data science|"
    r"mathematics|cs\b)|"
    r"\b(?:bs|ms|b\.s\.?|m\.s\.?)\b(?:\s*/\s*(?:bs|ms|b\.s\.?|m\.s\.?))?\s+(?:in\s+)?"
    r"(?:computer science|computer engineering|(?:[\w-]+\s+){0,2}engineering|software development|"
    r"computer information systems|data science|cs)\b|"
    r"\b(?:(?:[\w-]+\s+){0,2}engineering|computer science|computer engineering|software development|"
    r"data science|technical)\s+degree\b",
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
    plain = re.sub(r"(?i)</?(?:p|div|br|li|ul|ol|h[1-6]|tr|section)\b[^>]*>", "\n", plain)
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
    return [(line, mode or bool(_PREFERRED_RE.search(_IDEAL_DOMAIN_RE.sub("", line)))) for line, mode in _sections_mode(text)]


def _sections_mode(text):
    """(line, under_a_preferred_heading) for each line. A preferred word inside the line is not counted here."""
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
        out.append((line, mode_pref))
    return out


def _clause_at(line, pos):
    """Text around position `pos` that decides whether a years figure is preferred or required.

    It is the text after `pos` (within its ';' clause, at most 100 characters, cut at the next
    sentence end), plus the 60 characters before it only when they end in a "Preferred:" heading.
    A "preferred" far later in a squashed run of text, or a "(Masters preferred)" that
    belongs to the degree before the figure, must not make the figure optional.
    """
    start = line.rfind(";", 0, pos) + 1
    end = line.find(";", pos)
    tail = line[pos:] if end < 0 else line[pos:end]
    tail = tail[:100]
    stop = tail.find(". ")
    if stop >= 0:
        tail = tail[:stop]
    head = line[max(start, pos - 60):pos]
    head_pref = re.search(r"(?:preferred|nice to have|bonus|desired)[^.;]{0,25}[:\-–]\s*$", head, re.I)
    return (head if head_pref else "") + tail


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


# Postings for a graduating class ("2025 or 2026 graduate", "class of 2026", "graduating in
# May 2026"). He graduated in 2019, so none of these is open to him. Years before 2022 are
# left alone: "graduated in 2019" in a line is not a class requirement.
_GRAD_YEAR = r"20(?:2[2-9]|30)"
_GRAD_YEAR_RE = re.compile(
    rf"\b{_GRAD_YEAR}\b(?:\s*(?:,|/|or|and|&)\s*{_GRAD_YEAR}\b)*\s+(?:graduat(?:e|es|ing)|grads?)\b|"
    rf"\bclass\s+of\s+{_GRAD_YEAR}\b|"
    rf"\bgraduat(?:e|es|ed|ing)\s+(?:in|by|between|before|on|from)\s+(?:\w+\s+)?{_GRAD_YEAR}\b|"
    rf"\bgraduation(?:\s+(?:date|year))?\s*(?:is|of|in|by|between|:)?\s*(?:\w+\s+)?{_GRAD_YEAR}\b",
    re.I,
)


def graduate_year_required(text):
    """The line that limits the role to a recent graduating class, or ''. Preferred-only lines
    do not count."""
    for line, preferred in sections(text):
        if not preferred and _GRAD_YEAR_RE.search(line):
            return line[:240]
    return ""


# Languages and tools he has not worked in. A required line naming one of these
# with none of his own stack beside it ("Java or Python") is a real requirement.
_LACKING_SKILL_RE = re.compile(
    r"\b(c#|\.net|c\+\+|golang|rust|kotlin|swift|scala|ruby|php|kubernetes|k8s|"
    r"terraform|ansible|hadoop|spark|snowflake|databricks|apex|sap|oracle|mainframe|"
    r"cobol|perl|matlab|unity|unreal|pytorch model training|cuda|splunk|datadog|kafka|elasticsearch|siem|"
    r"active directory|bgp|vmware|"
    r"ach|rtp|fedwire|wire transfers?|card networks?|payment rails)\b",
    re.I,
)
_OWN_SKILL_RE = re.compile(
    r"\b(python|java|javascript|typescript|react|node|next\.?js|fastapi|flask|sql|postgres(?:ql)?|mysql|html|css|"
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
    r"cka\b|ccna|ccnp|salesforce certified|itil(?: 4)?(?: foundation)? certif\w+|aws solutions architect[- ]associate|"
    r"nursing licen\w+|rn\b|"
    r"(?:state|professional|valid) licen[sc]e)\b",
    re.I,
)


_ANY_OF_RE = re.compile(r"\b(?:one or more|at least one|any (?:one )?of|one of)\b[^.]{0,30}\b(?:following|these|below)\b", re.I)
_NOT_A_REQUIREMENT_RE = re.compile(
    r"^\s*(professional development|learning|growth|benefits|perks)\b|"
    r"\b(opportunit\w+ to|you(?:'ll| will) (?:learn|grow|get to))\b",
    re.I,
)


def missing_skill(text):
    """First required line naming a language or tool you lack, or ''."""
    rows = sections(text)
    # "One or more of the following" lists: naming a language he lacks is fine when the same list
    # also names one he has (React, Node, SQL), because any one of them satisfies the posting.
    any_of_ok = [False] * len(rows)
    start = None
    for i, (line, _) in enumerate(rows + [("", False)]):
        header = (line.rstrip().endswith(":") or bool(_ANY_OF_RE.search(line))) if line else True
        if start is not None and (header or i == len(rows)):
            group = rows[start:i]
            if any(_OWN_SKILL_RE.search(g) for g, _ in group):
                for k in range(start, i):
                    any_of_ok[k] = True
            start = None
        if line and _ANY_OF_RE.search(line):
            start = i + 1
    for i, (line, preferred) in enumerate(rows):
        if preferred or any_of_ok[i] or len(line) > 400 or _NOT_A_REQUIREMENT_RE.search(line):
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
    for line, under_pref_heading in _sections_mode(_numerals(text)):
        for m in _YEARS_RE.finditer(line):
            # "6+ years of pre-sales required; SaaS selling strongly preferred" asks for 6: the
            # preferred word belongs to the clause after the semicolon, not to the years.
            # "5+ years of pre-sales, ideally with open source" asks for 5: "ideally with" narrows the domain.
            clause = _IDEAL_DOMAIN_RE.sub("", _clause_at(line, m.start()))
            preferred = under_pref_heading or bool(_PREFERRED_RE.search(clause))
            if preferred:
                n = int(m.group(1))
                if 0 < n <= 20:
                    pref = max(pref, n)
                continue
            n = int(m.group(1))
            if not 0 < n <= 20:
                continue
            ctx = m.group(3) or ""
            # Squashed text runs two requirements together; the second figure ends the first one's context.
            ctx = re.split(r"\d+\s*\+?\s*(?:years?|yrs?)", ctx, maxsplit=1)[0]
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


# --- Fit points ---------------------------------------------------------------
# The four dimensions of .claude/skills/job-application-assistant/04-job-evaluation.md,
# computed from the posting. Location is a gate (pipeline.cap_high_for_location), not points.
# Every number is a judgment call. job_scraper/FIT_SCALE.md says why, so it can be changed.
WEIGHTS = {"career": 30, "technical": 30, "experience": 25, "behavior": 15}
HIGH_AT = 70
MEDIUM_AT = 50
CAP_UNREAD = 64       # requirements unchecked: never above medium
CAP_ADJACENT = 59     # account management, implementation, onboarding, customer success
CAP_QA = 55
CAP_UNPROVEN = 69     # level, or openness to a sales background, is not shown in the posting
CAP_OFF_DOMAIN = 64   # hardware or trade product, or a staffing firm in the middle

# Roles he is aiming at.
CORE_SE_RE = re.compile(
    r"\b(?:solutions?|sales|pre[-\s]?sales|presales|customer|technical solutions?) engineer|"
    r"\bpre[-\s]?sales (?:consultant|specialist|architect)|\bsolutions? consultant|\bsales consultant",
    re.I,
)
# Technical delivery seats: forward deployed, architects, technical consultants. They reach high only
# when the posting itself shows it is open to someone without years of engineering.
FORWARD_DEPLOYED_RE = re.compile(r"forward deployed", re.I)
TECHNICAL_DELIVERY_RE = re.compile(
    r"architect|forward deployed|technical consultant|implementation manager", re.I
)

_AI_RE = re.compile(
    r"\b(llms?|large language models?|generative ai|gen ?ai|rag|agentic|ai agents?|ai[- ]powered|"
    r"ai[- ]native|machine learning|openai|anthropic|claude|gpt|copilot|ai)\b",
    re.I,
)
_DEV_PLATFORM_RE = re.compile(
    r"\b(apis?|sdks?|developers?|devops|observability|cloud|cyber ?security|security|identity|"
    r"data platform|infrastructure|integrations?|open[- ]source)\b",
    re.I,
)
_SAAS_RE = re.compile(r"\b(saas|software|platform|cloud)\b", re.I)
_PUBLIC_SAFETY_RE = re.compile(
    r"\b(public safety|911|law enforcement|first responders?|govtech|gov tech|public sector|state and local government|sled)\b", re.I)
_CLINICAL_SOFTWARE_RE = re.compile(r"\b(ehr|emr|clinical|patient|hipaa|payers?|providers?)\b", re.I)

# The stack he has shipped in, one group per idea so repeating a word earns nothing.
_STACK_GROUPS = (
    ("full-stack web work", re.compile(r"\b(full[- ]?stack|front[- ]?end|back[- ]?end|web applications?)\b", re.I)),
    ("Python", re.compile(r"\bpython\b", re.I)),
    ("Java", re.compile(r"\bjava\b", re.I)),
    ("TypeScript or JavaScript", re.compile(r"\b(typescript|javascript|node(?:\.?js)?)\b", re.I)),
    ("React or Next.js", re.compile(r"\b(react|next\.?js)\b", re.I)),
    ("FastAPI, Flask or Express", re.compile(r"\b(fastapi|flask|express)\b", re.I)),
    ("SQL or Postgres", re.compile(r"\b(sql|postgres(?:ql)?|mysql|firestore|supabase|prisma)\b", re.I)),
    ("Docker or CI/CD", re.compile(r"\b(docker|ci/?cd|github actions|containers?)\b", re.I)),
    ("REST or GraphQL APIs", re.compile(r"\b(rest(?:ful)?|graphql|webhooks?)\b", re.I)),
    ("LLM or RAG work", re.compile(r"\b(llms?|rag|prompt(?:s| engineering)?|ai agents?|generative ai|mcp|model context protocol)\b", re.I)),
    ("AWS, Azure or GCP", re.compile(r"\b(aws|azure|gcp|google cloud)\b", re.I)),
    ("OAuth, JWT or SSO", re.compile(r"\b(oauth|jwt|sso|rbac|authentication|authorization)\b", re.I)),
    ("test automation", re.compile(r"\b(playwright|vitest|jest|pytest|unit tests?)\b", re.I)),
    ("Firebase, Tailwind or Vercel", re.compile(r"\b(firebase|tailwind(?: css)?|vercel|cloudflare|vite)\b", re.I)),
    ("Redis or caching", re.compile(r"\b(redis|caching)\b", re.I)),
    ("AI coding tools", re.compile(
        r"\b(cursor|claude code|copilot|agentic (?:coding|workflows?|development)|vibe coding|ai[- ]assisted (?:coding|development))\b", re.I)),
    ("API tooling", re.compile(r"\b(postman|swagger|openapi|api documentation)\b", re.I)),
)
# What a sales or solutions engineer does all day, which his 100+ demos, RFPs and IT-buyer calls cover.
_PRESALES_GROUPS = (
    ("demos", re.compile(r"\b(demos?|demonstrations?)\b", re.I)),
    ("proofs of concept", re.compile(r"\b(proofs? of concept|pocs?|pilots?|trials?)\b", re.I)),
    ("technical discovery", re.compile(r"\b(technical discovery|discovery calls?|requirements gathering|solution design)\b", re.I)),
    ("RFPs and security questionnaires", re.compile(r"\b(rfps?|rfis?|security questionnaires?)\b", re.I)),
    ("integrations", re.compile(r"\b(integrations?|apis?)\b", re.I)),
)

# Work style comes from a personality self-assessment, kept out of this file on purpose. The scores and
# the reasoning are in job_scraper/FIT_SCALE.md. In short: competitive, autonomous, customer-facing
# work fits; caretaking and ticket-queue support, admin-heavy duties, pay risk, on-call and
# high-pressure cultures cost points. It is self-report, so work style stays at 15 of 100 points.
_BEHAVIOR_POSITIVE = (
    ("customer-facing, stakeholder-facing work", re.compile(
        r"\b(customer[- ]facing|client[- ]facing|stakeholders?|executive presentations?|"
        r"technical presentations?|translate technical|business and technical)\b", re.I)),
    ("competitive, deal-driven work", re.compile(
        r"\b(negotiat\w+|quota|competitive (?:deals?|evaluations?|displacement|situations?)|win rate|"
        r"close deals?|bake[- ]?offs?|win/loss)\b", re.I)),
    ("ownership and autonomy", re.compile(
        r"\b(end[- ]to[- ]end|ownership|own (?:the|your)|from idea to|autonomy|autonomous|flat (?:structure|organization))\b", re.I)),
    ("cross-functional work with technical and non-technical teams", re.compile(
        r"\b(cross[- ]functional|non[- ]technical|product and engineering)\b", re.I)),
    ("remote-first, written communication", re.compile(
        r"\b(remote[- ]first|async(?:hronous)?(?: communication| first| work)?|written communication|distributed team)\b", re.I)),
    ("generalist, early-stage work", re.compile(
        r"\b(generalist|wear many hats|series [ab]\b|seed[- ]stage|early[- ]stage)\b", re.I)),
    ("mentorship and a structured start", re.compile(
        r"\b(mentor(?:ship|ed|s)?|training program|onboarding program|apprentice\w*|rotational|"
        r"pair programming|30[- ]60[- ]90|career growth|learning (?:budget|stipend))\b", re.I)),
)
# (name, penalty, pattern)
_BEHAVIOR_FRICTION = (
    ("commission-only pay", 3, re.compile(r"\b(commission[- ]only|100% commission|1099)\b", re.I)),
    ("overnight or night-shift hours", 2, re.compile(r"\b(overnight|night shift|graveyard|midnight shift)\b", re.I)),
    ("heavy travel", 3, re.compile(r"\btravel[^.]{0,30}\b(?:50|60|70|75|80|100)\s?%", re.I)),
    ("on-call duty", 3, re.compile(r"\b(on[- ]call|pager ?duty)\b", re.I)),
    ("a high-pressure culture", 2, re.compile(
        r"\b(high[- ]pressure|high[- ]stress|sink or swim|up or out|work hard,? play hard|hustle culture|"
        r"aggressive (?:quotas?|targets?))\b", re.I)),
    ("caretaking, ticket-queue support work", 2, re.compile(
        r"\b(empath\w+|compassion\w*|nurtur\w+|help ?desk|ticket queue|tier [12] support|support tickets)\b", re.I)),
    ("rigid process or admin-heavy duties", 2, re.compile(
        r"\b(strict (?:adherence|compliance)|bureaucra\w+|chain of command|paperwork|documentation[- ]heavy|"
        r"administrative duties|compliance reporting)\b", re.I)),
    ("high-volume dialing", 2, re.compile(
        r"\b(\d+\+? (?:cold )?(?:calls|dials) (?:a|per) day|dial\w* all day|door[- ]to[- ]door)\b", re.I)),
)


_PART_NAMES = {"career": "role and market", "technical": "stack overlap", "experience": "experience asked", "behavior": "work style"}


def role_points(title, family, entry_title, entry_evidence):
    """(points out of 22, why). What the seat is, for a sales-to-engineering career changer."""
    if family == "qa":
        return 5, "QA is next to your target roles."
    if family == "lane":
        return 12, "A seat next to your target roles (AI evaluation, developer relations, support, integration or sales tooling) that you can grow from."
    if ADJACENT_RE.search(title):
        return 6, "Account management, implementation and onboarding sit next to your target roles."
    if family == "engineering":
        if entry_title:
            return 22, "The title is entry-level engineering, which is the engineering goal."
        if entry_evidence:
            return 20, "An engineering title, and the posting welcomes entry-level or career-change people."
        return 8, "An engineering title with no level stated."
    if CORE_SE_RE.search(title):
        return 22, "Sales and solutions engineering is a target role. Demos, RFPs and IT buyers are your background."
    if TECH_SALES_SEAT_RE.search(title):
        return 20, "Technical sales is a target role. You have just under five years selling technical products."
    if TECHNICAL_DELIVERY_RE.search(title):
        if entry_title or entry_evidence:
            return 20, "A technical delivery seat, and the posting is open to entry-level or career-change people."
        return 14, "A technical delivery seat that usually wants engineering years."
    return 16, "A pre-sales or technical-sales seat."


def domain_points(title, desc, industrial):
    """(points out of 8, why). The product and market, scored against what he has built and sold."""
    if industrial:
        return 0, ""
    text = f"{title} {desc}"
    ai = len(_AI_RE.findall(text))
    options = [(3, "")]
    if _AI_RE.search(title) or ai >= 4:
        options.append((8, "The product is AI or LLM work, which is the direction you want."))
    # The exact market is not a priority for him, so any software or platform product scores the same.
    if len(_DEV_PLATFORM_RE.findall(text)) >= 5 or len(_SAAS_RE.findall(text)) >= 3:
        options.append((5, "The product is software."))
    if len(_CLINICAL_SOFTWARE_RE.findall(text)) >= 4:
        return 2, "The product is clinical software, a market you have not worked in."
    safety_terms = {m.lower() for m in _PUBLIC_SAFETY_RE.findall(text)}
    if (len(safety_terms) >= 2 or _PUBLIC_SAFETY_RE.search(title)) and (len(_SAAS_RE.findall(text)) >= 2 or _DEV_PLATFORM_RE.search(text)):
        options.append((6, "The product is public-safety or government software, a market you sold into at Citizen Observer."))
    return max(options, key=lambda o: o[0])


def technical_points(family, desc, title=""):
    """(points out of 30, matched stack names). Base for the seat plus overlap with what he has shipped."""
    text = f"{title} {desc}"
    base = 10
    hits = [name for name, rx in _STACK_GROUPS if rx.search(text)]
    if family == "bridge":
        hits += [name for name, rx in _PRESALES_GROUPS if rx.search(text)]
    return base + min(20, 3 * len(hits)), hits


def behavior_points(desc):
    """(points out of 15, positives, frictions)."""
    good = [name for name, rx in _BEHAVIOR_POSITIVE if rx.search(desc)]
    hit = [(name, cost) for name, cost, rx in _BEHAVIOR_FRICTION if rx.search(desc)]
    bad = [name for name, _ in hit]
    pts = 6 + 2 * len(good) + (2 if len(_AI_RE.findall(desc)) >= 4 else 0) - sum(cost for _, cost in hit)
    return max(0, min(15, pts)), good, bad


def se_years_required(text):
    """Largest required years of pre-sales or sales-engineering experience, which he does not have."""
    need = 0
    for line, preferred in sections(_numerals(text)):
        if preferred:
            continue
        for m in _YEARS_RE.finditer(line):
            n = int(m.group(1))
            if 0 < n <= 20 and _SE_CTX.search(m.group(3) or ""):
                need = max(need, n)
    return need


_SE_CTX = re.compile(
    r"\b(sales engineer\w*|solutions? engineer\w*|pre[- ]?sales|solutions? consult\w*|"
    r"solutions? architect\w*|technical sales|customer engineer\w*|forward deployed)\b",
    re.I,
)


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

    state = {"score": 0, "parts": None, "stretch_notes": []}

    def result(grade, entry=False, disqualified=False):
        if grade == _LOW:
            del reasons[:]
            if not state["parts"]:
                state["score"] = 0
        if grade == _LOW:
            minimums, note = "no", (blockers[-1] if blockers else "")
        elif not verified:
            minimums, note = "unchecked", "The posting text could not be read, so the stated minimums are unchecked."
        elif state["stretch_notes"]:
            minimums, note = "stretch", " ".join(state["stretch_notes"])
        else:
            minimums, note = "yes", "Nothing in the posting asks for more years, tools or credentials than you have."
        return {
            "minimums": minimums,
            "minimums_note": note,
            "disqualified": disqualified,
            "grade": grade,
            "score": state["score"],
            "parts": state["parts"],
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
    if family == "qa" and not SOFTWARE_QA_RE.search(title):
        blockers.append("The QA title does not say software testing, so it may be operations or compliance work.")
        return result(_LOW)
    if OFFSHORE_COMPANY_RE.search(company) or MILL_COMPANY_RE.search(company):
        blockers.append(f"{company} looks like an offshore firm, a resume mill, or a shell posting as an employer.")
        return result(_LOW)
    if FOREIGN_TITLE_RE.search(title):
        blockers.append("The title is for another region or needs a language you do not speak.")
        return result(_LOW)
    if foreign_only(job.get("location")):
        blockers.append(f"The card location is outside the United States: {job.get('location')}.")
        return result(_LOW)
    if PENTEST_TITLE_RE.search(title):
        blockers.append("The title is penetration testing or offensive security, a field you have not worked in.")
        return result(_LOW)
    if territory_excludes_him(title):
        blockers.append("The title is a sales territory (for example Northeast, West, NM/CO) that leaves out Minnesota and the Gulf Coast, so a remote hire would have to live there.")
        return result(_LOW)
    if job.get("body_status") == "gone":
        blockers.append("The posting page is gone or says the job is closed.")
        return result(_LOW)
    limited_to = state_restriction(job.get("description"))
    if limited_to and not (HOME_STATES | RELOCATION_STATES) & set(limited_to):
        blockers.append(f"The posting limits hires to {', '.join(limited_to)}, none of which are Minnesota or a state you would relocate to.")
        return result(_LOW)
    if NOT_A_CAREER_RE.search(title):
        blockers.append("The title is part-time, seasonal, hourly, or an enablement seat, not a full-time sales or engineering role.")
        return result(_LOW)
    if ADJACENT_RE.search(title) and not ADJACENT_TECH_RE.search(title):
        blockers.append("The title is an implementation, onboarding, or customer-success service seat, not a role you are aiming at.")
        return result(_LOW)
    industrial = bool(INDUSTRIAL_TITLE_RE.search(title) or INDUSTRIAL_COMPANY_RE.search(company))
    tech_sales_seat = bool(TECH_SALES_SEAT_RE.search(title))
    industrial_note = "The product is hardware or trade equipment: your sales years carry over, the technical depth may not."
    if industrial and not tech_sales_seat:
        blockers.append("The role is engineering sales for hardware, factory, or trade equipment, which needs expertise you do not have.")
        return result(_LOW)
    if VERTICAL_TITLE_RE.search(title):
        blockers.append("The title is for a payroll, accounting, health-records, or platform-admin specialty you have not worked in.")
        return result(_LOW)
    if MANAGER_RE.search(title) and not ACCOUNT_MANAGER_RE.search(title):
        blockers.append("The title is a manager role.")
        return result(_LOW)

    senior = bool(SENIOR_RE.search(title)) and not ENTRY_TITLE_RE.search(title)
    if senior:
        blockers.append("The title is senior, lead, director, or a higher level than entry.")
        return result(_LOW)

    entry_title = bool(ENTRY_TITLE_RE.search(title))
    adjacent = family in ("qa", "lane") or bool(ADJACENT_RE.search(title))
    if family == "lane" and AI_WORK_FARM_RE.search(company):
        blockers.append(f"{company} is a crowd-work platform, not a career seat.")
        return result(_LOW)
    technical_bridge = family == "bridge" and bool(TECHNICAL_DELIVERY_RE.search(title))
    staffing = bool(STAFFING_COMPANY_RE.search(company))
    if staffing:
        blockers.append(f"{company} looks like a staffing firm, not the hiring company.")

    # -- posting text -------------------------------------------------------
    if not verified:
        # The snippet can still rule a job out in plain words (clearance, on-site, a fixed territory).
        why = snippet_blocker(company, desc)
        if why:
            blockers.append(why)
            return result(_LOW)
        # The title fits a role he is aiming at; the page just did not load. That is an unknown,
        # not a poor fit, so it stays on the list as medium until a later run reads the text.
        blockers.append("The posting text could not be read, so the requirements are unchecked.")
        if adjacent or FORWARD_DEPLOYED_RE.search(title):
            blockers.append("It also sits next to the roles you are aiming at, or needs the posting to prove it is open to your background, so an unread posting is not worth listing.")
            return result(_LOW)
        if industrial:
            blockers.append(industrial_note)
        role, _ = role_points(title, family, entry_title, False)
        tech, _ = technical_points(family, "", title)
        dom, _ = domain_points(title, "", industrial)
        beh, _, _ = behavior_points("")
        parts = {"career": role + dom, "technical": tech, "experience": 18, "behavior": beh}
        state["score"] = min(sum(parts.values()), CAP_UNREAD)
        state["parts"] = parts
        return result(_MED)

    if not industrial and industrial_domain(desc):
        industrial = True
        if not tech_sales_seat:
            blockers.append("The posting is about factory or trade equipment more than software.")
            return result(_LOW)

    if COMMISSION_ONLY_RE.search(desc):
        blockers.append("The pay is commission only, with no base salary.")
        return result(_LOW, disqualified=True)
    if family == "lane" and PIECE_RATE_RE.search(desc):
        blockers.append("The pay is per task, which is gig work, not a career seat.")
        return result(_LOW, disqualified=True)
    if UNPAID_RE.search(desc):
        blockers.append("The posting describes an unpaid role.")
        return result(_LOW, disqualified=True)
    if LANGUAGE_REQUIRED_RE.search(desc):
        blockers.append("The posting requires fluency in a language other than English.")
        return result(_LOW, disqualified=True)
    if len(_NON_LATIN_RE.findall(desc)) >= 30:
        blockers.append("The posting is written in another language, so it is not a U.S. hire.")
        return result(_LOW, disqualified=True)

    degree = exclusive_degree(desc)
    if degree:
        blockers.append(f"It requires a degree you do not have, with no experience alternative: \"{degree[:140]}\"")
        return result(_LOW, disqualified=True)

    grad_class = graduate_year_required(desc)
    if grad_class:
        blockers.append(f"It is limited to a recent graduating class, and you graduated in 2019: \"{grad_class[:140]}\"")
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

    if _SENIOR_TEXT_RE.search(desc):
        blockers.append("The posting describes a senior-level role.")
        return result(_LOW, disqualified=True)

    entry_evidence = bool(_ENTRY_TEXT_RE.search(_EEO_SENTENCE_RE.sub(" ", desc)))
    if entry_evidence:
        reasons.append("The posting welcomes entry-level, career-change, bootcamp, or equivalent experience.")

    # -- experience: 25 points, minus what the posting asks for beyond his history ----------
    exp = WEIGHTS["experience"]
    sales_y, tech_y, other_y, pref_y = years_required(desc)
    if technical_bridge:   # "5+ years" on a forward-deployed or architect seat means engineering years
        tech_y, other_y = max(tech_y, other_y), 0
    stretch = False        # the posting asks for more than he has: worth a look, never high
    need = 0
    if family in ("engineering", "qa"):
        need = max(tech_y, other_y)
        if need > MAX_ENTRY_TECH_YEARS + 1:   # 4+
            blockers.append(f"It requires {need}+ years of engineering experience; you have about {TECH_YEARS}.")
            return result(_LOW, disqualified=True)
        if need == MAX_ENTRY_TECH_YEARS + 1:  # 3
            blockers.append(f"It requires {need}+ years of engineering experience; you have about {TECH_YEARS}.")
            if not entry_evidence:
                return result(_LOW, disqualified=True)
            exp -= 16
            stretch = True
        elif need == MAX_ENTRY_TECH_YEARS:   # 2
            blockers.append("It asks for 2 years of engineering experience, one more than your projects.")
            exp -= 6
            stretch = True
        elif need:
            reasons.append(f"It asks for {need} year of experience or less.")
            exp -= 2
        if family == "engineering" and not entry_title and not entry_evidence:
            if need not in (1, MAX_ENTRY_TECH_YEARS):
                blockers.append("Nothing in the title or posting says this is open to entry-level or career-change candidates.")
                return result(_LOW)
            blockers.append("The title does not say junior or entry, so the level is unknown.")
            exp -= 3
    else:  # bridge
        if tech_y >= 3:
            blockers.append(f"It requires {tech_y}+ years in a technical or engineering role; you have about {TECH_YEARS}.")
            return result(_LOW, disqualified=True)
        if tech_y == 2:
            blockers.append("It asks for 2 years in a technical role, one more than your projects.")
            exp -= 8
            stretch = True
        se_need = se_years_required(desc)
        if se_need >= 5:
            blockers.append(f"It requires {se_need}+ years of pre-sales or sales engineering, a seat you have not held.")
            return result(_LOW, disqualified=True)
        if se_need >= 3:
            blockers.append(f"It asks for {se_need}+ years of pre-sales or sales engineering. You have demoed and sold, but not held that seat.")
            exp -= 10
            stretch = True
        elif se_need == 2:
            blockers.append("It asks for 2 years of pre-sales or sales engineering.")
            exp -= 5
            stretch = True
        sales_need = max(sales_y, other_y)
        if sales_need >= SALES_YEARS + 2:     # 6+: not qualified
            blockers.append(f"It requires {sales_need}+ years of experience; you have about {SALES_YEARS} in sales.")
            return result(_LOW, disqualified=True)
        if sales_need == SALES_YEARS + 1:     # 5+: he does not have five years (the dates say about 4.8)
            blockers.append(f"It asks for {sales_need}+ years; you have under {sales_need} in sales, so it is a stretch.")
            exp -= 6
            stretch = True
        elif sales_need:
            reasons.append(f"It asks for {sales_need} year{'s' if sales_need != 1 else ''}, inside your sales history.")

    if stretch:
        state["stretch_notes"] = [b for b in blockers if "year" in b.lower() and ("asks" in b.lower() or "requires" in b.lower())]

    have = SALES_YEARS if family == "bridge" else TECH_YEARS
    if pref_y > have:
        reasons.append(f"{pref_y}+ years is listed as preferred, not required.")
        exp -= 2
    if entry_evidence:
        exp += 4
    if staffing:
        exp -= 6
    exp = max(0, min(WEIGHTS["experience"], exp))

    # -- the other three dimensions ----------------------------------------
    role, role_why = role_points(title, family, entry_title, entry_evidence)
    dom, dom_why = domain_points(title, desc, industrial)
    tech, stack = technical_points(family, desc, title)
    beh, good, bad = behavior_points(desc)
    parts = {"career": min(WEIGHTS["career"], role + dom), "technical": min(WEIGHTS["technical"], tech),
             "experience": exp, "behavior": beh}
    raw = sum(parts.values())

    # -- ceilings: a seat can score well and still not earn high ----------------
    cap, cap_why = 100, ""
    if adjacent:
        cap = CAP_QA if family == "qa" else CAP_ADJACENT
        cap_why = "This sits next to the roles you are aiming at, so it tops out at medium."
    if industrial:
        cap = min(cap, CAP_OFF_DOMAIN)
        cap_why = cap_why or industrial_note
    if staffing:
        cap = min(cap, CAP_OFF_DOMAIN)
    unproven = not entry_title and not entry_evidence
    if stretch:
        cap = min(cap, CAP_UNPROVEN)
    if family == "engineering" and unproven:
        cap = min(cap, CAP_UNPROVEN)
    if technical_bridge and unproven:
        cap = min(cap, CAP_UNPROVEN)
        blockers.append("This title is mostly technical delivery; it needs proof it is open to sales backgrounds.")
    total = min(raw, cap)
    if cap_why and raw >= HIGH_AT:
        blockers.append(cap_why)
    elif industrial:
        blockers.append(industrial_note)

    state["score"], state["parts"] = total, parts
    if total < MEDIUM_AT:
        weakest = sorted(parts, key=lambda k: parts[k] / WEIGHTS[k])[:2]
        blockers.append(
            f"The fit score is {total}/100, under the {MEDIUM_AT} that earns a place on the list. "
            f"Weakest areas: {' and '.join(_PART_NAMES[k] for k in weakest)}."
        )
        return result(_LOW, entry_evidence)

    reasons.insert(0, role_why)
    if dom_why:
        reasons.append(dom_why)
    if len(stack) >= 2:
        reasons.append(f"The posting overlaps your stack and work: {', '.join(stack[:5])}.")
    if good:
        reasons.append(f"The work style fits: {good[0]}.")
    if bad:
        blockers.append(f"The posting mentions {bad[0]}.")
    grade = _HIGH if total >= HIGH_AT else _MED
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
    job["fit_score"] = out["score"]
    job["fit_parts"] = out["parts"]
    job["entry_evidence"] = out["entry_evidence"]
    job["verified"] = out["verified"]
    job["minimums"] = out["minimums"]
    job["minimums_note"] = out["minimums_note"]
    job["disqualified"] = out["disqualified"]
    return job
