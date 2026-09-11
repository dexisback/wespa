from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import RAW_DIR
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

            docs = [ingest_url(url, source=req.get("source"))]
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
