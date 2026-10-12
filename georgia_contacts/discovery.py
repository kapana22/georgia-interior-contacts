"""Bounded directory discovery from observed links, never invented pagination."""
from urllib.parse import parse_qs, urlsplit, urljoin
from bs4 import BeautifulSoup
from .urls import canonical_url, domain, SOCIAL_HOSTS
from .network import FetchError


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


def discover(client, config, events):
    results = []
    for directory in config.get("directories", []):
        if not directory.get("enabled", False):
            continue
        if directory.get("adapter") != "yell" or not directory.get("policy_reviewed_at"):
            raise ValueError("Directory adapter / documented policy review required")
        limit = max(1, min(10, int(directory.get("max_pages", 1))))
        queue, visited = [directory["url"]], set()
        while queue and len(visited) < limit:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                final, html = client.fetch(url, page_limit=limit)
                sites, more = yell_links(html, final)
                results.extend(sites)
                events.append({"url": final, "status": "ok", "reason": "directory", "discovered": len(sites)})
                queue.extend(x for x in more if x not in visited)
            except FetchError as exc:
                events.append({"url": url, "status": exc.status, "reason": exc.reason})
                break
    return results
