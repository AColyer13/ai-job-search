import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'job_scraper'))
import apply_answers as aa
from apply_forms import Field, normalize_label
from application_knowledge import question_kind

BANK = {'auth': {'label': 'Are you authorized to work in the United States?', 'answer': 'Yes'},
        'sponsor': {'label': 'Do you require sponsorship?', 'answer': 'No'}}


def field(label, kind='combobox', options=None):
    return Field(kind=kind, label=label, key=normalize_label(label), required=True,
                 options=['Yes', 'No'] if options is None else options)


@pytest.mark.parametrize('label,expected', [
    ('Are you currently based in the US East Coast ?*', 'No'),
    ('Have you worked with AI coding agents ?*', 'Yes'),
    ('Do you have the right to work in the United States, without requiring company sponsorship?', 'Yes'),
    ('Are you authorized to work in the country you currently reside in?', 'Yes'),
])
def test_historical_known_questions(label, expected):
    assert aa.resolve(field(label), aa.DEFAULT_PROFILE, BANK)[:2] == ('fill', expected)


def test_inference_answer_keeps_infrastructure_gap_visible():
    outcome, text, source = aa.resolve(field('Describe your experience with inference, KV cache, tokenomics', 'textarea', []), aa.DEFAULT_PROFILE, BANK)
    assert outcome == 'fill' and 'Ollama' in text and 'not documented direct production work tuning KV caches' in text
    assert 'profile' in source


@pytest.mark.parametrize('label,expected', [
    ('Have you never worked with AI coding agents?', 'No'),
    ('Are you authorized to work in Canada?', 'No'),
    ('Do you consent to Metaview transcribing our interviews?', 'Yes'),
    ('Do you require reasonable adjustments?', 'No'),
    ('Have you worked with AI coding agents for ten years?', 'No'),
    ('Are you currently based in the US East Coast or willing to relocate?', 'Yes'),
    ('Will you now or in the future require sponsorship for employment visa status?', 'No'),
    ('Are you authorized to work in the country you currently reside in?', 'Yes'),
    ('Are you over the age of 18 years?', 'Yes'),
    ('Are you legally eligible to be employed in the United States?', 'Yes'),
])
def test_standing_facts_answer_without_telegram(label, expected):
    assert aa.resolve(field(label), aa.DEFAULT_PROFILE, {})[:2] == ('fill', expected)


def test_a_plain_arithmetic_blank_is_not_a_captcha():
    question = 'To help us prevent automated job applications, solve for X: 1 + X = 3'
    assert aa.resolve(field(question, 'text', []), aa.DEFAULT_PROFILE, {})[:2] == ('fill', '2')


def test_citizenship_and_birth_country_stay_blank_until_the_profile_records_them():
    assert aa.resolve(field('Are you a US Citizen?'), aa.DEFAULT_PROFILE, BANK)[0] == 'ask'
    assert aa.resolve(field('In what country were you born?'), aa.DEFAULT_PROFILE, {})[0] == 'ask'
    profile = {**aa.DEFAULT_PROFILE, 'citizenship': 'US Citizen', 'birth_country': 'United States'}
    assert aa.resolve(field('Are you a US Citizen?'), profile, {})[:2] == ('fill', 'Yes')
    born = field('In what country were you born?', options=['Canada', 'United States'])
    assert aa.resolve(born, profile, {})[:2] == ('fill', 'United States')


def test_searchable_country_menu_does_not_need_to_list_every_country():
    f = field('What is your current country of residence?', options=['Albania', 'Canada'])
    assert aa.resolve(f, aa.DEFAULT_PROFILE, BANK)[:2] == ('fill', 'United States')


def test_user_experience_answer_can_be_remembered_but_job_motivation_cannot(tmp_path):
    path = tmp_path / 'bank.json'
    assert aa.save_to_bank('experience', 'Describe your experience with local LLMs', 'User-confirmed experience.', path=path)
    assert not aa.save_to_bank('why', 'Why this company?', 'A job-specific reason.', path=path)


def test_reader_defect_is_not_a_candidate_question():
    assert question_kind(field('section_123_question_4')) == 'form_reader_gap'
