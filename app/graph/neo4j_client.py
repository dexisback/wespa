from __future__ import annotations

import threading

from neo4j import GraphDatabase
from neo4j.graph import Node, Relationship

from ..config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME
from ..util import cypher_name_key


def _convert(value):
    if isinstance(value, Node):
        return dict(value)
    if isinstance(value, Relationship):
        d = dict(value)
        d["_type"] = value.type
        d["_start"] = value.start_node.get("id")
        d["_end"] = value.end_node.get("id")
        return d
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_convert(v) for v in value]
    return value


class Neo4jClient:
    def __init__(self):
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

    def verify(self):
        self.driver.verify_connectivity()

    def health(self) -> bool:
        try:
            self.verify()
            return True
        except Exception:
            return False

    def run(self, cypher: str, **params) -> list[dict]:
        with self.driver.session() as session:
            return [
                {k: _convert(v) for k, v in record.items()}
                for record in session.run(cypher, **params)
            ]

    def ensure_constraints(self):
        stmts = [
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (s:Source) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            # name_key backs exact seed matching in match_entities (see graph_retriever)
            "CREATE INDEX entity_name_key IF NOT EXISTS FOR (e:Entity) ON (e.name_key)",
        ]
        for s in stmts:
            self.run(s)
        # One-time migrations, guarded by a marker node so startups stay cheap.
        if not self.run("MATCH (m:Meta {key: 'mentions_backfill'}) RETURN 1 AS x LIMIT 1"):
            # Backfill provenance for documents ingested before Document->Entity
            # mention edges became part of the graph contract.
            self.run(
                """MATCH (d:Document), (s:Entity)-[r]->(o:Entity)
                   WHERE r.document_id = d.id
                   MERGE (d)-[:MENTIONS]->(s)
                   MERGE (d)-[:MENTIONS]->(o)"""
            )
            self.run("MERGE (m:Meta {key: 'mentions_backfill'})")
        # Fill name_key for entities written before the property existed (idempotent:
        # after the first run, the WHERE clause matches nothing).
        self.run(
            f"""MATCH (e:Entity) WHERE e.name_key IS NULL
               SET e.name_key = {cypher_name_key('toLower(e.name)')}"""
        )

    def close(self):
        self.driver.close()


# The Source id->name map is read on every query but changes only on ingest,
# so it is cached briefly (and invalidated on upsert) instead of costing a
# full Cypher round trip per retrieval.
_src_names: dict[str, str] = {}
_src_names_at = 0.0
_src_lock = threading.Lock()
_SRC_TTL_S = 30.0


def get_source_names(client: Neo4jClient | None = None) -> dict[str, str]:
    global _src_names, _src_names_at
    import time

    with _src_lock:
        if _src_names and time.monotonic() - _src_names_at < _SRC_TTL_S:
            return dict(_src_names)
    g = client or get_graph()
    data = {r["id"]: r["name"] for r in g.run("MATCH (s:Source) RETURN s.id AS id, s.name AS name")}
    with _src_lock:
        _src_names, _src_names_at = data, time.monotonic()
    return dict(data)


def invalidate_source_names() -> None:
    global _src_names, _src_names_at
    with _src_lock:
        _src_names, _src_names_at = {}, 0.0


_graph: Neo4jClient | None = None


def get_graph() -> Neo4jClient:
    global _graph
    if _graph is None:
        _graph = Neo4jClient()
    return _graph
