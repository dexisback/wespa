from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
from bs4 import BeautifulSoup

from ..extraction.schemas import Document
from ..fetch_guard import guarded_get
from ..trust.source_weights import reliability
from ..util import stable_hash

log = logging.getLogger("ingest.rss")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


# Ordered most-to-least authoritative; first parseable value wins.
_META_DATE_FIELDS = [
    ("property", "article:published_time"),
    ("property", "og:article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "date"),
    ("name", "dc.date.issued"),
    ("name", "parsely-pub-date"),
]


def _parse_iso(value: str) -> datetime | None:
    v = (value or "").strip()
    if not v:
        return None
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        try:  # bare "2025-06-12" dates
            dt = datetime.fromisoformat(v.split("T")[0])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_published(soup: BeautifulSoup) -> datetime | None:
    """Article publish date from meta tags, JSON-LD, or <time datetime>."""
    for attr, value in _META_DATE_FIELDS:
        tag = soup.find("meta", {attr: value})
        if tag and tag.get("content"):
            dt = _parse_iso(tag["content"])
            if dt:
                return dt
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            for key in ("datePublished", "dateCreated", "uploadDate"):
                if node.get(key):
                    dt = _parse_iso(str(node[key]))
                    if dt:
                        return dt
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        return _parse_iso(time_tag["datetime"])
    return None


def fetch_article(url: str, timeout: float = 30) -> tuple[str, str, datetime | None]:
    """Returns (title, text, published_at) for a single article URL.

    published_at comes from the page's own metadata when available — live
    documents must not pretend they were published 'now', because that would
    skew the temporal supersede/conflict window."""
    resp = guarded_get(url, timeout=timeout, extra_headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url
    published = _extract_published(soup)
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "h2"])]
    text = "\n\n".join(p for p in paragraphs if len(p) > 40)
    return title, text, published


def ingest_url(url: str, source: str | None = None, published_at: datetime | None = None) -> Document:
    title, text, extracted = fetch_article(url)
    now = datetime.now(timezone.utc)
    # An explicit publish date (e.g. from the RSS feed) wins, then the page's
    # own metadata, then retrieval time.
    published_at = published_at or extracted or now
    return Document(
        document_id=f"doc_{stable_hash(url) % 10**10:010d}",
        title=title,
        url=url,
        source=source or _domain(url).split(".")[0].title(),
        published_at=published_at,
        retrieved_at=now,
        text=text,
        source_reliability=reliability(source or _domain(url)),
    )


def fetch_rss(rss_urls: list[str], limit_per_feed: int = 10) -> list[Document]:
    """Pull recent articles from RSS feeds. Individual failures are logged, not fatal."""
    docs: list[Document] = []
    for feed_url in rss_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:limit_per_feed]:
                link = entry.get("link")
                if not link:
                    continue
                try:
                    published = None
                    if entry.get("published_parsed"):
                        import time as _t

                        published = datetime.fromtimestamp(_t.mktime(entry.published_parsed), tz=timezone.utc)
                    docs.append(ingest_url(link, source=feed.get("feed", {}).get("title"), published_at=published))
                except Exception as e:
                    log.warning("skipping failed article %s: %s", link, e)
                    continue
        except Exception as e:
            log.warning("skipping failed feed %s: %s", feed_url, e)
            continue
    return docs
