"""Bounded directory discovery from observed links, never invented pagination."""
from urllib.parse import parse_qs, urlsplit, urljoin
from bs4 import BeautifulSoup
from .urls import canonical_url, domain, SOCIAL_HOSTS
from .network import FetchError
from .directories import yell_candidates, city24_links


def yell_links(html, url):
    soup = BeautifulSoup(html, "html.parser")
    websites, pages = [], []
    for meta in soup.find_all("meta", attrs={"name": "robots"}):
        if any(x in meta.get("content", "").lower() for x in ("none", "noindex", "nofollow")):
            raise FetchError("blocked", "directory_meta_robots_opt_out")
    for a in soup.find_all("a", href=True):
        if "nofollow" in a.get("rel", []):
            continue
        target = canonical_url(urljoin(url, a["href"]))
        if not target:
            continue
        label = a.get_text(" ", strip=True).casefold()
        # Yell's dedicated website link excludes directory ads, social and utility links.
        if a.get("id") == "SR_links_2_New" and label in {"website", "ვებგვერდი", "ვებ-გვერდი", "сайт"}:
            if domain(target) not in SOCIAL_HOSTS | {"yell.ge", "yellowpages.ge"}:
                websites.append({"url": target, "name": "", "discovered_from": url})
        p = urlsplit(target)
        query = parse_qs(p.query)
        if domain(target) == "yell.ge" and p.path == "/companies.php" and query.get("rub") == ["151"]:
            if not any(k in query for k in ("sort", "filter", "ubani")) and target != url:
                if label.isdigit() or any(x in label for x in ("next", "შემდეგ", "след")) or "next" in a.get("rel", []):
                    pages.append(target)
    return websites, list(dict.fromkeys(pages))


def discover(client, config, events, progress=None):
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    progress = progress if progress is not None else {}
    for directory in config.get("directories", []):
        if not directory.get("enabled", False):
            continue
        if directory.get("adapter") not in {"yell", "city24", "links", "pdf_catalogue", "dizaini", "editorial", "awards"} or not directory.get("policy_reviewed_at"):
            raise ValueError("Directory adapter / documented policy review required")
        limit = max(1, min(5, int(directory.get("max_pages", 1))))
        cursor = progress.setdefault(directory['url'], {'pending': [directory['url']], 'seen': []})
        if directory.get('revision') is not None and cursor.get('revision') != directory['revision']:
            cursor = {'pending': [directory['url']], 'seen': [], 'revision': directory['revision']}
            progress[directory['url']] = cursor
        queue, visited = cursor['pending'], set()
        seen = set(cursor['seen'])
        while queue and len(visited) < limit:
            url = queue[0]
            if url in visited:
                queue.pop(0)
                continue
            visited.add(url)
            try:
                if directory['adapter'] == 'pdf_catalogue':
                    from .pdf_catalogue import read_catalogue
                    final, html = client.fetch(url, page_limit=5, pdf=True)
                    sites = read_catalogue(html, directory, timestamp, client.check, events)
                    more = []
                else:
                    final, html = client.fetch(url, page_limit=5)
                if directory['adapter'] == 'pdf_catalogue':
                    pass
                elif directory['adapter'] == 'yell':
                    sites, more = yell_links(html, final)
                    if directory.get('collect_listing_contacts'):
                        enriched = yell_candidates(html, final, timestamp)
                        enriched_hosts = {domain(s['url']) for s in enriched if s['kind'] == 'website'}
                        sites = enriched + [s for s in sites if domain(s['url']) not in enriched_hosts]
                elif directory['adapter'] == 'city24':
                    sites, more = city24_links(html, final)
                elif directory['adapter'] in {'dizaini', 'editorial', 'awards'}:
                    from .source_pages import page_candidates
                    sites, more = page_candidates(html, final, directory, timestamp)
                else:
                    sites, more = approved_external_links(html, final, directory), []
                results.extend(sites)
                events.append({"url": final, "status": "ok", "reason": "directory", "discovered": len(sites)})
                queue.pop(0)
                seen.add(url)
                queue.extend(x for x in more if x not in seen and x not in queue)
                cursor['seen'] = sorted(seen)
            except FetchError as exc:
                events.append({"url": url, "status": exc.status, "reason": exc.reason})
                if exc.reason in {'http_404', 'http_410', 'directory_profile_layout_changed', 'directory_business_name_missing'}:
                    # One deleted/malformed listing must not permanently block the queue.
                    queue.pop(0)
                    seen.add(url)
                    cursor['seen'] = sorted(seen)
                    continue
                break
            except Exception as exc:
                from .network import Deadline
                if isinstance(exc, Deadline):
                    raise
                events.append({'url': url, 'status': 'error', 'reason': 'directory_parser_' + type(exc).__name__})
                break
        if not queue:
            cursor['pending'] = [directory['url']]
            cursor['seen'] = []
    return results


def approved_external_links(html, url, directory):
    """Explicit editable selector for a researched guide/association page."""
    soup = BeautifulSoup(html, 'html.parser')
    for meta in soup.find_all('meta', attrs={'name': 'robots'}):
        if any(x in meta.get('content', '').lower() for x in ('none', 'noindex', 'nofollow')):
            raise FetchError('blocked', 'directory_meta_robots_opt_out')
    selector = directory.get('link_selector')
    if not selector:
        raise ValueError('Link discovery requires an explicit reviewed selector')
    results = []
    for a in soup.select(selector):
        if 'nofollow' in a.get('rel', []):
            continue
        target = canonical_url(urljoin(url, a.get('href', '')))
        if target and domain(target) != domain(url) and domain(target) not in SOCIAL_HOSTS:
            label = a.get_text(' ', strip=True)
            if directory.get('label_pattern'):
                import re
                if not re.search(directory['label_pattern'], label, re.I):
                    continue
            results.append({'url': target, 'name': '', 'discovered_from': url})
    return results
