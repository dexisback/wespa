from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

RetrievalMode = Literal["vector", "graph", "hybrid"]


class Source(BaseModel):
    source_id: str
    name: str
    domain: str = ""
    reliability_weight: float = 0.6


class Document(BaseModel):
    document_id: str
    title: str
    url: str
    source: str
    published_at: datetime
    retrieved_at: datetime
    text: str
    source_reliability: float = 0.6


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    embedding_id: str
    source: str = ""
    title: str = ""
    url: str = ""
    published_at: Optional[str] = None
    similarity: Optional[float] = None


class Entity(BaseModel):
    entity_id: str
    name: str
    type: str = "Organization"


class Fact(BaseModel):
    fact_id: str
    subject_id: str
    subject_name: str = ""
    relation: str
    object_id: str
    object_name: str = ""
    confidence: float = 0.0
    source_id: str = ""
    source_name: str = ""
    document_id: str = ""
    observed_at: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    extraction_confidence: float = 0.8
    active: bool = True
    conflict: bool = False
    corroborations: int = 1
    supersedes: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    type: str = "Organization"


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    confidence: float = 0.0
    source_id: str = ""
    active: bool = True


class GraphPath(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    paths: list[list[str]] = Field(default_factory=list)


class SourceCard(BaseModel):
    source_name: str
    title: str = ""
    url: str = ""
    published_at: Optional[str] = None
    retrieved_at: Optional[str] = None
    confidence: Optional[float] = None


class QueryResult(BaseModel):
    query_id: str = ""
    answer: str
    facts: list[Fact] = Field(default_factory=list)
    passages: list[Chunk] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    source_cards: list[SourceCard] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_label: str = ""
    conflicts: list[str] = Field(default_factory=list)
    entities_matched: list[str] = Field(default_factory=list)
    retrieval_mode: RetrievalMode = "hybrid"
    latency_ms: float = 0.0
    graph_path: Optional[GraphPath] = None


class QueryRequest(BaseModel):
    question: str
    retrieval_mode: RetrievalMode = "hybrid"


class FactAction(BaseModel):
    action: str
    timestamp: str
    detail: str = ""


class FactHistory(BaseModel):
    fact: Fact
    versions: list[Fact] = Field(default_factory=list)
    audit: list[FactAction] = Field(default_factory=list)


class IngestionSummary(BaseModel):
    run_id: str
    documents_seen: int = 0
    documents_added: int = 0
    documents_skipped_duplicate: int = 0
    documents_failed: int = 0
    entities_added: int = 0
    relationships_added: int = 0
    facts_corroborated: int = 0
    facts_superseded: int = 0
    facts_deleted: int = 0
    conflicts_flagged: int = 0
    chunks_embedded: int = 0
    errors: list[str] = Field(default_factory=list)
    message: str = ""
