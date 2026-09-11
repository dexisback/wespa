#!/usr/bin/env python3
"""Deterministic memory bootstrap.

Loads the curated seed corpus + hand-verified facts directly into Neo4j, ChromaDB
and PostgreSQL without calling the LLM. Use this when Groq rate limits block the
LLM-driven build_memory.py pipeline; the application is then fully populated and
demo-ready, and live LLM extraction can be re-enabled later by switching back to
scripts/build_memory.py.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import RAW_DIR
from app.db.postgres import Postgres
from app.extraction.schemas import Chunk, Document
from app.graph.graph_writer import GraphWriter
from app.ingestion.chunker import chunk_id_for, chunk_text
from app.ingestion.cleaner import clean_document_text
from app.ingestion.pipeline import content_hash
from app.trust.source_weights import reliability, source_id_for
from app.vector.vector_retriever import add_chunks


def load_corpus(path: Path):
    data = json.loads(path.read_text())
    now = datetime.now(timezone.utc)
    docs = {}
    for d in data.get("documents", []):
        pub = d.get("published_at")
        pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else now
        docs[d["document_id"]] = Document(
            document_id=d["document_id"],
            title=d.get("title", ""),
            url=d.get("url", ""),
            source=d.get("source", "Unknown"),
            published_at=pub_dt,
            retrieved_at=now,
            text=d.get("text", ""),
            source_reliability=reliability(d.get("source", "")),
        )
    return docs


def bootstrap():
    corpus = load_corpus(RAW_DIR / "seed_corpus.json")
    facts = json.loads((Path(__file__).resolve().parent.parent / "data" / "processed" / "seed_facts.json").read_text())
    fixture = json.loads((RAW_DIR / "fixtures" / "ssi_june_update.json").read_text())
    corpus.update(load_corpus(RAW_DIR / "fixtures" / "ssi_june_update.json"))
    facts["documents"].update(facts.get("fixture", {}))

    db = Postgres()
    graph = GraphWriter()
    graph.g.ensure_constraints()

    entity_types = {e["entity_id"]: e["type"] for e in facts["entities"]}
    entity_names = {e["entity_id"]: e["name"] for e in facts["entities"]}
    for e in facts["entities"]:
        graph.upsert_entity(e["entity_id"], e["name"], e["type"])

    seen = 0
    added = 0
    entities_added = 0
    relationships_added = 0
    superseded = 0
    corroborated = 0
    conflicts = 0

    def process_doc(doc: Document, rels: list[dict]):
        nonlocal seen, added, entities_added, relationships_added, superseded, corroborated, conflicts
        seen += 1
        cleaned = clean_document_text(doc.text)
        chash = content_hash(cleaned, doc.url)
        if db.document_exists_by_hash(chash):
            return

        src_id = source_id_for(doc.source)
        rel_weight = reliability(doc.source)
        graph.upsert_source(src_id, doc.source, rel_weight)
        graph.upsert_document(doc.document_id, doc.title, doc.url, doc.published_at.isoformat(), src_id)
        db.upsert_source(src_id, doc.source, "", rel_weight)

        pieces = chunk_text(cleaned)
        chunks = []
        for i, piece in enumerate(pieces):
            chunks.append(
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
        add_chunks(chunks)

        new_ents = 0
        for r in rels:
            sid = next((k for k, v in entity_names.items() if v == r["subject"]), None)
            oid = next((k for k, v in entity_names.items() if v == r["object"]), None)
            if not sid or not oid:
                continue
            if not graph.entity_exists(sid):
                new_ents += 1
            if not graph.entity_exists(oid):
                new_ents += 1
        entities_added += new_ents

        for r in rels:
            sid = next((k for k, v in entity_names.items() if v == r["subject"]), None)
            oid = next((k for k, v in entity_names.items() if v == r["object"]), None)
            if not sid or not oid:
                continue
            res = graph.ingest_fact(
                subject_id=sid,
                subject_name=r["subject"],
                relation=r["relation"],
                object_id=oid,
                object_name=r["object"],
                subject_type=entity_types.get(sid, "Organization"),
                object_type=entity_types.get(oid, "Organization"),
                source_id=src_id,
                source_name=doc.source,
                document_id=doc.document_id,
                observed_at=doc.published_at,
                extraction_confidence=r.get("extraction_confidence", 0.9),
                reliability=rel_weight,
            )
            relationships_added += 1
            action = res["action"]
            if action == "SUPERSEDE":
                superseded += 1
                for old_id in res.get("closed", []):
                    old = graph.get_fact(old_id)
                    old_desc = f"{old['subject_name']} {old['relation']} {old['object_name']}" if old else old_id
                    db.fact_audit(res["fact_id"], "SUPERSEDES", old_desc, f"{r['subject']} {r['relation']} {r['object']}")
                    db.fact_audit(old_id, "SUPERSEDED", old_desc, res["fact_id"])
            elif action == "CONFLICT":
                conflicts += 1
                db.fact_audit(res["fact_id"], "CONFLICT", ", ".join(res.get("conflicts_with", [])), f"{r['subject']} {r['relation']} {r['object']}")
            elif action == "CORROBORATE":
                corroborated += 1
                db.fact_audit(res["fact_id"], "CORROBORATES", ", ".join(res.get("corroborated", [])), doc.source)

        db.insert_document(doc.document_id, src_id, doc.url, doc.title, doc.published_at, doc.retrieved_at, chash)
        added += 1

    for doc_id, doc in corpus.items():
        rels = facts["documents"].get(doc_id, {}).get("relations", [])
        process_doc(doc, rels)

    db.log_ingestion(
        f"bootstrap_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        datetime.now(timezone.utc), datetime.now(timezone.utc),
        seen, added, 0,
    )

    print(
        f"bootstrap complete: seen={seen} added={added} "
        f"entities=+{entities_added} rels=+{relationships_added} "
        f"superseded={superseded} corroborated={corroborated} conflicts={conflicts}"
    )


if __name__ == "__main__":
    bootstrap()
