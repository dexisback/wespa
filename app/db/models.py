from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SourceRow(BaseModel):
    source_id: str
    name: str
    domain: str = ""
    reliability_weight: float = 0.6


class DocumentRow(BaseModel):
    document_id: str
    source_id: str
    url: str
    title: str
    published_at: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    content_hash: str = ""


class IngestionLogRow(BaseModel):
    run_id: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    documents_seen: int = 0
    documents_added: int = 0
    errors: int = 0


class FactAuditRow(BaseModel):
    fact_id: str
    action: str
    old_value: str = ""
    new_value: str = ""
    timestamp: Optional[datetime] = None


class QueryRow(BaseModel):
    query_id: str
    question: str
    retrieval_mode: str
    latency_ms: float
    created_at: Optional[datetime] = None


DDL = [
    """
    CREATE TABLE IF NOT EXISTS sources (
        source_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        domain TEXT DEFAULT '',
        reliability_weight REAL DEFAULT 0.6
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        document_id TEXT PRIMARY KEY,
        source_id TEXT REFERENCES sources(source_id),
        url TEXT,
        title TEXT,
        published_at TIMESTAMPTZ,
        retrieved_at TIMESTAMPTZ,
        content_hash TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_logs (
        run_id TEXT PRIMARY KEY,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        documents_seen INT DEFAULT 0,
        documents_added INT DEFAULT 0,
        errors INT DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_audit (
        fact_id TEXT,
        action TEXT,
        old_value TEXT,
        new_value TEXT,
        timestamp TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queries (
        query_id TEXT PRIMARY KEY,
        question TEXT,
        retrieval_mode TEXT,
        latency_ms REAL,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS answer_facts (
        query_id TEXT,
        fact_id TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_fact_audit_fact ON fact_audit(fact_id)",
    "CREATE INDEX IF NOT EXISTS idx_answer_facts_fact ON answer_facts(fact_id)",
    "CREATE INDEX IF NOT EXISTS idx_answer_facts_query ON answer_facts(query_id)",
]
