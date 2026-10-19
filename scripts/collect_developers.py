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
        facts = [f for f in facts if f['kind'] != 'name' and not (f['kind'] == 'website' and f.get('value') == record['directory_profile'])]
        # A successful but sparse page must not erase earlier public contacts.
        merged = {(f['kind'], f['value'], f['source_url']): f for f in record.get('facts', []) if f['kind'] != 'name'}
        for fact in facts:
            key = (fact['kind'], fact['value'], fact['source_url'])
            prior = merged.get(key)
            if prior:
                fact['first_seen'] = prior['first_seen']
            merged[key] = fact
        record['facts'] = list(merged.values())
        record['profile_contact_status'] = 'contacts_found' if facts else 'no_contacts_published'
    else:
        record['profile_contact_status'] = 'contact_section_absent'
    record['profile_collected_at'] = now()

def main():
    p = argparse.ArgumentParser(); p.add_argument('--max-minutes', type=float, default=10); p.add_argument('--profiles', type=int, default=40)
    p.add_argument('--state', default='.leads-state/developers-saxlebi.json'); p.add_argument('--output', default='output/companies-growth/developers-saxlebi.json')
    args = p.parse_args()
    if not 0 < args.max_minutes <= 60 or not 0 <= args.profiles <= 2000:
        p.error('max-minutes must be 0–60 and profiles 0–2000')
    path = Path(args.state)
    state = json.loads(path.read_text()) if path.exists() else {'queue':[ROOT], 'seen':[], 'records':{}, 'errors':[]}
    state.setdefault('cooldowns', {})
    if state.get('resume_after', 0) > time.time():
        print('Source cooldown active'); return
    state.pop('resume_after', None)
    client = Client(time.monotonic()+args.max_minutes*60, delay=2, cooldowns=state['cooldowns'])
    def save():
        state['errors']=state['errors'][-100:]
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
            if r.get('profile_collected_at') or r.get('retry_after',0)>time.time() or done>=args.profiles:continue
            try:
                final,body=client.fetch(r['directory_profile'],page_limit=2000)
                profile(body,r);r.pop('retry_after',None);r.pop('failures',None);done+=1
            except FetchError as e:
                state['errors'].append({'url':r['directory_profile'],'reason':str(e),'at':now()})
                r['failures']=r.get('failures',0)+1
                r['retry_after']=time.time()+min(86400,300*2**min(r['failures'],8))
                if e.status=='blocked':raise
                done+=1
            save()
            if done%10==0:print(json.dumps({'profiles_processed':done}),flush=True)
    except (Deadline,FetchError) as e:
        state['errors'].append({'reason':type(e).__name__+':'+str(e),'at':now()})
        if isinstance(e,FetchError):state['resume_after']=max(time.time()+3600,max(state['cooldowns'].values(),default=0))
        print(str(e),flush=True)
    finally:
        save()
    print(json.dumps({'records':len(state['records']),'listing_pages':len(state['seen']),'remaining_pages':len(state['queue']),'remaining_profiles':sum(not r.get('profile_collected_at') for r in state['records'].values())}),flush=True)

if __name__=='__main__':main()
