"""Supabase persistence. Full, lossless checkpoints plus compact public rows.

Only changed directory rows are uploaded. A failed upload never advances the
collector checkpoint. Restore replays the previous cursor safely on retry.
"""
import base64
import gzip
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

FILES = ('data.json', 'developers.json', 'yell.json', 'report.json', 'deferred.json',
         'campaign.json', 'growth-baseline.json', 'b2b-target.json', 'b2b-target.csv')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def public_record(row):
    # Current fields and evidence stay visible; historical copies stay losslessly
    # in the server checkpoint rather than being downloaded on every page load.
    return {k: v for k, v in row.items() if k not in ('history', 'source_records')}


def pack(tree):
    files = {name: (Path(tree)/name).read_text() for name in FILES if (Path(tree)/name).exists()}
    raw = encoded(files)
    return base64.b64encode(gzip.compress(raw, mtime=0)).decode(), hashlib.sha256(raw).hexdigest()


def unpack(checkpoint, tree):
    raw = gzip.decompress(base64.b64decode(checkpoint['archive'], validate=True))
    if hashlib.sha256(raw).hexdigest() != checkpoint['sha256']:
        raise ValueError('Checkpoint checksum mismatch')
    files = json.loads(raw)
    required = {'data.json', 'developers.json', 'yell.json', 'report.json'}
    if not required <= files.keys() or not files.keys() <= set(FILES):
        raise ValueError('Invalid checkpoint files')
    data = json.loads(files['data.json'])
    if not data.get('records'):
        raise ValueError('Empty directory checkpoint')
    Path(tree).mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (Path(tree)/name).write_text(body)


class CompanyStore:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ['SUPABASE_URL']).rstrip('/')
        if not self.url.startswith('https://') or not self.url.endswith('.supabase.co'):
            raise ValueError('Expected a Supabase HTTPS project URL')
        self.session = requests.Session()
        self.session.headers.update(apikey=key or os.environ['SUPABASE_SECRET_KEY'])

    def request(self, method, path, body=None, params=None):
        # GET and explicit upserts are idempotent. RPC publication is not blindly
        # retried: its committed revision is checked after an ambiguous response.
        for attempt in range(4):
            try:
                response = self.session.request(method, self.url+'/rest/v1/'+path,
                    params=params, json=body, timeout=(10, 120), allow_redirects=False,
                    headers={'Prefer': 'resolution=merge-duplicates,return=minimal'})
                if response.ok:
                    return response.json() if response.content else None
                if response.status_code not in (429, 502, 503, 504):
                    try:
                        error = response.json()
                        detail = str(error.get('code', ''))+' '+str(error.get('message', ''))[:200]
                    except ValueError:
                        detail = 'Non-JSON response'
                    detail = detail.replace(self.session.headers['apikey'], '[redacted]')
                    raise RuntimeError(f'Supabase {method} {path}: HTTP {response.status_code} {detail}')
            except requests.RequestException:
                pass
            if path.startswith('rpc/') or attempt == 3:
                raise RuntimeError(f'Supabase {method} {path}: request failed')
            time.sleep(2**attempt)

    def all_rows(self, table, columns='*', **filters):
        rows = []
        cursor = None
        while True:
            params = dict(select=columns, order='id.asc', limit=1000, **filters)
            if cursor is not None:
                params['id'] = 'gt.'+cursor
            batch = self.request('GET', table, params=params)
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            cursor = batch[-1]['id']

    def restore(self, tree):
        cache = os.environ.get('COMPANY_CHECKPOINT_CACHE')
        if cache and Path(cache).exists():
            checkpoint = json.loads(Path(cache).read_text())
            if checkpoint.get('revision') == self.checkpoint_revision():
                unpack(checkpoint, tree)
                print('Restored verified GitHub Actions cache; no full Supabase download')
                return
        rows = self.request('GET', 'collector_checkpoints', params={'id': 'eq.current', 'select': '*'})
        if len(rows) != 1:
            raise RuntimeError('Supabase checkpoint missing; migrate before enabling collection')
        unpack(rows[0], tree)

    def checkpoint_revision(self):
        rows = self.request('GET', 'collector_checkpoints', params={'id': 'eq.current', 'select': 'revision'})
        if len(rows) != 1:
            raise RuntimeError('Supabase checkpoint missing')
        return rows[0]['revision']

    def save(self, tree):
        tree = Path(tree)
        data = json.loads((tree/'data.json').read_text())
        report = json.loads((tree/'report.json').read_text())
        rows = data['records']
        ids = {r['id'] for r in rows}
        if not rows or len(ids) != len(rows):
            raise ValueError('Empty directory or duplicate IDs')
        archive, checksum = pack(tree)
        existing = {r['id']: r for r in self.all_rows('companies', 'id,content_hash,active')}
        revision = str(uuid.uuid4())
        self.request('POST', 'directory_meta', {
            'id': 'current', 'status': 'publishing', 'revision': revision,
            'total': len(rows), 'updated_at': data['updated_at'], 'report': report})
        changed = []
        for row in rows:
            record = public_record(row)
            digest = hashlib.sha256(encoded(record)).hexdigest()
            old = existing.get(row['id'], {})
            if old.get('content_hash') != digest or not old.get('active'):
                changed.append(dict(id=row['id'], record=record, content_hash=digest, active=True))
        # Distinct IDs make these bounded upserts independent and retry-safe.
        batches = [changed[start:start+1000] for start in range(0, len(changed), 1000)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = {pool.submit(self.request, 'POST', 'companies', batch): len(batch) for batch in batches}
            done = 0
            for future in as_completed(pending):
                future.result()
                done += pending[future]
                print(f'Supabase records uploaded: {done}/{len(changed)}', flush=True)
        # Preserve merged/retired rows privately; never permanently delete them.
        retired = [r['id'] for r in existing.values() if r['active'] and r['id'] not in ids]
        for start in range(0, len(retired), 100):
            quoted = ','.join(json.dumps(v) for v in retired[start:start+100])
            self.request('PATCH', 'companies', {'active': False}, {'id': f'in.({quoted})'})
        body = dict(p_archive=archive, p_sha256=checksum, p_revision=revision,
                    p_total=len(rows), p_updated_at=data['updated_at'], p_report=report)
        try:
            self.request('POST', 'rpc/publish_company_directory', body)
        except RuntimeError:
            saved = self.request('GET', 'collector_checkpoints', params={'id': 'eq.current', 'select': 'revision'})
            if not saved or saved[0]['revision'] != revision:
                raise
        print(f'Supabase published: {len(rows)} companies; {len(changed)} changed; revision {revision}', flush=True)
        cache = os.environ.get('COMPANY_CHECKPOINT_CACHE')
        if cache:
            Path(cache).parent.mkdir(parents=True, exist_ok=True)
            Path(cache).write_text(json.dumps(dict(archive=archive, sha256=checksum, revision=revision)))
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write('checkpoint_revision='+revision+'\n')
        return revision
