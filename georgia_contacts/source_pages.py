"""Reviewed portal/HTML adapters with persistent, published-link-only queues."""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .directories import city_facts
from .extract import extract, normalize_phone, RELEVANT
from .network import FetchError
from .urls import canonical_url, domain, SOCIAL_HOSTS

EXCLUDED_LINK_HOSTS = SOCIAL_HOSTS | {'youtube.com', 'youtu.be', 'linkedin.com', 'pinterest.com',
                                    'tiktok.com', 'google.com', 'goo.gl', 'maps.app.goo.gl', 'wa.me'}


def external_business_links(fragment, url):
    result = []
    for a in fragment.select('a[href]'):
        if 'nofollow' in a.get('rel', []):
            continue
        target = canonical_url(urljoin(url, a['href']))
        if not target or domain(target) == domain(url):
            continue
        host = domain(target)
        if any(host == h or host.endswith('.' + h) for h in EXCLUDED_LINK_HOSTS):
            continue
        if re.search(r'\.(?:jpg|jpeg|png|webp|svg|pdf|zip)(?:$|\?)', target, re.I):
            continue
        result.append({'url': target, 'name': '', 'discovered_from': url})
    return result


def dizaini_profile(soup, url, timestamp):
    section = soup.select_one('#more-info')
    if not section:
        raise FetchError('unreachable', 'directory_profile_layout_changed')
    name_node = next((n for n in section.select('h5') if 'კომპანია' in n.get_text()), None)
    if not name_node:
        raise FetchError('unreachable', 'directory_business_name_missing')
    name = name_node.get_text(' ', strip=True).split(':', 1)[-1].strip()
    parsed = extract(str(section), url, timestamp, name)
    if not RELEVANT.search(parsed['text']):
        return []
    facts = [f for f in parsed['facts'] if f['kind'] != 'website']
    # This public listing exposes the number with an unauthenticated client-side
    # toggle. Read ONLY that business field; don't call its tracking POST endpoint.
    for reveal in section.select('[onclick^="showAndTrack("]'):
        number = reveal.select_one('.full-number')
        label = reveal.select_one('.view-label')
        if number and label and 'ნომრ' in label.get_text():
            value = normalize_phone(number.get_text(strip=True))
            if value:
                existing = next((f for f in facts if f['kind'] == 'phone' and f['value'] == value), None)
                if existing:
                    existing['source_disclosure'] = 'public_client_side_reveal'
                else:
                    facts.append({'kind': 'phone', 'value': value, 'source_url': url,
                                  'first_seen': timestamp, 'last_seen': timestamp,
                                  'source_disclosure': 'public_client_side_reveal'})
    sites = external_business_links(section, url)
    for site in sites:
        facts.append({'kind': 'website', 'value': site['url'], 'source_url': url,
                      'first_seen': timestamp, 'last_seen': timestamp})
    facts.extend(city_facts(parsed['text'], url, timestamp))
    owner = section.select_one('a[href^="/products/"]')
    identity = owner['href'].rstrip('/').split('/')[-1] if owner else urlsplit(url).path.rsplit('/', 1)[-1]
    return [{'url': sites[0]['url'] if sites else url, 'name': name,
             'kind': 'website' if sites else 'listing', 'business_key': 'dizaini:' + identity,
             'directory_facts': facts, 'directory_service_claim': True, 'discovered_from': url}]


def page_candidates(html, url, source, timestamp):
    soup = BeautifulSoup(html, 'html.parser')
    for meta in soup.find_all('meta', attrs={'name': re.compile('^robots$', re.I)}):
        if any(x in meta.get('content', '').casefold() for x in ('none', 'noindex', 'nofollow')):
            raise FetchError('blocked', 'directory_meta_robots_opt_out')
    adapter = source['adapter']
    results, pages = [], []
    if adapter == 'dizaini':
        if re.fullmatch(r'/product/\d+/?', urlsplit(url).path):
            return dizaini_profile(soup, url, timestamp), []
        priority = {}
        for a in soup.select('a[href]'):
            target = canonical_url(urljoin(url, a['href']))
            if target and domain(target) == domain(url) and re.fullmatch(r'/product/\d+/?', urlsplit(target).path):
                if 'nofollow' not in a.get('rel', []):
                    pages.append(target)
                    rank = 0 if RELEVANT.search(a.get_text(' ', strip=True)) else 1
                    priority[target] = min(priority.get(target, rank), rank)
        pages.sort(key=lambda target: priority[target])
    elif adapter == 'editorial':
        content = soup.select_one(source.get('content_selector', '.entry-content'))
        if content:
            results.extend(external_business_links(content, url))
        else:
            for a in soup.select(source.get('article_selector', '.entry-title a[href]')):
                target = canonical_url(urljoin(url, a.get('href', '')))
                if target and domain(target) == domain(url) and 'nofollow' not in a.get('rel', []):
                    pages.append(target)
        for link in soup.select('link[rel="next"][href], [data-g1-next-page-url]'):
            target = canonical_url(urljoin(url, link.get('href') or link.get('data-g1-next-page-url', '')))
            if target and domain(target) == domain(url):
                pages.append(target)
    elif adapter == 'awards':
        if '/work/' in urlsplit(url).path:
            content = soup.select_one('.project-details') or soup.select_one('.project') or soup.select_one('main')
            if content:
                results.extend(external_business_links(content, url))
        else:
            path = urlsplit(url).path
            category = bool(re.fullmatch(r'/en/Awards/\d{4}/\d+', path))
            for a in soup.select('a[href]'):
                target = canonical_url(urljoin(url, a['href']))
                if not target or domain(target) != domain(url) or 'nofollow' in a.get('rel', []):
                    continue
                target_path = urlsplit(target).path
                if category and re.fullmatch(r'/en/work/\d+', target_path):
                    pages.append(target)
                elif re.fullmatch(r'/en/Awards/\d{4}/\d+', target_path) and re.search(r'interior|ინტერიერ', a.get_text(' ', strip=True), re.I):
                    pages.append(target)
                elif re.fullmatch(r'/en/Awards/\d{4}', target_path) and int(target_path[-4:]) >= source.get('min_year', 2023):
                    pages.append(target)
    return results, list(dict.fromkeys(pages))
