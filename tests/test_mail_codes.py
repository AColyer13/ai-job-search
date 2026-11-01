"""The emailed security code reader. No network: IMAP is replaced by a fake mailbox."""
import sys
import time
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import mail_codes  # noqa: E402


@pytest.mark.parametrize("subject,body,code", [
    ("Security code for your application to Huntress", "Copy and paste this code into the security code field on your application: Ab12Cd34\n\nThanks", "Ab12Cd34"),
    ("Your code", "Your verification code is 482913. Do not share it.", "482913"),
    ("hi", "Please enter the confirmation code\n\nkP3mRz9T\n", "kP3mRz9T"),
    ("Security code for your application to Huntress", "Thanks for applying to Huntress. Your security code field is below.", ""),  # words, not a code
    ("Weekly digest", "Nothing here but the Huntress team and your application.", ""),
])
def test_extract_code(subject, body, code):
    assert mail_codes.extract_code(subject, body) == code


def test_html_mail_is_reduced_to_text():
    msg = EmailMessage()
    msg.set_content("x")
    msg.add_alternative("<html><style>p{color:red}</style><body><p>Your security code is</p><h1>Zq81LmPw</h1></body></html>", subtype="html")
    text = mail_codes._text_of(msg)
    assert "Zq81LmPw" in text and "color:red" not in text
    assert mail_codes.extract_code("", text) == "Zq81LmPw"


def message(sender, subject, body, when):
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Date"] = sender, subject, format_datetime(datetime.fromtimestamp(when, timezone.utc))
    msg.set_content(body)
    return msg.as_bytes()


class FakeBox:
    """Stands in for imaplib.IMAP4_SSL: one folder with the given raw messages."""
    opened = []

    def __init__(self, raws, fail_login=False):
        self.raws, self.fail_login, self.selected = raws, fail_login, []

    def login(self, user, password):
        if self.fail_login:
            import imaplib
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, folder, readonly=False):
        assert readonly, "the mailbox must be opened read-only"
        self.selected.append(folder)
        return ("OK", [b"1"]) if folder == '"[Gmail]/All Mail"' else ("NO", [b"missing"])

    def search(self, charset, *criteria):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.raws)))]

    def fetch(self, num, what):
        assert "PEEK" in what, "fetching must not mark mail as read"
        return "OK", [(b"1 (BODY[]", self.raws[int(num) - 1]), b")"]

    def logout(self):
        pass


def test_the_newest_code_from_an_application_system_after_the_click_is_returned():
    now = time.time()
    raws = [
        message("Greenhouse <no-reply@us.greenhouse-mail.io>", "Security code for your application", "Your security code: OLD12345", now - 600),
        message("Greenhouse <no-reply@us.greenhouse-mail.io>", "Security code for your application", "Your security code: Ab12Cd34", now - 5),
        message("Scam <x@evil.example>", "Security code", "Your security code: ZZ99ZZ99", now - 1),
    ]
    code, note = mail_codes.fetch_code(now - 20, creds=("a@b.co", "pw"), connect=lambda host: FakeBox(raws), timeout=0)
    assert (code, note) == ("Ab12Cd34", "")


def test_mail_from_other_senders_is_never_read_for_a_code():
    now = time.time()
    raws = [message("Someone <x@evil.example>", "Security code", "Your security code: ZZ99ZZ99", now - 1)]
    code, note = mail_codes.fetch_code(now - 20, creds=("a@b.co", "pw"), connect=lambda host: FakeBox(raws), timeout=0)
    assert code == "" and "no code" in note


def test_a_confirmation_mail_that_names_the_company_counts_and_nothing_else_does():
    now = time.time()
    yes = message("Ashby <no-reply@ashbyhq.com>", "Thank you for applying", "We received your application to True Talent Solutions.", now - 5)
    other = message("Ashby <no-reply@ashbyhq.com>", "Thank you for applying", "We received your application to Inworld.", now - 5)
    old = message("Ashby <no-reply@ashbyhq.com>", "Thank you for applying", "We received your application to True Talent.", now - 900)
    assert mail_codes.find_confirmation(now - 60, "True Talent Solutions Partners", creds=("a@b.co", "pw"), connect=lambda host: FakeBox([yes])) is True
    assert mail_codes.find_confirmation(now - 60, "True Talent Solutions Partners", creds=("a@b.co", "pw"), connect=lambda host: FakeBox([other])) is False
    assert mail_codes.find_confirmation(now - 60, "True Talent Solutions Partners", creds=("a@b.co", "pw"), connect=lambda host: FakeBox([old])) is False


def test_confirmation_lookup_says_nothing_when_mail_cannot_be_read(monkeypatch):
    monkeypatch.setattr(mail_codes, "credentials", lambda env=None: None)
    assert mail_codes.find_confirmation(time.time(), "Acme") is None


@pytest.mark.parametrize("subject,body", [
    ("Application update", "Thanks for applying to True Talent. Your security code is 482913."),
    ("Application update", "It is true: we received your application to Globex."),
])
def test_code_prompts_and_shared_words_are_not_confirmation(subject, body):
    assert not mail_codes.confirmation_matches(subject, body, "True Talent Solutions Partners")


def test_confirmation_must_match_the_role_when_known():
    body = "We received your application for Sales Engineer at Acme."
    assert mail_codes.confirmation_matches("Thank you for applying", body, "Acme", "Sales Engineer")
    assert not mail_codes.confirmation_matches("Thank you for applying", body, "Acme", "Software Engineer")


