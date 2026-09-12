from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..extraction.schemas import Fact, FactAction, FactHistory
from ..graph.graph_writer import GraphWriter
from ..trust.source_weights import reliability

router = APIRouter()


def _to_fact(d: dict) -> Fact:
    corrob = d.get("corroborates") or []
    return Fact(
        fact_id=d["fact_id"],
        subject_id=d.get("subject_id", ""),
        subject_name=d.get("subject_name", ""),
        relation=d.get("relation", ""),
        object_id=d.get("object_id", ""),
        object_name=d.get("object_name", ""),
        confidence=float(d.get("confidence") or 0.0),
        source_id=d.get("source_id", ""),
        source_name=d.get("source_name", ""),
        document_id=d.get("document_id", ""),
        observed_at=d.get("observed_at"),
        valid_from=d.get("valid_from"),
        valid_to=d.get("valid_to"),
        extraction_confidence=float(d.get("extraction_confidence") or 0.8),
        active=(d.get("valid_to") in (None, "")),
        conflict=bool(d.get("conflict")),
        corroborations=1 + len(corrob),
        supersedes=list(d.get("supersedes") or []),
    )


class Resolution(BaseModel):
    competing_claims: list[dict] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)


def _resolution(w: GraphWriter, current: dict) -> Resolution:
    """Explain why the current belief wins when multiple claims exist for the
    same (subject, relation): competing claims + human-readable reasons."""
    try:
        rows = w.g.run(
            """MATCH (s:Entity {id:$sid})-[r]->(o:Entity)
               WHERE r.relation=$rel
               OPTIONAL MATCH (src:Source {id: r.source_id})
               RETURN r.fact_id AS fact_id, o.name AS object_name,
                      r.confidence AS confidence, r.observed_at AS observed_at,
                      r.valid_to AS valid_to, r.corroborates AS corroborates,
                      coalesce(src.name, r.source_id) AS source_name,
                      coalesce(src.reliability, 0.6) AS source_reliability
               ORDER BY r.valid_to IS NOT NULL, r.observed_at DESC""",
            sid=current.get("subject_id") or "",
            rel=current.get("relation") or "",
        )
    except Exception:
        return Resolution()

    claims, why = [], []
    parsed = []
    for r in rows:
        parsed.append(
            {
                "fact_id": r["fact_id"],
                "claim": f"{current.get('subject_name', '')} {r.get('relation', '')} {r.get('object_name', '')}".strip(),
                "object": r.get("object_name", ""),
                "source": r.get("source_name", ""),
                "source_reliability": float(r.get("source_reliability") or 0.6),
                "observed_at": str(r.get("observed_at") or ""),
                "confidence": float(r.get("confidence") or 0.0),
                "corroborations": 1 + len(r.get("corroborates") or []),
                "active": r.get("valid_to") in (None, ""),
            }
        )
    for p in parsed:
        claims.append({**p, "status": "current belief" if p["active"] else "superseded"})

    active = next((p for p in parsed if p["active"]), None)
    superseded = [p for p in parsed if not p["active"]]
    if active and superseded:
        newer = [s for s in superseded if s["observed_at"] and s["observed_at"] < active["observed_at"]]
        if newer:
            why.append(
                f"Newer observation: {active['observed_at'][:10]} supersedes {newer[0]['observed_at'][:10]}"
            )
        if active["source_reliability"] > max(s["source_reliability"] for s in superseded):
            why.append(
                f"More reliable source ({active['source']} · {active['source_reliability']:.2f} trust)"
            )
        if active["corroborations"] > 1:
            why.append(f"Corroborated by {active['corroborations']} source(s)")
        if not why:
            why.append("Strongest remaining claim by combined confidence score")
    if len(claims) > 1:
        claims_header = True
    return Resolution(competing_claims=claims, why=why)


class FactHistoryWithResolution(FactHistory):
    resolution: Resolution = Field(default_factory=Resolution)


@router.get("/facts/{fact_id}", response_model=FactHistoryWithResolution)
def fact_history(fact_id: str) -> FactHistoryWithResolution:
    try:
        w = GraphWriter()
        current = w.get_fact(fact_id)
        if not current:
            raise HTTPException(status_code=404, detail=f"fact {fact_id} not found")
        versions = w.get_fact_versions(fact_id)
        all_ids = {v["fact_id"] for v in versions} | {fact_id}
        audit_rows = []
        try:
            from ..db.postgres import get_db

            audit_rows = get_db().get_fact_audit(fact_id)
            for v in versions:
                audit_rows += get_db().get_fact_audit(v["fact_id"])
        except Exception:
            pass
        seen_audit = set()
        audit = []
        for row in audit_rows:
            key = (row["fact_id"], row["action"], str(row.get("old_value")))
            if key in seen_audit:
                continue
            seen_audit.add(key)
            audit.append(
                FactAction(
                    action=row["action"],
                    timestamp=str(row["timestamp"]),
                    detail=str(row.get("new_value") or ""),
                )
            )
        return FactHistoryWithResolution(
            fact=_to_fact(current),
            versions=[_to_fact(v) for v in versions],
            audit=audit,
            resolution=_resolution(w, current),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"fact lookup failed: {e}")
