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
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_MAX_CHARS = 6000
_BAD_HOSTS = ("duckduckgo.com", "google.", "bing.")


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


def wikipedia_fallback(query: str) -> dict | None:
    """Reliable co-provider: the best-matching Wikipedia article as clean plaintext.
    Returns {url, title, text} or None."""
    try:
        r = httpx.get(
            _WIKI_API,
            params={
                "action": "query", "format": "json", "list": "search",
                "srsearch": query, "srlimit": 1,
            },
            headers={"User-Agent": _UA},
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0]["title"]
        r2 = httpx.get(
            _WIKI_API,
            params={
                "action": "query", "format": "json", "prop": "extracts",
                "explaintext": 1, "redirects": 1, "titles": title,
            },
            headers={"User-Agent": _UA},
            timeout=10,
        )
        r2.raise_for_status()
        pages = r2.json().get("query", {}).get("pages", {})
        text = next(iter(pages.values()), {}).get("extract", "")
        if not text or len(text) < 300:
            return None
        slug = quote(title.replace(" ", "_"))
        return {
            "url": f"https://en.wikipedia.org/wiki/{slug}",
            "title": title,
            "text": text[:_MAX_CHARS],
        }
    except Exception as e:
        log.info("wikipedia fallback failed: %s", e)
        return None


_TLD_LABELS = ("www", "com", "org", "net", "in", "co", "ai", "io", "us", "uk", "news")


def _source_name(url: str) -> str:
    """Human source name from a URL: 'en.wikipedia.org' -> 'Wikipedia',
    'www.nxcar.in' -> 'Nxcar', 'ev.datalab.in' -> 'Ev Datalab'."""
    host = urlparse(url).netloc.lower()
    if not host:
        return "Web"
    if "wikipedia.org" in host:
        return "Wikipedia"
    labels = [l for l in host.split(".") if l and l not in _TLD_LABELS]
    return (" ".join(labels) or host).title()[:40]


def live_retrieval(question: str, max_docs: int = 3) -> dict:
    """Controlled memory-first fallback: search the web, ingest what we find,
    return an IngestionSummary-shaped dict so the caller can show what changed."""
    results = search_web(question, max_results=max_docs + 1)

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
        docs.append(
            {
                "document_id": f"doc_live_{_stable_hash(r['url']) % 10**10:010d}",
                "title": title or r["title"],
                "url": r["url"],
                "source": _source_name(r["url"]),
                "published_at": now.isoformat(),
                "text": text[:_MAX_CHARS],
            }
        )

    # Wikipedia co-provider: guarantees at least one content-rich, reliable source
    # when search results turn out to be JS shells or thin pages.
    if len(docs) < max_docs:
        wiki = wikipedia_fallback(question)
        if wiki and not any(d["url"] == wiki["url"] for d in docs):
            docs.append(
                {
                    "document_id": f"doc_live_{_stable_hash(wiki['url']) % 10**10:010d}",
                    "title": wiki["title"],
                    "url": wiki["url"],
                    "source": "Wikipedia",
                    "published_at": now.isoformat(),
                    "text": wiki["text"],
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
    out["fetched_sources"] = [
        {"url": d["url"], "title": d["title"], "source": d["source"]} for d in docs
    ]
    if not out["ok"]:
        out["error"] = "ingested content added nothing new to memory"
        out["ingested_but_empty"] = summary.documents_skipped_duplicate > 0
    return out
