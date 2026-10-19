"""Independent, finite and resumable B2B collection. Never writes interior state."""
import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
from bs4 import BeautifulSoup
from georgia_contacts.network import Client, FetchError, Deadline
from georgia_contacts.state import atomic_text, load_state, save_state
from georgia_contacts.export import write_csv
from georgia_contacts.urls import canonical_url, domain
from .model import listing_records, published_pages, page_key, merge_lead, changes, opportunity

PIPELINE = 'b2b-leads'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('pipeline') != PIPELINE or not isinstance(data.get('categories'), list):
        raise ValueError('Expected an independent b2b-leads configuration')
    ids = set()
    for cat in data['categories']:
        p = canonical_url(cat.get('url', ''))
        if not re.fullmatch(r'[a-z0-9-]{1,40}', cat.get('id', '')) or cat['id'] in ids:
            raise ValueError('Invalid or duplicate category')
        if domain(p) != 'yell.ge' or not cat.get('name') or not isinstance(cat.get('max_pages'), int) or not 1 <= cat['max_pages'] <= 500:
            raise ValueError('Only reviewed Yell categories, 1–500 pages, are supported')
        # Reject forbidden query parameters before any request.
        from urllib.parse import urlsplit, parse_qs
        if urlsplit(p).path not in {'/companies.php', '/restaurants.php'} or not set(parse_qs(urlsplit(p).query)) <= {'rub', 'lan', 'SR_pg'}:
            raise ValueError('Unsupported directory URL')
        if cat.get('adapter') not in {None,'yell-restaurants'} or (cat.get('adapter')=='yell-restaurants' and urlsplit(p).path!='/restaurants.php'):
            raise ValueError('Unsupported directory adapter')
        cat['url'] = p; ids.add(cat['id'])
    return data


def load_leads(path):
    state = load_state(path)
    if Path(path).exists() and state.get('pipeline') != PIPELINE:
        raise ValueError('Refusing to read another pipeline state')
    state['pipeline'] = PIPELINE
    state.setdefault('cursors', {}); state.setdefault('pending', []); state.setdefault('site_queue', [])
    state.setdefault('site_done', []); state.setdefault('cooldowns', {})
    return state


def prepare(state, config, restart=False):
    if restart:
        state['cursors'] = {}; state['pending'] = []; state['site_queue'] = []; state['site_done'] = []
    for cat in config['categories']:
        key = cat['id']
        previous = state['cursors'].get(key)
        if not previous or previous.get('start') != cat['url'] or previous.get('adapter') != cat.get('adapter') or (previous.get('capped') and previous.get('page_limit') != cat.get('max_pages')):
            state['cursors'][key] = {'start': cat['url'], 'adapter':cat.get('adapter'), 'page_limit':cat.get('max_pages'), 'queue': [cat['url']], 'seen': [], 'failures': {}, 'pages': 0}


