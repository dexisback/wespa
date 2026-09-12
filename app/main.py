from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes_facts import router as facts_router
from .api.routes_impact import router as impact_router
from .api.routes_ingest import router as ingest_router
from .api.routes_query import router as query_router
from .config import EVAL_RESULTS_PATH, FRONTEND_DIR
from .db.postgres import get_db
from .graph.neo4j_client import get_graph
from .vector.chroma_client import chroma_health

app = APIRouter()


@app.get("/health")
def health():
    status = {"postgres": False, "neo4j": False, "chroma": False, "groq_key": False, "gemini_key": False, "gemini_fallback_key": False, "openrouter_key": False}
    try:
        status["postgres"] = get_db().health()
    except Exception:
        pass
    try:
        status["neo4j"] = get_graph().health()
    except Exception:
        pass
    try:
        status["chroma"] = chroma_health()
    except Exception:
        pass
    from .config import GEMINI_API_KEY, GEMINI_API_KEY_FALLBACK, GROQ_API_KEY, OPENROUTER_API_KEY

    status["groq_key"] = bool(GROQ_API_KEY)
    status["gemini_key"] = bool(GEMINI_API_KEY)
    status["gemini_fallback_key"] = bool(GEMINI_API_KEY_FALLBACK)
    status["openrouter_key"] = bool(OPENROUTER_API_KEY)
    status["llm_ready"] = any([status["gemini_key"], status["gemini_fallback_key"], status["openrouter_key"], status["groq_key"]])
    status["all_ready"] = all([status["postgres"], status["neo4j"], status["chroma"], status["llm_ready"]])
    return status


@app.get("/stats")
def stats():
    g = get_graph()
    try:
        entities = g.run("MATCH (e:Entity) RETURN count(e) AS n")[0]["n"]
        facts = g.run("MATCH ()-[r]->() WHERE r.relation IS NOT NULL RETURN count(r) AS n")[0]["n"]
        active = g.run("MATCH ()-[r]->() WHERE r.relation IS NOT NULL AND r.valid_to IS NULL RETURN count(r) AS n")[0]["n"]
        historical = g.run("MATCH ()-[r]->() WHERE r.relation IS NOT NULL AND r.valid_to IS NOT NULL RETURN count(r) AS n")[0]["n"]
        conflicts = g.run("MATCH ()-[r]->() WHERE r.relation IS NOT NULL AND r.conflict = true RETURN count(r) AS n")[0]["n"]
        corroborated = g.run("MATCH ()-[r]->() WHERE r.relation IS NOT NULL AND size(r.corroborates) > 0 RETURN count(r) AS n")[0]["n"]
        docs = g.run("MATCH (d:Document) RETURN count(d) AS n")[0]["n"]
        srcs = g.run("MATCH (s:Source) RETURN count(s) AS n")[0]["n"]
    except Exception:
        raise HTTPException(status_code=503, detail="graph store unavailable")
    last_ing = None
    last_doc = None
    try:
        last_doc = get_graph().run(
            "MATCH (d:Document) RETURN d.published_at AS at ORDER BY d.published_at DESC LIMIT 1"
        )
        last_doc = last_doc[0]["at"] if last_doc else None
    except Exception:
        pass
    try:
        row = get_db().last_ingestion()
        if row:
            last_ing = {"run_id": row["run_id"], "completed_at": str(row["completed_at"]), "documents_added": row["documents_added"]}
    except Exception:
        pass
    return {
        "entities": entities,
        "facts": facts,
        "active_facts": active,
        "historical_facts": historical,
        "conflicts": conflicts,
        "corroborated_facts": corroborated,
        "documents": docs,
        "sources": srcs,
        "last_ingestion": last_ing,
        "last_document_at": str(last_doc) if last_doc else None,
    }


@app.get("/eval/results")
def eval_results():
    if EVAL_RESULTS_PATH.exists():
        import json

        return json.loads(EVAL_RESULTS_PATH.read_text())
    return {"status": "not_run", "message": "Run evaluation to generate metrics."}


_eval_lock = threading.Lock()


@app.post("/eval/run")
def eval_run():
    if not _eval_lock.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        from .evaluation.evaluate import run_evaluation

        return run_evaluation()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"evaluation failed: {e}")
    finally:
        _eval_lock.release()


def create_app():
    from fastapi import FastAPI

    application = FastAPI(title="AI Knowledge Memory Engine", version="1.0.0")
    application.include_router(app)
    application.include_router(query_router)
    application.include_router(ingest_router)
    application.include_router(facts_router)
    application.include_router(impact_router)
    application.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @application.on_event("startup")
    def startup():
        try:
            db = get_db()
            db.init_schema()
        except Exception as e:
            print(f"[startup] postgres unavailable: {e}")
        try:
            get_graph().ensure_constraints()
        except Exception as e:
            print(f"[startup] neo4j unavailable: {e}")

    if (FRONTEND_DIR / "index.html").exists():
        application.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return application


fastapi_app = create_app()
