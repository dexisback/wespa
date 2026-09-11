from __future__ import annotations

from datetime import datetime, timezone

from app.graph.graph_writer import GraphWriter  # noqa: F401 (interface reference)
from app.trust.contradiction import classify_fact
from app.trust.confidence import confidence as compute_confidence
from app.trust.source_weights import reliability


class FakeGraphWriter:
    """In-memory stand-in for GraphWriter. Shares the real temporal decision logic
    (classify_fact) and the real confidence formula, but needs no Neo4j."""

    def __init__(self):
        self.entities = {}
        self.sources = {}
        self.documents = {}
        self.facts = {}

    def upsert_source(self, source_id, name, reliability_weight):
        self.sources[source_id] = {"name": name, "reliability": reliability_weight}

    def upsert_document(self, document_id, title, url, published_at, source_id):
        self.documents[document_id] = {"title": title, "url": url, "published_at": published_at}

    def upsert_entity(self, entity_id, name, etype):
        if entity_id not in self.entities:
            self.entities[entity_id] = {"name": name, "type": etype}

    def entity_exists(self, entity_id):
        return entity_id in self.entities

    def get_active_facts(self, subject_id, relation):
        out = []
        for f in self.facts.values():
            if f["subject_id"] == subject_id and f["relation"] == relation and not f["valid_to"]:
                out.append({
                    "fact_id": f["fact_id"], "object_id": f["object_id"],
                    "subject_id": f["subject_id"], "relation": f["relation"],
                    "source_id": f["source_id"], "observed_at": f["observed_at"],
                    "valid_to": None,
                })
        return out

    def ingest_fact(self, *, subject_id, subject_name, relation, object_id, object_name,
                    subject_type="Organization", object_type="Organization", source_id,
                    source_name="", document_id="", observed_at, extraction_confidence,
                    reliability_weight=None, **_):
        self.upsert_entity(subject_id, subject_name, subject_type)
        self.upsert_entity(object_id, object_name, object_type)
        rel = reliability_weight if reliability_weight is not None else reliability(source_name)
        decision = classify_fact(subject_id, relation, object_id, observed_at, self.get_active_facts(subject_id, relation))
        observed_iso = observed_at.isoformat() if isinstance(observed_at, datetime) else str(observed_at)
        fact_id = f"fact_{len(self.facts) + 1:05d}"

        if decision["action"] == "CORROBORATE":
            conf = compute_confidence(rel, len(decision["corroborate_ids"]) + 1, extraction_confidence)
        elif decision["action"] == "CONFLICT":
            conf = compute_confidence(rel, 1, extraction_confidence, conflict=True)
        else:
            conf = compute_confidence(rel, 1, extraction_confidence)

        self.facts[fact_id] = {
            "fact_id": fact_id, "subject_id": subject_id, "subject_name": subject_name,
            "relation": relation, "object_id": object_id, "object_name": object_name,
            "confidence": conf, "extraction_confidence": extraction_confidence,
            "source_id": source_id, "source_name": source_name, "document_id": document_id,
            "observed_at": observed_iso, "valid_from": observed_iso, "valid_to": None,
            "conflict": decision["action"] == "CONFLICT",
            "supersedes": decision["supersede_ids"], "corroborates": decision["corroborate_ids"],
        }
        if decision["action"] == "SUPERSEDE":
            for old_id in decision["supersede_ids"]:
                self.close_fact(old_id, observed_iso)
        if decision["action"] == "CONFLICT":
            for other_id in decision["conflict_ids"]:
                self.facts[other_id]["conflict"] = True
        return {"action": decision["action"], "fact_id": fact_id,
                "closed": decision["supersede_ids"], "corroborated": decision["corroborate_ids"],
                "conflicts_with": decision["conflict_ids"]}

    def close_fact(self, fact_id, valid_to):
        if fact_id in self.facts:
            self.facts[fact_id]["valid_to"] = valid_to

    def flag_conflict(self, fact_id):
        if fact_id in self.facts:
            self.facts[fact_id]["conflict"] = True

    def get_fact(self, fact_id):
        return self.facts.get(fact_id)

    def get_fact_versions(self, fact_id):
        versions = [self.facts[fact_id]] if fact_id in self.facts else []
        target = self.facts.get(fact_id, {})
        for f in self.facts.values():
            if fact_id in (f.get("supersedes") or []):
                versions.append(f)
            if f["fact_id"] != fact_id and target.get("supersedes") and f["fact_id"] in target["supersedes"]:
                if f not in versions:
                    versions.append(f)
        return sorted(versions, key=lambda f: f["valid_from"])


class FakeDB:
    def __init__(self):
        self.hashes = set()
        self.documents = {}
        self.audit = []
        self.queries = []
        self.logs = []

    def upsert_source(self, *a, **k):
        pass

    def document_exists_by_hash(self, h):
        return h in self.hashes

    def document_exists(self, did):
        return did in self.documents

    def insert_document(self, document_id, source_id, url, title, published_at, retrieved_at, content_hash):
        self.documents[document_id] = {"content_hash": content_hash}
        self.hashes.add(content_hash)

    def fact_audit(self, fact_id, action, old_value="", new_value=""):
        self.audit.append({"fact_id": fact_id, "action": action, "old_value": old_value, "new_value": new_value})

    def get_fact_audit(self, fact_id):
        return [a for a in self.audit if a["fact_id"] == fact_id]

    def log_query(self, query_id, question, retrieval_mode, latency_ms):
        self.queries.append({"query_id": query_id, "question": question})

    def log_ingestion(self, *a, **k):
        self.logs.append(a)

    def health(self):
        return True


class FakeVectors:
    def __init__(self):
        self.added = []

    def add_chunks(self, chunks):
        self.added.extend(chunks)
        return len(chunks)


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)
