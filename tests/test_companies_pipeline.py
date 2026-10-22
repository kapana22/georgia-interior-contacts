import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/companies_pipeline.py'
spec = importlib.util.spec_from_file_location('companies_pipeline', SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def test_production_company_sources_are_valid():
    from georgia_leads.cli import read_config
    config = read_config(SCRIPT.parents[1] / 'config/companies-sources.json')
    ids = {category['id'] for category in config['categories']}
    assert {'logistics', 'medical-supply', 'packaging', 'metalwork'} <= ids


def test_b2b_target_stops_before_any_collector_is_called(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {})
    pipeline.write(tmp_path/'tree/data.json',{'records':[{'id':'a','role':'მიმწოდებელი','legal_status':'შპს','phone':'+995599123456'}]})
    monkeypatch.setenv('B2B_TARGET','1')
    monkeypatch.setenv('B2B_MODE','existing')
    monkeypatch.setenv('GITHUB_OUTPUT',str(tmp_path/'outputs'))
    monkeypatch.setattr(pipeline,'run',lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('Collector must not run')))
    pipeline.collect()
    assert pipeline.campaign_progress()['paused']
    assert 'did_work=true' in (tmp_path/'outputs').read_text()


def test_paused_b2b_campaign_disables_schedule_instead_of_dispatching(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {'b2b_campaign':{'paused':True},'work_ready':True})
    calls=[]
    monkeypatch.setattr(pipeline,'run',lambda *args,**kwargs:calls.append(args))
    pipeline.resume()
    assert calls==[('gh','workflow','disable','companies.yml')]


def setup_state(tmp_path, monkeypatch, report):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    monkeypatch.setattr(pipeline, 'TREE', tmp_path / 'tree')
    monkeypatch.setattr(pipeline.time, 'time', lambda: 200000)
    monkeypatch.setattr(pipeline, 'run', lambda *args, **kwargs: None)
    monkeypatch.delenv('REFRESH_CATALOG', raising=False)
    import georgia_leads.cli as leads
    monkeypatch.setattr(leads, 'read_config', lambda path: {'categories': []})
    pipeline.write('.leads-state/developers-saxlebi.json', {'queue': [], 'seen': ['old'], 'records': {}})
    pipeline.write('.leads-state/companies.json', {'records': [], 'cursors': {}})
    (tmp_path / '.leads-state/companies-restored').touch()
    pipeline.write(tmp_path / 'tree/report.json', report)


def test_missing_refresh_timestamp_does_not_stall_forever(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {})
    # Keep this test offline and isolate catalogue scheduling from source parsing.
    import georgia_leads.cli as leads
    monkeypatch.setattr(leads, 'read_config', lambda path: {})
    monkeypatch.setattr(leads, 'prepare', lambda *args, **kwargs: None)
    pipeline.collect()
    assert pipeline.read('.leads-state/developers-saxlebi.json')['queue']
    assert pipeline.read(tmp_path / 'tree/report.json')['catalog_refresh_at'] == 200000


def test_manual_refresh_restarts_completed_catalogues_before_daily_deadline(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {'catalog_refresh_at': 199999})
    pipeline.collect()
    assert pipeline.read('.leads-state/developers-saxlebi.json')['queue'] == []
    import georgia_leads.cli as leads
    monkeypatch.setattr(leads, 'read_config', lambda path: {})
    monkeypatch.setattr(leads, 'prepare', lambda *args, **kwargs: None)
    monkeypatch.setenv('REFRESH_CATALOG', 'true')
    pipeline.collect()
    assert pipeline.read('.leads-state/developers-saxlebi.json')['queue'] == ['https://saxlebi.com/developers']


def test_blocked_developer_listing_does_not_starve_daily_yell_refresh(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {'catalog_refresh_at': 1})
    import georgia_leads.cli as leads
    config={'categories':[{'id':'logistics','url':'https://www.yell.ge/companies.php?rub=28'}]}
    monkeypatch.setattr(leads,'read_config',lambda path:config)
    pipeline.write('.leads-state/developers-saxlebi.json',{'queue':['https://saxlebi.com/developers'],'seen':[], 'records':{},'resume_after':300000})
    pipeline.write('.leads-state/companies.json',{'records':[{'id':'keep'}],'cursors':{'logistics':{'start':config['categories'][0]['url'],'queue':[]}}})
    pipeline.collect()
    assert pipeline.read('.leads-state/companies.json')['cursors']['logistics']['queue']
    assert pipeline.read('.leads-state/companies.json')['records']==[{'id':'keep'}]
    assert pipeline.read('.leads-state/developers-saxlebi.json')['resume_after']==300000
    assert pipeline.read(tmp_path/'tree/report.json')['catalog_refresh_at']==200000


def test_pending_records_resume_even_without_pages_or_with_cooldown(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {'catalog_refresh_at': 199999})
    pipeline.write('.leads-state/companies.json', {'pending':[{'name':'Buffered'}], 'cursors':{}, 'cooldowns':{'yell.ge':300000}})
    assert pipeline.report()['work_ready']
    assert pipeline.report()['remaining_buffered_records'] == 1
    pipeline.collect()
    assert pipeline.read('.leads-state/companies.json')['pending']


def test_transient_error_resumes_when_cooldown_expires(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {})
    cursor={'queue':['https://www.yell.ge/companies.php'], 'last_error':{'reason':'rate_limited'}}
    state={'cursors':{'construction':cursor}, 'cooldowns':{'yell.ge':300000}}
    pipeline.write('.leads-state/companies.json',state)
    assert not pipeline.report()['work_ready']
    state['cooldowns']={}
    pipeline.write('.leads-state/companies.json',state)
    assert pipeline.report()['work_ready']
    assert pipeline.report()['source_statuses']['construction']['status']=='retry_pending'


def test_new_categories_start_without_waiting_for_daily_refresh(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {'catalog_refresh_at':199999})
    import georgia_leads.cli as leads
    monkeypatch.setattr(leads,'read_config',lambda path:{'categories':[{'id':'beauty','url':'https://www.yell.ge/companies.php?rub=398'}]})
    pipeline.collect()
    assert pipeline.read('.leads-state/companies.json')['cursors']['beauty']['queue']


def test_targeted_category_retry_does_not_restart_other_catalogues(tmp_path, monkeypatch):
    setup_state(tmp_path, monkeypatch, {'catalog_refresh_at': 199999})
    import georgia_leads.cli as leads
    config={'categories':[{'id':'beauty','url':'https://www.yell.ge/companies.php?rub=398'},
                          {'id':'auto','url':'https://www.yell.ge/companies.php?rub=3487'}]}
    monkeypatch.setattr(leads, 'read_config', lambda path: config)
    state=pipeline.read('.leads-state/companies.json')
    state['cursors']={'beauty':{'start':config['categories'][0]['url'],'queue':[],'failed_pages':{'old':'403'},'pages':12},
                      'auto':{'start':config['categories'][1]['url'],'queue':[],'pages':2,'sentinel':'keep'}}
    pipeline.write('.leads-state/companies.json',state)
    monkeypatch.setenv('RESTART_CATEGORIES','beauty')
    pipeline.collect()
    cursors=pipeline.read('.leads-state/companies.json')['cursors']
    assert cursors['beauty']['queue']==[config['categories'][0]['url']]
    assert cursors['auto']['sentinel']=='keep'
