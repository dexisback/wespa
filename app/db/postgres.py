from __future__ import annotations

import contextlib
import threading
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import psycopg2.pool

from ..config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)
from .models import DDL

# A single shared connection cannot serve FastAPI's request threadpool safely,
# and it dies permanently if Postgres restarts. A small pool hands each request
# its own connection; the _conn() wrapper rolls back and returns it on error.
# maxconn covers request threads plus the background query-logging threads.
_POOL_MIN, _POOL_MAX = 1, 16

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX,
                    host=POSTGRES_HOST,
                    port=POSTGRES_PORT,
                    dbname=POSTGRES_DB,
                    user=POSTGRES_USER,
                    password=POSTGRES_PASSWORD,
                )
    return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


@contextlib.contextmanager
def _conn():
    """Yield a pooled connection; roll back on error so an aborted transaction
    can never poison later requests, and always return the connection."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        pool.putconn(conn)


class Postgres:
    def __init__(self):
        self.init_schema()

    @property
    def conn(self):
        """Compatibility property for legacy callers/tests expecting a direct connection."""
        if not hasattr(self, "_compat_conn") or self._compat_conn is None or getattr(self._compat_conn, "closed", True):
            self._compat_conn = get_pool().getconn()
        return self._compat_conn

    def init_schema(self):
        with _conn() as conn, conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
            conn.commit()

    def health(self) -> bool:
        try:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            return False

    def upsert_source(self, source_id: str, name: str, domain: str = "", reliability_weight: float = 0.6):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sources(source_id, name, domain, reliability_weight)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (source_id) DO UPDATE SET name=EXCLUDED.name, reliability_weight=EXCLUDED.reliability_weight""",
                (source_id, name, domain, reliability_weight),
            )
            conn.commit()

    def get_source(self, source_id: str):
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM sources WHERE source_id=%s", (source_id,))
            return cur.fetchone()

    def document_exists_by_hash(self, content_hash: str) -> bool:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM documents WHERE content_hash=%s LIMIT 1", (content_hash,))
            return cur.fetchone() is not None

    def document_exists(self, document_id: str) -> bool:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM documents WHERE document_id=%s LIMIT 1", (document_id,))
            return cur.fetchone() is not None

    def insert_document(self, document_id, source_id, url, title, published_at, retrieved_at, content_hash):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents(document_id, source_id, url, title, published_at, retrieved_at, content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (document_id) DO NOTHING""",
                (document_id, source_id, url, title, published_at, retrieved_at, content_hash),
            )
            conn.commit()

    def get_document(self, document_id: str):
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM documents WHERE document_id=%s", (document_id,))
            return cur.fetchone()

    def log_ingestion(self, run_id, started_at, completed_at, documents_seen, documents_added, errors):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ingestion_logs(run_id, started_at, completed_at, documents_seen, documents_added, errors)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING""",
                (run_id, started_at, completed_at, documents_seen, documents_added, errors),
            )
            conn.commit()

    def fact_audit(self, fact_id, action, old_value="", new_value=""):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fact_audit(fact_id, action, old_value, new_value, timestamp) VALUES (%s,%s,%s,%s,%s)",
                (fact_id, action, old_value, new_value, datetime.now(timezone.utc)),
            )
            conn.commit()

    def get_fact_audit(self, fact_id: str):
        rows = self.get_fact_audit_many([fact_id])
        return rows.get(fact_id, [])

    def get_fact_audit_many(self, fact_ids: list[str]) -> dict[str, list[dict]]:
        """Audit rows for many facts in one query, grouped by fact_id."""
        fact_ids = [f for f in fact_ids if f]
        if not fact_ids:
            return {}
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM fact_audit WHERE fact_id = ANY(%s) ORDER BY timestamp ASC",
                (fact_ids,),
            )
            rows = cur.fetchall()
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["fact_id"], []).append(row)
        return grouped

    def recent_fact_audits(self, actions: list[str], limit: int = 10) -> list[dict]:
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT fact_id, action, old_value, new_value, timestamp
                   FROM fact_audit WHERE action = ANY(%s)
                   ORDER BY timestamp DESC LIMIT %s""",
                (actions, limit),
            )
            return cur.fetchall()

    def log_query(self, query_id, question, retrieval_mode, latency_ms):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO queries(query_id, question, retrieval_mode, latency_ms, created_at) VALUES (%s,%s,%s,%s,%s)",
                (query_id, question, retrieval_mode, latency_ms, datetime.now(timezone.utc)),
            )
            conn.commit()

    def log_answer_facts(self, query_id: str, fact_ids: list[str]):
        """Record which facts an answer depended on (for impact / stale detection).
        Single execute_values round trip instead of one INSERT per fact."""
        if not fact_ids:
            return
        now = datetime.now(timezone.utc)
        with _conn() as conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO answer_facts(query_id, fact_id, created_at) VALUES %s",
                [(query_id, fid, now) for fid in fact_ids],
            )
            conn.commit()

    def get_answers_for_fact(self, fact_id: str, limit: int = 25) -> list[dict]:
        """Previous answers that cited this fact."""
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT q.query_id, q.question, q.retrieval_mode, q.created_at
                   FROM answer_facts af JOIN queries q ON q.query_id = af.query_id
                   WHERE af.fact_id = %s
                   ORDER BY q.created_at DESC LIMIT %s""",
                (fact_id, limit),
            )
            return cur.fetchall()

    def get_answer_dependencies(self, query_id: str) -> list[str]:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT fact_id FROM answer_facts WHERE query_id=%s", (query_id,))
            return [r[0] for r in cur.fetchall()]

    def get_question(self, query_id: str) -> str:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT question FROM queries WHERE query_id=%s", (query_id,))
            row = cur.fetchone()
            return row[0] if row else ""

    def last_ingestion(self):
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
