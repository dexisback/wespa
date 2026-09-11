from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..trust.contradiction import (
    ACTION_CONFLICT,
    ACTION_CORROBORATE,
    ACTION_SUPERSEDE,
    classify_fact,
)
from .neo4j_client import get_graph

log = logging.getLogger("graph.writer")


class GraphWriter:
    """Writes entities, sources, documents and versioned facts to Neo4j.

    A changed fact never overwrites the old one: the old edge gets valid_to set
    and a new edge is inserted — preserving the full history of the belief.
    """

    def __init__(self, client=None):
        self.g = client or get_graph()

    def upsert_source(self, source_id: str, name: str, reliability: float):
        self.g.run(
            "MERGE (s:Source {id:$sid}) SET s.name=$name, s.reliability=$rel",
            sid=source_id, name=name, rel=reliability,
        )

    def upsert_document(self, document_id: str, title: str, url: str, published_at: str, source_id: str):
        self.g.run(
            """MERGE (d:Document {id:$did})
               SET d.title=$title, d.url=$url, d.published_at=$pub
               WITH d MATCH (s:Source {id:$sid})
               MERGE (s)-[:PUBLISHED]->(d)""",
            did=document_id, title=title, url=url, pub=published_at, sid=source_id,
        )

    def upsert_entity(self, entity_id: str, name: str, etype: str):
        self.g.run(
            """MERGE (e:Entity {id:$eid})
               ON CREATE SET e.name=$name, e.type=$etype
               ON MATCH SET e.name = CASE WHEN e.name IS NULL OR size(e.name) < size($name) THEN $name ELSE e.name END""",
            eid=entity_id, name=name, etype=etype,
        )

    def entity_exists(self, entity_id: str) -> bool:
        return bool(self.g.run("MATCH (e:Entity {id:$eid}) RETURN 1 AS x LIMIT 1", eid=entity_id))

    def get_active_facts(self, subject_id: str, relation: str) -> list[dict]:
        rows = self.g.run(
            """MATCH (s:Entity {id:$sid})-[r]->(o:Entity)
               WHERE r.relation=$rel AND r.valid_to IS NULL
               RETURN r.fact_id AS fact_id, o.id AS object_id, r.source_id AS source_id,
                      r.observed_at AS observed_at
               LIMIT 50""",
            sid=subject_id, rel=relation,
        )
        return rows

    def write_fact(
        self,
        fact_id: str,
        subject_id: str,
        relation: str,
        object_id: str,
        confidence: float,
        extraction_confidence: float,
        source_id: str,
        document_id: str,
        observed_at: str,
        valid_from: str,
        valid_to: str | None,
        supersedes: list[str],
        conflict: bool,
        corroborates: list[str],
    ):
        rel_type = "".join(ch for ch in relation.upper() if ch.isalpha() or ch == "_")
        self.g.run(
            f"""MATCH (s:Entity {{id:$sid}}), (o:Entity {{id:$oid}})
                CREATE (s)-[r:{rel_type} {{
                    fact_id:$fid, relation:$relation, confidence:$conf,
                    extraction_confidence:$econf, source_id:$src, document_id:$doc,
                    observed_at:$obs, valid_from:$vfrom, valid_to:$vto,
                    supersedes:$sup, conflict:$conflict, corroborates:$cor
                }}]->(o)
                RETURN r.fact_id AS fact_id""",
            sid=subject_id, oid=object_id, fid=fact_id, relation=relation,
            conf=confidence, econf=extraction_confidence, src=source_id, doc=document_id,
            obs=observed_at, vfrom=valid_from, vto=valid_to,
            sup=supersedes, conflict=conflict, cor=corroborates,
        )

    def close_fact(self, fact_id: str, valid_to: str):
        """Preserve the prior fact version: mark its valid_to instead of deleting it."""
        self.g.run(
            "MATCH ()-[r]->() WHERE r.fact_id=$fid SET r.valid_to=$vto, r.active=false",
            fid=fact_id, vto=valid_to,
        )
        log.info("preserving prior fact version, not overwriting (fact_id=%s closed at %s)", fact_id, valid_to)

    def flag_conflict(self, fact_id: str):
        self.g.run("MATCH ()-[r]->() WHERE r.fact_id=$fid SET r.conflict=true", fid=fact_id)

    def get_fact(self, fact_id: str) -> dict | None:
        rows = self.g.run(
            """MATCH (s:Entity)-[r]->(o:Entity)
               WHERE r.fact_id=$fid
               OPTIONAL MATCH (src:Source {id: r.source_id})
               RETURN s.id AS subject_id, s.name AS subject_name,
                      o.id AS object_id, o.name AS object_name,
                      r.fact_id AS fact_id, r.relation AS relation,
                      r.confidence AS confidence, r.extraction_confidence AS extraction_confidence,
                      r.source_id AS source_id, coalesce(src.name, r.source_id) AS source_name,
                      r.document_id AS document_id, r.observed_at AS observed_at,
                      r.valid_from AS valid_from, r.valid_to AS valid_to,
                      r.conflict AS conflict, r.supersedes AS supersedes, r.corroborates AS corroborates
               LIMIT 1""",
            fid=fact_id,
        )
        return rows[0] if rows else None

    def get_fact_versions(self, fact_id: str) -> list[dict]:
        """Walk the supersedes chain in both directions to return every version."""
        forward = self.g.run(
            """MATCH (s:Entity)-[r]->(o:Entity)
               WHERE r.fact_id=$fid OR $fid IN r.supersedes
               RETURN DISTINCT r.fact_id AS fact_id""",
            fid=fact_id,
        )
        frontier = [f["fact_id"] for f in forward]
        seen = set(frontier)
        for _ in range(10):
            if not frontier:
                break
            rows = self.g.run(
                "MATCH ()-[r]->() WHERE r.fact_id IN $ids AND size(r.supersedes) > 0 UNWIND r.supersedes AS sid RETURN DISTINCT sid AS fact_id",
                ids=frontier,
            )
            nxt = [r["fact_id"] for r in rows if r["fact_id"] not in seen]
            seen.update(nxt)
            frontier = nxt
        versions = []
        for fid in seen:
            f = self.get_fact(fid)
            if f:
                versions.append(f)
        versions.sort(key=lambda f: f.get("valid_from") or "")
        return versions

    def ingest_fact(
        self,
        *,
        subject_id: str,
        subject_name: str,
        relation: str,
        object_id: str,
        object_name: str,
        subject_type: str = "Organization",
        object_type: str = "Organization",
        source_id: str,
        source_name: str,
        document_id: str,
        observed_at: datetime,
        extraction_confidence: float,
        reliability: float,
    ) -> dict:
        """Normalize/dedup + temporal decision + write. Returns action summary."""
        self.upsert_entity(subject_id, subject_name, subject_type)
        self.upsert_entity(object_id, object_name, object_type)
        existing = self.get_active_facts(subject_id, relation)
        decision = classify_fact(
            subject_id, relation, object_id, observed_at,
            [
                {
                    "fact_id": e["fact_id"],
                    "subject_id": subject_id,
                    "relation": relation,
                    "object_id": e.get("object_id") or "",
                    "source_id": e.get("source_id") or "",
                    "observed_at": e.get("observed_at"),
                    "valid_to": None,
                }
                for e in existing
            ],
        )

        if decision["action"] == ACTION_CORROBORATE:
            corroboration_count = len(decision["corroborate_ids"]) + 1
            fact_id = f"fact_{object_id[-6:]}_{observed_at.strftime('%Y%m%d')}_{abs(hash(document_id + relation + object_id)) % 100000}"
            from ..trust.confidence import confidence as compute_confidence
            conf = compute_confidence(reliability, corroboration_count, extraction_confidence)
            self.write_fact(
                fact_id, subject_id, relation, object_id, conf, extraction_confidence,
                source_id, document_id, observed_at.isoformat(), observed_at.isoformat(), None,
                [], False, decision["corroborate_ids"],
            )
            return {"action": ACTION_CORROBORATE, "fact_id": fact_id, "corroborated": decision["corroborate_ids"]}

        fact_id = f"fact_{abs(hash(f'{document_id}|{relation}|{object_id}|{observed_at.isoformat()}')) % 10**12:012d}"
        from ..trust.confidence import confidence as compute_confidence

        if decision["action"] == ACTION_SUPERSEDE:
            conf = compute_confidence(reliability, 1, extraction_confidence)
            self.write_fact(
                fact_id, subject_id, relation, object_id, conf, extraction_confidence,
                source_id, document_id, observed_at.isoformat(), observed_at.isoformat(), None,
                decision["supersede_ids"], False, [],
            )
            for old_id in decision["supersede_ids"]:
                self.close_fact(old_id, observed_at.isoformat())
            return {"action": ACTION_SUPERSEDE, "fact_id": fact_id, "closed": decision["supersede_ids"]}

        if decision["action"] == ACTION_CONFLICT:
            conf = compute_confidence(reliability, 1, extraction_confidence, conflict=True)
            self.write_fact(
                fact_id, subject_id, relation, object_id, conf, extraction_confidence,
                source_id, document_id, observed_at.isoformat(), observed_at.isoformat(), None,
                [], True, [],
            )
            for other_id in decision["conflict_ids"]:
                self.flag_conflict(other_id)
            return {"action": ACTION_CONFLICT, "fact_id": fact_id, "conflicts_with": decision["conflict_ids"]}

        conf = compute_confidence(reliability, 1, extraction_confidence)
        self.write_fact(
            fact_id, subject_id, relation, object_id, conf, extraction_confidence,
            source_id, document_id, observed_at.isoformat(), observed_at.isoformat(), None,
            [], False, [],
        )
        return {"action": "NEW", "fact_id": fact_id}


def get_writer() -> GraphWriter:
    return GraphWriter()
