from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from ..config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)
from .models import DDL


def connect():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


class Postgres:
    def __init__(self):
        self.conn = connect()
        self.init_schema()

    def init_schema(self):
        with self.conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
        self.conn.commit()

    def health(self) -> bool:
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            return False

    def upsert_source(self, source_id: str, name: str, domain: str = "", reliability_weight: float = 0.6):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sources(source_id, name, domain, reliability_weight)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (source_id) DO UPDATE SET name=EXCLUDED.name, reliability_weight=EXCLUDED.reliability_weight""",
                (source_id, name, domain, reliability_weight),
            )
        self.conn.commit()

    def get_source(self, source_id: str):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM sources WHERE source_id=%s", (source_id,))
            return cur.fetchone()

    def document_exists_by_hash(self, content_hash: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM documents WHERE content_hash=%s LIMIT 1", (content_hash,))
            return cur.fetchone() is not None

    def document_exists(self, document_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM documents WHERE document_id=%s LIMIT 1", (document_id,))
            return cur.fetchone() is not None

    def insert_document(self, document_id, source_id, url, title, published_at, retrieved_at, content_hash):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents(document_id, source_id, url, title, published_at, retrieved_at, content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (document_id) DO NOTHING""",
                (document_id, source_id, url, title, published_at, retrieved_at, content_hash),
            )
        self.conn.commit()

    def get_document(self, document_id: str):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM documents WHERE document_id=%s", (document_id,))
            return cur.fetchone()

    def log_ingestion(self, run_id, started_at, completed_at, documents_seen, documents_added, errors):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ingestion_logs(run_id, started_at, completed_at, documents_seen, documents_added, errors)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING""",
                (run_id, started_at, completed_at, documents_seen, documents_added, errors),
            )
        self.conn.commit()

    def fact_audit(self, fact_id, action, old_value="", new_value=""):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fact_audit(fact_id, action, old_value, new_value, timestamp) VALUES (%s,%s,%s,%s,%s)",
                (fact_id, action, old_value, new_value, datetime.now(timezone.utc)),
            )
        self.conn.commit()

    def get_fact_audit(self, fact_id: str):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fact_audit WHERE fact_id=%s ORDER BY timestamp ASC", (fact_id,))
            return cur.fetchall()

    def log_query(self, query_id, question, retrieval_mode, latency_ms):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO queries(query_id, question, retrieval_mode, latency_ms, created_at) VALUES (%s,%s,%s,%s,%s)",
                (query_id, question, retrieval_mode, latency_ms, datetime.now(timezone.utc)),
            )
        self.conn.commit()


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:10]}"


_db: Postgres | None = None


def get_db() -> Postgres:
    global _db
    if _db is None:
        _db = Postgres()
    return _db
