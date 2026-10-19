import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/companies_pipeline.py'
spec = importlib.util.spec_from_file_location('companies_pipeline', SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


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
