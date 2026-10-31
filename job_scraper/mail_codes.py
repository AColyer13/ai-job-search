"""Read the one-time security code an application site emails to Adam, so the apply agent can enter it.

Greenhouse (and sometimes Lever or Ashby) emails a short code after Submit and the form will not go through
until it is typed in. The Python jobs have no mail access of their own, so this reads Adam's own Gmail over
IMAP with a Gmail app password kept in .env:

    GMAIL_IMAP_USER=adamcolyer@gmail.com
    GMAIL_IMAP_PASSWORD=<16-character app password from myaccount.google.com/apppasswords>

Limits, on purpose:
- Read-only. The mailbox is opened read-only; nothing is sent, flagged, moved or deleted.
- Only mail received after the Submit click is looked at, and only if it is from an application system
  (Greenhouse, Lever, Ashby) or names the company Adam is applying to (any other site mails its own code).
- Only a code is taken out of a message. Nothing else in the mail is read as an instruction.
- It looks in All Mail, Trash and Spam too, because a filter may have archived the message.
"""
import email
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import telegram_notify

IMAP_HOST = "imap.gmail.com"
SENDER_DOMAINS = ("greenhouse-mail.io", "greenhouse.io", "lever.co", "ashbyhq.com", "hire.lever.co")
FOLDERS = ('"[Gmail]/All Mail"', "INBOX", '"[Gmail]/Spam"', '"[Gmail]/Trash"')
CODE_LABEL = re.compile(r"(?:security|verification|confirmation|one[- ]time|access|login|sign[- ]in|authentication)\s+code|\bpasscode\b|\byour code\b", re.I)
TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{4,10}(?![A-Za-z0-9])")
CODE_ALONE = re.compile(r"^\s*([A-Za-z0-9]{6,10})\s*$")
LOOK_AHEAD = 160  # characters after the words "security code" in which the code itself is expected


def credentials(env=None):
    values = telegram_notify.read_env() if env is None else env
    user, password = values.get("GMAIL_IMAP_USER", ""), values.get("GMAIL_IMAP_PASSWORD", "").replace(" ", "")
    return (user, password) if user and password else None


def _text_of(message):
    parts = []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if part.get_content_type() == "text/html":
                text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
                text = re.sub(r"<br\s*/?>|</(p|div|tr|h\d)>", "\n", text, flags=re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"&nbsp;", " ", text)
            parts.append(text)
    return "\n".join(parts)


def extract_code(subject, body):
    """The security code in an application email, or ''.

    A code is 4 to 10 letters and digits that contains a digit or an upper-case letter after the first character
    beside a lower-case one, so an ordinary word or a capitalised name that follows 'code' is not taken for one.
    """
    for text in (body or "", subject or ""):
        for label in CODE_LABEL.finditer(text):
            for token in TOKEN.finditer(text[label.end():label.end() + LOOK_AHEAD]):
                if _looks_like_code(token.group()):
                    return token.group()
    for line in (body or "").splitlines():
        match = CODE_ALONE.match(line)
        if match and _looks_like_code(match.group(1)):
            return match.group(1)
    return ""


def _looks_like_code(token):
    if token.isdigit():
        return 4 <= len(token) <= 10
    return bool(re.search(r"\d", token) or (re.search(r"[a-z]", token) and re.search(r"[A-Z]", token[1:])))


