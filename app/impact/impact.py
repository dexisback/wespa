"""Knowledge Impact Analysis.

When a fact is superseded or contradicted, compute what it affects:
which previous answers cited it, whether they materially depended on it,
and what is now potentially stale. This turns "the fact changed" into
"here is what the change breaks".
"""
from __future__ import annotations

from ..db.postgres import get_db
from ..graph.graph_writer import GraphWriter


def _fact_label(f: dict | None) -> str:
    if not f:
        return ""
    return f"{f.get('subject_name', '')} {f.get('relation', '')} {f.get('object_name', '')}".strip()


def _successor(client, fact: dict) -> dict | None:
    """The active fact that superseded / replaces the given one (same subject+relation)."""
    rows = client.run(
        """MATCH (s:Entity {id:$sid})-[r]->(o:Entity)
           WHERE r.relation=$rel AND r.valid_to IS NULL AND r.fact_id <> $fid
           RETURN r.fact_id AS fact_id, o.name AS object_name, r.confidence AS confidence,
                  r.observed_at AS observed_at, r.conflict AS conflict
           ORDER BY r.observed_at DESC LIMIT 3""",
        sid=fact.get("subject_id") or "",
        rel=fact.get("relation") or "",
        fid=fact.get("fact_id") or "",
    )
    return rows[0] if rows else None


def _dependent_facts(client, fact: dict, limit: int = 8) -> list[dict]:
    """Active facts sharing an endpoint with the changed fact — downstream context
    that may need revisiting."""
    rows = client.run(
        """MATCH (s:Entity)-[r]->(o:Entity)
           WHERE r.valid_to IS NULL AND r.fact_id <> $fid
             AND (s.id IN [$sid, $oid] OR o.id IN [$sid, $oid])
           RETURN r.fact_id AS fact_id, s.name AS subject_name, r.relation AS relation,
                  o.name AS object_name, r.confidence AS confidence
           ORDER BY r.confidence DESC LIMIT $limit""",
        fid=fact.get("fact_id") or "",
        sid=fact.get("subject_id") or "",
        oid=fact.get("object_id") or "",
        limit=limit,
    )
    return rows


def _answer_status(
    cited_fact_ids: list[str],
    changed_fact_id: str,
    successor_id: str | None,
    conflicted: bool,
) -> tuple[str, str]:
    """CURRENT / POTENTIALLY STALE / INVALIDATED with a human-readable reason."""
    cited = set(cited_fact_ids)
    if successor_id and successor_id in cited:
        return "CURRENT", "This answer also cites the newer version of the fact, so it already reflects the update."
    if conflicted:
        return (
            "INVALIDATED",
            "The cited fact was contradicted by a competing claim; both versions are kept and the answer's conclusion may flip.",
        )
    return (
        "POTENTIALLY STALE",
        "The answer relied on a fact version that has since been superseded; its claim should be re-checked against the newer version.",
    )


def impact_for_fact(fact_id: str) -> dict:
    g = GraphWriter()
    fact = g.get_fact(fact_id)
    if not fact:
        return {"ok": False, "error": f"fact {fact_id} not found"}
    changed = _fact_label(fact) or fact_id
    successor = _successor(g.g, fact)
    dependents = _dependent_facts(g.g, fact)

    db = get_db()
    answers = []
    try:
        rows = db.get_answers_for_fact(fact_id)
        answer_ids = [r["query_id"] for r in rows]
        cited_map: dict[str, list[str]] = {}
        for qid in answer_ids:
            cited_map[qid] = db.get_answer_dependencies(qid)
        for r in rows:
            qid = r["query_id"]
            status, reason = _answer_status(
                cited_map.get(qid, []),
                fact_id,
                (successor or {}).get("fact_id"),
                bool(fact.get("conflict")),
            )
            answers.append(
                {
                    "query_id": qid,
                    "question": r["question"],
                    "retrieval_mode": r["retrieval_mode"],
                    "answered_at": str(r["created_at"]),
                    "cited_fact_id": fact_id,
                    "all_cited": cited_map.get(qid, []),
                    "status": status,
                    "reason": reason,
                }
            )
    except Exception:
        answers = []

    order = {"INVALIDATED": 0, "POTENTIALLY STALE": 1, "CURRENT": 2}
    answers.sort(key=lambda a: (order.get(a["status"], 3), a["answered_at"]), reverse=False)
    stale_count = sum(1 for a in answers if a["status"] != "CURRENT")

    return {
        "ok": True,
        "fact": {
            "fact_id": fact_id,
            "label": changed,
            "object": fact.get("object_name", ""),
            "source": fact.get("source_name", ""),
            "valid_from": str(fact.get("valid_from") or ""),
            "valid_to": str(fact.get("valid_to") or "") or None,
            "active": fact.get("valid_to") in (None, ""),
            "conflict": bool(fact.get("conflict")),
        },
        "successor": (
            {
                "fact_id": successor["fact_id"],
                "object": successor.get("object_name", ""),
                "confidence": successor.get("confidence"),
                "observed_at": str(successor.get("observed_at") or ""),
            }
            if successor
            else None
        ),
        "impact": {
            "answers_affected": len(answers),
            "answers_stale": stale_count,
            "dependent_facts": len(dependents),
            "headline": (
                f"{stale_count} previous answer(s) may be stale · {len(dependents)} related fact(s) depend on this"
                if stale_count or dependents
                else "No recorded answers or related facts depend on this change yet"
            ),
        },
        "answers": answers,
        "dependent_facts": dependents,
    }


