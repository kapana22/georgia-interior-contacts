import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

SOCIAL_HOSTS = {"instagram.com", "facebook.com", "fb.com", "fb.me"}


def canonical_url(value):
    try:
        p = urlsplit(value.strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return ""
        if p.port not in {None, 80, 443}:
            return ""
        host = p.hostname.encode("idna").decode().lower()
        return urlunsplit((p.scheme, host, p.path or "/", p.query, ""))
    except (ValueError, UnicodeError):
        return ""


def domain(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def public_url(url):
    """Reject credentials, non-HTTP schemes, local names and non-public addresses."""
    if not canonical_url(url):
        return False
    try:
        host = urlsplit(url).hostname
        if host == "localhost" or host.endswith((".local", ".internal")):
            return False
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(x[4][0]).is_global for x in addresses)
    except (OSError, ValueError):
        return False
