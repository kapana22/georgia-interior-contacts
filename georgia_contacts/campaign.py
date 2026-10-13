"""Finite collection campaigns. Exhaustion means reviewed, configured sources only."""
import hashlib
import json
import time
from .directories import source_key
from .urls import domain


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def source_signature(source):
    # Observation timestamps change on every discovery; they are not new work.
    stable = {k: v for k, v in source.items() if k not in {'directory_facts', 'discovered_at'}}
    stable['directory_facts'] = sorted((f['kind'], f['value'], f['source_url']) for f in source.get('directory_facts', []))
    return fingerprint(stable)


def candidates(state, config):
    merged = {}
    for item in state['candidates'] + config['businesses']:
        key = source_key(item)
        merged[key] = {**merged.get(key, {}), **item}
    excluded = set(config.get('excluded_business_domains', []))
    return [s for s in merged.values() if s.get('enabled', True) and domain(s['url']) not in excluded]


def prepare(state, config, sources_file, started_at):
    signature = fingerprint({'config': config, 'sources': str(sources_file)})
    campaign = state.get('campaign', {})
    if campaign.get('signature') != signature:
        previous = campaign if campaign.get('sources') == str(sources_file) else {}
        campaign = {'signature': signature, 'started_at': previous.get('started_at', started_at), 'sources': str(sources_file),
                    'processed': previous.get('processed', {}), 'failures': previous.get('failures', {}), 'status': 'active'}
        state['campaign'] = campaign
    return campaign


def pending_businesses(state, config):
    processed = state['campaign']['processed']
    return [s for s in candidates(state, config) if processed.get(source_key(s), {}).get('signature') != source_signature(s)]


def finish_attempt(campaign, source, events):
    key, signature = source_key(source), source_signature(source)
    # Page-budget skips defer all or part of a business to a fresh batch.
    if any(e['status'] == 'skipped' and e['reason'] in {'page_limit', 'retry_after_cooldown'} for e in events):
        return
    failed = any(e['status'] in {'unreachable', 'error'} or e['reason'] == 'rate_limited' for e in events)
    count_key = key + ':' + signature
    if failed:
        campaign['failures'][count_key] = campaign['failures'].get(count_key, 0) + 1
        if campaign['failures'][count_key] < 3:
            return
    outcome = 'unavailable' if failed or any(e['status'] in {'blocked', 'javascript_required'} for e in events) else 'done'
    campaign['processed'][key] = {'signature': signature, 'outcome': outcome}


def update_report(state, config, report, *, no_discovery=False):
    campaign = state['campaign']
    directories = [] if no_discovery else [d for d in config.get('directories', []) if d.get('enabled')]
    remaining_pages = sum(len(state.get('discovery_queue', {}).get(d['url'], {}).get('pending', [d['url']])) for d in directories)
    pending = pending_businesses(state, config)
    remaining_businesses = len(pending)
    report['remaining_directory_pages'] = remaining_pages
    report['remaining_businesses'] = remaining_businesses
    report['work_remaining'] = remaining_pages + remaining_businesses > 0
    if report.get('collection_stop_reason') in {'no_progress_requires_review', 'unexpected_error_requires_review', 'interrupted_requires_review'}:
        report['work_remaining'] = False
        campaign['status'] = report['collection_status'] = 'error' if report.get('status') == 'failed' else 'paused'
        return
    campaign['status'] = 'active' if report['work_remaining'] else 'exhausted'
    report['collection_status'] = campaign['status']
    report['collection_stop_reason'] = '' if report['work_remaining'] else 'available_sources_exhausted'
    urls = [s['url'] for s in pending] + [url for d in directories for url in state.get('discovery_queue', {}).get(d['url'], {}).get('pending', [d['url']])]
    waits = [state.get('cooldowns', {}).get(domain(url), 0) for url in urls]
    report['resume_after'] = 0
    if waits and all(until > time.time() for until in waits):
        report['resume_after'] = min(waits)
        campaign['status'] = report['collection_status'] = 'waiting'
    report['unavailable_sources_total'] = sum(v['outcome'] == 'unavailable' for v in campaign['processed'].values()) + sum(
        len(state.get('discovery_queue', {}).get(d['url'], {}).get('unavailable', {})) for d in directories)


def progress_signature(state):
    return fingerprint({'processed': state['campaign']['processed'], 'failures': state['campaign']['failures'],
                        'discovery': state.get('discovery_queue', {}),
                        'candidates': [(source_key(s), source_signature(s)) for s in state['candidates']]})
