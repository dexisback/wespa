"""One shared, pooled httpx.Client for every outbound HTTP call.

A single client re-uses TCP+TLS connections (lower latency), centralizes
timeout/User-Agent defaults, and is thread-safe for concurrent requests.
Redirects default to OFF: web fetching goes through app.fetch_guard.guarded_get
so every redirect hop is SSRF-checked."""
from __future__ import annotations

import httpx

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )
    return _client


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
