"""Browser layer for the apply agent: read an application form, fill it, submit it.

Supports the three hosted forms that need no account: Greenhouse, Lever and Ashby. All three are read
the same way, from the page's own labels and inputs, so one reader covers them. No AI is involved here.

`discover` only reads. Nothing is typed, uploaded or clicked. `fill` types the approved answers.
`submit` is a separate call that the caller makes only after Adam approved on Telegram.
"""
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

ATS_HOSTS = {
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"),
    "lever": ("jobs.lever.co", "jobs.eu.lever.co"),
    "ashby": ("jobs.ashbyhq.com",),
}
TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|gh_jid|lever-source.*|ref|source|src)$", re.I)
NAV_TIMEOUT_MS = 45000
SUBMIT_WAIT_MS = 25000

# Phrases on the page that mean a person has to do this one.
BLOCKER_TEXT = (
    (r"verify (that )?you('| a)re (a )?human|are you a robot|complete the captcha|solve the captcha", "CAPTCHA"),
    (r"(security|verification|confirmation|one[- ]time|access|login|sign[- ]in) code|passcode|enter the (\d+[- ](digit|character) )?code|"
     r"code (we|that we|was|has been) (sent|emailed)|we (sent|emailed) (you )?a (\d+[- ](digit|character) )?code", "email verification code"),
    (r"sign in to (apply|continue)|log ?in to (apply|continue)|create an account to apply", "account or login required"),
)
BLOCKER_SELECTORS = (
    ("iframe[src*='recaptcha/api2/anchor']:visible, iframe[src*='hcaptcha.com/captcha']:visible, "
     "iframe[src*='challenges.cloudflare.com']:visible, .h-captcha:visible, .cf-turnstile:visible", "CAPTCHA"),
    # Lever draws the hCaptcha only when Submit is pressed, but its container and response field are on the page from the start.
    (".h-captcha[data-sitekey], input[name='h-captcha-response']", "hCaptcha on the form"),
    ("iframe[src*='recaptcha'][src*='bframe']:visible", "CAPTCHA"),  # reCAPTCHA (also Enterprise, which Ashby uses): the image challenge
    ("input[type=password]:visible", "account or login required"),
)
SUCCESS_URL = re.compile(r"/confirmation|/thanks|/thank-you|/submitted|application_submitted", re.I)
SUCCESS_TEXT = re.compile(
    r"thank you for (applying|your application|your interest)|application (has been |was )?(successfully )?"
    r"(submitted|received)|we('ve| have) received your application|your application (has been |was )?(sent|submitted)",
    re.I,
)

