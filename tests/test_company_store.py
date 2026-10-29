import json
import pytest
from georgia_contacts.company_store import CompanyStore, pack, unpack, public_record, encoded
import hashlib


def checkpoint_tree(path):
    path.mkdir(exist_ok=True)
    row = {'id': 'a', 'name': 'კომპანია', 'history': [{'name': 'old'}], 'source_records': [{'raw': 'original'}], 'evidence': [{'url': 'source'}]}
    for name, data in {'data.json': {'version': 1, 'updated_at': '2026-10-24T00:00:00Z', 'records': [row]},
                       'developers.json': {'queue': ['next']}, 'yell.json': {'records': []}, 'report.json': {'records_total': 1}}.items():
        (path/name).write_text(json.dumps(data, ensure_ascii=False))
    return row


def test_checkpoint_roundtrip_preserves_every_byte_and_checks_integrity(tmp_path):
    source = tmp_path/'source'
    checkpoint_tree(source)
    archive, checksum = pack(source)
    unpack({'archive': archive, 'sha256': checksum}, tmp_path/'restored')
    for path in source.iterdir():
        assert (tmp_path/'restored'/path.name).read_bytes() == path.read_bytes()
    with pytest.raises(ValueError, match='checksum'):
        unpack({'archive': archive, 'sha256': 'bad'}, tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


@pytest.mark.parametrize('newline', ['\r\n', '\n', '\r'])
def test_checkpoint_preserves_csv_bom_line_endings_and_quoted_newlines(tmp_path, newline):
    source = tmp_path/'source'
    checkpoint_tree(source)
    csv = ('\ufeffid,name,notes' + newline +
           'a,კომპანია,"first\r\nsecond\nthird\rfourth"' + newline).encode('utf-8')
    (source/'b2b-target.csv').write_bytes(csv)
    archive, checksum = pack(source)
    unpack({'archive': archive, 'sha256': checksum}, tmp_path/'restored')
    assert (tmp_path/'restored'/'b2b-target.csv').read_bytes() == csv


def test_public_record_keeps_evidence_but_not_historical_copies(tmp_path):
    row = checkpoint_tree(tmp_path)
    result = public_record(row)
    assert result['evidence'] == row['evidence']
    assert 'source_records' not in result and 'history' not in result
    assert row['source_records']  # input not modified


class FakeStore(CompanyStore):
    def __init__(self, existing=(), fail_upload=False):
        self.existing = existing
        self.calls = []
        self.fail_upload = fail_upload

    def all_rows(self, *args, **kwargs):
        return self.existing

    def request(self, method, path, body=None, params=None):
        self.calls.append((method, path, body, params))
        if path == 'companies' and self.fail_upload:
            raise RuntimeError('upload interrupted')


def test_unchanged_rows_are_not_rewritten_and_checkpoint_is_committed_last(tmp_path):
    row = checkpoint_tree(tmp_path)
    digest = hashlib.sha256(encoded(public_record(row))).hexdigest()
    store = FakeStore([{'id': 'a', 'content_hash': digest, 'active': True}])
    store.save(tmp_path)
    assert [c[1] for c in store.calls] == ['directory_meta', 'rpc/publish_company_directory']
    archive = store.calls[-1][2]['p_archive']
    assert archive


def test_failed_upload_never_advances_durable_checkpoint(tmp_path):
    checkpoint_tree(tmp_path)
    store = FakeStore(fail_upload=True)
    with pytest.raises(RuntimeError):
        store.save(tmp_path)
    assert all(not c[1].startswith('rpc/') for c in store.calls)


def test_retired_ids_are_deactivated_not_deleted(tmp_path):
    checkpoint_tree(tmp_path)
    store = FakeStore([{'id': 'merged', 'content_hash': 'old', 'active': True}])
    store.save(tmp_path)
    changes = [c for c in store.calls if c[0] == 'PATCH']
    assert len(changes) == 1 and changes[0][2] == {'active': False}
    assert changes[0][3] == {'id': 'in.("merged")'}
    assert all(c[0] != 'DELETE' for c in store.calls)


def test_cache_is_used_only_for_the_exact_durable_revision(tmp_path, monkeypatch):
    source=tmp_path/'source'
    checkpoint_tree(source)
    archive,checksum=pack(source)
    cached=dict(archive=archive,sha256=checksum,revision='current-version')
    cache=tmp_path/'cache.json';cache.write_text(json.dumps(cached))
    monkeypatch.setenv('COMPANY_CHECKPOINT_CACHE',str(cache))
    store=FakeStore()
    calls=[]
    def request(method,path,body=None,params=None):
        calls.append(params['select'])
        return [{'revision':'current-version'}] if params['select']=='revision' else [cached]
    store.request=request
    store.restore(tmp_path/'cached-restore')
    assert calls==['revision']
    cached['revision']='stale';cache.write_text(json.dumps(cached));calls.clear()
    store.restore(tmp_path/'remote-restore')
    assert calls==['revision','*']
