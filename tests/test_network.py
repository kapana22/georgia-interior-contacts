import requests
import pytest
from requests.structures import CaseInsensitiveDict
from georgia_contacts.network import Client, FetchError, Deadline, retry_after


class Response:
    def __init__(self, code=200, body=b'hello', headers=None):
        self.status_code, self.body = code, body
        self.headers = CaseInsensitiveDict(headers or {'Content-Type':'text/html'})
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def iter_content(self, size):
        yield self.body


class Session:
    def __init__(self, replies):
        self.replies, self.calls, self.headers = list(replies), [], {}
        self.cookies = requests.cookies.RequestsCookieJar()
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def client(replies):
    ticks = [0.0]
    sleeps = []
    def sleep(n):
        sleeps.append(n)
        ticks[0] += n
    session = Session(replies)
    c = Client(1000, session=session, clock=lambda:ticks[0], sleep=sleep, validator=lambda u:True)
    return c, session, sleeps


def robots(body=b'User-agent: *\nDisallow: /private\n'):
    return Response(body=body, headers={'content-type':'text/plain'})


def test_robots_wildcards_disallow_no_request():
    c, s, _ = client([robots(b'User-agent: *\nDisallow: /*?*sort=\n')])
    with pytest.raises(FetchError, match='robots_disallowed'):
        c.fetch('https://studio.test/?sort=name')
    assert len(s.calls) == 1


def test_robot_failure_closed_cached():
    c, s, _ = client([Response(403)])
    for _ in range(2):
        with pytest.raises(FetchError, match='robots_unavailable'):
            c.fetch('https://studio.test/')
    assert len(s.calls) == 1


def test_429_wait_and_domain_pacing():
    c, s, sleeps = client([robots(), Response(429, headers={'Retry-After':'7'}), Response()])
    c.fetch('https://studio.test/')
    assert 7 in sleeps and 2 in sleeps
    assert all(not kw['allow_redirects'] for _, kw in s.calls)


def test_long_retry_after_stops_not_shortens():
    c, s, sleeps = client([robots(), Response(429, headers={'Retry-After':'120'})])
    with pytest.raises(FetchError, match='rate_limited'):
        c.fetch('https://studio.test/')
    assert len(s.calls) == 2
    assert 120 not in sleeps


def test_retry_date_and_deadline():
    assert retry_after('Thu, 01 Jan 1970 00:02:00 GMT', now=60) == 60
    c, _, _ = client([])
    with pytest.raises(Deadline):
        c.pause(2000)


def test_redirect_cannot_bypass_robots_or_change_domain():
    c, s, _ = client([robots(), Response(302, headers={'Location':'/private'})])
    with pytest.raises(FetchError, match='robots_disallowed'):
        c.fetch('https://studio.test/')
    assert len(s.calls) == 2
    c, s, _ = client([robots(), Response(302, headers={'Location':'https://elsewhere.test/'})])
    with pytest.raises(FetchError, match='cross_domain_redirect'):
        c.fetch('https://studio.test/')
    assert len(s.calls) == 2


def test_limit_and_empty_response():
    c, s, _ = client([robots(), Response()])
    c.fetch('https://studio.test/', page_limit=1)
    with pytest.raises(FetchError, match='page_limit'):
        c.fetch('https://studio.test/contact', page_limit=1)
    assert len(s.calls) == 2
    c, _, _ = client([robots(), Response(body=b'')])
    with pytest.raises(FetchError, match='empty_response'):
        c.fetch('https://studio.test/')


def test_timeout_retries_are_bounded():
    c, s, _ = client([requests.Timeout(),requests.Timeout(),requests.Timeout()])
    with pytest.raises(FetchError, match='network_error'):
        c.fetch('https://studio.test/')
    assert len(s.calls) == 3


def test_block_no_retry():
    c, s, _ = client([robots(),Response(403)])
    with pytest.raises(FetchError, match='http_403'):
        c.fetch('https://studio.test/')
    assert len(s.calls) == 2


def test_crawl_delay_and_case_insensitive_headers():
    c, _, sleeps = client([robots(b'User-agent: *\nCrawl-delay: 6\n'),Response(headers={'content-type':'text/html'})])
    c.fetch('https://studio.test/')
    assert 6 in sleeps


def test_known_policy_prohibition_prevents_all_requests():
    c, s, _ = client([])
    with pytest.raises(FetchError, match='source_policy_prohibits_collection'):
        c.fetch('https://www.georgiayp.com/category/Interior_design')
    assert s.calls == []


def test_pdf_fetch_uses_same_robots_and_explicit_larger_bounded_response():
    c, s, _ = client([robots(), Response(body=b'%PDF-' + b'a' * 2_000_001,
                                       headers={'Content-Type': 'application/pdf'})])
    final, body = c.fetch('https://studio.test/catalogue.pdf', pdf=True)
    assert final.endswith('.pdf') and body.startswith(b'%PDF-') and len(s.calls) == 2
    c, s, _ = client([robots(b'User-agent: *\nDisallow: /catalogue.pdf'), Response()])
    with pytest.raises(FetchError, match='robots_disallowed'):
        c.fetch('https://studio.test/catalogue.pdf', pdf=True)
    assert len(s.calls) == 1
    c, _, _ = client([Response(body=b'12345')])
    with pytest.raises(FetchError, match='response_too_large'):
        c.raw('https://studio.test/', max_bytes=4)


def test_retry_after_survives_a_new_client_and_does_not_hit_network_early():
    cooldowns={}
    first=Client(1000,session=Session([Response(429,headers={'Retry-After':'120'})]),clock=lambda:0,wall_clock=lambda:1000,validator=lambda u:True,cooldowns=cooldowns)
    with pytest.raises(FetchError,match='rate_limited'):first.raw('https://studio.test/')
    assert cooldowns['studio.test']==1120
    session=Session([Response()])
    second=Client(1000,session=session,clock=lambda:0,wall_clock=lambda:1050,validator=lambda u:True,cooldowns=cooldowns)
    with pytest.raises(FetchError,match='retry_after_cooldown'):second.fetch('https://studio.test/')
    assert not session.calls
    third=Client(1000,session=session,clock=lambda:0,wall_clock=lambda:1121,validator=lambda u:True,cooldowns=cooldowns)
    assert third.raw('https://studio.test/')[0]==200


def test_public_restaurant_post_keeps_robots_and_page_budget():
    url='https://www.yell.ge/SR_.php'
    c,s,_=client([robots(),Response(body=b'<table>business</table>')])
    s.post=s.get
    assert c.fetch(url,page_limit=1,form={'n':'fad','i':'1','x':'1'})[1]==b'<table>business</table>'
    assert s.calls[-1][1]['data']['x']=='1'
    with pytest.raises(FetchError,match='page_limit'):c.fetch(url,page_limit=1,form={'x':'2'})
    c,s,_=client([robots(b'User-agent: *\nDisallow: /SR_.php\n')]);s.post=s.get
    with pytest.raises(FetchError,match='robots_disallowed'):c.fetch(url,form={'x':'1'})
    assert len(s.calls)==1


def test_restaurant_post_never_follows_redirect():
    c,s,_=client([robots(),Response(302,headers={'Location':'/other.php'})]);s.post=s.get
    with pytest.raises(FetchError,match='directory_post_redirect'):
        c.fetch('https://www.yell.ge/SR_.php',form={'x':'1'})
    assert len(s.calls)==2
