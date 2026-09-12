"""Memory-first → live-retrieval fallback tests.

Covers the required behaviors:
1. sufficient memory -> no live retrieval
2. insufficient memory + fetch enabled -> live retrieval + re-retrieval
3. insufficient memory + fetch disabled -> no retrieval, transparent response
4. successful retrieval -> ingestion -> re-retrieval (answer from updated memory)
5. failed retrieval -> graceful failure, no crash
6. insufficient evidence after retrieval -> explicit still-insufficient answer
7. duplicate source -> no duplicate memory
8. fact update via live ingestion -> old version preserved
"""
import pytest

import app.ingestion.web_search as web_search
import app.rag.answer_generator as answer_gen
import app.rag.hybrid_retriever as hybrid
from app.extraction.schemas import Chunk, Fact
from app.rag.hybrid_retriever import _sufficiency, answer_question

from .fakes import FakeDB


def _fact(rel="FOUNDED", s="Mira Murati", o="Thinking Machines Lab", fid="f1"):
    return Fact(
        fact_id=fid, subject_id="ent_" + s.lower().replace(" ", "_"), subject_name=s, relation=rel,
        object_id="ent_" + o.lower().replace(" ", "_"), object_name=o, confidence=0.85,
        source_id="src_verge", source_name="The Verge", observed_at="2025-03-05T00:00:00Z",
        valid_from="2025-03-05T00:00:00Z", valid_to=None, active=True,
    )


def _passage(text="Mira Murati founded Thinking Machines Lab.", source="The Verge", sim=0.8):
    return Chunk(chunk_id="c1", document_id="d1", text=text, embedding_id="c1",
                 source=source, title="t", url="https://example.com/a", similarity=sim)


@pytest.fixture
def rich_memory(monkeypatch):
    """Memory retrieval returns strong, topically relevant evidence."""
    calls = {"live": 0, "retrievals": 0}
    monkeypatch.setattr(hybrid, "vector_search", lambda q, k=5, as_of=None: [_passage("Mira Murati founded Thinking Machines Lab.")])
    monkeypatch.setattr(hybrid, "extract_query_entities", lambda q: ["Mira Murati"])
    monkeypatch.setattr(hybrid, "retrieve_facts", lambda names, **kw: (calls.__setitem__("retrievals", calls["retrievals"] + 1) or {"facts": [_fact()], "graph_path": None, "matched": ["Mira Murati"]}))
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "Mira Murati founded Thinking Machines Lab (Source: The Verge).")
    monkeypatch.setattr(hybrid, "get_db", lambda: FakeDB())
    monkeypatch.setattr(web_search, "live_retrieval", lambda q, max_docs=2: (calls.__setitem__("live", calls["live"] + 1) or {}))
    return calls


@pytest.fixture
def empty_memory(monkeypatch):
    """Memory retrieval returns nothing relevant."""
    calls = {"live": 0, "retrievals": 0}
    monkeypatch.setattr(hybrid, "vector_search", lambda q, k=5, as_of=None: [])
    monkeypatch.setattr(hybrid, "extract_query_entities", lambda q: ["Unknown Topic"])
    monkeypatch.setattr(hybrid, "retrieve_facts", lambda names, **kw: (calls.__setitem__("retrievals", calls["retrievals"] + 1) or {"facts": [], "graph_path": None, "matched": []}))
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "I have no evidence in the provided material, so I am uncertain.")
    monkeypatch.setattr(hybrid, "get_db", lambda: FakeDB())
    return calls


def _live_ok_summary(**overrides):
    base = {
        "ok": True,
        "run_id": "run_x",
        "documents_seen": 1, "documents_added": 1, "documents_skipped_duplicate": 0,
        "documents_failed": 0, "entities_added": 2, "relationships_added": 3,
        "facts_corroborated": 0, "facts_superseded": 0, "facts_deleted": 0,
        "conflicts_flagged": 0, "chunks_embedded": 1, "errors": [], "message": "ingested",
        "searched_urls": ["https://example.com/new"], "fetched_sources": [
            {"url": "https://example.com/new", "title": "New article", "source": "Example"},
        ],
    }
    base.update(overrides)
    return base


# 1. sufficient memory -> no live retrieval
def test_sufficient_memory_skips_live_retrieval(rich_memory):
    assert rich_memory["live"] == 0
    r = answer_question("Which companies did Mira Murati found?", "hybrid")
    assert r.memory_sufficient is True
    assert r.live_retrieval_used is False
    assert rich_memory["live"] == 0
    assert r.live_fetch is None
    assert r.memory_reason  # human-readable reason present


