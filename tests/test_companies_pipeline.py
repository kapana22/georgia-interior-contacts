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
