import pytest
from fastapi.testclient import TestClient

import app.api.routes_query as routes_query
import app.rag.answer_generator as answer_gen
import app.rag.hybrid_retriever as hybrid
from app.extraction.schemas import Chunk, Fact, QueryResult
from app.rag.hybrid_retriever import answer_question, retrieve

from .fakes import FakeDB, FakeGraphWriter, FakeVectors, utc


def fake_fact(rel="FOUNDED", s="Mira Murati", o="Thinking Machines Lab", active=True):
    return Fact(
        fact_id="f1", subject_id="ent_mira", subject_name=s, relation=rel,
        object_id="ent_tml", object_name=o, confidence=0.8, source_id="src_verge",
        source_name="The Verge", observed_at="2025-03-05T00:00:00Z",
        valid_from="2025-03-05T00:00:00Z", valid_to=None if active else "2025-06-01T00:00:00Z",
        active=active,
    )


def fake_passage(text="Mira Murati founded Thinking Machines Lab.", source="The Verge", sim=0.8):
    return Chunk(chunk_id="c1", document_id="d1", text=text, embedding_id="c1",
                 source=source, title="Murati launch", url="https://x.com", similarity=sim)


@pytest.fixture
def patched(monkeypatch):
    calls = {"graph": 0, "vector": 0, "entities": 0}

    monkeypatch.setattr(hybrid, "vector_search", lambda q, k=5, as_of=None: (calls.__setitem__("vector", calls["vector"] + 1) or [fake_passage()]))
    monkeypatch.setattr(hybrid, "extract_query_entities", lambda q: (calls.__setitem__("entities", calls["entities"] + 1) or ["OpenAI"]))
    monkeypatch.setattr(hybrid, "retrieve_facts", lambda names, **kw: (calls.__setitem__("graph", calls["graph"] + 1) or {"facts": [fake_fact()], "graph_path": None, "matched": ["Mira Murati"]}))
    monkeypatch.setattr(hybrid, "get_db", lambda: FakeDB())
    return calls


def test_vector_mode_does_not_use_graph(patched):
    bundle = retrieve("Who founded Thinking Machines?", "vector")
    assert patched["graph"] == 0 and patched["entities"] == 0
    assert patched["vector"] == 1
    assert bundle["facts"] == [] and bundle["passages"]


def test_graph_mode_does_not_use_vector(patched):
    bundle = retrieve("Who founded Thinking Machines?", "graph")
    assert patched["vector"] == 0
    assert patched["graph"] == 1 and patched["entities"] == 1
    assert bundle["facts"] and bundle["passages"] == []


def test_hybrid_combines_both(patched):
    bundle = retrieve("Who founded Thinking Machines?", "hybrid")
    assert patched["vector"] == 1 and patched["graph"] == 1
    assert bundle["facts"] and bundle["passages"]


def test_latency_returned(patched, monkeypatch):
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "Stub grounded answer with cited sources.")
    result = answer_question("test", "hybrid")
    assert result.latency_ms > 0
    assert result.retrieval_mode == "hybrid"
    assert result.confidence > 0


def test_weak_evidence_expresses_uncertainty(monkeypatch, patched):
    monkeypatch.setattr(hybrid, "vector_search", lambda q, k=5: [])
    monkeypatch.setattr(hybrid, "retrieve_facts", lambda names, **kw: {"facts": [], "graph_path": None, "matched": []})
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "I have no evidence in the provided material, so I am uncertain.")
    result = answer_question("completely unknown topic", "graph")
    assert result.confidence < 0.4
    assert any(m in result.answer.lower() for m in ("uncertain", "limited", "no evidence"))


def test_answer_confidence_vector_mode_uses_reliability():
    conf, label = answer_gen.answer_confidence([], [fake_passage(source="Reuters", sim=0.9)], conflicts=0)
    # 0.5*0.95 + 0.3*0.5 + 0.2*0.9 = 0.805
    assert conf == 0.805 and label == "High"


def test_api_query_endpoint(patched, monkeypatch):
    fake_result = QueryResult(
        answer="Anthropic and Thinking Machines Lab.",
        facts=[fake_fact()], passages=[fake_passage()], sources=["The Verge"],
        confidence=0.82, confidence_label="High", retrieval_mode="hybrid", latency_ms=312.0,
    )
    monkeypatch.setattr(routes_query, "answer_question", lambda q, m, allow_live=False, as_of=None: fake_result)
    from app.main import fastapi_app

    client = TestClient(fastapi_app)
    resp = client.post("/query", json={"question": "Which companies did ex-OpenAI people found?", "retrieval_mode": "hybrid"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"].startswith("Anthropic")
    assert data["latency_ms"] == 312.0
    assert data["retrieval_mode"] == "hybrid"


def test_api_query_validation(patched):
    from app.main import fastapi_app

    client = TestClient(fastapi_app)
    resp = client.post("/query", json={"question": "   ", "retrieval_mode": "hybrid"})
    assert resp.status_code == 422
