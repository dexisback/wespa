from __future__ import annotations

import logging

from ..config import MAX_GRAPH_FACTS, MAX_PATHS
from ..extraction.schemas import Fact, GraphEdge, GraphNode, GraphPath
from .neo4j_client import get_graph

log = logging.getLogger("graph.retriever")


def _client(client=None):
    return client or get_graph()


def _fmt_dt(v) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else getattr(v, "iso_format", lambda: str(v))()


def match_entities(names: list[str], client=None) -> list[dict]:
    """Match seed names to graph entities, allowing partial matches
    ("david" -> "David Luan", "google" -> "Google")."""
    g = _client(client)
    seeds = [n.lower().strip() for n in names if n and n.strip() and len(n.strip()) >= 3]
    if not seeds:
        return []
    return g.run(
        """MATCH (e:Entity)
           WITH e, toLower(e.name) AS ln
           WHERE any(x IN $seeds WHERE ln CONTAINS x OR x CONTAINS ln)
           RETURN e.id AS id, e.name AS name, e.type AS type LIMIT 20""",
        seeds=seeds,
    )


def retrieve_facts(seed_names: list[str], client=None, max_facts: int = MAX_GRAPH_FACTS) -> dict:
    """Traverse 1-2 hops from matched entities (both directions) and return
    evidence facts plus a compact GraphPath payload for visualization."""
    g = _client(client)
    lowered = [n.lower().strip() for n in seed_names if n and n.strip()]
    empty = {"facts": [], "graph_path": GraphPath(), "matched": []}
    if not lowered:
        return empty

    matched = match_entities(seed_names, g)
    matched_names = {m["name"].lower() for m in matched}
    src_names = {s["id"]: s["name"] for s in g.run("MATCH (s:Source) RETURN s.id AS id, s.name AS name")}
    names = list(matched_names or lowered)

    def hop1_out():
        return g.run(
            """MATCH (a:Entity)-[r1]->(b:Entity)
               WHERE toLower(a.name) IN $names
               RETURN a, r1, b LIMIT 25""",
            names=names,
        )

    def hop1_in():
        return g.run(
            """MATCH (a:Entity)-[r1]->(b:Entity)
               WHERE toLower(b.name) IN $names
               RETURN a, r1, b LIMIT 25""",
            names=names,
        )

    def expand(row):
        return g.run(
            """WITH $aid AS aid, $bid AS bid
               MATCH (a:Entity {id: aid})-[r1]->(b:Entity {id: bid})
               OPTIONAL MATCH (b)-[r2]->(c:Entity) WHERE r2.relation IS NOT NULL AND c <> a
               WITH a, r1, b, r2, c
               OPTIONAL MATCH (d:Entity)-[r3]->(b) WHERE r3.relation IS NOT NULL AND d <> a
               WITH a, r1, b, r2, c, r3, d
               OPTIONAL MATCH (a)-[r4]->(e:Entity) WHERE r4.relation IS NOT NULL AND e <> b
               WITH a, r1, b, r2, c, r3, d, r4, e
               OPTIONAL MATCH (f:Entity)-[r5]->(a) WHERE r5.relation IS NOT NULL AND f <> b
               RETURN a, r1, b, r2, c, r3, d, r4, e, r5, f LIMIT 40""",
            aid=row["a"]["id"], bid=row["b"]["id"],
        )

    seen_h1: set[str] = set()
    facts: dict[str, Fact] = {}
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    chains: set[tuple[str, ...]] = set()

    def add_node(row):
        if row and row.get("id") and row["id"] not in nodes:
            nodes[row["id"]] = GraphNode(
                id=row["id"], label=row.get("name") or row["id"], type=row.get("type") or "Organization"
            )

    def add_fact(rel, s_row, o_row):
        if not rel or not rel.get("fact_id") or not rel.get("relation"):
            return
        fid = rel["fact_id"]
        add_node(s_row)
        add_node(o_row)
        facts[fid] = Fact(
            fact_id=fid,
            subject_id=s_row["id"] if s_row else "",
            subject_name=(s_row or {}).get("name", ""),
            relation=rel.get("relation", ""),
            object_id=o_row["id"] if o_row else "",
            object_name=(o_row or {}).get("name", ""),
            confidence=float(rel.get("confidence") or 0.0),
            source_id=rel.get("source_id") or "",
            source_name=src_names.get(rel.get("source_id"), rel.get("source_id") or ""),
            document_id=rel.get("document_id") or "",
            observed_at=_fmt_dt(rel.get("observed_at")),
            valid_from=_fmt_dt(rel.get("valid_from")),
            valid_to=_fmt_dt(rel.get("valid_to")),
            extraction_confidence=float(rel.get("extraction_confidence") or 0.8),
            active=rel.get("valid_to") in (None, ""),
            conflict=bool(rel.get("conflict")),
            supersedes=list(rel.get("supersedes") or []),
        )
        edges[fid] = GraphEdge(
            id=fid,
            source=s_row["id"] if s_row else "",
            target=o_row["id"] if o_row else "",
            label=rel.get("relation", ""),
            confidence=float(rel.get("confidence") or 0.0),
            source_id=rel.get("source_id") or "",
            active=rel.get("valid_to") in (None, ""),
        )

    def node_row(prefix):
        r = {"id": prefix.get("id"), "name": prefix.get("name"), "type": prefix.get("type")}
        return r if r["id"] else None

    def process_row(row):
        a, r1, b = row.get("a"), row.get("r1"), row.get("b")
        if not a or not b:
            return
        add_fact(r1, node_row(a), node_row(b))
        chains.add((a["id"], b["id"]))
        c = row.get("c")
        if c and row.get("r2"):
            add_fact(row["r2"], node_row(b), node_row(c))
            chains.add((a["id"], b["id"], c["id"]))
        d = row.get("d")
        if d and row.get("r3"):
            add_fact(row["r3"], node_row(d), node_row(b))
            chains.add((d["id"], b["id"], a["id"]))
        e = row.get("e")
        if e and row.get("r4"):
            add_fact(row["r4"], node_row(a), node_row(e))
            chains.add((b["id"], a["id"], e["id"]))
        f = row.get("f")
        if f and row.get("r5"):
            add_fact(row["r5"], node_row(f), node_row(a))
            chains.add((f["id"], a["id"], b["id"]))

    for base in (hop1_out, hop1_in):
        for r in base():
            fid = (r.get("r1") or {}).get("fact_id")
            if fid and fid in seen_h1:
                continue
            if fid:
                seen_h1.add(fid)
            process_row(r)
            for er in expand(r):
                process_row(er)

    for m in matched:
        add_node({"id": m["id"], "name": m["name"], "type": m.get("type")})

    fact_list = sorted(facts.values(), key=lambda f: (not f.active, -f.confidence))[:max_facts]
    keep = {f.fact_id for f in fact_list}
    for eid in list(edges):
        if eid not in keep:
            del edges[eid]

    # Facts that sit on a 2-hop chain get highlighted for the UI and ranked first.
    chain_fact_ids: set[str] = set()
    for ch in chains:
        for x, y in zip(ch, ch[1:]):
            for f in facts.values():
                if f.subject_id == x and f.object_id == y:
                    chain_fact_ids.add(f.fact_id)

    path_chains = [list(ch) for ch in chains if len(ch) >= 3]
    path_chains.sort(
        key=lambda p: -sum(
            f.confidence
            for f in fact_list
            if f.subject_id in p and f.object_id in p
        )
    )
    seen_paths, final_paths = set(), []
    for p in path_chains:
        key = tuple(p)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        final_paths.append(p)
        if len(final_paths) >= MAX_PATHS:
            break
    if not final_paths:
        for eid, e in edges.items():
            final_paths.append([e.source, e.target])
            if len(final_paths) >= MAX_PATHS:
                break

    return {
        "facts": fact_list,
        "graph_path": GraphPath(nodes=list(nodes.values()), edges=list(edges.values()), paths=final_paths),
        "matched": [m["name"] for m in matched],
        "chain_fact_ids": sorted(chain_fact_ids),
    }
