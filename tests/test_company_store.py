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
    assert checksum == hashlib.sha256(encoded({p.name:p.read_bytes().decode('utf-8') for p in source.iterdir()})).hexdigest()
    unpack({'archive': archive, 'sha256': checksum}, tmp_path/'restored')
    for path in source.iterdir():
        assert (tmp_path/'restored'/path.name).read_bytes() == path.read_bytes()
    with pytest.raises(ValueError, match='checksum'):
        unpack({'archive': archive, 'sha256': 'bad'}, tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_optional_catalogue_progress_survives_checkpoint_without_breaking_old_checkpoints(tmp_path):
    source = tmp_path/'source'
    checkpoint_tree(source)
    state = {'sources': {'geostat': {'queue': [{'url': 'next', 'kind': 'listing'}]}},
             'outbox': {'geostat:123456789': {'name': 'შპს სატესტო'}}, 'website_queue': [{'id': 'a'}]}
    (source/'catalogues.json').write_text(json.dumps(state, ensure_ascii=False))
    archive, checksum = pack(source)
    unpack({'archive': archive, 'sha256': checksum}, tmp_path/'restored')
    assert json.loads((tmp_path/'restored/catalogues.json').read_text()) == state


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

    def stage_archive(self, archive, checksum):
        return archive

    def prune_archives(self):
        pass

    def stage_directory(self, data):
        return {'path': 'snapshot', 'total': len(data['records'])}

    def prune_directory(self, manifest):
        pass

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


def test_private_storage_roundtrip_and_reference_validation(tmp_path):
    from georgia_contacts.company_store import ARCHIVE_BUCKET, ARCHIVE_PREFIX
    import base64
    import requests
    checkpoint_tree(tmp_path)
    archive, checksum = pack(tmp_path)
    store = CompanyStore('https://example.supabase.co', 'secret')
    objects = {}
    calls = []
    def storage(method, path, **kwargs):
        calls.append((method, path, kwargs))
        response = requests.Response()
        response.status_code = 200
        if path == 'bucket/'+ARCHIVE_BUCKET:
            response._content = b'{"public":false}'
        elif method == 'POST':
            objects[path.removeprefix('object/')] = kwargs['data']
        else:
            response._content = objects[path.removeprefix('object/authenticated/')]
        return response
    store.storage = storage
    reference = store.stage_archive(archive, checksum)
    assert reference == ARCHIVE_PREFIX+'archives/'+checksum+'.gz'
    checkpoint = store.hydrate_checkpoint(dict(archive=reference, sha256=checksum))
    assert base64.b64decode(checkpoint['archive']) == base64.b64decode(archive)
    assert calls[1][2]['headers']['x-upsert'] == 'false'
    with pytest.raises(ValueError, match='reference'):
        store.hydrate_checkpoint(dict(archive='storage:v1:evil/../../secret', sha256=checksum))
    objects[reference.removeprefix('storage:v1:')] = __import__('gzip').compress(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        store.hydrate_checkpoint(dict(archive=reference, sha256=checksum))


def test_archive_failure_does_not_start_publication(tmp_path):
    checkpoint_tree(tmp_path)
    store = FakeStore()
    def fail(*args):
        raise RuntimeError('Storage unavailable')
    store.stage_archive = fail
    with pytest.raises(RuntimeError):
        store.save(tmp_path)
    assert store.calls == []


def test_public_snapshot_keeps_all_current_fields_and_excludes_only_private_history(tmp_path):
    import gzip
    import requests
    row = checkpoint_tree(tmp_path)
    data = json.loads((tmp_path/'data.json').read_text())
    store = CompanyStore('https://example.supabase.co', 'secret')
    uploads = []
    def storage(method, path, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"public":true}'
        if method == 'POST':
            uploads.append(kwargs['data'])
        return response
    store.storage = storage
    manifest = store.stage_directory(data)
    assert len(uploads) == 1
    assert hashlib.sha256(uploads[0]).hexdigest() == manifest['sha256']
    assert manifest['bytes'] == len(uploads[0])
    assert json.loads(gzip.decompress(uploads[0]))['records'] == [public_record(row)]
