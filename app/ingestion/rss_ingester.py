from __future__ import annotations

def _stable_hash(s: str) -> int:
    """Deterministic hash (Python's builtin hash() is salted per process)."""
    import hashlib
    return int(hashlib.sha256(s.encode()).hexdigest()[:16], 16)


import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..trust.source_weights import reliability
from ..extraction.schemas import Document

log = logging.getLogger("ingest.rss")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MemoryEngine/1.0)"}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def fetch_article(url: str) -> tuple[str, str]:
    """Returns (title, text) for a single article URL."""
    resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "h2"])]
    text = "\n\n".join(p for p in paragraphs if len(p) > 40)
    return title, text


def ingest_url(url: str, source: str | None = None, published_at: datetime | None = None) -> Document:
    title, text = fetch_article(url)
    now = datetime.now(timezone.utc)
    return Document(
        document_id=f"doc_{_stable_hash(url) % 10**10:010d}",
        title=title,
        url=url,
        source=source or _domain(url).split(".")[0].title(),
        published_at=published_at or now,
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
