import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from georgia_contacts.business_catalogues import geostat_page, html_page, task_for
from georgia_contacts.catalogue_collector import prepare, collect, enqueue_websites, ready, buffer
from georgia_contacts.network import FetchError, Deadline
from georgia_contacts.urls import domain

CONFIG = json.loads((Path(__file__).parents[1] / 'config/business-catalogues.json').read_text())
GEOSTAT = CONFIG['sources'][0]['seed']
STAMP = '2026-10-30T12:00:00Z'


def registry_body(page=1, total_pages=2):
    return json.dumps({'data': [{'Legal_Code': '123456789', 'Full_Name': 'შპს სატესტო',
        'Legal_Form_ID': 1, 'ISActive': 1, 'Email': 'office@business.ge', 'mob': '599123456',
        'web': 'business.ge/contact', 'Head': 'Do not store', 'Head_PN': '12345678901',
        'Personal_no': '12345678901', 'Partner': 'Do not store', 'Partner_PN': '12345678901'}],
        'pagination': {'page': page, 'limit': 500, 'totalPages': total_pages, 'total': 501}}).encode()


def test_registry_corporate_allowlist_contacts_and_server_pagination():
    records, tasks, meta = geostat_page(registry_body(), GEOSTAT, STAMP)
    row = records[0]
    assert row['tax_id'] == '123456789' and row['legal_status'] == 'შპს'
    assert {('phone', '+995599123456'), ('email', 'office@business.ge'),
            ('website', 'https://business.ge/contact')} <= {(f['kind'], f['value']) for f in row['facts']}
    assert '12345678901' not in json.dumps(records) and 'Do not store' not in json.dumps(records)
    assert all('source_url' in f for f in row['facts'])
    assert parse_qs(urlsplit(tasks[0]['url']).query)['page'] == ['2']
    assert meta['available_active_llcs'] == 501
    assert not geostat_page(registry_body(total_pages=1), GEOSTAT, STAMP)[1]


@pytest.mark.parametrize('change', ['wrong_page', 'empty_page', 'wrong_form', 'personal_tax_id', 'inactive'])
def test_registry_rejects_changed_filter_or_response_without_advancing(change):
    data = json.loads(registry_body())
    if change == 'wrong_page': data['pagination']['page'] = 4
    if change == 'empty_page': data['data'] = []
    if change == 'wrong_form': data['data'][0]['Legal_Form_ID'] = 30
    if change == 'personal_tax_id': data['data'][0]['Legal_Code'] = '12345678901'
    if change == 'inactive': data['data'][0]['ISActive'] = 0
    with pytest.raises(FetchError):
        geostat_page(json.dumps(data), GEOSTAT, STAMP)


def test_08_cards_and_profile_do_not_absorb_neighbour_or_portal_contacts():
    body = '''<div class="card"><div class="card-top"><a href="/organizations/view/12/test">Test Ltd</a></div>
      <a href="tel:599123456">Call</a></div><footer><a href="tel:577111222">Portal</a></footer>
      <a href="/organizations/filter/test?category=2&page=2">Next</a>
      <a href="https://evil.test/organizations/view/23/test">External</a>'''
    task = {'url': 'https://08.ge/organizations/filter/test?category=2', 'kind': 'listing'}
    rows, tasks, _ = html_page('08', body, task, STAMP)
    assert len(rows) == 1 and rows[0]['id'] == '08:12'
    assert [f['value'] for f in rows[0]['facts'] if f['kind'] == 'phone'] == ['+995599123456']
    assert len(tasks) == 2
    profile = '''<h1>Test Ltd</h1><div><p><b>საკონტაქტო მონაცემები</b></p>
      <a href="tel:599123456">Call</a><a href="https://business.ge">Website</a></div>
      <aside><a href="tel:577111222">Other company</a></aside><footer>portal@portal.ge</footer>'''
    rows, _, _ = html_page('08', profile, task_for('08', 'https://08.ge/organizations/view/12/test'), STAMP)
    assert {(f['kind'], f['value']) for f in rows[0]['facts']} == {('phone', '+995599123456'), ('website', 'https://business.ge/')}


def test_1020_embedded_phone_ids_and_profile_share_links_are_isolated():
    body = '''<div class="items_list"><div class="item" id="business_22"><div class="item_tl">
      <a href="/business/abc/test">Test</a></div></div></div>
      <script>app.phones('[{"id":22,"phones":{"original":"599123456"}},{"id":33,"phones":[{"original":"577111222"}]},{"id":44,"phones":{"hidden":null,"original":""}}]')</script>'''
    rows, _, _ = html_page('1020', body, {'url': 'https://www.1020.ge/category/test', 'kind': 'listing'}, STAMP)
    assert [f['value'] for f in rows[0]['facts'] if f['kind'] == 'phone'] == ['+995599123456']
    profile = '''<h1>Test</h1><div class="detail_item"><div class="upper_main"><a href="tel:599123456">Call</a></div>
      <div class="info_list"><a href="https://business.ge">Website</a></div>
      <div class="share_stuff"><a href="https://reddit.com/submit">Share</a></div>
      <div id="commentSection">reviewer@private.ge</div></div><footer>portal@portal.ge</footer>'''
    rows, _, _ = html_page('1020', profile, task_for('1020', 'https://www.1020.ge/business/abc/test'), STAMP)
    assert {(f['kind'], f['value']) for f in rows[0]['facts']} == {('phone', '+995599123456'), ('website', 'https://business.ge/')}


