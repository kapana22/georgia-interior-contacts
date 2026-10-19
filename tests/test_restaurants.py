import pytest
from georgia_leads.restaurants import fetch_page, BASE
from georgia_leads.model import listing_records
from georgia_contacts.network import Client, FetchError

HTML=b'''<table class="SR_" id="SR__1"><tr><td><a class="SR_name_" href="company.php?lan=eng&id=135882">Keria</a><div class="SR_address_">Tbilisi. Example Street</div><div class="SR_tel_">(595) 01 55 82, 238 08 02</div><a href="mailto:business@cafe.ge">email</a><a href="https://cafe.ge">website</a><a href="company.php?id=999">All objects</a></td></tr></table><footer>support@portal.ge</footer>'''


def test_restaurant_card_identity_contacts_and_short_number():
    rows=listing_records(HTML,BASE,'now','food')
    assert len(rows)==1 and rows[0]['business_key']=='yell:135882'
    facts={(f['kind'],f['value']) for f in rows[0]['facts']}
    assert ('phone','+995595015582') in facts
    assert ('phone_unverified','238 08 02') in facts
    assert ('email','business@cafe.ge') in facts
    assert ('website','https://cafe.ge/') in facts
    assert not any('support@' in v for k,v in facts)


def test_published_batches_resume_without_refetching_index():
    class Fixture:
        def __init__(self):self.calls=[]
        def fetch(self,url,**kw):
            self.calls.append((url,kw))
            if url==BASE:return url,b'<script>var markers_mt=123;</script>'
            if 'load_fad' in url:return url,b'3.1,2.3'
            return url,HTML
    client=Fixture();cursor={}
    url,body,pages=fetch_page(client,BASE,cursor)
    assert len(pages)==2 and cursor['published_records']==3
    fetch_page(client,pages[1],cursor)
    assert len(client.calls)==4
    assert client.calls[-1][1]['form']['x']=='3'


def test_invalid_index_does_not_invent_pages():
    class Fixture:
        def fetch(self,url,**kw):return url,b'<script>var markers_mt=123;</script>' if url==BASE else b'100.1,2'
    with pytest.raises(FetchError,match='restaurant_index_count_mismatch'):fetch_page(Fixture(),BASE,{})


def test_directory_post_cannot_call_other_endpoints():
    client=Client(0)
    with pytest.raises(FetchError,match='unsupported_directory_request'):
        client.fetch('https://www.yell.ge/delete_to_my_yellow_book.php',form={'n':'x'})
