"""Browser layer for the apply agent: read an application form, fill it, submit it.

Supports the three hosted forms that need no account: Greenhouse, Lever and Ashby. All three are read
the same way, from the page's own labels and inputs, so one reader covers them. No AI is involved here.

`discover` only reads. Nothing is typed, uploaded or clicked. `fill` types the approved answers.
`submit` is a separate call that the caller makes only after Adam approved on Telegram.
"""
import re
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
    (r"security code|verification code|enter the code (we|that we) sent", "email verification code"),
    (r"sign in to (apply|continue)|log ?in to (apply|continue)|create an account to apply", "account or login required"),
)
BLOCKER_SELECTORS = (
    ("iframe[src*='recaptcha/api2/anchor']:visible, iframe[src*='hcaptcha.com/captcha']:visible, "
     "iframe[src*='challenges.cloudflare.com']:visible, .h-captcha:visible, .cf-turnstile:visible", "CAPTCHA"),
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
      const cands = box.querySelectorAll('legend, label, [class*="label"], [class*="Label"], [class*="title"], [class*="Title"]');
      for (const l of cands) {
        const holdsInput = l.querySelector('input[type=radio], input[type=checkbox]');
        if (skipOptionLabels && (holdsInput || l.contains(el))) continue;
        const t = l.contains(el) ? ownText(l) : clean(l.innerText);
        if (t) return t;
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
    return t || clean(el.getAttribute('placeholder')) || el.name || el.id || '';
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
  const requiredOf = (el, label) =>
    el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label) || /\brequired\b/i.test(label);

  const out = [];
  const seenGroups = new Map();
  const scope = document.querySelector('form') || document.body;
  const els = scope.querySelectorAll('input, textarea, select, [role=combobox]');
  els.forEach((el, i) => {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    if (el.tagName === 'INPUT' && el.getAttribute('role') === 'combobox' && el.closest('[role=combobox]') && el.closest('[role=combobox]') !== el) return;
    if (type !== 'file' && !visible(el)) return;
    if (el.closest('[aria-hidden=true]') && type !== 'file') return;
    el.setAttribute('data-apply-idx', String(i));
    const sel = '[data-apply-idx="' + i + '"]';
    if (type === 'radio' || type === 'checkbox') {
      const name = el.name || '';
      const optLabel = labelOf(el);
      const key = type + ':' + (name || groupLabel(el));
      let g = seenGroups.get(key);
      if (!g) {
        const q = groupLabel(el);
        g = { kind: type, label: q, name, dom_id: el.id, required: requiredOf(el, q), options: [], selectors: [], multi: type === 'checkbox' };
        seenGroups.set(key, g); out.push(g);
      }
      g.options.push(optLabel); g.selectors.push(sel);
      if (g.options.length === 1 && type === 'checkbox') g.label = optLabel;  // a lone consent box is its own question
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
    out.push({ kind, label, name: el.name || '', dom_id: el.id || '', required: requiredOf(el, label), options, selectors: [sel], multi: false,
               accept: el.getAttribute('accept') || '' });
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
    ats = detect_ats(url)
    if not ats:
        raise FormError(f"not a Greenhouse, Lever or Ashby link: {urllib.parse.urlparse(url).netloc}")
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
    if re.search(r"no longer (open|available|accepting)|job (is )?not found|this (job|position) (has been|is) (closed|filled)|"
                 r"page (you are looking for )?(does not|doesn't) exist|404", page_text[:1500], re.I):
        raise FormError("the posting is closed or gone")
    return read_form(page, ats, target)


def read_form(page, ats, target):
    """Read the fields of the form on the current page. Types nothing."""
    raw = page.evaluate(DISCOVER_JS)
    form = Form(ats=ats, url=target, title=raw.get("title", ""))
    form.fields = [Field(**f) for f in raw["fields"]]
    seen = {}
    for f in form.fields:
        f.label = re.split(r"No location found|Couldn't auto-read|Analyzing resume|Autofill", f.label)[0].strip() or f.label
        base = normalize_label(f.label or f.name or f.dom_id) or "field"
        seen[base] = seen.get(base, 0) + 1
        f.key = base if seen[base] == 1 else f"{base} {seen[base]}"
        if f.kind == "combobox" and not f.options:
            f.options = _combobox_options(page, f)
    if not [f for f in form.fields if f.kind != "file" or f.required]:
        raise FormError("no application form found on the page")
    form.blocker = blocker_on_page(page)
    return form


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
    pick = best_option(texts, value) or (texts[0] if texts and f.key.startswith(("location", "current location")) else None)
    if pick is None:
        page.keyboard.press("Escape")
        raise FormError(f"no option matches {value!r} (choices: {texts[:6]})")
    options.nth(texts.index(pick)).click(timeout=4000)


def _fill_one(page, f, value):
    sel = f.selectors[0]
    if f.kind == "file":
        page.locator(sel).first.set_input_files(str(value))
    elif f.kind in ("text", "email", "tel", "url", "number", "date", "textarea"):
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
        page.locator(f.selectors[f.options.index(pick)]).first.check(force=True, timeout=5000)
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


def submit(page):
    """Click the form's submit button and watch for the result. Call only after Adam approved.

    Returns ("submitted", "") when a confirmation shows, ("blocked", reason) when a CAPTCHA or a code
    prompt appears, ("invalid", messages) when the form refused it, or ("unknown", note) otherwise.
    "unknown" means the click went through but no confirmation was seen, so the application may exist.
    """
    button = page.locator("button[type=submit]:visible, input[type=submit]:visible").first
    if not button.count():
        button = page.get_by_role("button", name=re.compile(r"submit|apply", re.I)).first
    if not button.count():
        return "invalid", "no submit button found"
    before = page.url
    button.click(timeout=8000)
    waited = 0
    while waited < SUBMIT_WAIT_MS:
        page.wait_for_timeout(700)
        waited += 700
        if page.url != before and SUCCESS_URL.search(page.url):
            return "submitted", ""
        text = _scan_text(page)
        if SUCCESS_TEXT.search(text) or SUCCESS_TEXT.search(_page_text(page)):
            return "submitted", ""
        reason = blocker_on_page(page)
        if reason:
            return "blocked", reason
        if waited >= 4000:
            messages = _validation_messages(page)
            if messages:
                return "invalid", "; ".join(messages)
    return "unknown", "no confirmation page appeared within %d seconds" % (SUBMIT_WAIT_MS // 1000)


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
