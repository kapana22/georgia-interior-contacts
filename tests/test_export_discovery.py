import csv
import io
import pytest
from georgia_contacts.export import safe_cell, write_csv
from georgia_contacts.discovery import yell_links, discover
from georgia_contacts.cli import crawl


@pytest.mark.parametrize('value', ['=1+1','+995599554188','-1','@SUM(A1)','  =1','\tfoo','\nbar','\ufeff=1'])
def test_formula_guard(value):
    assert safe_cell(value).startswith("'")


def test_bom_and_quotes(tmp_path):
    path=tmp_path/'out.csv'
    write_csv(path,['name','phone'],[{'name':'სტუდია, "ერთი"','phone':'+995599554188'}])
    raw=path.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')
    row=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))[0]
    assert row['name']=='სტუდია, "ერთი"'
    assert row['phone']=="'+995599554188"


def test_directory_no_ads_no_contacts_and_real_pagination_only():
    html='''<a id="SR_links_2_New" href="https://studio.test">website</a>
    <a href="https://ad.test">ad</a><a id="SR_links_2_New" href="https://facebook.com/studio">website</a>
    <a href="/companies.php?rub=151&lan=eng&page=2">2</a>
    <a href="/companies.php?rub=151&sort=alphabet">3</a>'''
    sites,pages=yell_links(html,'https://www.yell.ge/companies.php?rub=151&lan=eng')
    assert [s['url'] for s in sites]==['https://studio.test/']
    assert pages==['https://www.yell.ge/companies.php?rub=151&lan=eng&page=2']


def test_max_five_business_pages_and_js_classification():
    class Client:
        def __init__(self):
            self.calls=[]
        def fetch(self,url,**kw):
            self.calls.append(url)
            return url, b'<p>Interior design in Georgia; call +995599554188 for professional business services.</p>' + b''.join(f'<a href="/contact{i}">Contact</a>'.encode() for i in range(10))
    c=Client()
    record=crawl(c,{'url':'https://studio.test/'},[])
    assert len(c.calls)==5 and record is not None
    class JS:
        def fetch(self,*a,**kw):
            return 'https://studio.test/', b'<div id="root"></div><script src="app.js"></script>'
    events=[]
    assert crawl(JS(),{'url':'https://studio.test/'},events) is None
    assert events[0]['status']=='javascript_required'


def test_directory_page_limit():
    class Client:
        def __init__(self): self.calls=[]
        def fetch(self,url,**kw):
            self.calls.append(url)
            n=len(self.calls)+1
            return url, f'<a href="/companies.php?rub=151&page={n}">{n}</a>'.encode()
    c=Client()
    discover(c,{'directories':[{'enabled':True,'adapter':'yell','policy_reviewed_at':'today','url':'https://www.yell.ge/companies.php?rub=151','max_pages':2}]},[])
    assert len(c.calls)==2