# Runs in the page. Returns every visible input a person would fill, one record per question.
DISCOVER_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && (r.width > 0 || r.height > 0);
  };
  const textOf = (id) => { const n = id && document.getElementById(id); return n ? clean(n.innerText || n.textContent) : ''; };
  // A label's own words, without the select options or input text nested inside it.
  const ownText = (node) => {
    const c = node.cloneNode(true);
    c.querySelectorAll('select, option, input, textarea, button, svg').forEach(n => n.remove());
    return clean(c.innerText || c.textContent);
  };
  // The question printed near a control. Climbs only while the box holds this one question,
  // so it can never pick up the label of a neighbouring field.
  const questionText = (el, skipOptionLabels) => {
    let box = el.parentElement;
    for (let n = 0; box && n < 7; n++, box = box.parentElement) {
      const others = Array.from(box.querySelectorAll('input:not([type=hidden]), select, textarea'))
        .filter(c => c !== el && !(c.name && c.name === el.name));
      if (others.length) break;
      const cands = box.querySelectorAll('legend, label, h2, h3, h4, [class*="label"], [class*="Label"], [class*="title"], [class*="Title"]');
      for (const l of cands) {
        const holdsInput = l.querySelector('input[type=radio], input[type=checkbox]');
        const target = l.htmlFor && document.getElementById(l.htmlFor);
        if (skipOptionLabels && target && ['radio', 'checkbox'].includes(target.type)) continue;  // the label of one option, not the question
        if (skipOptionLabels && (holdsInput || l.contains(el))) continue;
        const t = l.contains(el) ? ownText(l) : clean(l.innerText);
        if (t) return t;
      }
    }
    return '';
  };
  const GENERIC_LABEL = /^(search|select|select\.\.\.|type to search|choose|please select|please select\.\.\.)$/i;
  const MACHINE_LABEL = /^(section_\d+_question_\d+|field-\d+)$/i;
  // Breezy names a question section_123_question_0 and puts the words in an h3. Rippling's custom
  // questions have no label, only a sibling heading, and their menus say aria-label "Select".
  const nearbyHeading = (el) => {
    let box = el.parentElement;
    for (let n = 0; box && n < 6; n++, box = box.parentElement) {
      for (const c of box.children) {
        if (c === el || c.contains(el)) continue;
        const t = clean(c.innerText);
        if (t && t.length < 240 && !GENERIC_LABEL.test(t)) return t;
      }
    }
    return '';
  };
  const labelOf = (el) => {
    let t = '';
    const by = el.getAttribute('aria-labelledby');
    if (by) t = clean(by.split(' ').map(textOf).join(' '));
    if (!t && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = ownText(l); }
    if (!t) { const l = el.closest('label'); if (l) t = ownText(l); }
    if (!t) t = clean(el.getAttribute('aria-label'));
    if (!t) t = questionText(el, false);
    t = t || clean(el.getAttribute('placeholder')) || el.name || el.id || '';
    // A hashed id or "Select" is not the question. An option whose name is its own words ("my first startup") is.
    const bareId = (t === (el.name || '') || t === (el.id || '')) && !/\s/.test(t);
    if (!t || GENERIC_LABEL.test(t) || MACHINE_LABEL.test(t) || bareId) {
      t = nearbyHeading(el) || t;
    }
    return t;
  };
  const GENERIC = /^(attach|upload|browse|choose file|select file|add file|drop.*|or drag.*)$/i;
  const fileLabel = (el) => {
    let t = labelOf(el);
    if (!GENERIC.test(t)) return t;
    let box = el.parentElement;
    for (let n = 0; box && n < 5; n++, box = box.parentElement) {
      const l = box.querySelector('label, legend, [class*="label"], [class*="Label"], h3, h4');
      if (l && !GENERIC.test(clean(l.innerText)) && clean(l.innerText)) return clean(l.innerText);
    }
    return el.id || el.name || t;
  };
  const groupLabel = (el) => {
    const fs = el.closest('fieldset');
    const lg = fs && fs.querySelector('legend');
    if (lg && clean(lg.innerText)) return clean(lg.innerText);
    return questionText(el, true) || labelOf(el);
  };
  // The question above a set of options, skipping labels that wrap one option.
  const headingOf = (box) => {
    const heads = box.querySelectorAll('label, legend, [class*="question-title"], [class*="Label"], [class*="title"]');
    for (const h of heads) {
      if (h.querySelector('input, select, textarea')) continue;
      const t = clean(h.innerText || h.textContent);
      if (t) return t;
    }
    return '';
  };
  // Checkboxes that are one multi-select even when each option has its own name (Ashby does this).
  // Stops at the first ancestor that also holds a text box, so two consent boxes on a form stay separate.
  const checkGroup = (el) => {
    let box = el.parentElement, found = null;
    for (let n = 0; box && n < 7; n++, box = box.parentElement) {
      const inputs = Array.from(box.querySelectorAll('input:not([type=hidden]), select, textarea'));
      if (inputs.some(c => !['radio', 'checkbox'].includes((c.getAttribute('type') || '').toLowerCase()))) break;
      const same = inputs.filter(c => (c.getAttribute('type') || '').toLowerCase() === 'checkbox');
      if (same.length > 1) found = box;
    }
    return found;
  };
  const markedRequired = (el) => {
    const box = el.closest('fieldset, [data-field-path], [class*="fieldEntry"]');
    return !!(box && box.querySelector('label[class*="required" i], legend[class*="required" i]'));
  };
  const requiredOf = (el, label) =>
    el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) || /\brequired\b/i.test(label) || markedRequired(el);

  const out = [];
  const seenGroups = new Map();
  const scope = document.querySelector('form') || document.body;
  const els = scope.querySelectorAll('input, textarea, select, [role=combobox]');
  els.forEach((el, i) => {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    if (el.tagName === 'INPUT' && el.getAttribute('role') === 'combobox' && el.closest('[role=combobox]') && el.closest('[role=combobox]') !== el) return;
    const styled = (type === 'radio' || type === 'checkbox') && ((el.labels && Array.from(el.labels).some(visible)) || (el.closest('label') && visible(el.closest('label'))));  // a custom-drawn radio hides the real input behind its label
    if (type !== 'file' && !visible(el) && !styled) return;
    if (el.closest('[aria-hidden=true]') && type !== 'file') return;
    el.setAttribute('data-apply-idx', String(i));
    const sel = '[data-apply-idx="' + i + '"]';
    if (type === 'radio' || type === 'checkbox') {
      const name = el.name || '';
      const optLabel = labelOf(el);
      const grouped = type === 'checkbox' ? checkGroup(el) : null;
      const heading = grouped ? headingOf(grouped) : '';
      const key = heading ? (type + ':group:' + heading) : (type + ':' + (name || groupLabel(el)));
      let g = seenGroups.get(key);
      if (!g) {
        const q = heading || groupLabel(el);
        const req = requiredOf(el, q) || !!(grouped && grouped.querySelector('label[class*="required" i], legend[class*="required" i]'));
        g = { kind: type, label: q, name, dom_id: el.id, required: req, options: [], selectors: [], multi: type === 'checkbox' };
        seenGroups.set(key, g); out.push(g);
      }
      g.options.push(optLabel); g.selectors.push(sel);
      if (heading) g.label = heading;
      else if (g.options.length === 1 && type === 'checkbox') g.label = optLabel;  // a lone consent box is its own question
      else if (g.options.length === 2 && type === 'checkbox' && g.label === g.options[0]) g.label = groupLabel(el);
      return;
    }
    const label = type === 'file' ? fileLabel(el) : labelOf(el);
    let kind = 'text', options = [];
    if (el.tagName === 'TEXTAREA') kind = 'textarea';
    else if (el.tagName === 'SELECT') { kind = 'select'; options = Array.from(el.options).map(o => clean(o.textContent)).filter(Boolean); }
    else if (type === 'file') kind = 'file';
    else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox' || el.getAttribute('aria-autocomplete')) kind = 'combobox';
    else if (['email', 'tel', 'url', 'number', 'date'].includes(type)) kind = type;
    const around = (el.className || '') + ' ' + (el.getAttribute('placeholder') || '') + ' ' +
      ((el.closest('.react-datepicker-wrapper, [class*="datepicker"]') || {}).className || '');
    if (kind === 'text' && /pick date|input-date|datepicker/i.test(around)) kind = 'date';
    out.push({ kind, label, name: el.name || '', dom_id: el.id || '', required: requiredOf(el, label), options, selectors: [sel], multi: false,
               accept: el.getAttribute('accept') || '' });
  });
  scope.querySelectorAll('[class*="yesno" i]').forEach((box) => {
    const btns = Array.from(box.querySelectorAll('button')).filter(visible);
    if (btns.length < 2) return;
    const entry = box.closest('[data-field-path], fieldset') || box.parentElement;
    const lab = entry && entry.querySelector('label, legend');
    const q = lab ? clean(lab.innerText) : '';
    const sels = btns.map((b, k) => { const id = 'yn' + out.length + '_' + k; b.setAttribute('data-apply-idx', id); return '[data-apply-idx="' + id + '"]'; });
    out.push({ kind: 'radio', label: q, name: '', dom_id: '', required: markedRequired(box) || /\*\s*$/.test(q),
               options: btns.map((b) => clean(b.innerText)), selectors: sels, multi: false, accept: '' });
  });
  const title = clean((document.querySelector('h1') || {}).innerText);
  return { title, fields: out };
}
"""


@dataclass
class Field:
    kind: str
    label: str
    name: str = ""
    dom_id: str = ""
    required: bool = False
    options: list = field(default_factory=list)
    selectors: list = field(default_factory=list)
    multi: bool = False
    accept: str = ""
    key: str = ""  # normalized question text, made unique within the form by `discover`


@dataclass
class Form:
    ats: str
    url: str
    title: str = ""
    fields: list = field(default_factory=list)
    blocker: str = ""


class FormError(Exception):
    pass


def normalize_label(text):
    """Stable key for a question: lowercase words only, no '*' or '(required)' marks."""
    text = re.sub(r"\(?\brequired\b\)?", " ", (text or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", text))


def detect_ats(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    for ats, hosts in ATS_HOSTS.items():
        if host in hosts:
            return ats
    return ""


def clean_url(url):
    """The posting URL without the tracking parameters the scraper's sources add."""
    parts = urllib.parse.urlparse(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)]
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(query), fragment=""))


