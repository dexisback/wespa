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

    def log_answer_facts(self, query_id: str, fact_ids: list[str]):
        """Record which facts an answer depended on (for impact / stale detection)."""
        if not fact_ids:
            return
        now = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            for fid in fact_ids:
                cur.execute(
                    "INSERT INTO answer_facts(query_id, fact_id, created_at) VALUES (%s,%s,%s)",
                    (query_id, fid, now),
                )
        self.conn.commit()

    def get_answers_for_fact(self, fact_id: str, limit: int = 25) -> list[dict]:
        """Previous answers that cited this fact."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT q.query_id, q.question, q.retrieval_mode, q.created_at
                   FROM answer_facts af JOIN queries q ON q.query_id = af.query_id
                   WHERE af.fact_id = %s
                   ORDER BY q.created_at DESC LIMIT %s""",
                (fact_id, limit),
            )
            return cur.fetchall()

    def get_answer_dependencies(self, query_id: str) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT fact_id FROM answer_facts WHERE query_id=%s", (query_id,))
            return [r[0] for r in cur.fetchall()]

    def last_ingestion(self):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT run_id, completed_at, documents_added FROM ingestion_logs WHERE documents_added >= 0 ORDER BY completed_at DESC NULLS LAST LIMIT 1"
            )
            return cur.fetchone()


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:10]}"


_db: Postgres | None = None


def get_db() -> Postgres:
    global _db
    if _db is None:
        _db = Postgres()
    return _db
