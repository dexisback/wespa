from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..extraction.schemas import QueryRequest, QueryResult
from ..rag.hybrid_retriever import answer_question

router = APIRouter()


@router.post("/query", response_model=QueryResult)
def query(req: QueryRequest) -> QueryResult:
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")
    try:
        return answer_question(
            question,
            req.retrieval_mode,
            allow_live=req.allow_live,
            as_of=req.as_of,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")
