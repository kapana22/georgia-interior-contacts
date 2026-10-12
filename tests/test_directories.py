from georgia_contacts.directories import yell_candidates, city24_links, city24_profile, source_key
from georgia_contacts.state import merge_record
from georgia_contacts.cli import crawl
from georgia_contacts.extract import extract
from georgia_contacts.discovery import discover


def card(number, name, email, phone, website=''):
    site = f'<a id="SR_links_2_New" href="{website}">website</a>' if website else ''
    return f'''<div id="SR_div_{number}"><a href="company.php?id={number}&lan=geo">{name}</a>
    <p>თბილისი. მისამართი: სატესტო ქუჩა</p><p>ტელ: {phone}</p>
    {site}<a href="mailto:{email}">email</a><p>ინტერიერის დიზაინი</p></div>'''


def test_directory_businesses_without_websites_have_separate_identity():
    html = card(1,'სტუდია','one@studio.test','599554188') + card(2,'სტუდია','two@studio.test','551147111')
    sources = yell_candidates(html,'https://www.yell.ge/companies.php?rub=151&lan=geo','2026-10-12')
    assert len(sources)==2 and source_key(sources[0]) != source_key(sources[1])
    class NoNetwork:
        def fetch(self,*a,**kw): raise AssertionError('Inline contacts do not fetch portal as a business')
    records=[]
    for source in sources:
        row=crawl(NoNetwork(),source,[])
        merge_record(records,row)
    assert len(records)==2 and records[0]['domains']==[]
    assert all('directory_only_service_claim' in r['review_reasons'] for r in records)
    assert all(f['source_url']=='https://www.yell.ge/companies.php?rub=151&lan=geo' for r in records for f in r['facts'])


def test_catalogue_facts_merge_with_own_domain_and_normalized_contacts():
    sources=yell_candidates(card(1,'სტუდია','one@studio.test','599554188','https://studio.test'),'https://www.yell.ge/companies.php?rub=151','today')
    assert source_key(sources[0])=='studio.test'
    assert any(f['kind']=='phone' and f['value']=='+995599554188' for f in sources[0]['directory_facts'])


def test_city24_profile_excludes_portal_support_and_neighbour_contacts():
    html='''<h1>სტუდია</h1><div id="tab_contact"><ul><li class="csd-org-li-phone"><a href="tel:+995599554188">599554188</a></li><li class="csd-org-li-www"><a href="https://studio.test">website</a></li><li class="csd-org-li-address">თბილისი</li></ul></div>
    <div id="tab_about">ინტერიერის დიზაინი საქართველოში</div>
    <footer>support@portal.test</footer><div>Neighbour: neighbour@other.test</div>'''
    p=city24_profile(html,'https://www.city24.ge/ge/tbilisi/organizations/1/studio/','today','Studio')
    assert p['websites']==['https://studio.test/']
    assert not any('portal.test' in f['value'] or 'other.test' in f['value'] for f in p['facts'])
    assert any(f['kind']=='city' and f['value']=='თბილისი' for f in p['facts'])


def test_pagination_cursor_continues_next_run_with_same_budget():
    class Client:
        def __init__(self):self.calls=[]
        def fetch(self,url,**kw):
            self.calls.append(url)
            if 'page-2' in url:return url,b'<ul class="csd-organizations-ul"></ul>'
            return url,b'<div class="csd-pagination"><a href="/ge/tbilisi/catalogue/real-estate-construction/interior-design/page-2">2</a></div>'
    config={'directories':[{'adapter':'city24','enabled':True,'url':'https://www.city24.ge/ge/tbilisi/catalogue/real-estate-construction/interior-design/','max_pages':1,'policy_reviewed_at':'today'}]}
    progress={};c=Client();discover(c,config,[],progress);discover(c,config,[],progress)
    assert len(c.calls)==2 and c.calls[1].endswith('page-2')


def test_public_practitioner_jsonld_but_not_review_authors():
    html='''<script type="application/ld+json">{"@type":"Person","name":"სატესტო დიზაინერი","jobTitle":"Interior Designer","email":"designer@studio.test","telephone":"+995599554188"}</script>
    <script type="application/ld+json">{"@type":"Person","name":"Reviewer","email":"reviewer@private.test"}</script>'''
    p=extract(html,'https://studio.test/','today')
    assert any(f['kind']=='email' and f['value']=='designer@studio.test' for f in p['facts'])
    assert not any(f['value']=='reviewer@private.test' for f in p['facts'])


def test_catalogue_social_website_is_link_only_never_crawled():
    source=yell_candidates(card(1,'სტუდია','one@studio.test','599554188','https://facebook.com/studio'),'https://www.yell.ge/companies.php?rub=151','today')[0]
    assert source['kind']=='listing'
    assert any(f['kind']=='facebook' for f in source['directory_facts'])
    class NoNetwork:
        def fetch(self,*a,**kw): raise AssertionError('Social profiles must not be fetched')
    assert crawl(NoNetwork(),source,[]) is not None
    events=[]
    assert crawl(NoNetwork(),{'url':'https://facebook.com/studio'},events) is None
    assert events[0]['reason']=='social_profile_not_crawled'


def test_link_discovery_honors_meta_and_link_opt_out():
    import pytest
    from georgia_contacts.discovery import approved_external_links
    from georgia_contacts.network import FetchError
    config={'link_selector':'a[href]'}
    with pytest.raises(FetchError):
        approved_external_links('<meta name="robots" content="nofollow"><a href="https://studio.test">Website</a>','https://guide.test',config)
    assert approved_external_links('<a rel="nofollow" href="https://studio.test">Website</a>','https://guide.test',config)==[]
