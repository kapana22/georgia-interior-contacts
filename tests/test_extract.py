from pathlib import Path
import pytest
from georgia_contacts.extract import extract, normalize_phone, assess, social_url


@pytest.mark.parametrize(('raw', 'expected'), [
    ('599 55 41 88', '+995599554188'), ('+995 (599) 55-41-88', '+995599554188'),
    ('00995 599 55 41 88', '+995599554188'), ('995599554188', '+995599554188'),
    ('0322 05 39 39', '+995322053939'), ('+995 32 2 25 89 79', '+995322258979'),
    ('2053939', ''), ('123456789', ''), ('599554188777', ''), ('2026-10-12', ''),
    ('+44 20 7946 0958', '+442079460958'), ('tel:%2B995599554188', '+995599554188')])
def test_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_html_jsonld_and_provenance():
    parsed = extract(Path('tests/fixtures/business.html').read_bytes(), 'https://studio.test/', '2026-10-12')
    values = {(f['kind'], f['value']) for f in parsed['facts']}
    assert ('name', 'სტუდია ტესტი') in values
    assert ('email', 'info@studio.test') in values
    assert ('email', 'hello@studio.test') in values
    assert ('phone', '+995322053939') in values
    assert ('city', 'თბილისი') in values
    assert ('instagram', 'https://www.instagram.com/studio.test') in values
    assert ('facebook', 'https://www.facebook.com/profile.php?id=12345') in values
    assert not any('hidden@' in x[1] or 'private@' in x[1] or 'builder.test' in x[1] for x in values)
    assert parsed['links'] == ['https://studio.test/კონტაქტი', 'https://studio.test/about']
    assert all(f['source_url'] == 'https://studio.test/' and f['last_seen'] == '2026-10-12' for f in parsed['facts'])
    assert assess(parsed['text'], parsed['facts']) == []


def test_no_guesses_and_placeholder_suppression():
    parsed = extract('<h1>Architecture</h1><p>Project in Batumi</p><p>info@mysite.com</p><a href="tel:2053939">Phone</a>', 'https://studio.test/', 'today')
    assert not any(f['kind'] in {'city', 'email', 'phone'} for f in parsed['facts'])
    assert 'unverified_phone_structure' in parsed['reasons']
    assert 'interior_service_not_confirmed' in assess(parsed['text'], parsed['facts'])
    assert social_url('https://facebook.com.evil.test/profile') is None


def test_structured_arbitrary_city_and_malformed_ld():
    html = '<script type="application/ld+json">{"@type":"LocalBusiness","address":{"addressLocality":"Ambrolauri"}}</script><script type="application/ld+json">{bad}</script>'
    p = extract(html, 'https://studio.test/', 'today')
    assert any(f['kind'] == 'city' and f['value'] == 'Ambrolauri' for f in p['facts'])
    assert 'malformed_json_ld' in p['reasons']


def test_meta_opt_out():
    p = extract('<meta name="robots" content="noindex"><p>info@studio.test</p>', 'https://studio.test/', 'today')
    assert p['blocked'] and not p['facts']


def test_question_mark_does_not_hide_later_email():
    parsed=extract('<p>Questions?</p><p>hello@studio.test</p>', 'https://studio.test/', 'today')
    assert any(f['kind']=='email' and f['value']=='hello@studio.test' for f in parsed['facts'])


def test_social_case_normalization():
    assert social_url('https://www.facebook.com/Interiorismo.ge')==social_url('https://facebook.com/interiorismo.ge/')


def test_automated_access_prohibition():
    p=extract('<p>Automated access is prohibited.</p>', 'https://studio.test/', 'today')
    assert p['blocked']
