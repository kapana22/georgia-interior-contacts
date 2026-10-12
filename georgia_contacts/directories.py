"""Business-specific directory slices; never parse a whole portal as one business."""
import re
from urllib.parse import urljoin, urlsplit, parse_qs
from bs4 import BeautifulSoup
from .extract import extract, CITIES, RELEVANT
from .urls import canonical_url, domain, SOCIAL_HOSTS
from .network import FetchError


def source_key(source):
    if source.get('kind') in {'listing', 'profile'}:
        return source['business_key']
    return domain(source['url'])


def city_facts(text, url, timestamp):
    result = []
    for city, variants in CITIES.items():
        if any(re.search(r'(?<!\w)' + re.escape(v) + r'(?!\w)', text.casefold()) for v in variants):
            result.append({'kind': 'city', 'value': city, 'source_url': url,
                           'first_seen': timestamp, 'last_seen': timestamp})
    return result


def yell_candidates(html, url, timestamp):
    soup = BeautifulSoup(html, 'html.parser')
    candidates = []
    for card in soup.select('[id^="SR_div_"]'):
        identity = card.get('id', '').removeprefix('SR_div_')
        if not identity.isdigit():
            continue
        names = [a for a in card.find_all('a', href=True)
                 if 'company.php' in a['href'] and a.get_text(' ', strip=True)]
        if not names:
            continue
        name = names[0].get_text(' ', strip=True)
        profile = canonical_url(urljoin(url, names[0]['href']))
        website = next((canonical_url(a['href']) for a in card.find_all('a', href=True)
                        if a.get_text(' ', strip=True).casefold() == 'website'), '')
        if domain(website) in SOCIAL_HOSTS:
            website = ''
        parsed = extract(str(card), url, timestamp, name)
        facts = [f for f in parsed['facts'] if f['kind'] not in {'website', 'name'}]
        facts.append({'kind': 'name', 'value': name, 'source_url': url,
                      'first_seen': timestamp, 'last_seen': timestamp})
        if website:
            facts.append({'kind': 'website', 'value': website, 'source_url': url,
                          'first_seen': timestamp, 'last_seen': timestamp})
        # Cards contain an explicit city/address line, with no projects or portal footer.
        facts.extend(city_facts(card.get_text(' ', strip=True).split('Tel:')[0].split('ტელ:')[0], url, timestamp))
        candidates.append({'url': website or profile or url, 'name': name,
                           'kind': 'website' if website else 'listing',
                           'business_key': 'yell:' + identity,
                           'discovered_from': url, 'directory_profile': profile,
                           'directory_facts': facts,
                           'directory_service_claim': bool(RELEVANT.search(parsed['text']))})
    return candidates


def city24_links(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    profiles, pages = [], []
    for card in soup.select('.csd-organizations-ul > li'):
        a = card.select_one('.csd-org-name a[href]')
        if not a:
            continue
        target = canonical_url(urljoin(url, a['href']))
        match = re.search(r'/organizations/(\d+)/', target)
        if target and match and domain(target) == 'city24.ge':
            # Published profiles redirect to a trailing slash; avoid wasting the shared budget.
            target = target.rstrip('/') + '/'
            profiles.append({'url': target, 'name': a.get_text(' ', strip=True),
                             'kind': 'profile', 'profile_adapter': 'city24',
                             'business_key': 'city24:' + match.group(1), 'discovered_from': url})
    # Follow only actually published pagination links; never manufacture page numbers.
    for a in soup.select('.csd-pagination a[href]'):
        target = canonical_url(urljoin(url, a['href']))
        if target and domain(target) == 'city24.ge' and '/interior-design/' in target:
            if 'csd-pagination-active' not in a.get('class', []):
                pages.append(target)
    return profiles, list(dict.fromkeys(pages))


def city24_profile(html, url, timestamp, name):
    soup = BeautifulSoup(html, 'html.parser')
    meta = soup.find('meta', attrs={'name': 'robots'})
    if meta and any(x in meta.get('content', '').lower() for x in ('none', 'noindex', 'nofollow')):
        raise FetchError('blocked', 'directory_meta_robots_opt_out')
    contacts = soup.select_one('#tab_contact')
    if not contacts:
        raise FetchError('unreachable', 'directory_profile_layout_changed')
    about = soup.select_one('#tab_about')
    title = soup.find('h1')
    business_name = title.get_text(' ', strip=True) if title else name
    # Exclude city menus, neighbours, reviews, support emails and portal footer.
    fragment = '<meta charset="utf-8">' + str(contacts) + (str(about) if about else '')
    parsed = extract(fragment, url, timestamp, business_name)
    parsed['facts'] = [f for f in parsed['facts'] if f['kind'] != 'website']
    websites = []
    for a in contacts.select('.csd-org-li-www a[href]'):
        website = canonical_url(a['href'])
        if website and domain(website) != domain(url) and domain(website) not in SOCIAL_HOSTS:
            websites.append(website)
            parsed['facts'].append({'kind': 'website', 'value': website, 'source_url': url,
                                    'first_seen': timestamp, 'last_seen': timestamp})
    for node in contacts.select('.csd-org-li-address, .csd-org-li-map-marker, .csd-org-li-location'):
        parsed['facts'].extend(city_facts(node.get_text(' ', strip=True), url, timestamp))
    parsed['websites'] = list(dict.fromkeys(websites))
    return parsed
