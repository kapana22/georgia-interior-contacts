import io

import pytest

from georgia_contacts.pdf_catalogue import catalogue_candidates, read_catalogue
from georgia_contacts.source_pages import page_candidates
from georgia_contacts.export import export_all
from georgia_contacts.network import FetchError


def pdf_source():
    return {'url': 'https://guide.test/catalogue.pdf', 'entries': [
        {'name': 'Studio', 'description_page': 1, 'contact_page': 2, 'expected_heading': 'Studio'}]}


def test_pdf_per_business_provenance_and_historical_marker():
    pages = ['Architecture and Interior Design\nStudio\nInterior design services in Georgia.',
             'CONTACT INFORMATION\nTbilisi, Georgia\n+995 599 554 188\nwork@studio.test\nwww. studio.test']
    result = catalogue_candidates(pages, pdf_source(), 'today', '2020-01-29')
    assert result[0]['url'] == 'https://www.studio.test/'
    assert any(f['kind'] == 'phone' and f['value'] == '+995599554188' for f in result[0]['directory_facts'])
    assert all(f['source_url'].endswith('#page=2') and f['historical_source'] for f in result[0]['directory_facts'])
    assert 'historical_pdf_contacts_require_verification' in result[0]['review_reasons']


def test_pdf_shared_category_heading_does_not_prove_interior_service():
    pages = ['Architecture and Interior Design\nStudio\nRoads and civil engineering.',
             'CONTACT INFORMATION\n+995599554188\nengineer@studio.test\nwww.studio.test']
    result = catalogue_candidates(pages, pdf_source(), 'today')
    assert len(result) == 1 and 'directory_facts' not in result[0]
    # Discover the actually printed website to assess its own service offer.
    assert result[0]['url'] == 'https://www.studio.test/'


def test_pdf_changed_mapping_cannot_assign_contacts_to_wrong_company():
    events = []
    assert catalogue_candidates(['Other business', 'CONTACT INFORMATION\nother@studio.test'], pdf_source(), 'today', events=events) == []
    assert events[0]['reason'] == 'pdf_page_mapping_changed'
    assert catalogue_candidates(['Studio interior design', 'CONTACT INFORMATION\nwork@studio.test'], pdf_source(), 'today')[0]['kind'] == 'listing'
    # Email domain is not turned into an invented business website.
    assert not any(f['kind'] == 'website' for f in catalogue_candidates(['Studio interior design', 'CONTACT INFORMATION\nwork@studio.test'], pdf_source(), 'today')[0]['directory_facts'])


def test_malformed_and_image_only_pdf_are_isolated():
    with pytest.raises(FetchError, match='pdf_parse_'):
        read_catalogue(b'not a pdf', pdf_source(), 'today', lambda: None, [])
    from pypdf import PdfWriter
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO(); writer.write(stream)
    with pytest.raises(FetchError, match='pdf_requires_ocr'):
        read_catalogue(stream.getvalue(), pdf_source(), 'today', lambda: None, [])


def historical_record():
    facts = [{'kind': 'email', 'value': 'work@studio.test', 'source_url': 'https://guide.test/catalogue.pdf#page=2', 'first_seen': 'today', 'last_seen': 'today', 'historical_source': True}]
    record = {'id': 'a', 'name': 'Studio', 'domains': [], 'facts': facts, 'status': 'review', 'review_reasons': [], 'first_seen': 'today', 'last_seen': 'today'}
    return record


def test_history_target_export(tmp_path):
    record = historical_record()
    report = {'started_at': 'today', 'status': 'completed', 'sites_processed': 1, 'events': []}
    export_all(tmp_path, {'records': [record]}, [], report)
    assert report['direct_contacts_unique'] == 1 and report['relevant_direct_contacts_unique'] == 0
    record['facts'].append({**record['facts'][0], 'source_url': 'https://studio.test/contact', 'historical_source': False})
    export_all(tmp_path, {'records': [record]}, [], report)
    assert report['relevant_direct_contacts_unique'] == 1


def test_dizaini_scoped_public_business_reveal_excludes_neighbours_footer():
    html = '''<div id="more-info"><h5>კომპანია : სტუდია</h5><h5>ტიპი : ინტერიერის დიზაინი</h5>
    <p>Based in Tbilisi, Georgia</p><a href="/products/66">Profile</a>
    <div onclick="showAndTrack(this, 141)"><span class="full-number">598703730</span><span class="view-label">ნომრის ნახვა</span></div>
    <a href="https://facebook.com/studio">Website</a></div>
    <div id="reviews">private@reviewer.test</div><footer>info@portal.test +995599554188</footer>'''
    result, more = page_candidates(html, 'https://dizaini.ge/product/141', {'adapter': 'dizaini'}, 'today')
    assert not more and result[0]['business_key'] == 'dizaini:66' and result[0]['kind'] == 'listing'
    facts = result[0]['directory_facts']
    assert any(f['kind'] == 'phone' and f['value'] == '+995598703730' for f in facts)
    assert not any(f['value'] in {'private@reviewer.test', 'info@portal.test', '+995599554188'} for f in facts)