def application_url(url):
    """Where the form lives. Lever and Ashby put it on a sub-page; Greenhouse has it on the posting."""
    url = clean_url(url)
    ats = detect_ats(url)
    parts = urllib.parse.urlparse(url)
    path = parts.path.rstrip("/")
    if ats == "lever" and not path.endswith("/apply"):
        path += "/apply"
    if ats == "ashby" and not path.endswith("/application"):
        path += "/application"
    return urllib.parse.urlunparse(parts._replace(path=path))


def blocker_on_page(page):
    """A reason a person must take over (CAPTCHA, login, code), or ''."""
    for selector, reason in BLOCKER_SELECTORS:
        try:
            if page.locator(selector).count():
                return reason
        except Exception:  # noqa: BLE001 - a selector problem is not a blocker
            continue
    text = _scan_text(page)
    for pattern, reason in BLOCKER_TEXT:
        if re.search(pattern, text, re.I):
            return reason
    return ""


def _combobox_options(page, f):
    """The choices a dropdown lists when opened. Opening it types nothing. A typed-search box lists none."""
    try:
        box = page.locator(f.selectors[0]).first
        listbox = box.get_attribute("aria-controls")
        box.click(timeout=2500)
        page.wait_for_timeout(300)
        scope = f"[id='{listbox}'] [role=option]" if listbox and page.locator(f"[id='{listbox}']").count() else "[role=option]:visible"
        options = [t.strip() for t in page.locator(scope).all_inner_texts() if t.strip()][:40]
        page.keyboard.press("Escape")
        return options
    except Exception:  # noqa: BLE001
        return []


