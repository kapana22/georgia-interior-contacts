"""Source-backed NACE enrichment from Geostat's public CSV export."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import requests

from .network import FetchError, USER_AGENT, retry_after
from .urls import domain

EXPORT = 'https://br-api.geostat.ge/api/documents/export?legalForm=1&isActive=true'
COUNT = 'https://br-api.geostat.ge/api/documents?lang=ge&legalForm=1&isActive=true&page=1&limit=1'


def fetch_activities(client):
    _, payload = client.fetch(COUNT, json_response=True)
    expected = json.loads(payload)['pagination']['total']
    if not isinstance(expected, int) or not 1 <= expected <= 150000:
        raise FetchError('unreachable', 'activity_export_unexpected_count')
    rules = client.rules(EXPORT)
    if not rules.can_fetch(EXPORT, USER_AGENT):
        raise FetchError('blocked', 'robots_disallowed')
    host = domain(EXPORT)
    if client.counts.get(host, 0) >= 5:
        raise FetchError('skipped', 'page_limit')
    client.pause(max(0, client.last.get(host, 0) + client.delays.get(host, client.delay) - client.clock()))
    client.counts[host] = client.counts.get(host, 0) + 1
    client.last[host] = client.clock()
    client.session.cookies.clear()
    records = {}
    try:
        with client.session.get(EXPORT, stream=True, allow_redirects=False, timeout=(5, 40), headers={'Accept': 'text/csv'}) as response:
            if response.status_code == 429:
                client.cooldowns[host] = time.time() + max(3600, retry_after(response.headers.get('Retry-After')))
            if response.status_code != 200:
                raise FetchError('blocked' if response.status_code in {401, 403, 429, 451} else 'unreachable', 'http_' + str(response.status_code))
            if 'csv' not in response.headers.get('Content-Type', '').lower():
                raise FetchError('unreachable', 'activity_export_not_csv')
            if re.search('noindex|nofollow', response.headers.get('X-Robots-Tag', ''), re.I):
                raise FetchError('blocked', 'x_robots_opt_out')

            def lines():
                size = 0
                for line in response.iter_lines():
                    client.check()
                    size += len(line) + 1
                    if size > 128 * 1024 * 1024:
                        raise FetchError('unreachable', 'activity_export_too_large')
                    yield line.decode('utf-8-sig') + '\n'

            reader = csv.DictReader(lines())
            required = {'Legal_Code', 'Legal_Form_ID', 'ISActive', 'Full_Name', 'Activity_2_Code', 'Activity_2_Name'}
            if not required <= set(reader.fieldnames or []):
                raise FetchError('unreachable', 'activity_export_layout_changed')
            for row in reader:
                tax = row.get('Legal_Code', '').strip()
                if (row.get('Legal_Form_ID') not in {'1', 'შეზღუდული პასუხისმგებლობის საზოგადოება'}
                        or row.get('ISActive') not in {'1', 'აქტიური'} or not re.fullmatch(r'\d{9}', tax)):
                    raise FetchError('unreachable', 'activity_export_filter_changed')
                # Never retain personal numbers, managers, partners or other CSV fields.
                records[tax] = {'name': row['Full_Name'], 'code': row['Activity_2_Code'], 'activity': row['Activity_2_Name']}
                if len(records) > expected:
                    raise FetchError('unreachable', 'activity_export_count_changed')
    except (requests.RequestException, UnicodeError, csv.Error) as exc:
        raise FetchError('unreachable', 'activity_export_incomplete') from exc
    if len(records) != expected:
        raise FetchError('unreachable', 'activity_export_incomplete')
    return {'version': 1, 'source': EXPORT, 'updated_at': datetime.now(timezone.utc).isoformat(),
            'refresh_after': time.time() + 30 * 86400, 'records': records}


def ensure_activities(client, path):
    path = Path(path)
    if path.exists() and json.loads(path.read_text()).get('refresh_after', 0) > time.time():
        return False
    catalogue = fetch_activities(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(catalogue, ensure_ascii=False))
    temporary.replace(path)
    return True


def apply_activities(rows, catalogue):
    changed = 0
    for row in rows:
        tax = str(row.get('tax_id') or '').strip()
        activity = catalogue['records'].get(tax)
        if not activity:
            continue
        patch = {'activity_code': activity['code'], 'activity_name': activity['activity'],
                 'registered_name': activity['name'], 'activity_source_url': 'https://br.geostat.ge/?identificationNumber=' + tax,
                 'registry_verified_at': catalogue['updated_at']}
        if any(row.get(k) != v for k, v in patch.items()):
            row.update(patch)
            changed += 1
    return changed
