"""Reviewed public business sources, with company-scoped contact extraction."""
import html as html_lib
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .directories import city24_profile, city_facts
from .extract import extract, normalize_phone, social_url
from .network import FetchError
from .urls import canonical_url, domain

HOSTS = {'geostat': 'br-api.geostat.ge', '08': '08.ge', '1020': '1020.ge', 'city24': 'city24.ge'}
PORTALS = set(HOSTS.values()) | {'br.geostat.ge', 'yell.ge', 'saxlebi.com'}
SOCIAL = {'facebook.com', 'instagram.com', 'linkedin.com', 'youtube.com', 'youtu.be',
          'tiktok.com', 'twitter.com', 'x.com', 'fb.com', 'fb.me', 't.me', 'wa.me',
          'reddit.com', 'pinterest.com', 'tumblr.com'}


def business_website(value):
    value = str(value or '').strip()
    if not value or '@' in value or re.search(r'\s', value):
        return ''
    url = canonical_url(value if '://' in value else 'https://' + value.removeprefix('//'))
    host = domain(url)
    if not host or '.' not in host or any(host == h or host.endswith('.' + h) for h in PORTALS | SOCIAL):
        return ''
    if host in {'google.com', 'maps.google.com', 'goo.gl', 'infoshop.ge', '08group.ge'}:
        return ''
    return url


def fact(kind, value, url, timestamp):
    return {'kind': kind, 'value': value, 'source_url': url, 'first_seen': timestamp, 'last_seen': timestamp}


def unique_facts(facts):
    return list({(f['kind'], f['value'], f['source_url']): f for f in facts}.values())


def record(source, identity, name, facts, url, timestamp, **fields):
    return {'id': source + ':' + str(identity), 'source_id': str(identity),
            'source': 'საქსტატი / Geostat' if source == 'geostat' else HOSTS[source],
            'name': name.strip(), 'facts': unique_facts(facts), 'source_url': url,
            'last_seen': timestamp, 'categories': [], **fields}


def geostat_page(payload, url, timestamp):
    """Keep corporate fields only; never retain directors, owners or personal IDs."""
    try:
        data = json.loads(payload)
        rows, paging = data['data'], data['pagination']
        query = parse_qs(urlsplit(url).query)
        page, limit = int(query['page'][0]), int(query['limit'][0])
        if (not isinstance(rows, list) or paging['page'] != page or paging['limit'] != limit
                or not isinstance(paging['totalPages'], int) or not isinstance(paging['total'], int)
                or len(rows) > limit or (not rows and page <= paging['totalPages'])):
            raise ValueError('Invalid pagination')
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError('unreachable', 'geostat_response_layout_changed') from exc
    records = []
    for row in rows:
        if not isinstance(row, dict):
            raise FetchError('unreachable', 'geostat_response_layout_changed')
        tax = str(row.get('Legal_Code') or '').strip()
        name = str(row.get('Full_Name') or '').strip()
        if row.get('Legal_Form_ID') != 1 or row.get('ISActive') != 1 or not re.fullmatch(r'\d{9}', tax) or not name:
            continue
        source_url = 'https://br.geostat.ge/?identificationNumber=' + tax
        # Escape API strings before using the shared HTML contact normalizer.
        fragment = '<p>' + html_lib.escape(str(row.get('mob') or '')) + '</p><p>' + html_lib.escape(str(row.get('Email') or '')) + '</p>'
        facts = [f for f in extract(fragment, source_url, timestamp, name)['facts'] if f['kind'] in {'phone', 'email'}]
        for raw in re.split(r'[,;\n]+', str(row.get('web') or '')):
            website = business_website(raw)
            if website:
                facts.append(fact('website', website, source_url, timestamp))
            else:
                social = social_url(raw if '://' in raw else 'https://' + raw.strip())
                if social:
                    facts.append(fact(*social, source_url, timestamp))
        fields = {'tax_id': tax, 'legal_status': 'შპს',
                  'legal_status_full': 'შეზღუდული პასუხისმგებლობის საზოგადოება', 'country': 'საქართველო',
                  'city': str(row.get('City_name2') or row.get('City_name') or '').strip(),
                  'address': str(row.get('Address2') or row.get('Address') or '').strip(),
                  'registered_at': str(row.get('Init_Reg_date') or '')[:10],
                  'quality_notes': 'საქსტატი: აქტიური შპს; საკონტაქტო არხის მუშაობა დასადასტურებელია.'}
        facts.extend(fact(k, v, source_url, timestamp) for k, v in fields.items() if v and k != 'quality_notes')
        records.append(record('geostat', tax, name, facts, source_url, timestamp, **fields))
    if rows and not records:
        raise FetchError('unreachable', 'geostat_active_llc_filter_changed')
    tasks = []
    if page < paging['totalPages']:
        query['page'] = [str(page + 1)]
        p = urlsplit(url)
        tasks.append({'url': urlunsplit((p.scheme, p.netloc, p.path, urlencode(query, doseq=True), '')), 'kind': 'listing'})
    return records, tasks, {'available_active_llcs': paging['total'], 'total_pages': paging['totalPages']}


def decode_public_emails(soup):
    # Decode the same public email text displayed by the site's email-protection JS.
    for node in soup.select('[data-cfemail], a[href*="/cdn-cgi/l/email-protection#"]'):
        encoded = node.get('data-cfemail') or node.get('href', '').split('#')[-1]
        try:
            data = bytes.fromhex(encoded)
            address = bytes(x ^ data[0] for x in data[1:]).decode('utf-8')
        except (ValueError, UnicodeError, IndexError):
            continue
        if '@' in address:
            if node.name == 'a':
                node['href'] = 'mailto:' + address
            else:
                node.string = address


