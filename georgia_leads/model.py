"""Evidence-based leads; missing listings never prove that a website does not exist."""
import copy
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode, urlunsplit
from bs4 import BeautifulSoup
from georgia_contacts.directories import yell_candidates
from georgia_contacts.state import identities, merge_record, semantic
from georgia_contacts.urls import canonical_url, domain, SOCIAL_HOSTS

SOCIAL = SOCIAL_HOSTS | {'linkedin.com', 'tiktok.com', 'youtube.com', 'x.com', 'twitter.com', 'linktr.ee', 'wa.me', 't.me'}
FREE_EMAIL = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'mail.ru', 'icloud.com', 'live.com', 'yandex.ru', 'yandex.com'}


def social(url):
    host = domain(url)
    return any(host == h or host.endswith('.' + h) for h in SOCIAL)


def listing_records(html, url, timestamp, category):
    soup = BeautifulSoup(html, 'html.parser')
    # Reviews/user comments and portal support are not business contacts.
    for node in soup.select('.review, .reviews, [id^="review"], [id^="Review"], .comment, .comments'):
        node.decompose()
    meta = soup.find('meta', attrs={'name': re.compile('^robots$', re.I)})
    if meta and re.search(r'noindex|nofollow|none', meta.get('content', ''), re.I):
        from georgia_contacts.network import FetchError
        raise FetchError('blocked', 'directory_meta_robots_opt_out')
    records = []
    for candidate in yell_candidates(str(soup), url, timestamp):
        card = soup.find(id='SR_div_' + candidate['business_key'].split(':')[1])
        references = [canonical_url(urljoin(url, a['href'])) for a in card.select('a[href]')
                      if a.get_text(' ', strip=True).casefold() in {'website', 'ვებსაიტი', 'ვებგვერდი', 'საიტი'}]
        own = [x for x in references if x and not social(x) and domain(x) != domain(url)]
        unresolved = bool(references) and not own and any(not x or domain(x) == domain(url) for x in references)
        facts = [f for f in candidate['directory_facts'] if f['kind'] != 'website']
        for site in own:
            facts.append({'kind': 'website', 'value': site, 'source_url': url, 'first_seen': timestamp, 'last_seen': timestamp})
        status = 'listed' if own else 'reference_unresolved' if unresolved else 'social_only' if any(f['kind'] in {'facebook', 'instagram'} for f in facts) else 'not_listed'
        hints = sorted({f['value'].split('@')[-1].lower() for f in facts if f['kind'] == 'email' and f['value'].split('@')[-1].lower() not in FREE_EMAIL})
        records.append({'name': candidate['name'], 'business_key': candidate['business_key'], 'domains': sorted({domain(x) for x in own}),
                        'facts': facts, 'status': 'review', 'review_reasons': ['website_need_requires_manual_confirmation'],
                        'categories': [category], 'checks': [{'source_url': url, 'profile_url': candidate['directory_profile'],
                        'checked_at': timestamp, 'outcome': status}], 'website_checks': [], 'email_domain_hints': hints,
                        'first_seen': timestamp, 'last_seen': timestamp})
    return records