def export(output, state, before, report, config):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    records = state['records']; delta = changes(before, records, report['started_at'])
    report['records_total'] = len(records)
    report['prospects_total'] = sum(opportunity(r) in {'not_listed', 'social_only', 'website_unreachable'} and any(f['kind'] in {'phone','email'} for f in r['facts']) for r in records)
    report['direct_contacts_unique'] = len({(f['kind'], f['value']) for r in records for f in r['facts'] if f['kind'] in {'phone', 'email'}})
    report['new_records'] = sum(c['type'] == 'new' for c in delta); report['changed_records'] = sum(c['type'] == 'changed' for c in delta)
    report['errors'] = sum(e['status'] != 'ok' for e in report['events'])
    report['error_categories'] = dict(Counter(e['status'] for e in report['events'] if e['status'] != 'ok'))
    active = [state['cursors'][c['id']] for c in config['categories'] if c.get('enabled', True)]
    report['source_statuses'] = {c['id']: state['cursors'][c['id']].get('last_error') for c in config['categories']}
    report['failed_pages'] = {c['id']: state['cursors'][c['id']].get('failed_pages', {}) for c in config['categories']}
    report['page_caps_reached'] = [c['id'] for c in config['categories'] if state['cursors'][c['id']].get('capped')]
    report['remaining_directory_pages'] = sum(len(c['queue']) for c in active)
    report['remaining_businesses'] = len(state['pending']) + (0 if report.get('directory_only') else len(state['site_queue']))
    report['work_remaining'] = bool(report['remaining_directory_pages'] or report['remaining_businesses'])
    incomplete = bool(report['page_caps_reached'] or any(c.get('last_error') or c.get('failed_pages') for c in active))
    report['collection_status'] = 'active' if report['work_remaining'] else 'partial' if incomplete else 'exhausted'
    pending_hosts = {domain(u) for c in active for u in c['queue']} | {domain(u) for u in state['site_queue']}
    waits = [state['cooldowns'].get(h, 0) for h in pending_hosts]
    if report['work_remaining'] and not state['pending'] and waits and all(w > time.time() for w in waits):
        report['resume_after'] = min(waits); report['collection_status'] = 'waiting'
    else: report['resume_after'] = 0
    report['updated_at'] = now()
    rows = []
    for record in records:
        row = {'record_id': record['id'], 'name': record['name'], 'categories': '; '.join(record['categories']), 'opportunity': opportunity(record),
               'review_reason': 'Website need requires confirmation; absence from a listing is not proof',
               'checked_sources': json.dumps(record['checks'], ensure_ascii=False), 'website_checks': json.dumps(record['website_checks'], ensure_ascii=False),
               'evidence_json': json.dumps(record['facts'], ensure_ascii=False), 'last_seen': record['last_seen']}
        for kind in ['phone','email','website','city','instagram','facebook']:
            row[kind] = '; '.join(sorted({f['value'] for f in record['facts'] if f['kind'] == kind}))
        rows.append(row)
    fields = ['record_id','name','categories','opportunity','phone','email','website','city','instagram','facebook','review_reason','checked_sources','website_checks','last_seen','evidence_json']
    write_csv(output/'leads.csv', fields, rows)
    atomic_text(output/'leads.json', json.dumps(records, ensure_ascii=False, indent=2)+'\n')
    write_csv(output/'changes.csv', ['type','record_id','name','collected_at','before','after'], delta)
    atomic_text(output/'report.json', json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    atomic_text(output/'snapshot.json', json.dumps({'version':1,'pipeline':PIPELINE,'categories':config['categories'],'records':records,'report':report}, ensure_ascii=False)+'\n')
    atomic_text(output/'report.txt', f"B2B ვებსაიტის კლიენტები — {report['collection_status']}\nბიზნესები: {len(records)}; პოტენციური: {report['prospects_total']}; ტელეფონი/ელფოსტა: {report['direct_contacts_unique']}\nახალი: {report['new_records']}; შეცვლილი: {report['changed_records']}; შეცდომები/ბლოკირება/JS: {report['errors']}\nდამუშავებული კატალოგის გვერდები: {report['pages_processed']}; ვებსაიტის შემოწმებები: {report['sites_processed']}\nსაიტის საჭიროება გადასამოწმებელია. ინტერიერის მონაცემები ცალკეა.\n")


def collect(state, config, client, *, limit=50, checkpoint=lambda: None, report=None, directory_only=False):
    report = report if report is not None else {'events': [], 'pages_processed':0, 'sites_processed':0}
    count = 0
    enabled = [c for c in config['categories'] if c.get('enabled', True)]
    # Start the next batch with the next category so one shared host budget
    # cannot starve newly enabled directions behind large earlier catalogues.
    enabled_ids = [c['id'] for c in enabled]
    if enabled:
        offset = state.get('category_offset',0) % len(enabled)
        enabled = enabled[offset:] + enabled[:offset]
    # Round-robin categories; domain page budget remains at most five per run.
    while count < limit:
        if state['pending']:
            record = state['pending'].pop(0)
            merged = merge_lead(state['records'], record); count += 1
            for fact in merged['facts']:
                if fact['kind'] == 'website' and fact['value'] not in state['site_done'] + state['site_queue']:
                    state['site_queue'].append(fact['value'])
            checkpoint(); continue
        made_progress = False
        for cat in enabled:
            cursor = state['cursors'][cat['id']]
            if not cursor['queue']: continue
            if cursor['pages'] >= cat['max_pages']:
                cursor['queue'] = []; cursor['capped'] = True; continue
            url = cursor['queue'][0]
            try:
                if cat.get('adapter') == 'yell-restaurants':
                    from .restaurants import fetch_page
                    final, body, pages = fetch_page(client,url,cursor)
                else:
                    final, body = client.fetch(url)
                    pages = published_pages(body,final)
                records = listing_records(body, final, now(), cat['id'])
                if not records:
                    raise FetchError('javascript_only', 'no_business_cards_js_or_layout_requires_review')
                report['pages_processed'] += 1; cursor['pages'] += 1; cursor.pop('last_error', None)
                cursor['queue'].pop(0); cursor['seen'].extend([url, final])
                seen = {page_key(x) for x in cursor['queue'] + cursor['seen']}
                for target in pages:
                    if page_key(target) not in seen:
                        if cursor['pages'] + len(cursor['queue']) < cat['max_pages']:
                            cursor['queue'].append(target); seen.add(page_key(target))
                        else:
                            cursor['capped'] = True
                state['pending'].extend(records)
                report['events'].append({'source_url':url,'category':cat['id'],'status':'ok','records':len(records)})
                made_progress = True
                state['category_offset'] = (enabled_ids.index(cat['id'])+1) % len(enabled_ids)
            except FetchError as exc:
                if exc.reason in {'page_limit', 'retry_after_cooldown', 'rate_limited'}:
                    if exc.reason != 'page_limit':
                        cursor['last_error'] = {'status':exc.status, 'reason':exc.reason}
                    continue
                cursor['last_error'] = {'status':exc.status, 'reason':exc.reason}
                cursor['failures'][url] = cursor['failures'].get(url, 0) + 1
                report['events'].append({'source_url':url,'category':cat['id'],'status':exc.status,'reason':exc.reason})
                if exc.status in {'blocked','javascript_only'} or cursor['failures'][url] >= 3:
                    cursor.setdefault('failed_pages', {})[url] = {'status':exc.status, 'reason':exc.reason}
                    cursor['queue'].pop(0); cursor['seen'].append(url)
                made_progress = True
            checkpoint()
            # Visit each category before consuming pending business records.
        if not made_progress: break
    if directory_only:
        return count
    # Reachability checks are a review signal, never a claim that a business needs a site.
    for url in list(state['site_queue']):
        if client.cooldowns.get(domain(url), 0) > time.time(): continue
        check = {'url':url, 'checked_at':now()}
        try:
            final, body = client.fetch(url)
            soup = BeautifulSoup(body, 'html.parser')
            for node in soup(['script','style']): node.decompose()
            if len(soup.get_text(' ', strip=True)) < 40:
                raise FetchError('javascript_only', 'insufficient_html_text')
            check.update(outcome='reachable', source_url=final)
        except FetchError as exc:
            if exc.reason in {'page_limit','rate_limited','retry_after_cooldown'}: continue
            # A single temporary outage is uncertain, not a negative website assessment.
            check.update(outcome='uncertain' if exc.reason in {'network_error','empty_response','http_500','http_502','http_503','http_504','unsafe_or_unresolved_url'} else exc.status, reason=exc.reason)
            report['events'].append({'source_url':url,'status':exc.status,'reason':exc.reason})
        state['site_queue'].remove(url); state['site_done'].append(url); report['sites_processed'] += 1
        for record in state['records']:
            if any(f['kind']=='website' and f['value']==url for f in record['facts']):
                record['website_checks'] = [c for c in record['website_checks'] if c['url'] != url] + [copy.deepcopy(check)]
        checkpoint()
    return count


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', default='config/leads-sources.json')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--max-minutes', type=float, default=24)
    parser.add_argument('--state', default='.leads-state/state.json')
    parser.add_argument('--output', default='output/leads')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--directory-only', action='store_true', help='Collect listings and preserve links without visiting company websites')
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--publish-interval', type=int, default=0)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000 or not 0 < args.max_minutes <= 25: parser.error('limit 1–1000, max-minutes 0–25')
    # CLI cannot accidentally mix the interior pipeline directories.
    if Path(args.state).resolve() == Path('.state/state.json').resolve() or Path(args.output).resolve() == Path('output').resolve():
        parser.error('Interior state/output paths are reserved')
    config = read_config(args.sources); state = load_leads(args.state); before = copy.deepcopy(state['records'])
    prepare(state, config, args.restart)
    report = {'pipeline':PIPELINE,'started_at':now(),'status':'running','events':[],'pages_processed':0,'sites_processed':0,
              'continuous':args.continuous,'directory_only':args.directory_only,'batch_limit':args.limit,'sources_file':args.sources,'github_run_id':os.environ.get('GITHUB_RUN_ID','')}
    client = Client(time.monotonic()+args.max_minutes*60, cooldowns=state['cooldowns'])
    published = time.monotonic()
    def checkpoint():
        nonlocal published
        export(args.output, state, before, report, config); save_state(args.state, state)
        if args.publish_interval and time.monotonic()-published >= args.publish_interval:
            if args.state != '.leads-state/state.json' or args.output != 'output/leads': raise ValueError('Publishing requires default independent paths')
            subprocess.run([sys.executable,'scripts/leads_data_branch.py','save'], check=True)
            published = time.monotonic()
    checkpoint()
    try:
        collect(state, config, client, limit=args.limit, checkpoint=checkpoint, report=report, directory_only=args.directory_only)
        report['status'] = 'completed'
    except Deadline:
        report['status'] = 'partial_deadline'
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
    except Exception:
        report['status'] = 'failed'; raise
    finally:
        checkpoint()
    print(Path(args.output, 'report.txt').read_text())


if __name__ == '__main__': main()
