"""SSRF guard for any URL derived from user input.

`POST /ingest {mode:url}` and live-retrieval fetch attacker-influenced URLs.
Without a guard they can fetch cloud metadata endpoints (169.254.169.254),
localhost services, or LAN hosts. assert_safe_url resolves the hostname and
rejects private/loopback/link-local ranges; guarded_get fetches with manual
redirect handling so every hop is re-validated."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from .http_client import get_client

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal", "metadata.internal"}
_MAX_REDIRECTS = 5
_RESOLVE_TIMEOUT = 3.0


class SSRFBlocked(Exception):
    """Raised when a URL points at an internal/private/unsupported target."""


def _assert_safe_ip(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError) as e:
        raise SSRFBlocked(f"cannot resolve host {host!r}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SSRFBlocked(f"host {host!r} resolves to a private address ({ip})")


def assert_safe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlocked(f"scheme {parsed.scheme!r} is not allowed (http/https only)")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFBlocked("URL has no hostname")
    if host in _BLOCKED_HOSTS or host.endswith((".internal", ".local", ".localhost")):
        raise SSRFBlocked(f"host {host!r} is not allowed")
    # Private literal IPs are caught by the resolver check below; userinfo tricks
    # like https://user@internal/ carry the real host in parsed.hostname anyway.
    _assert_safe_ip(host)


def guarded_get(url: str, *, timeout: float = 30.0, extra_headers: dict | None = None) -> httpx.Response:
    """GET with SSRF validation on the first URL and every redirect hop.

    Redirects are handled one hop at a time (follow_redirects=False) so a
    public-looking URL that redirects to an internal address is still caught."""
    current = url
    response: httpx.Response | None = None
    client = get_client()
    for _hop in range(_MAX_REDIRECTS + 1):
        assert_safe_url(current)
        response = client.get(current, timeout=timeout, headers=extra_headers or {})
        if response.is_redirect or (300 <= response.status_code < 400 and response.headers.get("location")):
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
            continue
        return response
    return response  # too many redirects: return last response, caller decides