def scoped_facts(node, url, timestamp, name, websites=False):
    fragment = BeautifulSoup(str(node), 'html.parser')
    decode_public_emails(fragment)
    parsed = extract(str(fragment), url, timestamp, name)
    if parsed['blocked']:
        raise FetchError('blocked', parsed['reasons'][0])
    facts = [f for f in parsed['facts'] if f['kind'] not in {'website', 'name'}]
    if websites:
        for a in fragment.select('a[href]'):
            website = business_website(urljoin(url, a['href']))
            if website:
                facts.append(fact('website', website, url, timestamp))
    return facts


def task_for(source, url):
    if not canonical_url(url) or domain(url) != HOSTS[source]:
        return None
    path = urlsplit(url).path
    patterns = {'08': r'^/organizations/view/(\d+)/', '1020': r'^/business/([a-zA-Z0-9]+)/',
                'city24': r'^/ge/[^/]+/organizations/(\d+)/'}
    match = re.search(patterns.get(source, r'(?!)'), path)
    if match:
        if source == 'city24':
            url = url.rstrip('/') + '/'
        return {'url': url, 'kind': 'profile', 'identity': match.group(1)}
    allowed = (source == '08' and path.startswith('/organizations/filter') or
               source == '1020' and path.startswith('/category/') or
               source == 'city24' and path.startswith('/ge/georgia/catalogue/'))
    return {'url': url, 'kind': 'listing'} if allowed else None


def html_page(source, body, task, timestamp):
    url = task['url']
    soup = BeautifulSoup(body, 'html.parser')
    meta = soup.find('meta', attrs={'name': re.compile(r'^(robots|GeorgiaInteriorContacts)$', re.I)})
    if meta and re.search(r'none|noindex|nofollow', meta.get('content', ''), re.I):
        raise FetchError('blocked', 'directory_meta_robots_opt_out')
    records, tasks = [], []
    if task['kind'] == 'profile':
        title = soup.find('h1')
        if not title:
            raise FetchError('unreachable', 'directory_profile_layout_changed')
        name = title.get_text(' ', strip=True)
        if source == 'city24':
            facts = city24_profile(body, url, timestamp, name)['facts']
        else:
            if source == '08':
                heading = next((n for n in soup.select('p > b') if n.get_text(strip=True) == 'საკონტაქტო მონაცემები'), None)
                node = heading.parent.parent if heading else None
            else:
                detail = soup.select_one('.detail_item')
                parts = detail.select('.upper_main, .info_list') if detail else []
                node = BeautifulSoup(''.join(str(part) for part in parts), 'html.parser') if parts else None
            if node is None:
                raise FetchError('unreachable', 'directory_profile_layout_changed')
            facts = scoped_facts(node, url, timestamp, name, websites=True)
        if any(f['kind'] in {'phone', 'email', 'website', 'facebook', 'instagram'} for f in facts):
            records.append(record(source, task['identity'], name, facts, url, timestamp))
    else:
        # Phone JSON is scoped by each card's published numeric business ID.
        phone_map = {}
        if source == '1020':
            for script in soup.find_all('script'):
                for match in re.finditer(r"app\.phones\('(\[.*?\])'\)", script.get_text(), re.S):
                    try:
                        for item in json.loads(match.group(1)):
                            if not isinstance(item, dict):
                                continue
                            phones = item.get('phones') or []
                            phone_map[str(item['id'])] = [phones] if isinstance(phones, dict) else phones if isinstance(phones, list) else []
                    except (ValueError, KeyError, TypeError):
                        continue
        selectors = {'08': '.card:has(.card-top)', '1020': '.items_list .item[id^="business_"]'}
        if source in selectors:
            for card in soup.select(selectors[source]):
                a = card.select_one('.card-top a[href]' if source == '08' else '.item_tl a[href]')
                if not a:
                    continue
                target = canonical_url(urljoin(url, a['href']))
                profile = task_for(source, target)
                if not profile or profile['kind'] != 'profile':
                    continue
                name = a.get_text(' ', strip=True)
                facts = scoped_facts(card, url, timestamp, name)
                facts.extend(city_facts(card.get_text(' ', strip=True), url, timestamp))
                for phone in phone_map.get(card.get('id', '').removeprefix('business_'), []):
                    value = normalize_phone(str(phone.get('original', '') if isinstance(phone, dict) else phone))
                    if value:
                        facts.append(fact('phone', value, url, timestamp))
                if name and any(f['kind'] in {'phone', 'email', 'website'} for f in facts):
                    records.append(record(source, profile['identity'], name, facts, target, timestamp))
    # Follow only published, reviewed paths, never guessed IDs or hidden search APIs.
    # Profile recommendations are not crawled: only their company's contact block is read.
    if task['kind'] != 'profile':
        for a in soup.select('a[href]'):
            if 'nofollow' in a.get('rel', []) or a['href'].startswith('#'):
                continue
            candidate = task_for(source, canonical_url(urljoin(url, a['href'])))
            if candidate:
                tasks.append(candidate)
        if not records and not tasks:
            raise FetchError('unreachable', 'directory_listing_layout_changed')
    return records, list({t['url']: t for t in tasks}.values()), {}