def test_confirmation_older_than_forty_unrelated_messages_is_still_found():
    now = time.time()
    raws = [message("Ashby <no-reply@ashbyhq.com>", "Thank you for applying",
                    "We received your application for Sales Engineer at Acme.", now - 20)]
    raws += [message("Digest <news@example.com>", "News", "An unrelated newsletter", now - 1)] * 60
    assert mail_codes.find_confirmation(now - 60, "Acme", role="Sales Engineer", creds=("fixture", "fixture"),
                                        connect=lambda host: FakeBox(raws)) is True


def test_a_mailbox_that_cannot_be_searched_is_not_a_negative_confirmation():
    class FailedSearch(FakeBox):
        def search(self, *args):
            return "NO", [b"unavailable"]
    assert mail_codes.find_confirmation(time.time(), "Acme", creds=("fixture", "fixture"),
                                        connect=lambda host: FailedSearch([])) is None


def test_a_missing_login_says_so_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(mail_codes, "credentials", lambda env=None: None)
    code, note = mail_codes.fetch_code(time.time(), timeout=0)
    assert code == "" and "GMAIL_IMAP_PASSWORD" in note


def test_a_refused_login_is_reported_without_retrying():
    calls = []

    def connect(host):
        calls.append(host)
        return FakeBox([], fail_login=True)

    code, note = mail_codes.fetch_code(time.time(), creds=("a@b.co", "bad"), connect=connect, timeout=30, sleep=lambda s: None)
    assert code == "" and "refused" in note and len(calls) == 1


def test_it_waits_for_the_email_to_arrive():
    now, state = time.time(), {"n": 0}

    def connect(host):
        state["n"] += 1
        raws = [] if state["n"] < 3 else [message("Greenhouse <no-reply@us.greenhouse-mail.io>", "Security code", "Your security code: Ab12Cd34", now)]
        return FakeBox(raws)

    code, _ = mail_codes.fetch_code(now - 5, creds=("a@b.co", "pw"), connect=connect, timeout=30, poll=0, sleep=lambda s: None)
    assert code == "Ab12Cd34" and state["n"] == 3


def test_credentials_are_read_from_env_and_spaces_in_an_app_password_are_dropped():
    assert mail_codes.credentials({"GMAIL_IMAP_USER": "a@b.co", "GMAIL_IMAP_PASSWORD": "abcd efgh ijkl mnop"}) == ("a@b.co", "abcdefghijklmnop")
    assert mail_codes.credentials({"GMAIL_IMAP_USER": "a@b.co"}) is None


def html_message(sender, subject, html, when):
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Date"] = sender, subject, format_datetime(datetime.fromtimestamp(when, timezone.utc))
    msg.set_content("Please verify your email address.")
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


def test_a_workday_verification_link_is_opened_and_other_links_are_not():
    hrefs = [
        "https://salesforce.wd12.myworkdayjobs.com/en-US/site/activate?token=abc",
        "https://salesforce.wd12.myworkdayjobs.com/unsubscribe",
        "https://evil.example/verify?token=no",
    ]
    assert mail_codes.extract_verify_link("Verify your email", "Please verify your email address.", hrefs) == hrefs[0]
    assert mail_codes.extract_verify_link("Weekly jobs", "New roles", ["https://evil.example/verify"]) == ""
    assert mail_codes.extract_verify_link("News", "Hello", ["https://salesforce.wd12.myworkdayjobs.com/en-US/site/home"]) == ""


def test_the_verification_link_is_read_from_the_message_workday_sent():
    now = time.time()
    html = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/careers/activate?token=abc">Verify Email</a>'
    raws = [
        html_message("Workday <noreply@myworkday.com>", "Verify your candidate account", html, now - 5),
        html_message("Scam <x@evil.example>", "Verify your email", html, now - 1),
    ]
    url, note = mail_codes.fetch_verify_link(now - 20, creds=("a@b.co", "pw"), connect=lambda host: FakeBox(raws), timeout=0)
    assert (url, note) == ("https://acme.wd5.myworkdayjobs.com/en-US/careers/activate?token=abc", "")


def test_another_sites_code_is_taken_only_when_the_mail_names_the_company():
    now = time.time()
    raws = [
        message("Acme Careers <no-reply@acme-hr.example>", "Your sign-in code", "Your code is 482913 for your Acme Robotics application.", now - 3),
        message("Bank <alerts@bank.example>", "Your verification code", "Your verification code is 111222", now - 2),
    ]
    code, _ = mail_codes.fetch_code(now - 20, company="Acme Robotics Inc", creds=("a@b.co", "pw"), connect=lambda host: FakeBox(raws), timeout=0)
    assert code == "482913"
    code, note = mail_codes.fetch_code(now - 20, company="Globex", creds=("a@b.co", "pw"), connect=lambda host: FakeBox(raws), timeout=0)
    assert code == "" and "no code" in note


@pytest.mark.parametrize("subject,body,code", [
    ("Sign in to Acme", "Your code is 654321", "654321"),
    ("Login", "Your login code: Qw3rTy9Z", "Qw3rTy9Z"),
])
def test_more_code_phrasings(subject, body, code):
    assert mail_codes.extract_code(subject, body) == code
