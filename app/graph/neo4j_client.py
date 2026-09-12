from __future__ import annotations

from neo4j import GraphDatabase
from neo4j.graph import Node, Relationship

from ..config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME


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
        ]
        for s in stmts:
            self.run(s)
        # Backfill provenance for documents ingested before Document->Entity
        # mention edges became part of the graph contract. This is idempotent.
        self.run(
            """MATCH (d:Document), (s:Entity)-[r]->(o:Entity)
               WHERE r.document_id = d.id
               MERGE (d)-[:MENTIONS]->(s)
               MERGE (d)-[:MENTIONS]->(o)"""
        )

    def close(self):
        self.driver.close()


_graph: Neo4jClient | None = None


def get_graph() -> Neo4jClient:
    global _graph
    if _graph is None:
        _graph = Neo4jClient()
    return _graph
