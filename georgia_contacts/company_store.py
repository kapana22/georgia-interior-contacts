"""Supabase persistence. Full, lossless checkpoints plus compact public rows.

Only changed directory rows are uploaded. A failed upload never advances the
collector checkpoint. Restore replays the previous cursor safely on retry.
"""
import base64
import gzip
import hashlib
import io
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

FILES = ('data.json', 'developers.json', 'yell.json', 'report.json', 'deferred.json',
         'campaign.json', 'growth-baseline.json', 'b2b-target.json', 'b2b-target.csv', 'catalogues.json', 'activities.json')
ARCHIVE_BUCKET = 'company-checkpoints'
ARCHIVE_PREFIX = 'storage:v1:' + ARCHIVE_BUCKET + '/'
DIRECTORY_BUCKET = 'company-directory'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def public_record(row):
    # Current fields and evidence stay visible; historical copies stay losslessly
    # in the server checkpoint rather than being downloaded on every page load.
    return {k: v for k, v in row.items() if k not in ('history', 'source_records')}


def pack(tree):
    # Preserve the existing archive/checksum format without holding several
    # full copies of a large JSON snapshot in memory. newline='' retains CSV CRLF.
    compressed, digest = io.BytesIO(), hashlib.sha256()
    with gzip.GzipFile(fileobj=compressed, mode='wb', mtime=0) as archive:
        def emit(fragment):
            digest.update(fragment)
            archive.write(fragment)
        emit(b'{')
        names = sorted(name for name in FILES if (Path(tree)/name).exists())
        for index, name in enumerate(names):
            if index:
                emit(b',')
            emit(encoded(name) + b':"')
            with (Path(tree)/name).open(encoding='utf-8', newline='') as source:
                for chunk in iter(lambda: source.read(65536), ''):
                    emit(encoded(chunk)[1:-1])
            emit(b'"')
        emit(b'}')
    return base64.b64encode(compressed.getvalue()).decode(), digest.hexdigest()


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
        (Path(tree)/name).write_bytes(body.encode('utf-8'))