# 2+4. insufficient + enabled -> live retrieval -> ingest -> re-retrieval
def test_insufficient_with_fetch_triggers_learning(monkeypatch):
    """First ask: memory empty -> learn from web -> re-retrieval finds the new evidence."""
    live_calls = {"n": 0}
    retrievals = {"n": 0}

    def fake_retrieve_facts(names, **kw):
        retrievals["n"] += 1
        if retrievals["n"] == 1:
            return {"facts": [], "graph_path": None, "matched": []}
        # after "learning", the memory now contains the answer
        return {"facts": [_fact(rel="LAUNCHED", s="Maruti Suzuki", o="Alto K10", fid="f_new")], "graph_path": None, "matched": ["Maruti Suzuki"]}

    def fake_vector_search(q, k=5, as_of=None):
        vs["n"] += 1
        if vs["n"] == 1:
            return []
        return [Chunk(chunk_id="c_new", document_id="d_new", text="Maruti Suzuki launched the new Alto K10 hatchback.",
                      embedding_id="c_new", source="Example", title="New article", url="https://example.com/new", similarity=0.8)]

    vs = {"n": 0}
    monkeypatch.setattr(hybrid, "vector_search", fake_vector_search)
    monkeypatch.setattr(hybrid, "extract_query_entities", lambda q: ["Maruti Suzuki"])
    monkeypatch.setattr(hybrid, "retrieve_facts", fake_retrieve_facts)
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "The Maruti Suzuki lineup includes the Alto K10 (Source: Example).")
    monkeypatch.setattr(web_search, "live_retrieval", lambda q, max_docs=2: (live_calls.__setitem__("n", live_calls["n"] + 1) or _live_ok_summary()))
    monkeypatch.setattr(hybrid, "get_db", lambda: FakeDB())

    r = answer_question("What is the current Maruti Suzuki car lineup?", "hybrid", allow_live=True)
    assert live_calls["n"] == 1                # live retrieval happened once
    assert retrievals["n"] == 2                # memory searched again after learning
    assert r.live_retrieval_used is True
    assert r.memory_updates["documents_added"] == 1
    assert r.memory_updates["relationships_added"] == 3
    assert r.memory_sufficient is True         # post-update sufficiency verdict
    assert r.still_insufficient is False
    assert "Answer grounded in updated memory" in [s.name for s in r.pipeline]
    assert any(s.name == "Memory insufficient" for s in r.pipeline)
    assert any(s.name == "Memory updated" for s in r.pipeline)
    assert any(c.is_new for c in r.source_cards)  # new source flagged
    assert r.sources_fetched[0]["url"] == "https://example.com/new"
    assert "still weren't sufficient" not in r.answer


# 3. insufficient + fetch disabled -> transparent response, no external fetch
def test_fetch_disabled_no_retrieval(monkeypatch, empty_memory):
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "I have no evidence in the provided material, so I am uncertain.")
    r = answer_question("What is the current Maruti Suzuki car lineup?", "hybrid", allow_live=False)
    assert empty_memory["live"] == 0
    assert r.live_retrieval_used is False
    assert r.memory_sufficient is False
    assert "no evidence" in r.memory_reason or "thin" in r.memory_reason or "low-confidence" in r.memory_reason
    assert "uncertain" in r.answer.lower() or "no reliable evidence" in r.answer.lower()


# 5. retrieval fails -> graceful failure, no crash
def test_failed_retrieval_graceful(monkeypatch, empty_memory):
    def boom(q, max_docs=2):
        empty_memory["live"] += 1
        raise RuntimeError("network down")
    monkeypatch.setattr(web_search, "live_retrieval", boom)
    r = answer_question("What is the current Maruti Suzuki car lineup?", "hybrid", allow_live=True)
    assert empty_memory["live"] == 1
    assert "couldn't fetch new evidence" in r.answer.lower()
    assert r.confidence <= 0.25
    assert any(s.name == "Couldn't fetch new evidence" for s in r.pipeline)
    assert r.live_fetch["ok"] is False


# 6. evidence still insufficient after learning -> explicit, no hallucination
def test_still_insufficient_after_learning(monkeypatch, empty_memory):
    # even after "learning", memory stays empty (fetched pages had no useful text)
    summary = _live_ok_summary(ok=True, documents_added=1)
    monkeypatch.setattr(web_search, "live_retrieval", lambda q, max_docs=2: (empty_memory.__setitem__("live", empty_memory["live"] + 1) or summary))
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "I have no evidence in the provided material, so I am uncertain.")
    r = answer_question("What is the current Maruti Suzuki car lineup?", "hybrid", allow_live=True)
    assert r.live_retrieval_used is True
    assert r.still_insufficient is True
    assert "still weren't sufficient" in r.answer
    assert r.memory_sufficient is False
    assert any(s.name == "Evidence still insufficient" for s in r.pipeline)


# 7. duplicate source -> no duplicate memory (SHA256 protection via pipeline)
def test_duplicate_protection(tmp_path, monkeypatch):
    from app.extraction.schemas import Document
    from app.ingestion.pipeline import ingest_documents
    from app.db.postgres import get_db

    db = get_db()
    doc = Document(
        document_id="dup_test_doc", title="Duplicate test", url="https://example.com/dup",
        source="Example", published_at=__import__("datetime").datetime(2025, 1, 1, tzinfo=__import__("datetime").timezone.utc),
        retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        text="Maruti Suzuki launched the new Alto K10 hatchback in 2025 with a 1.0L engine and improved mileage.",
        source_reliability=0.6,
    )
    # force-clean any previous run of this test
    with db.conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE document_id='dup_test_doc'")
    db.conn.commit()

    s1 = ingest_documents([doc])
    s2 = ingest_documents([doc])
    assert s1.documents_added == 1
    assert s2.documents_added == 0
    assert s2.documents_skipped_duplicate == 1


