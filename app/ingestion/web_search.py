from __future__ import annotations

def _stable_hash(s: str) -> int:
    """Deterministic hash (Python's builtin hash() is salted per process)."""
    import hashlib
    return int(hashlib.sha256(s.encode()).hexdigest()[:16], 16)


import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlparse

import httpx

from ..ingestion.pipeline import ingest_documents
from ..ingestion.rss_ingester import fetch_article

log = logging.getLogger("live.retrieval")

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_MAX_CHARS = 6000
_BAD_HOSTS = ("duckduckgo.com", "google.", "bing.", "wikipedia.org")


def search_web(query: str, max_results: int = 3) -> list[dict]:
    """DuckDuckGo HTML search. Returns [{url, title}] — no API key required.
    Falls back to an empty list on any failure (caller degrades gracefully)."""
    try:
        resp = httpx.post(
            _DDG_ENDPOINT,
            data={"q": query, "kl": "wt-wt"},
            headers={"User-Agent": _UA},
            timeout=12,
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        log.warning("web search failed: %s", e)
        return []

    results: list[dict] = []
    seen: set[str] = set()
    # result links look like: <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=<encoded>&amp;rut=...">
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if "uddg=" in href:
            try:
                href = unquote(href.split("uddg=")[1].split("&")[0])
            except Exception:
                continue
        if not href.startswith("http"):
            href = "https:" + href if href.startswith("//") else ""
        if not href:
            continue
        host = urlparse(href).netloc.lower()
        if any(b in host for b in _BAD_HOSTS):
            continue
        key = href.split("#")[0]
        if key in seen or not title:
            continue
        seen.add(key)
        results.append({"url": href, "title": title})
        if len(results) >= max_results:
            break
    return results


def live_retrieval(question: str, max_docs: int = 2) -> dict:
    """Controlled memory-first fallback: search the web, ingest what we find,
    return an IngestionSummary-shaped dict so the caller can show what changed."""
    results = search_web(question, max_results=max_docs + 1)
    if not results:
        return {"ok": False, "error": "no web results found", "results": []}

    docs = []
    now = datetime.now(timezone.utc)
    for r in results[:max_docs]:
        try:
            title, text = fetch_article(r["url"])
        except Exception as e:
            log.info("skipping unfetchable %s: %s", r["url"], e)
            continue
        if len(text) < 200:
            continue
        host = urlparse(r["url"]).netloc.split(".")[0].title()
        docs.append(
            {
                "document_id": f"doc_live_{_stable_hash(r['url']) % 10**10:010d}",
                "title": title or r["title"],
                "url": r["url"],
                "source": host or "Web",
                "published_at": now.isoformat(),
                "text": text[:_MAX_CHARS],
            }
        )
    if not docs:
        return {"ok": False, "error": "retrieved pages had no usable text", "results": results}

    from ..extraction.schemas import Document
    from ..trust.source_weights import reliability

    documents = [
        Document(
            document_id=d["document_id"],
            title=d["title"],
            url=d["url"],
            source=d["source"],
            published_at=datetime.fromisoformat(d["published_at"]),
            retrieved_at=now,
            text=d["text"],
            source_reliability=reliability(d["source"]),
        )
        for d in docs
    ]
    summary = ingest_documents(documents)
    out = summary.model_dump()
    out["ok"] = summary.documents_added > 0
    out["searched_urls"] = [r["url"] for r in results[:3]]
    if not out["ok"]:
        out["error"] = "ingested content added nothing new to memory"
    return out
