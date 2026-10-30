"""Resumable multi-source business collection and public website enrichment."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from georgia_contacts.business_catalogues import (
    HOSTS, business_website, geostat_page, html_page, unique_facts,
)
from georgia_contacts.extract import extract
from georgia_contacts.network import Client, Deadline, FetchError
from georgia_contacts.urls import domain


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)


def prepare(state, config, stamp=None):
    stamp = time.time() if stamp is None else stamp
    state.setdefault('version', 1)
    for key in ('sources', 'outbox', 'cooldowns', 'website_done'):
        state.setdefault(key, {})
    state.setdefault('website_queue', [])
    for source in config['sources']:
        key = source['id']
        if key not in HOSTS or domain(source['seed']) != HOSTS[key]:
            raise ValueError('Unreviewed business source')
        cursor = state['sources'].setdefault(key, {'record_ids': [], 'pages': 0})
        if 'queue' not in cursor or (not cursor['queue'] and stamp >= cursor.get('refresh_after', float('inf'))):
            cursor.update(queue=[{'url': source['seed'], 'kind': 'listing'}], seen=[], failures={},
                          retry_after=0, refresh_after=stamp + source.get('refresh_days', 30) * 86400)
            cursor.pop('last_error', None)
    return state


def enqueue_websites(state, rows, stamp=None):
    stamp = time.time() if stamp is None else stamp
    candidates, owners = [], {}
    row_ids = {row['id'] for row in rows}
    for row in rows:
        links = re.split(r'[;\n]+', str(row.get('website') or ''))
        links += [f['value'] for f in row.get('facts', []) if f['kind'] == 'website']
        for url in {business_website(value) for value in links} - {''}:
            owners.setdefault(domain(url), set()).add(row['id'])
            candidates.append((row, url))
    state['website_queue'] = [t for t in state['website_queue'] if t['id'] in row_ids
                              and owners.get(domain(t['url'])) == {t['id']}]
    if len(state['website_queue']) >= 500:
        return
    queued = {t['key'] for t in state['website_queue']}
    # Shared portals and multi-company domains cannot safely identify a business.
    for row, url in sorted(candidates, key=lambda pair: bool(pair[0].get('email'))):
        key = row['id'] + '|' + url
        if (len(owners[domain(url)]) != 1 or key in queued
                or state['website_done'].get(key, {}).get('retry_after', 0) > stamp):
            continue
        state['website_queue'].append({'key': key, 'id': row['id'], 'name': row['name'], 'url': url,
                                       'source': row.get('source', 'company-website'), 'pages': 0})
        queued.add(key)
        if len(state['website_queue']) >= 500:
            break


def buffer(state, rows):
    for row in rows:
        old = state['outbox'].get(row['id'], {})
        combined = {**old, **row}
        combined['facts'] = unique_facts(old.get('facts', []) + row.get('facts', []))
        state['outbox'][row['id']] = combined


def ready(state, stamp=None):
    stamp = time.time() if stamp is None else stamp
    if state.get('outbox'):
        return True
    for key, cursor in state.get('sources', {}).items():
        if cursor.get('queue') and max(cursor.get('retry_after', 0), state.get('cooldowns', {}).get(HOSTS[key], 0)) <= stamp:
            return True
    return any(state.get('cooldowns', {}).get(domain(t['url']), 0) <= stamp for t in state.get('website_queue', []))


def status(state):
    return {'sources': {key: {'pages': c.get('pages', 0), 'companies_seen': len(c.get('record_ids', [])),
                'remaining_pages': len(c.get('queue', [])), 'last_error': c.get('last_error'),
                'retry_after': c.get('retry_after', 0), 'failed_pages': len(c.get('failures', {})),
                'status': 'waiting' if c.get('retry_after', 0) > time.time() else 'active' if c.get('queue') else 'partial' if c.get('failures') else 'exhausted',
                **c.get('metadata', {})} for key, c in state.get('sources', {}).items()},
            'websites_checked': len(state.get('website_done', {})),
            'websites_pending': len(state.get('website_queue', [])),
            'buffered_records': len(state.get('outbox', {}))}


def collect(state, config, checkpoint, *, max_minutes=4, client=None):
    prepare(state, config)
    client = client or Client(time.monotonic() + max_minutes * 60, cooldowns=state['cooldowns'])
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        # Every source gets an independent five-page host budget before enrichment.
        for source in config['sources']:
            key = source['id']
            cursor = state['sources'][key]
            seen, ids = set(cursor['seen']), set(cursor['record_ids'])
            while cursor['queue'] and cursor.get('retry_after', 0) <= time.time():
                client.check()
                if client.counts.get(HOSTS[key], 0) >= 5:
                    break
                profiles = [i for i, t in enumerate(cursor['queue']) if t['kind'] == 'profile']
                listings = [i for i, t in enumerate(cursor['queue']) if t['kind'] != 'profile']
                prefer_profile = cursor['pages'] % 5 != 0 if key == 'city24' else cursor['pages'] % 3 == 2
                index = profiles[0] if profiles and (prefer_profile or not listings) else listings[0] if listings else 0
                task = cursor['queue'][index]
                try:
                    final, body = client.fetch(task['url'], json_response=key == 'geostat')
                    actual = {**task, 'url': final}
                    rows, discovered, metadata = (geostat_page(body, final, timestamp) if key == 'geostat'
                                                   else html_page(key, body, actual, timestamp))
                except FetchError as exc:
                    if exc.reason == 'page_limit':
                        break
                    task['attempts'] = task.get('attempts', 0) + 1
                    cursor['last_error'] = {'url': task['url'], 'reason': exc.reason, 'at': timestamp}
                    cursor['retry_after'] = time.time() + (86400 if exc.status == 'blocked' else 3600)
                    # This API exposes the next page only after a successful response.
                    # Dropping its failed page would discard the rest of the registry.
                    if key == 'geostat' and task['attempts'] >= 3:
                        cursor['retry_after'] = max(cursor['retry_after'], time.time() + 6 * 3600)
                    elif task['attempts'] >= 3 and exc.status != 'blocked':
                        cursor['failures'][task['url']] = cursor['last_error']
                        cursor['queue'].pop(index)
                    checkpoint(state)
                    break
                buffer(state, rows)
                ids.update(r['id'] for r in rows)
                cursor['record_ids'] = sorted(ids)
                cursor['pages'] += 1
                cursor['metadata'] = {**cursor.get('metadata', {}), **metadata}
                cursor.pop('last_error', None)
                cursor['queue'].pop(index)
                seen.update((task['url'], final))
                cursor['seen'] = sorted(seen)
                known = seen | set(cursor['failures']) | {t['url'] for t in cursor['queue']}
                for item in discovered:
                    if item['url'] not in known:
                        cursor['queue'].append(item)
                        known.add(item['url'])
                checkpoint(state)
        # Homepage plus one actually linked contact/about page, with durable retries.
        for _ in range(min(20, len(state['website_queue']))):
            client.check()
            task = state['website_queue'][0]
            if (state['cooldowns'].get(domain(task['url']), 0) > time.time()
                    or client.counts.get(domain(task['url']), 0) >= 5):
                state['website_queue'].append(state['website_queue'].pop(0))
                continue
            reason, links = 'ok', []
            try:
                final, body = client.fetch(task['url'])
                parsed = extract(body, final, timestamp, task['name'])
                if parsed['blocked']:
                    raise FetchError('blocked', parsed['reasons'][0])
                facts = [f for f in parsed['facts'] if f['kind'] in {'phone', 'email', 'facebook', 'instagram'}]
                if facts:
                    buffer(state, [{'id': task['id'], 'name': task['name'], 'source': task['source'],
                                    'facts': facts, 'categories': [], 'last_seen': timestamp}])
                links = sorted((url for url in parsed['links'] if url not in {task['url'], final}),
                               key=lambda url: not re.search(r'contact|კონტაქტ|контакт', url, re.I))
            except FetchError as exc:
                if exc.reason == 'page_limit':
                    state['website_queue'].append(state['website_queue'].pop(0))
                    continue
                reason = exc.reason
            state['website_queue'].pop(0)
            if reason == 'ok' and task['pages'] == 0 and links:
                state['website_queue'].insert(0, {**task, 'url': links[0], 'pages': 1})
            else:
                state['website_done'][task['key']] = {'checked_at': timestamp, 'reason': reason,
                    'retry_after': time.time() + (90 if reason == 'ok' else 7) * 86400}
            checkpoint(state)
    except Deadline:
        checkpoint(state)
    state['updated_at'] = timestamp
    checkpoint(state)
    return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='config/business-catalogues.json')
    p.add_argument('--state', required=True)
    p.add_argument('--data', required=True)
    p.add_argument('--max-minutes', type=float, default=4)
    args = p.parse_args()
    state, config = read(args.state, {}), read(args.config)
    prepare(state, config)
    enqueue_websites(state, read(args.data, {'records': []})['records'])
    write(args.state, state)
    collect(state, config, lambda value: write(args.state, value), max_minutes=args.max_minutes)
    print(json.dumps(status(state), ensure_ascii=False))


if __name__ == '__main__':
    main()
