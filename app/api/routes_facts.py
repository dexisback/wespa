from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..extraction.schemas import Fact, FactAction, FactHistory
from ..graph.graph_writer import GraphWriter

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


@router.get("/facts/{fact_id}", response_model=FactHistory)
def fact_history(fact_id: str) -> FactHistory:
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
        return FactHistory(
            fact=_to_fact(current),
            versions=[_to_fact(v) for v in versions],
            audit=audit,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"fact lookup failed: {e}")