class CompanyStore:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ['SUPABASE_URL']).rstrip('/')
        if not self.url.startswith('https://') or not self.url.endswith('.supabase.co'):
            raise ValueError('Expected a Supabase HTTPS project URL')
        self.session = requests.Session()
        self.session.headers.update(apikey=key or os.environ['SUPABASE_SECRET_KEY'])

    def storage(self, method, path, **kwargs):
        for attempt in range(4):
            try:
                response = self.session.request(method, self.url+'/storage/v1/'+path,
                    timeout=(10, 120), allow_redirects=False, **kwargs)
                if response.ok or response.status_code in (400, 404, 409):
                    return response
                if response.status_code not in (429, 502, 503, 504):
                    raise RuntimeError(f'Storage {method}: HTTP {response.status_code}')
            except requests.RequestException:
                pass
            if attempt == 3:
                raise RuntimeError(f'Storage {method}: request failed')
            time.sleep(2**attempt)

    def stage_archive(self, archive, checksum):
        if not re.fullmatch(r'[a-f0-9]{64}', checksum):
            raise ValueError('Invalid archive checksum')
        bucket = self.storage('GET', 'bucket/'+ARCHIVE_BUCKET)
        if bucket.status_code in (400, 404):
            self.storage('POST', 'bucket', json=dict(id=ARCHIVE_BUCKET, name=ARCHIVE_BUCKET,
                public=False, file_size_limit=50*1024*1024, allowed_mime_types=['application/gzip']))
            bucket = self.storage('GET', 'bucket/'+ARCHIVE_BUCKET)
        if not bucket.ok or bucket.json().get('public') is not False:
            raise RuntimeError('Checkpoint bucket must be private')
        path = ARCHIVE_BUCKET+'/archives/'+checksum+'.gz'
        response = self.storage('POST', 'object/'+path, data=base64.b64decode(archive, validate=True),
            headers={'Content-Type': 'application/gzip', 'x-upsert': 'false'})
        if not response.ok and response.status_code not in (400, 409):
            raise RuntimeError('Checkpoint upload failed')
        # Read back before referencing it in the transaction. Also handles a
        # retry whose immutable object was already uploaded successfully.
        reference = ARCHIVE_PREFIX+'archives/'+checksum+'.gz'
        self.hydrate_checkpoint(dict(archive=reference, sha256=checksum))
        return reference

    def stage_directory(self, data):
        """One compressed public download, with exactly the same public rows."""
        compressed = io.BytesIO()
        snapshot = dict(version=1, updated_at=data['updated_at'],
            records=[public_record(row) for row in data['records']])
        with gzip.GzipFile(fileobj=compressed, mode='wb', mtime=0, compresslevel=6) as stream:
            writer = io.TextIOWrapper(stream, encoding='utf-8')
            json.dump(snapshot, writer, ensure_ascii=False, separators=(',', ':'))
            writer.flush()
            writer.detach()
        payload = compressed.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        path = 'snapshots/'+digest+'.json.gz'
        bucket = self.storage('GET', 'bucket/'+DIRECTORY_BUCKET)
        if bucket.status_code in (400, 404):
            self.storage('POST', 'bucket', json=dict(id=DIRECTORY_BUCKET, name=DIRECTORY_BUCKET,
                public=True, file_size_limit=50*1024*1024, allowed_mime_types=['application/gzip']))
            bucket = self.storage('GET', 'bucket/'+DIRECTORY_BUCKET)
        if not bucket.ok or bucket.json().get('public') is not True:
            raise RuntimeError('Public directory bucket unavailable')
        response = self.storage('POST', 'object/'+DIRECTORY_BUCKET+'/'+path, data=payload,
            headers={'Content-Type': 'application/gzip', 'x-upsert': 'false', 'Cache-Control': 'max-age=31536000'})
        if not response.ok:
            check = self.storage('GET', 'object/public/'+DIRECTORY_BUCKET+'/'+path)
            if not check.ok or hashlib.sha256(check.content).hexdigest() != digest:
                raise RuntimeError('Public directory snapshot upload failed')
        return dict(path=path, sha256=digest, bytes=len(payload), total=len(data['records']))

    def hydrate_checkpoint(self, checkpoint):
        reference = checkpoint['archive']
        if not reference.startswith('storage:'):
            return checkpoint  # Backward compatible with inline checkpoints/caches.
        if not re.fullmatch(re.escape(ARCHIVE_PREFIX)+r'archives/[a-f0-9]{64}\.gz', reference):
            raise ValueError('Invalid checkpoint storage reference')
        if reference != ARCHIVE_PREFIX+'archives/'+checkpoint['sha256']+'.gz':
            raise ValueError('Checkpoint reference checksum mismatch')
        response = self.storage('GET', 'object/authenticated/'+reference.removeprefix('storage:v1:'))
        if not response.ok:
            raise RuntimeError('Checkpoint object unavailable')
        if hashlib.sha256(gzip.decompress(response.content)).hexdigest() != checkpoint['sha256']:
            raise ValueError('Checkpoint checksum mismatch')
        return dict(checkpoint, archive=base64.b64encode(response.content).decode())

    def migrate_archives(self):
        """Run with collection paused; preserve both revisions and exact contents."""
        rows = self.request('GET', 'collector_checkpoints', params={'select': 'id'})
        for row in rows:
            checkpoint = self.request('GET', 'collector_checkpoints', params={
                'id': 'eq.'+row['id'], 'select': '*'})[0]
            if checkpoint['archive'].startswith(ARCHIVE_PREFIX):
                self.hydrate_checkpoint(checkpoint)
                continue
            reference = self.stage_archive(checkpoint['archive'], checkpoint['sha256'])
            self.request('PATCH', 'collector_checkpoints', {'archive': reference},
                {'id': 'eq.'+checkpoint['id'], 'revision': 'eq.'+checkpoint['revision']})
            saved = self.request('GET', 'collector_checkpoints', params={
                'id': 'eq.'+checkpoint['id'], 'select': 'archive,revision'})
            if len(saved) != 1 or saved[0] != dict(archive=reference, revision=checkpoint['revision']):
                raise RuntimeError('Checkpoint changed during migration; retry with collection paused')
            print('Moved verified checkpoint to private Storage:', checkpoint['id'], flush=True)

    def prune_archives(self):
        # Retain both durable checkpoints and all objects younger than one hour,
        # including uploads from interrupted runs. Collection has one writer.
        checkpoints = self.request('GET', 'collector_checkpoints', params={'select': 'archive'})
        keep = {row['archive'].removeprefix(ARCHIVE_PREFIX) for row in checkpoints}
        response = self.storage('POST', 'object/list/'+ARCHIVE_BUCKET,
            json={'prefix': 'archives', 'limit': 1000, 'sortBy': {'column': 'name', 'order': 'asc'}})
        if not response.ok:
            return
        obsolete = []
        for obj in response.json():
            if not re.fullmatch(r'[a-f0-9]{64}\.gz', obj.get('name', '')):
                continue
            path = 'archives/'+obj['name']
            created = datetime.fromisoformat(obj['created_at'].replace('Z', '+00:00'))
            if path not in keep and (datetime.now(timezone.utc)-created).total_seconds() > 3600:
                obsolete.append(path)
        if obsolete:
            response = self.storage('DELETE', 'object/'+ARCHIVE_BUCKET, json={'prefixes': obsolete})
            if not response.ok:
                raise RuntimeError('Archive retention cleanup failed')

    def prune_directory(self, manifest):
        response = self.storage('POST', 'object/list/'+DIRECTORY_BUCKET,
            json={'prefix': 'snapshots', 'limit': 1000})
        if not response.ok:
            return
        obsolete = []
        for obj in response.json():
            if not re.fullmatch(r'[a-f0-9]{64}\.json\.gz', obj.get('name', '')):
                continue
            path = 'snapshots/'+obj['name']
            created = datetime.fromisoformat(obj['created_at'].replace('Z', '+00:00'))
            if path != manifest['path'] and (datetime.now(timezone.utc)-created).total_seconds() > 3600:
                obsolete.append(path)
        if obsolete:
            self.storage('DELETE', 'object/'+DIRECTORY_BUCKET, json={'prefixes': obsolete})

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
        unpack(self.hydrate_checkpoint(rows[0]), tree)

    def checkpoint_revision(self):
        rows = self.request('GET', 'collector_checkpoints', params={'id': 'eq.current', 'select': 'revision'})
        if len(rows) != 1:
            raise RuntimeError('Supabase checkpoint missing')
        return rows[0]['revision']

    def upload_batch(self, rows):
        try:
            self.request('POST', 'companies', rows)
        except RuntimeError as error:
            # A timed-out PostgREST transaction has not partially committed.
            # Smaller upserts reduce lock/IO pressure on the small DB instance.
            if '57014' not in str(error) or len(rows) <= 100:
                raise
            middle = len(rows)//2
            self.upload_batch(rows[:middle])
            self.upload_batch(rows[middle:])

    def save(self, tree):
        tree = Path(tree)
        data = json.loads((tree/'data.json').read_text())
        report = json.loads((tree/'report.json').read_text())
        rows = data['records']
        ids = {r['id'] for r in rows}
        if not rows or len(ids) != len(rows):
            raise ValueError('Empty directory or duplicate IDs')
        archive, checksum = pack(tree)
        reference = self.stage_archive(archive, checksum)
        manifest = self.stage_directory(data)
        report = dict(report, ui_snapshot=manifest)
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
            pending = {pool.submit(self.upload_batch, batch): len(batch) for batch in batches}
            done = 0
            try:
                for future in as_completed(pending):
                    future.result()
                    done += pending[future]
                    print(f'Supabase records uploaded: {done}/{len(changed)}', flush=True)
            except Exception:
                for future in pending:
                    future.cancel()
                raise
        # Preserve merged/retired rows privately; never permanently delete them.
        retired = [r['id'] for r in existing.values() if r['active'] and r['id'] not in ids]
        for start in range(0, len(retired), 100):
            quoted = ','.join(json.dumps(v) for v in retired[start:start+100])
            self.request('PATCH', 'companies', {'active': False}, {'id': f'in.({quoted})'})
        body = dict(p_archive=reference, p_sha256=checksum, p_revision=revision,
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
        try:
            self.prune_archives()
            self.prune_directory(manifest)
        except (RuntimeError, ValueError, KeyError):
            print('Archive cleanup deferred; both durable checkpoints retained', flush=True)
        return revision