def _names_company(company, *texts):
    """True when the mail names the company (its first word is enough: 'Virtru' for 'Virtru Corporation')."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", company or "") if len(w) > 2 and w.lower() not in ("the", "inc", "llc", "ltd", "corp", "corporation", "company", "group", "holdings")]
    return bool(words) and words[0].lower() in " ".join(texts).lower()


def _sender_ok(sender):
    domain = sender.rsplit("@", 1)[-1].strip(" >").lower()
    return any(domain == d or domain.endswith("." + d) for d in SENDER_DOMAINS)


CONFIRMATION = re.compile(r"thank you for applying|thanks for applying|application (has been |was )?received|"
                          r"we (have )?received your application|successfully submitted", re.I)


def find_confirmation(since, company="", creds=None, connect=imaplib.IMAP4_SSL):
    """True when a confirmation mail for this company arrived after `since`.

    False when the mailbox was read and nothing matched. None when mail could not be read.
    Read-only, and a mail is a confirmation only when it names the company. Nothing in the mail is treated as an instruction.
    """
    creds = creds or credentials()
    if not creds:
        return None
    try:
        return _confirmation(connect, creds, since, company)
    except (imaplib.IMAP4.error, OSError, TypeError, ValueError):
        return None


def _confirmation(connect, creds, since, company):
    box = connect(IMAP_HOST)
    try:
        box.login(*creds)
        day = (datetime.fromtimestamp(since, timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
        for folder in FOLDERS:
            status, _ = box.select(folder, readonly=True)
            if status != "OK":
                continue
            status, data = box.search(None, "SINCE", day)
            if status != "OK" or not data or not data[0]:
                continue
            for num in reversed(data[0].split()[-40:]):
                status, raw = box.fetch(num, "(BODY.PEEK[])")
                if status != "OK" or not raw or not isinstance(raw[0], tuple):
                    continue
                message = email.message_from_bytes(raw[0][1])
                try:
                    sent = parsedate_to_datetime(message.get("Date")).timestamp()
                except (TypeError, ValueError):
                    continue
                if sent < since - 30:
                    continue
                sender = str(make_header(decode_header(message.get("From", ""))))
                subject = str(make_header(decode_header(message.get("Subject", ""))))
                body = _text_of(message)
                if CONFIRMATION.search(f"{subject}\n{body}") and _names_company(company, sender, subject, body):
                    return True
        return False
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass


def fetch_code(since, company="", creds=None, timeout=120, poll=5, connect=imaplib.IMAP4_SSL, sleep=time.sleep):
    """Wait up to `timeout` seconds for a code mailed after `since` (epoch seconds). Returns (code, note).

    code is '' when none arrived or mail could not be read; note says why, for the errors log.
    """
    creds = creds or credentials()
    if not creds:
        return "", "no Gmail IMAP login in .env (GMAIL_IMAP_USER, GMAIL_IMAP_PASSWORD)"
    deadline = time.time() + timeout
    last_note = "no code email arrived"
    while True:
        try:
            code = _look(connect, creds, since, company)
            if code:
                return code, ""
        except imaplib.IMAP4.error as e:
            return "", f"Gmail IMAP refused the login or search ({str(e)[:80]})"
        except OSError as e:
            last_note = f"could not reach Gmail ({type(e).__name__})"
        if time.time() >= deadline:
            return "", last_note
        sleep(poll)


def _look(connect, creds, since, company):
    box = connect(IMAP_HOST)
    try:
        box.login(*creds)
        day = (datetime.fromtimestamp(since, timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
        best = ("", 0.0)
        for folder in FOLDERS:
            status, _ = box.select(folder, readonly=True)
            if status != "OK":
                continue
            status, data = box.search(None, "SINCE", day)
            if status != "OK" or not data or not data[0]:
                continue
            for num in reversed(data[0].split()[-40:]):
                status, raw = box.fetch(num, "(BODY.PEEK[])")
                if status != "OK" or not raw or not isinstance(raw[0], tuple):
                    continue
                message = email.message_from_bytes(raw[0][1])
                sender = str(make_header(decode_header(message.get("From", ""))))
                try:
                    sent = parsedate_to_datetime(message.get("Date")).timestamp()
                except (TypeError, ValueError):
                    continue
                if sent < since - 30:
                    continue
                subject = str(make_header(decode_header(message.get("Subject", ""))))
                body = _text_of(message)
                if not _sender_ok(sender) and not _names_company(company, sender, subject, body):
                    continue  # another site's mail: only when it says which company it is for
                code = extract_code(subject, body)
                if code and sent > best[1]:
                    best = (code, sent)
        return best[0]
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
