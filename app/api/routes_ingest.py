from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import RAW_DIR
from ..graph.neo4j_client import get_graph
from ..ingestion.pipeline import ingest_documents, load_documents_from_file
from ..extraction.schemas import IngestionSummary

router = APIRouter()

FIXTURES_DIR = RAW_DIR / "fixtures"


@router.get("/ingest/fixtures")
def list_fixtures():
    files = []
    if FIXTURES_DIR.exists():
        for p in sorted(FIXTURES_DIR.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                title = (data.get("documents") or [{}])[0].get("title", p.name)
            except Exception:
                title = p.name
            files.append({"file": p.name, "title": title})
    return {"fixtures": files}


@router.get("/documents")
def list_documents(limit: int = 100):
    """Every document/article in memory — seed corpus, fixtures, manual ingests
    and AI-learned live retrievals (marked learned=true)."""
    try:
        lim = min(max(limit, 1), 200)
        g = get_graph()
        rows = g.run(
            """MATCH (s:Source)-[:PUBLISHED]->(d:Document)
               OPTIONAL MATCH ()-[r]->() WHERE r.document_id = d.id AND r.relation IS NOT NULL
               WITH d, s, count(r) AS facts
               RETURN d.id AS document_id, d.title AS title, d.url AS url,
                      toString(d.published_at) AS published_at,
                      s.name AS source, s.reliability AS reliability, facts
               ORDER BY d.published_at DESC LIMIT $lim""",
            lim=lim,
        )
        out = []
        for r in rows:
            did = r["document_id"] or ""
            out.append(
                {
                    "document_id": did,
                    "title": r["title"] or "(untitled)",
                    "url": r["url"] or "",
                    "source": r["source"] or "Unknown",
                    "published_at": r["published_at"],
                    "reliability": r["reliability"],
                    "facts_extracted": r["facts"] or 0,
                    "learned": did.startswith("doc_live_"),
                }
            )
        return {"documents": out, "count": len(out)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"document lookup failed: {e}")


@router.post("/ingest", response_model=IngestionSummary)
def ingest(req: dict):
    mode = (req or {}).get("mode", "fixture")
    try:
        if mode == "fixture":
            fixture = req.get("fixture")
            if not fixture:
                raise HTTPException(status_code=422, detail="fixture name required")
            path = FIXTURES_DIR / fixture
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"fixture {fixture} not found")
            docs = load_documents_from_file(path)
        elif mode == "url":
            url = req.get("url")
            if not url:
                raise HTTPException(status_code=422, detail="url required")
            from ..ingestion.rss_ingester import ingest_url

            try:
                docs = [ingest_url(url, source=req.get("source"))]
            except Exception as direct_error:
                # Publishers may block automated readers with a 403/paywall.
                # Keep URL ingest useful for the demo by learning from accessible
                # corroborating articles about the same URL topic.
                from urllib.parse import unquote, urlparse

                parsed = urlparse(url)
                topic = unquote(parsed.path.rsplit("/", 1)[-1]).replace("-", " ")
                topic = " ".join(topic.split()) or parsed.netloc
                if not topic:
                    raise
                from ..ingestion.web_search import live_retrieval

                fallback = live_retrieval(topic, max_docs=3)
                if fallback.get("ok"):
                    return fallback
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"publisher blocked the article and no accessible corroborating source was found: "
                        f"{direct_error}"
                    ),
                )
        elif mode == "raw":
            if not req.get("document"):
                raise HTTPException(status_code=422, detail="document payload required")
            from datetime import datetime, timezone

            d = req["document"]
            now = datetime.now(timezone.utc)
            from ..extraction.schemas import Document

            docs = [
                Document(
                    document_id=d.get("document_id", f"doc_manual_{now.strftime('%Y%m%d%H%M%S')}"),
                    title=d.get("title", ""),
                    url=d.get("url", ""),
                    source=d.get("source", "Unknown"),
                    published_at=now,
                    retrieved_at=now,
                    text=d.get("text", ""),
                )
            ]
        else:
            raise HTTPException(status_code=422, detail=f"unknown ingest mode {mode}")
        summary = ingest_documents(docs)
        return summary
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ingestion failed: {e}")