def published_pages(html, current):
    """Only actual anchors or published ItemList URLs + visible page controls."""
    soup = BeautifulSoup(html, 'html.parser')
    base = urlsplit(current)
    allowed = {'rub', 'lan', 'SR_pg'}
    query = parse_qs(base.query)
    def same_category(target):
        p = urlsplit(target); q = parse_qs(p.query)
        return domain(target) == domain(current) and p.path == base.path and set(q) <= allowed and all(q.get(k) == query.get(k) for k in ('rub', 'lan'))
    urls = []
    for a in soup.select('a[href]'):
        target = canonical_url(urljoin(current, a['href']))
        if target and same_category(target) and 'SR_pg' in parse_qs(urlsplit(target).query):
            urls.append(target)
    templates = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                items.extend(item.get('@graph', []) if isinstance(item.get('@graph'), list) else [])
                target = canonical_url(item.get('url', '') if isinstance(item.get('url'), str) else '')
                if item.get('@type') == 'ItemList' and target and same_category(target) and 'SR_pg' in parse_qs(urlsplit(target).query):
                    templates.append(target)
    # The portal sometimes publishes the wrong category in JSON-LD. Its
    # visible pagination uses these form fields; retain the requested category.
    form = soup.select_one('form#search_form')
    if form and form.select_one('input[name="SR_pg"]'):
        values = {n.get('name'): n.get('value') for n in form.select('input[name]')}
        if all([values.get(k)] == query.get(k) for k in ('rub', 'lan')):
            templates.append(current)
    numbers = {int(m.group(1)) for node in soup.select('[onclick]') if (m := re.fullmatch(r'\s*F_change_SR_pg\((\d+)\)\s*;?\s*', node['onclick'])) and 1 <= int(m.group(1)) <= 500}
    for template in templates:
        p = urlsplit(template)
        for number in sorted(numbers):
            q = parse_qs(p.query); q['SR_pg'] = [str(number)]
            urls.append(urlunsplit((p.scheme, p.netloc, p.path, urlencode(q, doseq=True), '')))
    # Stable logical URL order for equivalent parameter orders and page-1 aliases.
    return list(dict.fromkeys(urls))


def page_key(url):
    p = urlsplit(url); q = parse_qs(p.query); q.setdefault('SR_pg', ['1'])
    return (domain(url), p.path, tuple(sorted((k, tuple(v)) for k, v in q.items())))


def merge_lead(records, incoming):
    keys = identities(incoming); matches = []
    while True:
        new = [r for r in records if r not in matches and identities(r) & keys]
        if not new: break
        matches.extend(new)
        for r in new: keys |= identities(r)
    groups = [*matches, incoming]
    result = merge_record(records, incoming)
    result['categories'] = sorted({c for r in groups for c in r.get('categories', [])})
    # Latest observation per source, while facts remain append-only.
    for field, key in [('checks', 'source_url'), ('website_checks', 'url')]:
        observations = {}
        for r in groups:
            for check in r.get(field, []):
                if check.get('checked_at', '') >= observations.get(check[key], {}).get('checked_at', ''):
                    observations[check[key]] = copy.deepcopy(check)
        result[field] = sorted(observations.values(), key=lambda x: x[key])
    result['email_domain_hints'] = sorted({h for r in groups for h in r.get('email_domain_hints', [])})
    return result


def opportunity(record):
    websites = [f for f in record['facts'] if f['kind'] == 'website']
    if websites:
        checks = record.get('website_checks', [])
        if any(c['outcome'] == 'reachable' for c in checks): return 'website_listed'
        if any(c['outcome'] == 'unreachable' and c.get('reason') in {'http_404', 'http_410'} for c in checks): return 'website_unreachable'
        return 'website_listed'
    outcomes = {c['outcome'] for c in record.get('checks', [])}
    if 'reference_unresolved' in outcomes or 'listed' in outcomes: return 'uncertain'
    return 'social_only' if 'social_only' in outcomes else 'not_listed'


def lead_semantic(record):
    value = semantic(record)
    value.update(categories=record.get('categories', []), opportunity=opportunity(record),
                 checks=[{k:v for k,v in c.items() if k != 'checked_at'} for c in record.get('checks', [])],
                 website_checks=[{k:v for k,v in c.items() if k != 'checked_at'} for c in record.get('website_checks', [])])
    return value


def changes(before, after, timestamp):
    old = {r['id']: r for r in before}; rows = []
    for record in after:
        prior = old.get(record['id'])
        if prior is None or lead_semantic(prior) != lead_semantic(record):
            rows.append({'type': 'new' if prior is None else 'changed', 'record_id': record['id'], 'name': record['name'], 'collected_at': timestamp,
                         'before': json.dumps(lead_semantic(prior), ensure_ascii=False) if prior else '', 'after': json.dumps(lead_semantic(record), ensure_ascii=False)})
    for prior in before:
        if prior['id'] not in {r['id'] for r in after}:
            target = next((r for r in after if identities(prior) & identities(r)), None)
            if target: rows.append({'type': 'merged', 'record_id': prior['id'], 'name': prior['name'], 'collected_at': timestamp, 'before': prior['id'], 'after': target['id']})
    return rows
