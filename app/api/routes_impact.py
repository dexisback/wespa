from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..impact.impact import answer_freshness, impact_for_fact, recent_changes

router = APIRouter()


@router.get("/impact/recent")
def impact_recent(limit: int = 10):
    try:
        return recent_changes(min(max(limit, 1), 25))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"impact lookup failed: {e}")


@router.get("/impact/answer/{query_id}")
def impact_answer(query_id: str):
    try:
        data = answer_freshness(query_id)
        if not data.get("ok"):
            raise HTTPException(status_code=404, detail=data.get("error", "not found"))
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"freshness check failed: {e}")


@router.get("/impact/fact/{fact_id}")
def impact_fact(fact_id: str):
    try:
        data = impact_for_fact(fact_id)
        if not data.get("ok"):
            raise HTTPException(status_code=404, detail=data.get("error", "not found"))
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"impact analysis failed: {e}")
