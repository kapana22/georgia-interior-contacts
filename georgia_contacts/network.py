"""Fail-closed robots handling, bounded requests, redirects, and domain pacing."""
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urljoin

import requests
from protego import Protego

from .urls import canonical_url, domain, public_url

POLICY_BLOCKED = {
    'linkedin.com': 'https://www.linkedin.com/legal/user-agreement',
    'designgeorgia.org': 'https://designgeorgia.org/policies/terms-of-service',
    'bestexperts.ge': 'https://bestexperts.ge/terms/',
    'tbilisiexperts.ge': 'https://tbilisiexperts.ge/terms/',
    'bia.ge': 'https://www.bia.ge/Home/TermsAndConditions',
    'georgiayp.com': 'https://www.georgiayp.com/terms-of-use',
    'madloba.info': 'https://www.madloba.info/polzovatelskoye-soglasheniye/',
    'servisebi.ge': 'https://servisebi.ge/rules',
}

USER_AGENT = "GeorgiaInteriorContacts/0.1 (+https://github.com/kapana22/georgia-interior-contacts; public-business-contacts)"


class FetchError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


class Deadline(Exception):
    pass


def retry_after(value, now=None):
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, date.timestamp() - (now if now is not None else time.time()))
        except (TypeError, ValueError, OverflowError):
            return 0


class Client:
    def __init__(self, deadline, delay=2, session=None, clock=time.monotonic,
                 sleep=time.sleep, validator=public_url):
        self.deadline, self.delay = deadline, max(2, delay)
        self.clock, self.sleep, self.validator = clock, sleep, validator
        self.session = session or requests.Session()
        self.session.trust_env = False  # Do not inherit proxy credentials or .netrc.
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
        self.robots, self.last, self.delays, self.counts = {}, {}, {}, {}
        self.requests = 0

    def check(self):
        if self.clock() >= self.deadline:
            raise Deadline()

    def pause(self, seconds):
        self.check()
        if self.clock() + seconds >= self.deadline:
            raise Deadline()
        if seconds > 0:
            self.sleep(seconds)

    def raw(self, url, max_bytes=2_000_000):
        max_bytes = max(1, min(20_000_000, int(max_bytes)))
        for attempt in range(3):
            self.check()
            host = domain(url)
            if host in POLICY_BLOCKED or any(host.endswith('.' + h) for h in POLICY_BLOCKED):
                raise FetchError('blocked', 'source_policy_prohibits_collection')
            if not self.validator(url):
                raise FetchError("unreachable", "unsafe_or_unresolved_url")
            host = domain(url)
            self.pause(max(0, self.last.get(host, -1e9) + self.delays.get(host, self.delay) - self.clock()))
            self.last[host] = self.clock()
            self.requests += 1
            try:
                self.session.cookies.clear()
                with self.session.get(url, timeout=(5, 15), allow_redirects=False, stream=True) as r:
                    code, headers = r.status_code, r.headers.copy()
                    body = bytearray()
                    for chunk in r.iter_content(32768):
                        self.check()
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError("unreachable", "response_too_large")
                    payload = bytes(body)
            except requests.RequestException:
                if attempt < 2:
                    self.pause(2 ** (attempt + 1))
                    continue
                raise FetchError("unreachable", "network_error") from None
            if code in {429, 500, 502, 503, 504}:
                wait = max(2 ** (attempt + 1), retry_after(headers.get("Retry-After")))
                if attempt == 2 or wait > 60:
                    raise FetchError("blocked" if code == 429 else "unreachable",
                                     "rate_limited" if code == 429 else f"http_{code}")
                self.pause(wait)
                continue
            return code, headers, payload
        raise AssertionError("unreachable")

    def rules(self, url):
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:
            target = origin + "/robots.txt"
            try:
                for _ in range(4):
                    code, headers, body = self.raw(target)
                    if code in {301, 302, 303, 307, 308}:
                        new = canonical_url(urljoin(target, headers.get("Location", "")))
                        if not new or domain(new) != domain(url):
                            raise FetchError("blocked", "robots_cross_domain_redirect")
                        target = new
                        continue
                    if code in {404, 410}:
                        rules = Protego.parse("")
                    elif code == 200 and b"<html" not in body.lower() and b"<!doctype html" not in body.lower():
                        rules = Protego.parse(body.decode("utf-8-sig", errors="replace"))
                    else:
                        raise FetchError("blocked", f"robots_unavailable_{code}")
                    self.robots[origin] = rules
                    delay = rules.crawl_delay(USER_AGENT) or 0
                    rate = rules.request_rate(USER_AGENT)
                    if rate:
                        delay = max(delay, rate.seconds / rate.requests)
                    self.delays[domain(url)] = max(self.delay, delay)
                    break
                else:
                    raise FetchError("blocked", "robots_redirect_limit")
            except FetchError as exc:
                # Cache failure too: do not hammer an unavailable robots.txt.
                self.robots[origin] = exc
        rules = self.robots[origin]
        if isinstance(rules, FetchError):
            raise rules
        return rules

    def fetch(self, url, page_limit=5, *, pdf=False):
        original = domain(url)
        for _ in range(5):
            url = canonical_url(url)
            if not url:
                raise FetchError("blocked", "invalid_url")
            if domain(url) != original:
                raise FetchError("blocked", "cross_domain_redirect_requires_review")
            rules = self.rules(url)
            if not rules.can_fetch(url, USER_AGENT):
                raise FetchError("blocked", "robots_disallowed")
            if self.counts.get(original, 0) >= page_limit:
                raise FetchError("skipped", "page_limit")
            self.counts[original] = self.counts.get(original, 0) + 1
            code, headers, body = self.raw(url, max_bytes=20_000_000 if pdf else 2_000_000)
            if code in {301, 302, 303, 307, 308}:
                target = headers.get("Location")
                if not target:
                    raise FetchError("unreachable", "redirect_without_location")
                url = urljoin(url, target)
                continue
            if code in {401, 403, 451}:
                raise FetchError("blocked", f"http_{code}")
            if code != 200:
                raise FetchError("unreachable", f"http_{code}")
            if not body.strip():
                raise FetchError("unreachable", "empty_response")
            if ("application/pdf" if pdf else "html") not in headers.get("Content-Type", "").lower():
                raise FetchError("unreachable", "not_pdf" if pdf else "not_html")
            low = body.lower()
            if any(marker in low for marker in (b"cf-chl-", b"verify you are human", b"attention required! | cloudflare")):
                raise FetchError("blocked", "challenge")
            if "noindex" in headers.get("X-Robots-Tag", "").lower() or "nofollow" in headers.get("X-Robots-Tag", "").lower():
                raise FetchError("blocked", "x_robots_opt_out")
            return url, body
        raise FetchError("blocked", "redirect_limit")
