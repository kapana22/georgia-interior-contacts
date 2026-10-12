import copy
import importlib.util
import json
import time
from pathlib import Path
import pytest
from georgia_leads.model import listing_records, merge_lead, opportunity, changes, published_pages, page_key
from georgia_leads.cli import load_leads, prepare, collect, export, read_config
from georgia_contacts.network import FetchError
from georgia_contacts.state import save_state

URL = 'https://www.yell.ge/companies.php?rub=398&lan=eng'
HTML = '''<div id="SR_div_100"><a href="company.php?id=100">სალონი მზია</a><p>თბილისი Tel:</p><a href="tel:+995599123456">ტელეფონი</a><a href="mailto:salon@example.ge">ელფოსტა</a><a href="https://instagram.com/salon">Instagram</a><div class="review">private@example.org +995599888888</div></div><footer>support@yell.ge</footer>'''


def listing(html=HTML, category='beauty'):
    return listing_records(html, URL, '2026-10-12T19:00:00+00:00', category)[0]


def config():
    return {'pipeline':'b2b-leads','categories':[{'id':'beauty','name':'სალონები','url':URL,'max_pages':10}]}


def test_public_business_contacts_only_and_no_site_inference():
    record = listing()
    assert record['name'] == 'სალონი მზია'
    assert {f['value'] for f in record['facts'] if f['kind']=='email'} == {'salon@example.ge'}
    assert {f['value'] for f in record['facts'] if f['kind']=='phone'} == {'+995599123456'}
    assert opportunity(record) == 'social_only'
    assert record['email_domain_hints'] == ['example.ge']
    assert record['checks'][0]['source_url'] == URL
    assert record['status'] == 'review'


def test_website_reference_is_not_falsely_absent():
    record = listing(HTML.replace('</p>', '</p><a href="/link/site.php?id=1">Website</a>'))
    assert opportunity(record) == 'uncertain'
    record = listing(HTML.replace('</p>', '</p><a href="https://salon.example.ge">Website</a>'))
    assert opportunity(record) == 'website_listed'
    record['website_checks'] = [{'url':'https://salon.example.ge','outcome':'uncertain'}]
    assert opportunity(record) == 'website_listed'


def test_exact_merges_union_categories_and_name_only_does_not_merge():
    records=[]; first=merge_lead(records,listing()); second=listing(category='auto');merge_lead(records,second)
    assert len(records)==1 and records[0]['categories']==['auto','beauty']
    second=listing(HTML.replace('SR_div_100','SR_div_101').replace('id=100','id=101').replace('599123456','599123457').replace('salon@example.ge','other@example.ge'))
    merge_lead(records,second)
    assert len(records)==2


def test_changes_include_category_and_website_checks_but_ignore_dates():
    records=[];merge_lead(records,listing());before=copy.deepcopy(records)
    records[0]['checks'][0]['checked_at']='2026-10-13'
    assert changes(before,records,'now')==[]
    records[0]['categories'].append('auto')
    assert changes(before,records,'now')[0]['type']=='changed'


def test_only_observed_pagination_and_same_category():
    html='''<script type="application/ld+json">{"@type":"ItemList","url":"https://www.yell.ge/companies.php?rub=398&lan=eng&SR_pg=1"}</script><div onclick="F_change_SR_pg(2)">2</div><a href="?rub=398&lan=eng&SR_pg=3&filter=x">bad</a><a href="?rub=19&lan=eng&SR_pg=7">wrong category</a>'''
    pages=published_pages(html,URL)
    assert pages==['https://www.yell.ge/companies.php?rub=398&lan=eng&SR_pg=2']
    assert published_pages('<div onclick="F_change_SR_pg(8)">8</div>',URL)==[]
    assert page_key(URL)==page_key(URL+'&SR_pg=1')


def test_wrong_pipeline_state_rejected(tmp_path):
    path=tmp_path/'state.json';save_state(path,{'schema_version':1,'records':[]})
    with pytest.raises(ValueError): load_leads(path)


def test_network_failure_preserves_old_contacts_and_bounded_attempts(tmp_path):
    state=load_leads(tmp_path/'state.json');prepare(state,config());merge_lead(state['records'],listing())
    before=copy.deepcopy(state['records'])
    class Offline:
        cooldowns={}
        def fetch(self,url):raise FetchError('unreachable','network_error')
    report={'events':[],'pages_processed':0,'sites_processed':0}
    collect(state,config(),Offline(),report=report)
    assert state['records']==before and not state['cursors']['beauty']['queue']
    assert len(report['events'])==3


def test_partial_limit_buffers_businesses_without_loss(tmp_path):
    state=load_leads(tmp_path/'state.json');prepare(state,config())
    class Fixture:
        cooldowns={}
        def fetch(self,url):return url,(HTML+HTML.replace('SR_div_100','SR_div_101').replace('id=100','id=101').replace('599123456','599123457').replace('salon@example.ge','other@example.ge')).encode()
    collect(state,config(),Fixture(),limit=1)
    assert len(state['records'])==1 and len(state['pending'])==1
    collect(state,config(),Fixture(),limit=1)
    assert len(state['records'])==2 and not state['pending']


def test_export_bom_and_pipeline_snapshot(tmp_path):
    state=load_leads(tmp_path/'state.json');prepare(state,config());merge_lead(state['records'],listing())
    report={'started_at':'now','events':[],'pages_processed':0,'sites_processed':0}
    export(tmp_path,state,[],report,config())
    assert (tmp_path/'leads.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert json.loads((tmp_path/'snapshot.json').read_text())['pipeline']=='b2b-leads'
    assert report['prospects_total']==1


def test_config_is_separate_and_rejects_blocked_filters(tmp_path):
    with pytest.raises(ValueError):read_config('config/sources.json')
    data=config();data['categories'][0]['url']+='&filter=bad';path=tmp_path/'config.json';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):read_config(path)


def test_continuation_does_not_accept_interior_report():
    spec=importlib.util.spec_from_file_location('continue_leads',Path('scripts/continue_leads.py'));mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    report={'continuous':True,'work_remaining':True,'sources_file':'config/leads-sources.json','batch_limit':50}
    assert mod.decision(report,[])[0] is None
    report['pipeline']='b2b-leads'
    payload,_=mod.decision(report,[])
    assert payload['inputs']['sources']=='config/leads-sources.json'


def test_dns_failure_is_not_a_website_absence_signal():
    record = listing(HTML.replace('</p>', '</p><a href="https://salon.example.ge">Website</a>'))
    record['website_checks'] = [{'url':'https://salon.example.ge','outcome':'unreachable','reason':'unsafe_or_unresolved_url'}]
    assert opportunity(record) == 'website_listed'
    record['website_checks'][0]['reason'] = 'http_404'
    assert opportunity(record) == 'website_unreachable'
