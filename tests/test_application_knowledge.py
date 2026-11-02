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
    ('Have you worked with AI coding agents ?*', 'Yes'),
    ('Do you have the right to work in the United States, without requiring company sponsorship?', 'Yes'),
    ('Are you authorized to work in the country you currently reside in?', 'Yes'),
])
def test_historical_known_questions(label, expected):
    assert aa.resolve(field(label), aa.DEFAULT_PROFILE, BANK)[:2] == ('fill', expected)


def test_a_required_question_that_he_already_live_elsewhere_skips_the_job():
    outcome, _value, why = aa.resolve(field('Are you currently based in the US East Coast ?*'), aa.DEFAULT_PROFILE, BANK)
    assert outcome == 'giveup' and 'Minnesota' in why
    for state in ('Texas', 'Mississippi', 'Louisiana', 'Alabama', 'Florida', 'Georgia', 'South Carolina'):
        answer = aa.resolve(field(f'Are you currently based in {state}?'), aa.DEFAULT_PROFILE, BANK)
        assert answer[:2] == ('fill', 'Yes'), state


def test_birth_date_clearance_car_and_termination():
    assert aa.resolve(field('Date of birth', 'text', []), aa.DEFAULT_PROFILE, {})[:2] == ('fill', '09/13/1995')
    assert aa.resolve(field('Date of birth', 'date', []), aa.DEFAULT_PROFILE, {})[:2] == ('fill', '1995-09-13')
    assert aa.resolve(field('Are you eligible to obtain a security clearance?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'No')
    assert aa.resolve(field('Do you currently hold a clearance?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'No')
    assert aa.resolve(field('What is your current salary?', 'text', []), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'N/A')
    assert aa.resolve(field('May we contact your current employer?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'No')
    assert aa.resolve(field('Do you own a car?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Yes')
    assert aa.resolve(field('Are you at least 21 years of age?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Yes')
    assert aa.resolve(field('What country are you based in?', 'text', []), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'United States')
    policy = ('Orbis maintains a zero-tolerance policy on unauthorized assistance. During interviews you may not use AI tools.')
    assert aa.resolve(field(policy), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Yes')
    assert aa.resolve(field("Driver's license number", 'text', []), aa.DEFAULT_PROFILE, {})[0] == 'ask'
    fired = field('Have you ever been fired or terminated?')
    assert aa.resolve(fired, aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Yes')
    reason = field('If yes, explain the reason for leaving', 'textarea', [])
    text = aa.resolve(reason, aa.DEFAULT_PROFILE, {})[1]
    assert 'Epicor' in text and 'Citizen Observer' in text and 'tip411' not in text


def test_references_id_record_schedule_and_travel():
    assert aa.resolve(field('Please list your professional references', 'text', []), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'None')
    assert aa.resolve(field('Do you have professional references?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'No')
    assert aa.resolve(field('Do you have a valid driver\'s license?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Yes')
    assert aa.resolve(field('Have you ever been convicted of a felony?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'No')
    assert aa.resolve(field('Are you willing to work overtime, nights, and weekends?'), aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Yes')
    veteran = field('Veteran Status', 'select', ['I am a veteran', 'I am not a protected veteran', 'I prefer not to say'])
    assert aa.resolve(veteran, aa.DEFAULT_PROFILE, {})[1] == 'I am not a protected veteran'
    bands = field('What percentage of travel are you comfortable with?', 'select', ['10%', '25%', '50%', '75%'])
    assert aa.resolve(bands, aa.DEFAULT_PROFILE, {}, job={'description': 'No travel mentioned.'})[1] == '50%'
    posted = field('Travel requirement', 'select', ['Up to 25%', 'Up to 50%', 'Up to 75%'])
    assert aa.resolve(posted, aa.DEFAULT_PROFILE, {}, job={'description': 'This role includes up to 75% travel.'})[1] == 'Up to 75%'
    volume = field('How many customer interactions have you handled per day in previous support roles?', 'text', [])
    assert aa.resolve(volume, aa.DEFAULT_PROFILE, {})[:2] == ('fill', '0')


def test_languages_pay_and_work_he_has_not_done():
    estonian = field('What is your Estonian proficiency?', 'select', ['None', 'Beginner', 'Fluent'])
    assert aa.resolve(estonian, aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'None')
    county = field('County', 'text', [])
    assert aa.resolve(county, aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'Hennepin')
    tickets = field('How many years of customer support experience do you have?', 'text', [])
    assert aa.resolve(tickets, aa.DEFAULT_PROFILE, {})[:2] == ('fill', '0')
    kafka = field('Have you sold Kafka?')
    assert aa.resolve(kafka, aa.DEFAULT_PROFILE, {})[:2] == ('fill', 'No')
    nursing = field('Do you have experience in nursing?')
    assert aa.resolve(nursing, aa.DEFAULT_PROFILE, {})[0] == 'ask'
    applied = field('Have you applied to this company in the last 6 months?')
    assert aa.resolve(applied, aa.DEFAULT_PROFILE, {}, job={'company': 'Acme'})[:2] == ('fill', 'No')
    original = aa._applied_recently
    aa._applied_recently = lambda company, within_days=183: company == 'Acme'
    try:
        assert aa.resolve(applied, aa.DEFAULT_PROFILE, {}, job={'company': 'Acme'})[:2] == ('fill', 'Yes')
    finally:
        aa._applied_recently = original


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


def test_a_plain_arithmetic_blank_is_left_unanswered():
    question = 'To help us prevent automated job applications, solve for X: 1 + X = 3'
    assert aa.resolve(field(question, 'text', []), aa.DEFAULT_PROFILE, {})[0] == 'ask'


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
