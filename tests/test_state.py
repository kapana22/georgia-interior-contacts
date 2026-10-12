import copy
import json
from types import SimpleNamespace
from georgia_contacts.state import merge_record, changes_since, save_state, empty_state, load_state
from georgia_contacts.cli import run
from georgia_contacts.network import FetchError


def record(host='one.test', contact='info@one.test', name='Studio', day='2026-01-01'):
    return {'id': '', 'domains': [host], 'name': name, 'first_seen': day, 'last_seen': day,
            'status': 'candidate', 'review_reasons': [],
            'facts': [{'kind':'email', 'value':contact, 'source_url':'https://' + host + '/', 'first_seen':day, 'last_seen':day}]}


def test_exact_domain_contact_transitive_merge_not_name():
    records = []
    merge_record(records, record())
    merge_record(records, record('two.test', 'other@two.test'))
    assert len(records) == 2  # Same name does not merge.
    merge_record(records, record('three.test', 'info@one.test'))
    assert len(records) == 2
    merge_record(records, record('two.test', 'info@one.test'))
    assert len(records) == 1
    assert records[0]['domains'] == ['one.test','three.test','two.test']
    assert 'shared_contact_across_domains' in records[0]['review_reasons']


def test_changed_new_and_timestamp_only():
    records = []
    merge_record(records, record())
    assert changes_since([], records, 'today')[0]['type'] == 'new'
    before = copy.deepcopy(records)
    merge_record(records, record(day='2026-02-01'))
    assert changes_since(before, records, 'today') == []
    merge_record(records, record(contact='new@one.test'))
    assert changes_since(before, records, 'today')[0]['type'] == 'changed'
    assert {f['value'] for f in records[0]['facts']} == {'info@one.test','new@one.test'}


class FailedClient:
    requests = 1
    def check(self):
        pass
    def fetch(self, *a, **kw):
        raise FetchError('unreachable', 'network_error')


def test_network_failure_preserves_old_state(tmp_path):
    state = empty_state()
    merge_record(state['records'], record())
    state_path = tmp_path / 'state.json'
    save_state(state_path, state)
    sources = tmp_path / 'sources.json'
    sources.write_text(json.dumps({'businesses':[{'url':'https://one.test/'}]}))
    args = SimpleNamespace(state=state_path, sources=sources, output=tmp_path/'output', max_minutes=1, no_discovery=True, limit=1)
    report = run(args, client=FailedClient())
    assert load_state(state_path)['records'] == state['records']
    assert report['errors'] == 1 and report['new_records'] == 0 and report['changed_records'] == 0
    snapshot = json.loads((args.output/'snapshot.json').read_text())
    assert snapshot['version'] == 1
    assert snapshot['records'] == state['records']
    assert snapshot['report'] == json.loads((args.output/'report.json').read_text())
    assert (args.output/'contacts.csv').read_bytes().startswith(b'\xef\xbb\xbf')


def test_empty_success_never_removes_contacts():
    records = []
    merge_record(records, record())
    new = record(day='2026-02-01')
    new['facts'] = []
    merge_record(records, new)
    assert records[0]['facts'][0]['value'] == 'info@one.test'


def test_bad_state_fails_closed(tmp_path):
    import pytest
    p = tmp_path/'state.json'
    p.write_text('{"schema_version":99,"records":[]}')
    with pytest.raises(ValueError):
        load_state(p)
