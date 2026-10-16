"""Finite, resumable public developer catalogue import; no legal-form inference."""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs
from bs4 import BeautifulSoup
from georgia_contacts.network import Client, Deadline, FetchError
from georgia_contacts.extract import extract
from georgia_contacts.state import atomic_text

ROOT = 'https://saxlebi.com/developers'

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def document(body):
    soup = BeautifulSoup(body, 'html.parser')
    meta = soup.find('meta', attrs={'name': re.compile('^robots$', re.I)})
    if meta and re.search('noindex|nofollow|none', meta.get('content', ''), re.I):
        raise FetchError('blocked', 'meta_robots_opt_out')
    return soup

def listings(body, url):
    soup = document(body)
    records = []
    for a in soup.select('a[href]'):
        link = urljoin(url, a['href'])
        title = a.find('h3')
        if not title or not re.fullmatch(r'https://saxlebi.com/developers/\d+', link):
            continue
        records.append({'id': 'saxlebi:' + link.rsplit('/', 1)[-1], 'name': title.get_text(' ', strip=True),
                        'categories': ['development'], 'country': 'საქართველო', 'facts': [],
                        'directory_profile': link, 'listing_url': url, 'listing_text': a.get_text(' ', strip=True),
                        'first_seen': now(), 'last_seen': now()})
    pages = []
    for a in soup.select('a[href]'):
        link = urljoin(url, a['href']); p = urlsplit(link); q = parse_qs(p.query)
        if p.scheme == 'https' and p.netloc == 'saxlebi.com' and p.path == '/developers' and set(q) == {'page'} and q['page'][0].isdigit():
            pages.append(link)
    if not records:
        raise FetchError('unreachable', 'no_developer_cards')
    return records, pages

def profile(body, record):
    soup = document(body)
    title = soup.find('h1')
    if not title:
        raise FetchError('unreachable', 'missing_profile_title')
    record['profile_name'] = title.get_text(' ', strip=True)
    heading = next((h for h in soup.find_all('h2') if h.get_text(' ', strip=True) == 'Contact information'), None)
    if heading:
        section = heading.parent
        # Public email obfuscation is decoded by the site's own browser script.
        for node in section.select('[data-cfemail]'):
            try:
                raw = bytes.fromhex(node['data-cfemail'])
                node.string = bytes(c ^ raw[0] for c in raw[1:]).decode('utf-8')
            except (ValueError, UnicodeDecodeError):
                pass
        record['contact_text'] = section.get_text(' ', strip=True)
        facts = extract(str(section), record['directory_profile'], now(), record['name'])['facts']
        for a in section.select('a[href]'):
            value = a['href'].strip()
            if value.startswith(('tel:', 'mailto:', '/cdn-cgi/')):
                continue
            if re.match(r'^(?:https?://)?[\w.-]+\.[a-z]{2,}(?:/|$)', value, re.I):
                value = value if value.startswith(('http://', 'https://')) else 'https://' + value
                host = urlsplit(value).hostname or ''
                kind = next((k for k in ['facebook', 'instagram', 'linkedin', 'youtube', 'tiktok'] if host == k+'.com' or host.endswith('.'+k+'.com')), 'website')
                facts.append({'kind': kind, 'value': value, 'source_url': record['directory_profile'], 'first_seen': now(), 'last_seen': now()})
        record['facts'] = [f for f in facts if f['kind'] != 'name' and not (f['kind'] == 'website' and f.get('value') == record['directory_profile'])]
    record['profile_collected_at'] = now()

def main():
    p = argparse.ArgumentParser(); p.add_argument('--max-minutes', type=float, default=10); p.add_argument('--profiles', type=int, default=40)
    p.add_argument('--state', default='.leads-state/developers-saxlebi.json'); p.add_argument('--output', default='output/companies-growth/developers-saxlebi.json')
    args = p.parse_args()
    if not 0 < args.max_minutes <= 60 or not 0 <= args.profiles <= 2000:
        p.error('max-minutes must be 0–60 and profiles 0–2000')
    path = Path(args.state)
    state = json.loads(path.read_text()) if path.exists() else {'queue':[ROOT], 'seen':[], 'records':{}, 'errors':[]}
    client = Client(time.monotonic()+args.max_minutes*60, delay=2)
    def save():
        atomic_text(path,json.dumps(state,ensure_ascii=False))
        atomic_text(args.output,json.dumps({'records':list(state['records'].values()),'updated_at':now(),'remaining_listing_pages':len(state['queue']),'remaining_profiles':sum(not r.get('profile_collected_at') for r in state['records'].values())},ensure_ascii=False))
    try:
        while state['queue']:
            url=state['queue'][0]
            final, body=client.fetch(url,page_limit=2000)
            rows,pages=listings(body,final)
            for r in rows:
                state['records'].setdefault(r['id'],r)
            state['queue'].pop(0);state['seen'].append(url)
            state['queue'].extend(x for x in pages if x not in state['seen'] and x not in state['queue'])
            save();print(json.dumps({'pages':len(state['seen']),'developers':len(state['records'])}),flush=True)
        done=0
        for r in state['records'].values():
            if r.get('profile_collected_at') or done>=args.profiles:continue
            try:
                final,body=client.fetch(r['directory_profile'],page_limit=2000)
                profile(body,r);done+=1
            except FetchError as e:
                state['errors'].append({'url':r['directory_profile'],'reason':str(e),'at':now()})
                if e.status=='blocked':raise
                done+=1
            save()
            if done%10==0:print(json.dumps({'profiles_processed':done}),flush=True)
    except (Deadline,FetchError) as e:
        state['errors'].append({'reason':type(e).__name__+':'+str(e),'at':now()})
        print(str(e),flush=True)
    finally:
        save()
    print(json.dumps({'records':len(state['records']),'listing_pages':len(state['seen']),'remaining_pages':len(state['queue']),'remaining_profiles':sum(not r.get('profile_collected_at') for r in state['records'].values())}),flush=True)

if __name__=='__main__':main()