def recent_changes(limit: int = 10) -> dict:
    """Latest supersede/conflict events with their impact counts."""
    g = GraphWriter()
    db = get_db()
    with db.conn.cursor() as cur:
        cur.execute(
            """SELECT fact_id, action, old_value, new_value, timestamp
               FROM fact_audit WHERE action IN ('SUPERSEDES','SUPERSEDED','CONFLICT')
               ORDER BY timestamp DESC LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    events = []
    for fact_id, action, old_value, new_value, ts in rows:
        try:
            fact = g.get_fact(fact_id)
        except Exception:
            fact = None
        successor = _successor(g.g, fact) if fact else None
        stale = 0
        try:
            stale = sum(
                1
                for a in _answers_for(fact_id)
                if a["status"] != "CURRENT"
            )
        except Exception:
            stale = 0
        events.append(
            {
                "fact_id": fact_id,
                "action": action,
                "when": str(ts),
                "label": _fact_label(fact) or old_value,
                "old": old_value,
                "new": new_value,
                "successor_object": (successor or {}).get("object_name", ""),
                "answers_stale": stale,
            }
        )
    return {"ok": True, "events": events}


def _answers_for(fact_id: str) -> list[dict]:
    """Reuse impact_for_fact's answer logic without recursion."""
    fact = GraphWriter().get_fact(fact_id)
    if not fact:
        return []
    successor = _successor(GraphWriter().g, fact)
    db = get_db()
    out = []
    for r in db.get_answers_for_fact(fact_id):
        status, _ = _answer_status(
            db.get_answer_dependencies(r["query_id"]),
            fact_id,
            (successor or {}).get("fact_id"),
            bool(fact.get("conflict")),
        )
        out.append({"query_id": r["query_id"], "status": status})
    return out


def answer_freshness(query_id: str) -> dict:
    """Re-check a previous answer against current memory: which of the facts it
    cited have since changed?"""
    db = get_db()
    deps = db.get_answer_dependencies(query_id)
    if not deps:
        with db.conn.cursor() as cur:
            cur.execute("SELECT question FROM queries WHERE query_id=%s", (query_id,))
            row = cur.fetchone()
        return {"ok": False, "error": "no recorded dependencies for this answer"}

    g = GraphWriter()
    rows = db.get_answers_for_fact(deps[0]) if deps else []
    question = ""
    with db.conn.cursor() as cur:
        cur.execute("SELECT question FROM queries WHERE query_id=%s", (query_id,))
        got = cur.fetchone()
        if got:
            question = got[0]

    checks = []
    stale = 0
    for fid in deps:
        fact = g.get_fact(fid)
        if not fact:
            continue
        successor = _successor(g.g, fact)
        conflicted = bool(fact.get("conflict"))
        superseded = fact.get("valid_to") not in (None, "")
        if successor and successor.get("fact_id") in deps:
            status, reason = "CURRENT", "The answer also cites the newer version."
        elif conflicted:
            status, reason = "INVALIDATED", "Cited fact is part of a contradiction; both claims are retained."
        elif superseded and successor:
            status, reason = "POTENTIALLY STALE", (
                f"Superseded by '{fact.get('subject_name', '')} {fact.get('relation', '')} {successor.get('object_name', '')}' "
                f"(newer source, observed {str(successor.get('observed_at') or '')[:10]})."
            )
        else:
            status, reason = "CURRENT", "Fact unchanged since the answer was produced."
        if status != "CURRENT":
            stale += 1
        checks.append(
            {
                "fact_id": fid,
                "label": _fact_label(fact),
                "status": status,
                "reason": reason,
                "valid_to": str(fact.get("valid_to") or "") or None,
            }
        )
    order = {"INVALIDATED": 0, "POTENTIALLY STALE": 1, "CURRENT": 2}
    checks.sort(key=lambda c: order.get(c["status"], 3))
    verdict = (
        "STALE" if stale and stale == len(checks)
        else "POTENTIALLY STALE" if stale
        else "CURRENT"
    )
    return {
        "ok": True,
        "query_id": query_id,
        "question": question,
        "verdict": verdict,
        "stale_count": stale,
        "checks": checks,
    }