def test_city24_all_sectors_follow_only_published_links_and_meta_opt_out():
    task = {'url': 'https://www.city24.ge/ge/georgia/catalogue/shops', 'kind': 'listing'}
    body = '<a href="/ge/tbilisi/organizations/1/shop">Shop</a><a href="/ge/georgia/catalogue/shops/page-2">Next</a><a href="/en/georgia/catalogue/shops">English</a>'
    _, tasks, _ = html_page('city24', body, task, STAMP)
    assert len(tasks) == 2 and tasks[0]['identity'] == '1'
    with pytest.raises(FetchError, match='opt_out'):
        html_page('city24', '<meta name="robots" content="noindex">' + body, task, STAMP)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.counts = {}
        self.calls = []

    def check(self): pass

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception): raise response
        self.counts[domain(url)] = self.counts.get(domain(url), 0) + 1
        return url, response


def test_interruption_keeps_buffer_and_next_cursor_for_idempotent_resume():
    config = {'sources': [CONFIG['sources'][0]]}
    state = prepare({}, config)
    next_url = geostat_page(registry_body(), GEOSTAT, STAMP)[1][0]['url']
    first = FakeClient({GEOSTAT: registry_body(), next_url: Deadline()})
    checkpoints = []
    collect(state, config, lambda s: checkpoints.append(json.loads(json.dumps(s))), client=first)
    restored = checkpoints[-1]
    assert len(restored['outbox']) == 1 and restored['sources']['geostat']['queue'][0]['url'] == next_url
    second = FakeClient({next_url: registry_body(page=2)})
    collect(restored, config, lambda s: None, client=second)
    assert second.calls == [next_url]
    assert len(restored['outbox']) == 1 and not restored['sources']['geostat']['queue']


def test_one_blocked_host_does_not_stop_other_sources_or_spin():
    config = {'sources': CONFIG['sources'][:2]}
    state = prepare({}, config)
    catalogue = '<a href="/organizations/filter">Catalogue</a>'
    client = FakeClient({GEOSTAT: FetchError('blocked', 'robots_disallowed'), 'https://08.ge/': catalogue,
                         'https://08.ge/organizations/filter': catalogue})
    collect(state, config, lambda s: None, client=client)
    assert state['sources']['geostat']['queue'] and state['sources']['geostat']['retry_after'] > 0
    assert not state['sources']['08']['queue'] and not ready(state)


def test_registry_budget_is_five_pages_and_leaves_other_sources_a_turn():
    config = {'sources': CONFIG['sources'][:2]}
    state = prepare({}, config)
    catalogue = '<a href="/organizations/filter">Catalogue</a>'
    responses = {'https://08.ge/': catalogue, 'https://08.ge/organizations/filter': catalogue}
    url = GEOSTAT
    for page in range(1, 7):
        responses[url] = registry_body(page=page, total_pages=7)
        url = geostat_page(responses[url], url, STAMP)[1][0]['url']
    client = FakeClient(responses)
    collect(state, config, lambda s: None, client=client)
    assert client.counts['br-api.geostat.ge'] == 5
    assert client.counts['08.ge'] == 2
    pending = state['sources']['geostat']['queue'][0]['url']
    assert parse_qs(urlsplit(pending).query)['page'] == ['6']


def test_website_enrichment_uses_existing_identity_and_published_contact_page():
    state = prepare({}, {'sources': []})
    rows = [{'id': 'a', 'name': 'A', 'website': 'https://business.ge'},
            {'id': 'b', 'name': 'B', 'website': 'https://shared.ge'},
            {'id': 'c', 'name': 'C', 'website': 'https://shared.ge'},
            {'id': 'd', 'name': 'D', 'website': 'https://facebook.com/d'}]
    enqueue_websites(state, rows)
    assert [t['id'] for t in state['website_queue']] == ['a']
    client = FakeClient({'https://business.ge/': '<a href="/contact">Contact</a>',
                         'https://business.ge/contact': '<a href="mailto:office@business.ge">Email</a>'})
    collect(state, {'sources': []}, lambda s: None, client=client)
    # A second bounded pass resumes the saved contact-page task.
    collect(state, {'sources': []}, lambda s: None, client=client)
    assert state['outbox']['a']['facts'][0]['value'] == 'office@business.ge'
    assert len(state['website_done']) == 1
    enqueue_websites(state, rows)
    assert not state['website_queue']
