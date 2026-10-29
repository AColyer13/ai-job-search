"""Saved copies of real, public application forms (Greenhouse, Lever, Ashby), with company names replaced by "Acme".

The hand-written pages in test_apply_forms.py show what the reader should do. These show what the vendors actually ship, so a
change in the form markup or in the reader that loses a required field fails here. Nothing is typed or submitted.
To refresh one, open the live form with apply_forms.discover, keep the visible form markup, strip scripts and links, and
regenerate the .expected.json snapshot; read the diff before committing.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import apply_answers  # noqa: E402
import apply_forms as af  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats"
ATS = ("greenhouse", "lever", "ashby")
FILLABLE = {"text", "email", "tel", "url", "number", "date", "textarea", "select", "combobox", "radio", "checkbox", "file"}
# Required fields whose answer is not in the profile, so the agent must ask Adam. None of the three saved forms has one;
# a form that does must list its fields here on purpose.
REQUIRED_TO_ASK = {"greenhouse": set(), "lever": set(), "ashby": set()}


@pytest.fixture(scope="module")
def page():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


def read(page, ats):
    page.set_content((FIXTURES / f"{ats}.html").read_text(encoding="utf-8"))
    return af.read_form(page, ats, "http://localhost/fixture")


def outcome(field):
    return apply_answers.resolve(field, apply_answers.DEFAULT_PROFILE, {}, standard={})[0]


@pytest.mark.parametrize("ats", ATS)
def test_fields_match_the_saved_snapshot(page, ats):
    form = read(page, ats)
    seen = [{"kind": f.kind, "label": f.label, "required": f.required, "key": f.key, "options": f.options[:8]}
            for f in form.fields]
    assert seen == json.loads((FIXTURES / f"{ats}.expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("ats", ATS)
def test_every_required_field_is_readable_and_has_an_answer_path(page, ats):
    form = read(page, ats)
    required = [f for f in form.fields if f.required]
    assert required, "a real application form always has required fields"
    keys = [f.key for f in form.fields]
    assert len(keys) == len(set(keys)), "two fields share a key, so one answer would overwrite the other"
    for f in required:
        assert f.key and f.label.strip(), f"required field with no label: {f}"
        assert f.kind in FILLABLE, f"{f.label}: the filler cannot fill a {f.kind} field"
        assert f.selectors, f"{f.label}: no selector to find it again"
        assert outcome(f) in ("fill", "consent", "draft", "ask"), f"{f.label}: required but resolves to skip"


@pytest.mark.parametrize("ats", ATS)
def test_required_fields_the_profile_cannot_answer_are_the_expected_ones(page, ats):
    asked = {f.key for f in read(page, ats).fields if f.required and outcome(f) in ("ask", "draft")}
    assert asked == REQUIRED_TO_ASK[ats]


@pytest.mark.parametrize("ats", ATS)
def test_required_profile_fields_are_filled_with_the_profile_values(page, ats):
    form = read(page, ats)
    profile = apply_answers.DEFAULT_PROFILE
    answers = {}
    for f in form.fields:
        out, value, _ = apply_answers.resolve(f, profile, {}, standard={})
        # Optional comboboxes (Greenhouse "Country") load their choices from script when opened, which a saved page cannot do.
        if out in ("fill", "consent") and f.kind != "file" and f.required:
            answers[f.key] = value
    assert af.fill(page, form, answers) == {}
    typed = page.evaluate("() => [...document.querySelectorAll('input[type=text], input[type=email], input[type=tel]')]"
                          ".map(i => i.value).filter(Boolean)")
    assert profile["email"] in typed


def test_lever_hcaptcha_is_a_blocker_before_anything_is_typed(page):
    # Lever's hCaptcha widget draws itself only when Submit is pressed, but its container and response field are on the page
    # from the start. The applied log shows a Lever hCaptcha rejection and no Lever success.
    assert 'class="h-captcha"' in (FIXTURES / "lever.html").read_text(encoding="utf-8")
    assert "hCaptcha" in read(page, "lever").blocker


@pytest.mark.parametrize("ats", ("greenhouse", "ashby"))
def test_forms_without_a_captcha_container_show_no_blocker(page, ats):
    # Ashby carries an empty reCAPTCHA response box and still takes applications (9 in the log), so it must not count.
    assert read(page, ats).blocker == ""