def discover(page, url):
    """Open the application page and read its fields. Types nothing."""
    ats = detect_ats(url)  # only names the site for the log and picks Lever's and Ashby's /apply sub-page; any link is tried
    target = application_url(url)
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    except Exception as e:  # noqa: BLE001
        if "Timeout" not in type(e).__name__:
            raise FormError(f"could not open the page: {type(e).__name__}") from None
    page_text = ""
    try:
        page_text = page.inner_text("body", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    if robot_check_page(page_text, _title_of(page)):
        raise FormError("a robot check (Cloudflare or similar) blocked the page; the agent does not get around robot checks")
    if posting_closed(page_text, page.url):
        raise FormError("the posting is closed or gone")
    try:
        return read_form(page, ats, target)
    except FormError as first:
        if "no application form" not in str(first):
            raise
        page.wait_for_timeout(3000)  # a single-page app may draw the form late
        closed = _closed_now(page)
        if closed:
            raise FormError("the posting is closed or gone")
        try:
            return read_form(page, ats, target)
        except FormError as again:
            if "no application form" not in str(again):
                raise
            if not (_open_embedded_form(page) or _open_apply_link(page)):
                if _closed_now(page):
                    raise FormError("the posting is closed or gone")
                raise
        if _closed_now(page):
            raise FormError("the posting is closed or gone")
        return read_form(page, detect_ats(page.url), page.url)  # the Apply link or embedded frame led on to the form


ROBOT_PAGE = re.compile(r"performing security verification|verifying you are human|verify you are human|checking if the site connection is secure|"
                        r"checking your browser|enable javascript and cookies to continue|protect against malicious bots", re.I)


def robot_check_page(text, title=""):
    """True when the whole page is a bot-check interstitial (Cloudflare's 'Just a moment...') and not the posting."""
    return bool(re.match(r"\s*just a moment", title or "", re.I) or (len(text or "") < 1200 and ROBOT_PAGE.search(text or "")))


def _title_of(page):
    try:
        return page.title()
    except Exception:  # noqa: BLE001
        return ""


APPLY_LINK = re.compile(r"^\s*(apply( now| here| today| online| to position| for (this )?(job|position|role))?|apply to (this )?(job|position|role)|start (your )?application|i'?m interested)\s*$", re.I)


CLOSED_PAGE = re.compile(r"no longer (open|available|accepting)|job (is )?not found|this (job|position) (has been|is) (closed|filled)|"
                         r"page (you are looking for )?(does not|doesn't) exist|^\s*404|404\s*(error|not found|page)|error\s*404|"
                         r"^\s*page not found|the page you requested was not found|job (posting )?(is )?no longer exists|"
                         r"\bnotFound=1\b", re.I | re.M)


def posting_closed(text, url=""):
    """True when the posting itself is gone, including an iCIMS frame whose address says notFound=1."""
    blob = f"{text or ''}\n{url or ''}"
    return bool(CLOSED_PAGE.search(blob[:2500]))


def _closed_now(page):
    try:
        text = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        text = ""
    urls = " ".join(frame.url or "" for frame in page.frames)
    return posting_closed(text, urls)


def _frame_is_form(url):
    """An embedded application (Greenhouse's job_app frame), not a map or a CAPTCHA frame."""
    parts = urllib.parse.urlparse(url or "")
    host = parts.netloc.lower()
    if any(host == known or host.endswith("." + known) for hosts in ATS_HOSTS.values() for known in hosts):
        return True
    return bool(re.search(r"apply|application|job_app", url or "", re.I)) and "recaptcha" not in host


def _open_embedded_form(page):
    """A posting that shows the application in an iframe (Comeet, an embedded Greenhouse): open the frame's own page. True when it did."""
    here = urllib.parse.urlparse(page.url).netloc
    for frame in page.frames[1:]:
        url = frame.url
        if url.startswith("http") and urllib.parse.urlparse(url).netloc != here and _frame_is_form(url):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - a frame that will not open leaves the page as it was
                return False
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001 - a page that never goes quiet is still read
                pass
            page.wait_for_timeout(3000)  # the frame's form is drawn by script
            return True
    return False


def _follow_apply(page, el):
    """Open this Apply control. An address is followed directly, because a banner can sit over the click."""
    href = el.get_attribute("href") or ""
    if href.startswith(("http://", "https://", "/")):
        page.goto(urllib.parse.urljoin(page.url, href), wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    else:
        el.click(timeout=5000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(2000)  # the form may open on the same address (Rippling), so a changed page is not the test
    return True


def _open_apply_link(page):
    """On a posting page that is not itself a form, follow its own Apply link once. True when it did."""
    try:
        for kind in ("link", "button"):
            loc = page.get_by_role(kind, name=APPLY_LINK)
            if loc.count():
                return _follow_apply(page, loc.first)
        # The visible word is Apply, but the accessible name is the whole card (Paycom).
        for el in page.locator("a[href]").all()[:25]:
            if APPLY_LINK.match(re.sub(r"\s+", " ", el.inner_text() or "").strip()):
                return _follow_apply(page, el)
    except Exception:  # noqa: BLE001 - no usable Apply link means no form was found, which is reported
        return False
    return False


def read_form(page, ats, target):
    """Read the fields of the form on the current page. Types nothing."""
    raw = page.evaluate(DISCOVER_JS)
    form = Form(ats=ats, url=target, title=raw.get("title", ""))
    form.fields = [Field(**f) for f in raw["fields"]]
    seen = {}
    for f in form.fields:
        f.label = re.split(r"No location found|Couldn't auto-read|Analyzing resume|Autofill", f.label)[0].strip() or f.label
        f.label = re.sub(r"([A-Za-z])(Select a state)\b", r"\1 \2", f.label, flags=re.I)
        f.label = re.split(r"\bSelect a state\b", f.label, maxsplit=1, flags=re.I)[0].strip(" -:") or f.label
        base = normalize_label(f.label or f.name or f.dom_id) or "field"
        seen[base] = seen.get(base, 0) + 1
        f.key = base if seen[base] == 1 else f"{base} {seen[base]}"
        if f.kind == "combobox" and not f.options:
            f.options = _combobox_options(page, f)
    if not [f for f in form.fields if f.kind != "file" or f.required]:
        walled = blocker_on_page(page)
        raise FormError("no application form found on the page" + (f" ({walled})" if walled else ""))
    form.blocker = blocker_on_page(page)
    if not ats and not form.blocker and not any(f.kind == "file" for f in form.fields):
        # an unknown site: without a resume upload this is a search box or a newsletter sign-up, never an application
        raise FormError("no application form found on the page (no resume upload)")
    return form


OPTION_ALIASES = {
    "united states": ("united states of america", "usa", "us", "u s"),
    "minnesota": ("mn",),
    "bachelor s degree": ("bachelor s", "bachelors", "bachelor of arts", "bachelor of arts b a", "ba", "b a"),
}


def best_option(options, wanted):
    """The listed option that matches `wanted`: exact, then starts-with, then contains. None if nothing fits."""
    want = normalize_label(wanted)
    if not want:
        return None
    normed = [(normalize_label(o), o) for o in options]
    for test in (lambda n: n == want, lambda n: n.startswith(want + " ") or n.startswith(want),
                 lambda n: want in n, lambda n: n and n in want):
        for n, original in normed:
            if n and test(n):
                return original
    for alias in OPTION_ALIASES.get(want, ()):  # "United States" is listed as "USA" on some forms; exact only, "us" sits inside "Australia"
        for n, original in normed:
            if n == alias:
                return original
    return None


def _visible_options(page, field_selector):
    box = page.locator(field_selector).first
    listbox = box.get_attribute("aria-controls")
    scope = f"[id='{listbox}'] [role=option]" if listbox and page.locator(f"[id='{listbox}']").count() else "[role=option]:visible"
    return page.locator(scope)


def _fill_combobox(page, f, value):
    box = page.locator(f.selectors[0]).first
    box.click(timeout=4000)
    page.keyboard.type(str(value)[:40], delay=25)
    page.wait_for_timeout(700)
    options = _visible_options(page, f.selectors[0])
    texts = [t.strip() for t in options.all_inner_texts()]
    pick = best_option(texts, value) or (texts[0] if texts and f.key.startswith(("location", "current location", "home location", "your location")) else None)
    if pick is None:
        page.keyboard.press("Escape")
        raise FormError(f"no option matches {value!r} (choices: {texts[:6]})")
    options.nth(texts.index(pick)).click(timeout=4000)


def _fill_date(page, sel, value):
    """A calendar (Ashby's react-datepicker) rejects words like 'Immediately'. Type a date, or click today."""
    text = str(value).strip()
    if not re.match(r"\d{1,2}/\d{1,2}/\d{4}$", text):
        from datetime import date
        text = date.today().strftime("%m/%d/%Y")
    box = page.locator(sel).first
    box.click(timeout=4000)
    box.fill(text, timeout=5000)
    try:
        stuck = (box.input_value(timeout=2000) or "").strip()
    except Exception:  # noqa: BLE001
        stuck = ""
    if stuck:
        return
    today = page.locator(".react-datepicker__day--today").first
    if today.count():
        today.click(timeout=4000)
        return
    raise FormError(f"could not set the date {text}")


def _fill_one(page, f, value):
    sel = f.selectors[0]
    if f.kind == "file":
        page.locator(sel).first.set_input_files(str(value))
    elif f.kind == "date":
        _fill_date(page, sel, value)
    elif f.kind in ("text", "email", "tel", "url", "number", "textarea"):
        page.locator(sel).first.fill(str(value), timeout=5000)
    elif f.kind == "select":
        pick = best_option([o for o in f.options if not re.match(r"^(select|choose)\b", o, re.I)], value)
        if pick is None:
            raise FormError(f"no option matches {value!r} (choices: {f.options[:6]})")
        page.locator(sel).first.select_option(label=pick, timeout=5000)
    elif f.kind == "combobox":
        _fill_combobox(page, f, value)
    elif f.kind == "radio":
        pick = best_option(f.options, value)
        if pick is None:
            raise FormError(f"no option matches {value!r} (choices: {f.options[:6]})")
        choice = page.locator(f.selectors[f.options.index(pick)]).first
        if choice.evaluate("e => e.tagName") == "BUTTON":  # Ashby's Yes / No buttons
            choice.scroll_into_view_if_needed(timeout=5000)
            choice.evaluate("e => e.click()")  # a sticky logo can sit over the button and swallow a real click
        else:
            choice.check(force=True, timeout=5000)
    elif f.kind == "checkbox":
        wanted = value if isinstance(value, list) else [value]
        if wanted == [True]:
            wanted = list(f.options)
        for w in wanted:
            pick = best_option(f.options, w)
            if pick is None:
                raise FormError(f"no option matches {w!r}")
            page.locator(f.selectors[f.options.index(pick)]).first.check(force=True, timeout=5000)
    else:
        raise FormError(f"cannot fill a {f.kind} field")


def fill(page, form, answers):
    """Type the answers into the form. `answers` maps a field key to a value (a file path for uploads).

    Fields with no answer are left alone. Returns {field key: error text} for the ones that failed.
    """
    errors = {}
    for f in form.fields:
        if f.key not in answers:
            continue
        try:
            _fill_one(page, f, answers[f.key])
        except Exception as e:  # noqa: BLE001 - one bad field must not stop the rest
            errors[f.key] = str(e)[:160] if isinstance(e, FormError) else f"{type(e).__name__}: {str(e)[:120]}"
    return errors


def _scan_text(page):
    try:
        scope = page.locator("form").first if page.locator("form").count() else page.locator("body")
        return scope.inner_text(timeout=3000)
    except Exception:  # noqa: BLE001
        return ""


def _validation_messages(page):
    try:
        bad = page.locator("[aria-invalid='true']:visible, .field-error:visible, [class*='error' i]:visible")
        texts = [t.strip() for t in bad.all_inner_texts() if t.strip()]
    except Exception:  # noqa: BLE001
        return []
    return [t[:120] for t in dict.fromkeys(texts)][:6]


def confirmed(page, before_url=""):
    """True when the page shows that the application was received."""
    if before_url and page.url != before_url and SUCCESS_URL.search(page.url):
        return True
    return bool(SUCCESS_TEXT.search(_scan_text(page)) or SUCCESS_TEXT.search(_page_text(page)))


CODE_BOXES = (
    "input[autocomplete='one-time-code']:visible, input[id*='security' i]:visible, input[name*='security' i]:visible, "
    "input[aria-label*='security' i]:visible, input[aria-label*='verification' i]:visible, input[aria-label*='code' i]:visible, "
    "input[name*='code' i]:visible, input[id*='code' i]:visible, input[placeholder*='code' i]:visible"
)
SINGLE_CHARACTER_BOXES = "input[maxlength='1']:visible"


def enter_code(page, code):
    """Type an emailed security code into the box, or the row of one-character boxes, the page shows. True when typed."""
    try:
        singles = page.locator(SINGLE_CHARACTER_BOXES)
        if singles.count() >= len(code) > 1:  # Greenhouse draws one box per character
            for i, ch in enumerate(code):
                singles.nth(i).fill(ch)
            return True
        box = page.locator(CODE_BOXES).first
        if box.count():
            box.fill(code)
            return True
    except Exception:  # noqa: BLE001 - a page that will not take the code is reported, not guessed at
        return False
    return False


def _find_submit(page):
    button = page.locator("button[type=submit]:visible, input[type=submit]:visible").first
    if not button.count():
        button = page.get_by_role("button", name=re.compile(r"submit|apply", re.I)).first
    return button


def submit(page, code_getter=None):
    """Click the form's submit button and watch for the result. Call only after Adam approved.

    Returns ("submitted", "") when a confirmation shows, ("blocked", reason) when a CAPTCHA or a code
    prompt appears, ("invalid", messages) when the form refused it, or ("unknown", note) otherwise.
    "unknown" means the click went through but no confirmation was seen, so the application may exist.

    code_getter(since) -> (code, note) reads the emailed security code, so a site that mails one after Submit
    (Greenhouse) is finished by the script: the code is typed in and Submit is pressed once more.
    """
    clicked = time.time()
    status, detail = _press_submit(page)
    if status == "blocked" and detail == "email verification code" and code_getter:
        code, note = code_getter(clicked)
        if not code:
            return "blocked", f"email verification code: {note or 'no code arrived'}"
        if not enter_code(page, code):
            return "blocked", "email verification code: the page had no box I could type it in"
        status, detail = _press_submit(page)
        if status == "blocked" and detail == "email verification code":
            return "blocked", "email verification code: the site did not accept the code"
    return status, detail


UNANSWERED_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('[data-field-path], fieldset').forEach((e) => {
    const lab = e.querySelector('label[class*="required" i], legend[class*="required" i]');
    if (!lab || e.querySelector('input[type=file]')) return;
    let filled = !!e.querySelector('[aria-pressed=true]');
    e.querySelectorAll('input, textarea, select').forEach((i) => {
      if (i.type === 'radio' || i.type === 'checkbox') { if (i.checked) filled = true; }
      else if ((i.value || '').trim()) filled = true;
    });
    const text = (lab.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    if (!filled && text && !out.includes(text)) out.push(text);
  });
  return out;
}
"""


def _unanswered_required(page):
    """The required questions on the page that have no answer, so a refusal says why."""
    try:
        return list(page.evaluate(UNANSWERED_JS))
    except Exception:  # noqa: BLE001
        return []


def _press_submit(page):
    button = _find_submit(page)
    if not button.count():
        return "invalid", "no submit button found"
    before = page.url
    try:
        page.wait_for_load_state("networkidle", timeout=10000)  # a resume upload still in flight makes the site ignore the click
    except Exception:  # noqa: BLE001 - a page that never goes quiet is still clicked
        pass
    try:
        button.click(timeout=8000)
    except Exception as e:  # noqa: BLE001
        if "Timeout" in type(e).__name__:  # a robot-check overlay (Lever's hCaptcha) keeps the button from taking the click
            return "blocked", "robot check: the site would not take the click"
        raise
    waited = 0
    while waited < SUBMIT_WAIT_MS:
        page.wait_for_timeout(700)
        waited += 700
        if confirmed(page, before):
            return "submitted", ""
        reason = blocker_on_page(page)
        if reason:
            return "blocked", reason
        if waited >= 4000:
            messages = _validation_messages(page)
            if messages:
                return "invalid", "; ".join(messages)
    empty = _unanswered_required(page)
    if empty:
        return "invalid", "the site did not take it; required questions still empty: " + "; ".join(empty[:5])
    return "unknown", "no confirmation page appeared within %d seconds" % (SUBMIT_WAIT_MS // 1000)


def assist(page, url, answers, wait_seconds=1200, auto_submit=False, on_blocked=None, code_getter=None):
    """Open the form in a visible browser, type the approved answers, and wait for a person to finish it.

    Sites with a robot check (Ashby, Lever) cannot be submitted by a script, and this code never solves
    one. Adam presses Submit and clears the check himself. Returns ("submitted", ""), ("closed", "") when
    the window was closed first, ("timeout", "") or ("error", text).

    With auto_submit the script presses Submit itself, as any browser user would, and only falls back to
    waiting for a person when the site answers with a robot check, a validation error or no confirmation.
    on_blocked(status, detail) is called then, so the person can be told the window needs them.

    With auto_submit and no on_blocked nobody is pulled in: a refusal returns ("blocked", reason) at once so the
    caller can log it for fixing.
    """
    try:
        form = discover(page, url)
        errors = fill(page, form, answers)
        missing = [f.label for f in form.fields if f.required and f.key not in answers]
        if auto_submit and not missing:
            status, detail = submit(page, code_getter=code_getter)
            if status == "submitted":
                return "submitted", ""
            if not on_blocked:
                return "blocked", f"{status}: {detail}"
            page.bring_to_front()
            on_blocked(status, detail)
        else:
            if auto_submit and not on_blocked:
                return "blocked", "incomplete: " + "; ".join(missing[:3] + [f"{k}: {v}" for k, v in list(errors.items())[:2]])
            page.bring_to_front()
            if auto_submit and on_blocked:
                on_blocked("incomplete", "; ".join(missing[:3]))
        before, waited = page.url, 0
        while waited < wait_seconds * 1000:
            page.wait_for_timeout(1500)
            waited += 1500
            if confirmed(page, before):
                return "submitted", ""
        note = "; ".join(missing[:3] + [f"{k}: {v}" for k, v in list(errors.items())[:2]])
        return "timeout", note
    except FormError as e:
        return "error", str(e)
    except Exception as e:  # noqa: BLE001 - closing the window ends the wait
        return ("closed", "") if "closed" in str(e).lower() else ("error", type(e).__name__)


def _page_text(page):
    try:
        return page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return ""


def screenshot(page, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:  # noqa: BLE001
        return ""


COVER_LETTER_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{font-family:Georgia,'Times New Roman',serif;font-size:11.5pt;line-height:1.45;margin:0;color:#111}
h1{font-size:15pt;margin:0 0 2px}.contact{color:#444;font-size:10pt;margin-bottom:22px}p{margin:0 0 12px}
</style></head><body><h1>{name}</h1><div class="contact">{contact}</div><p>{date}</p>{body}<p>Sincerely,<br>{name}</p></body></html>"""


def render_pdf(page, path, name, contact, date_text, text):
    """Print a plain cover letter to a PDF at `path`. `text` is the letter body, blank lines between paragraphs."""
    import html as _html
    body = "".join(f"<p>{_html.escape(par.strip())}</p>" for par in re.split(r"\n\s*\n", text.strip()) if par.strip())
    doc = (COVER_LETTER_HTML.replace("{name}", _html.escape(name)).replace("{contact}", _html.escape(contact))
           .replace("{date}", _html.escape(date_text)).replace("{body}", body))
    page.set_content(doc)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    page.pdf(path=str(path), format="Letter", margin={"top": "0.9in", "bottom": "0.9in", "left": "1in", "right": "1in"})
    return str(path)
