from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from ..config import ALLOWED_RELATIONS, MAX_DOC_CHARS
from ..db.postgres import Postgres, new_run_id
from ..extraction.entity_extractor import extract_entities
from ..extraction.relation_extractor import extract_relations
from ..extraction.schemas import Chunk, Document, IngestionSummary
from ..graph.graph_writer import GraphWriter
from ..ingestion.chunker import chunk_id_for, chunk_text
from ..ingestion.cleaner import clean_document_text
from ..trust.source_weights import entity_id_for, reliability, source_id_for
from ..vector.vector_retriever import add_chunks

log = logging.getLogger("ingest.pipeline")


def content_hash(text: str, url: str) -> str:
    return hashlib.sha256(f"{url}|{text}".encode()).hexdigest()


def _chunk_models(doc: Document, cleaned: str) -> list[Chunk]:
    pieces = chunk_text(cleaned)
    out = []
    for i, piece in enumerate(pieces):
        out.append(
            Chunk(
                chunk_id=chunk_id_for(doc.document_id, i),
                document_id=doc.document_id,
                text=piece,
                embedding_id=chunk_id_for(doc.document_id, i),
                source=doc.source,
                title=doc.title,
                url=doc.url,
                published_at=doc.published_at.isoformat(),
            )
        )
    return out


def ingest_documents(
    documents: list[Document],
    *,
    graph: GraphWriter | None = None,
    vectors=None,
    db: Postgres | None = None,
    run_id: str | None = None,
) -> IngestionSummary:
    """Straight-line ingestion pipeline: clean -> chunk -> extract -> normalize/dedup
    -> temporal graph write -> vector write -> provenance. A single failed document
    never kills the batch."""
    graph = graph or GraphWriter()
    db = db or _default_db()
    run_id = run_id or new_run_id()
    started = datetime.now(timezone.utc)
    summary = IngestionSummary(run_id=run_id, documents_seen=len(documents))
    seen_hashes: set[str] = set()

    for doc in documents:
        try:
            cleaned = clean_document_text(doc.text)
            if len(cleaned) < 80:
                raise ValueError("document too short after cleaning")
            chash = content_hash(cleaned, doc.url)
            if chash in seen_hashes or db.document_exists_by_hash(chash):
                summary.documents_skipped_duplicate += 1
                log.info("skipping duplicate document %s", doc.document_id)
                continue
            seen_hashes.add(chash)

            chunks = _chunk_models(doc, cleaned)
            summary.chunks_embedded += vectors.add_chunks(chunks) if vectors else add_chunks(chunks)

            text_for_extraction = cleaned[:MAX_DOC_CHARS]
            entities = extract_entities(text_for_extraction)
            relations = extract_relations(
                text_for_extraction, [e.name for e in entities], ALLOWED_RELATIONS
            )

            src_id = source_id_for(doc.source)
            rel_weight = reliability(doc.source)
            graph.upsert_source(src_id, doc.source, rel_weight)
            graph.upsert_document(
                doc.document_id, doc.title, doc.url, doc.published_at.isoformat(), src_id
            )
            db.upsert_source(src_id, doc.source, "", rel_weight)

            entity_types = {e.name.lower(): e.type for e in entities}
            
            # Add all extracted entities to the graph, not just those in relations
            new_entities = 0
            for e in entities:
                eid = entity_id_for(e.name)
                if not graph.entity_exists(eid):
                    new_entities += 1
                graph.upsert_entity(eid, e.name, e.type)
            summary.entities_added += new_entities

            for r in relations:
                subject_id, object_id = entity_id_for(r["subject"]), entity_id_for(r["object"])
                stype = entity_types.get(r["subject"].lower(), r.get("subject_type", "Organization"))
                otype = entity_types.get(r["object"].lower(), r.get("object_type", "Organization"))
                result = graph.ingest_fact(
                    subject_id=subject_id,
                    subject_name=r["subject"],
                    relation=r["relation"],
                    object_id=object_id,
                    object_name=r["object"],
                    subject_type=stype,
                    object_type=otype,
                    source_id=src_id,
                    source_name=doc.source,
                    document_id=doc.document_id,
                    observed_at=doc.published_at,
                    extraction_confidence=r["extraction_confidence"],
                    reliability=rel_weight,
                )
                summary.relationships_added += 1
                action = result["action"]
                if action == "SUPERSEDE":
                    summary.facts_superseded += 1
                    for old_id in result.get("closed", []):
                        old = graph.get_fact(old_id)
                        old_desc = f"{old['subject_name']} {old['relation']} {old['object_name']}" if old else old_id
                        db.fact_audit(result["fact_id"], "SUPERSEDES", old_desc, f"{r['subject']} {r['relation']} {r['object']}")
                        db.fact_audit(old_id, "SUPERSEDED", old_desc, result["fact_id"])
                elif action == "CONFLICT":
                    summary.conflicts_flagged += 1
                    db.fact_audit(
                        result["fact_id"], "CONFLICT", ", ".join(result.get("conflicts_with", [])),
                        f"{r['subject']} {r['relation']} {r['object']}",
                    )
                elif action == "CORROBORATE":
                    summary.facts_corroborated += 1
                    db.fact_audit(result["fact_id"], "CORROBORATES", ", ".join(result.get("corroborated", [])), doc.source)

            db.insert_document(
                doc.document_id, src_id, doc.url, doc.title,
                doc.published_at, doc.retrieved_at, chash,
            )
            summary.documents_added += 1
        except Exception as e:
            summary.documents_failed += 1
            summary.errors.append(f"{doc.document_id}: {e}")
            log.error("document %s failed, continuing batch: %s", doc.document_id, e)
            continue

    db.log_ingestion(
        run_id, started, datetime.now(timezone.utc),
        summary.documents_seen, summary.documents_added, summary.documents_failed,
    )
    summary.message = _narrate(summary)
    return summary


def _narrate(s: IngestionSummary) -> str:
    parts = [f"{s.documents_added} new source(s) processed into memory"]
    if s.entities_added:
        parts.append(f"+{s.entities_added} entities")
    if s.relationships_added:
        parts.append(f"+{s.relationships_added} relationships")
    if s.facts_superseded:
        parts.append(f"{s.facts_superseded} fact(s) superseded — previous versions preserved, not deleted")
    if s.facts_corroborated:
        parts.append(f"{s.facts_corroborated} fact(s) corroborated by a second source")
    if s.conflicts_flagged:
        parts.append(f"{s.conflicts_flagged} conflicting claim(s) kept and flagged")
    if s.documents_skipped_duplicate:
        parts.append(f"{s.documents_skipped_duplicate} duplicate(s) skipped")
    if s.documents_failed:
        parts.append(f"{s.documents_failed} failed (batch continued)")
    return "; ".join(parts)


def _default_db() -> Postgres:
    from ..db.postgres import get_db

    return get_db()


def load_documents_from_file(path) -> list[Document]:
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text())
    now = datetime.now(timezone.utc)
    docs = []
    for d in data.get("documents", []):
        pub = d.get("published_at")
        pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else now
        docs.append(
            Document(
                document_id=d["document_id"],
                title=d.get("title", ""),
                url=d.get("url", ""),
                source=d.get("source", "Unknown"),
                published_at=pub_dt,
                retrieved_at=now,
                text=d.get("text", ""),
                source_reliability=reliability(d.get("source", "")),
            )
        )
    return docs