# 8. fact update via ingestion -> old version preserved, new active
def test_fact_update_preserves_history():
    from datetime import datetime, timedelta, timezone
    from app.graph.graph_writer import GraphWriter

    w = GraphWriter()
    now = datetime.now(timezone.utc)
    old = w.ingest_fact(
        subject_id="ent_testco", subject_name="TestCo", relation="VALUED_AT",
        object_id="ent_20b", object_name="$20 Billion", subject_type="Organization", object_type="Money",
        source_id="src_a", source_name="Source A", document_id="dup_test_doc2",
        observed_at=now, extraction_confidence=0.9, reliability=0.9,
    )
    newer = w.ingest_fact(
        subject_id="ent_testco", subject_name="TestCo", relation="VALUED_AT",
        object_id="ent_15b", object_name="$15 Billion", subject_type="Organization", object_type="Money",
        source_id="src_b", source_name="Source B", document_id="dup_test_doc3",
        observed_at=now + timedelta(days=8), extraction_confidence=0.9, reliability=0.95,
    )
    old_fact = w.get_fact(old["fact_id"])
    new_fact = w.get_fact(newer["fact_id"])
    try:
        assert old_fact["valid_to"] is not None          # closed, not deleted
        assert new_fact["valid_to"] is None              # current belief
        assert old_fact["fact_id"] in (new_fact.get("supersedes") or [])
    finally:
        # clean up test entities so the demo graph stays clean
        w.g.run("MATCH (e:Entity {id:'ent_testco'}) DETACH DELETE e")
        w.g.run("MATCH (e:Entity {id:'ent_20b'}) DETACH DELETE e")
        w.g.run("MATCH (e:Entity {id:'ent_15b'}) DETACH DELETE e")


# sufficiency verdict details
def test_sufficiency_reasons():
    v = _sufficiency([], [], 0.15, "What is the current Maruti Suzuki car lineup?")
    assert v["sufficient"] is False and v["evidence_count"] == 0
    v = _sufficiency([_fact()], [], 0.5, "Who founded Thinking Machines Lab?")
    assert v["sufficient"] is False and "thin" in v["reason"]
    v = _sufficiency([_fact(fid="f2")], [_passage("Mira Murati founded Thinking Machines Lab")], 0.7, "What is the current Maruti Suzuki car lineup?")
    assert v["sufficient"] is False and ("requested topic" in v["reason"] or "relevance" in v["reason"])
    v = _sufficiency([_fact(fid="f3")], [_passage("Mira Murati founded Thinking Machines Lab, her company")], 0.75, "Who founded Thinking Machines Lab?")
    assert v["sufficient"] is True
    # single precise on-topic fact IS sufficient (e.g. a direct valuation fact)
    v = _sufficiency([_fact(rel="VALUED_AT", s="Safe Superintelligence", o="$32 Billion", fid="f4")], [], 0.8,
                     "What is Safe Superintelligence valued at?")
    assert v["sufficient"] is True


# REGRESSION: irrelevant retrieved passages + fetch enabled -> live retrieval MUST trigger
def test_irrelevant_passages_trigger_live_retrieval(monkeypatch):
    """The Kia bug: memory retrieval returns passages about Maruti/Tata for a Kia
    question — memory_sufficient must be False and live retrieval must fire."""
    live_calls = {"n": 0}
    monkeypatch.setattr(hybrid, "vector_search", lambda q, k=5, as_of=None: [
        _passage("Maruti Suzuki launched the new Alto K10 hatchback in India.", source="Autoblog"),
        _passage("Tata Motors announced its EV expansion plans for the year.", source="Autoblog"),
        _passage("Maruti's new SUV sales crossed a milestone.", source="Autoblog"),
    ])
    monkeypatch.setattr(hybrid, "extract_query_entities", lambda q: ["Kia"])
    monkeypatch.setattr(hybrid, "retrieve_facts", lambda names, **kw: {"facts": [], "graph_path": None, "matched": []})
    monkeypatch.setattr(answer_gen, "chat", lambda *a, **k: "Grounded stub answer.")
    monkeypatch.setattr(web_search, "live_retrieval", lambda q, max_docs=3: (live_calls.__setitem__("n", live_calls["n"] + 1) or _live_ok_summary()))
    monkeypatch.setattr(hybrid, "get_db", lambda: FakeDB())

    r = answer_question("give me all model lineup of kia motor cars", "hybrid", allow_live=True)
    assert r.memory_sufficient is False or r.still_insufficient is True
    assert live_calls["n"] == 1, "live retrieval MUST trigger for irrelevant evidence"
    assert r.live_retrieval_used is True
    # the "Memory found" stage must NOT appear for irrelevant evidence
    assert "Memory found" not in [s.name for s in r.pipeline]
    assert any(s.name == "Memory insufficient" or s.name == "Fetching new evidence" for s in r.pipeline)
    assert "kia" in r.memory_reason