def test_dizaini_does_not_attribute_award_badges_as_business_websites():
    html = '''<div id="more-info"><h5>კომპანია : Studio</h5><h5>ტიპი : ინტერიერის დიზაინი</h5>
    <h5>ვებ-გვერდი: <a href="https://studio.test/">website</a></h5>
    <p>Membership <a href="https://association.test/">Association</a></p></div>'''
    rows, _ = page_candidates(html, 'https://dizaini.ge/product/1', {'adapter': 'dizaini'}, 'today')
    assert rows[0]['url'] == 'https://studio.test/'
    assert not any(f['kind'] == 'website' and 'association.test' in f['value'] for f in rows[0]['directory_facts'])


def test_public_service_contact_without_business_name_keeps_name_empty():
    html = '<div id="more-info"><h5>ტიპი : ინტერიერის დიზაინი</h5><a href="tel:+995599554188">Call</a></div>'
    rows, _ = page_candidates(html, 'https://dizaini.ge/product/2', {'adapter': 'dizaini'}, 'today')
    assert rows[0]['name'] == '' and rows[0]['review_reasons'] == ['business_name_missing']
    assert any(f['kind'] == 'phone' and f['value'] == '+995599554188' for f in rows[0]['directory_facts'])


def test_editorial_uses_body_links_and_published_next_page_only():
    config = {'adapter': 'editorial'}
    html = '<link rel="next" href="/category/people/page/2/"><h3 class="entry-title"><a href="/studio">Studio</a></h3>'
    sites, more = page_candidates(html, 'https://guide.test/category/people/', config, 'today')
    assert not sites and more == ['https://guide.test/studio', 'https://guide.test/category/people/page/2/']
    html = '<div class="entry-content"><a href="https://studio.test">Studio</a><a href="https://linkedin.com/in/person">Person</a></div><footer><a href="https://publisher.test">Publisher</a></footer>'
    sites, more = page_candidates(html, 'https://guide.test/studio', config, 'today')
    assert [s['url'] for s in sites] == ['https://studio.test/'] and not more


def test_awards_only_interior_nomination_not_login_or_private_registration():
    html = '<a href="/en/Awards/2025/219">Residential interior</a><a href="/en/Awards/2025/217">Single-family house</a><a href="/en/registration">Register</a>'
    sites, more = page_candidates(html, 'https://archiaward.com/en/Awards/2025', {'adapter': 'awards'}, 'today')
    assert not sites and more == ['https://archiaward.com/en/Awards/2025/219']


def test_directory_parser_error_does_not_stop_other_sources():
    from georgia_contacts.discovery import discover
    class Client:
        def check(self):
            pass
        def fetch(self, url, **kwargs):
            if url.endswith('.pdf'):
                return url, b'broken'
            return url, b'<a id="SR_links_2_New" href="https://studio.test">website</a>'
    config = {'directories': [
        {'adapter': 'pdf_catalogue', 'url': 'https://guide.test/catalogue.pdf', 'enabled': True, 'policy_reviewed_at': 'today'},
        {'adapter': 'yell', 'url': 'https://www.yell.ge/companies.php?rub=151', 'enabled': True, 'policy_reviewed_at': 'today'}]}
    events = []
    assert discover(Client(), config, events)[0]['url'] == 'https://studio.test/'
    assert events[0]['reason'].startswith('pdf_parse_')


def test_deleted_profile_queue_advances_and_new_directory_revision_restarts_index():
    from georgia_contacts.discovery import discover
    class Client:
        def fetch(self, url, **kwargs):
            if url.endswith('/gone'):
                raise FetchError('unreachable', 'http_404')
            return url, b'<a id="SR_links_2_New" href="https://studio.test">website</a>'
    url = 'https://www.yell.ge/companies.php?rub=151'
    config = {'directories': [{'adapter': 'yell', 'url': url, 'enabled': True, 'max_pages': 2, 'policy_reviewed_at': 'today'}]}
    progress = {url: {'pending': ['https://www.yell.ge/gone', url], 'seen': []}}
    assert discover(Client(), config, [], progress)[0]['url'] == 'https://studio.test/'
    progress = {url: {'pending': ['https://www.yell.ge/old'], 'seen': [url]}}
    config['directories'][0]['revision'] = 2
    assert discover(Client(), config, [], progress)[0]['discovered_from'] == url
