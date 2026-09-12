from __future__ import annotations

import logging

from ..extraction.schemas import Chunk
from .chroma_client import get_collection

log = logging.getLogger("vector.retriever")


def add_chunks(chunks: list[Chunk]) -> int:
    if not chunks:
        return 0
    col = get_collection()
    existing = set(col.get(ids=[c.chunk_id for c in chunks], include=[])["ids"])
    fresh = [c for c in chunks if c.chunk_id not in existing]
    if not fresh:
        return 0
    col.add(
        ids=[c.chunk_id for c in fresh],
        documents=[c.text for c in fresh],
        metadatas=[
            {
                "document_id": c.document_id,
                "source": c.source,
                "title": c.title,
                "url": c.url,
                "published_at": c.published_at or "",
            }
            for c in fresh
        ],
    )
    log.info("embedded %d new chunks into semantic memory", len(fresh))
    return len(fresh)


def search(query: str, k: int = 5, as_of: str | None = None) -> list[Chunk]:
    col = get_collection()
    if col.count() == 0:
        return []
    # fetch extra candidates when time-traveling, then filter by publication date
    fetch_k = min(k * 3, max(col.count(), 1)) if as_of else min(k, max(col.count(), 1))
    res = col.query(query_texts=[query], n_results=fetch_k)
    out: list[Chunk] = []
    for i, doc_id in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i]
        if as_of and (meta.get("published_at") or "") > as_of:
            continue
        dist = (res.get("distances") or [[0.0]] * 1)[0][i] if res.get("distances") else 0.0
        out.append(
            Chunk(
                chunk_id=doc_id,
                document_id=meta.get("document_id", ""),
                text=res["documents"][0][i],
                embedding_id=doc_id,
                source=meta.get("source", ""),
                title=meta.get("title", ""),
                url=meta.get("url", ""),
                published_at=meta.get("published_at") or None,
                similarity=round(max(0.0, 1.0 - float(dist)), 4),
            )
        )
        if len(out) >= k:
            break
    return out
