from datetime import datetime, timezone

import pytest

from app.ingestion.chunker import chunk_text
from app.ingestion.cleaner import clean_document_text
from app.ingestion.pipeline import content_hash, ingest_documents
from app.llm import LLMError
from app.extraction.schemas import Document

from .fakes import FakeDB, FakeGraphWriter, FakeVectors, utc


def make_doc(doc_id, text, url="https://example.com/a", source="TechCrunch", published="2025-03-03T09:00:00Z"):
    return Document(
        document_id=doc_id, title=f"Title {doc_id}", url=url, source=source,
        published_at=datetime.fromisoformat(published.replace("Z", "+00:00")),
        retrieved_at=utc(2025, 3, 4), text=text,
    )


DOC_TEXT = (
    "<p>Mira Murati left OpenAI and founded Thinking Machines Lab in February.</p>"
    "<script>evil()</script>\n\n\n   She leads Thinking Machines Lab as chief executive.\n\n"
    "The company is in talks at a $2 billion valuation. https://tracking.example.com/pixel"
)


def test_cleaner_strips_html_and_noise():
    cleaned = clean_document_text(DOC_TEXT)
    assert "script" not in cleaned and "evil" not in cleaned
    assert "https://tracking" not in cleaned
    assert "Mira Murati left OpenAI" in cleaned
    assert "\n\n\n" not in cleaned


def test_chunker_sizes_and_overlap():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) >= 3
    assert all(len(c) <= 200 for c in chunks)
    assert chunk_text("   ")[0:0] == []


def test_pipeline_ingests_document_end_to_end():
    graph, db, vectors = FakeGraphWriter(), FakeDB(), FakeVectors()
    docs = [make_doc("d1", "Ilya Sutskever founded Safe Superintelligence in March 2025. " * 3)]
    summary = ingest_documents(docs, graph=graph, db=db, vectors=vectors, run_id="r1")
    assert summary.documents_added == 1
    assert summary.documents_failed == 0
    assert len(vectors.added) >= 1
    assert summary.relationships_added >= 1
    assert any(f["relation"] == "FOUNDED" for f in graph.facts.values())


def test_pipeline_skips_duplicate_document():
    graph, db, vectors = FakeGraphWriter(), FakeDB(), FakeVectors()
    docs = [make_doc("d1", "Ilya Sutskever founded Safe Superintelligence in March 2025. " * 3)]
    ingest_documents(docs, graph=graph, db=db, vectors=vectors, run_id="r1")
    summary2 = ingest_documents(docs, graph=graph, db=db, vectors=vectors, run_id="r2")
    assert summary2.documents_skipped_duplicate == 1
    assert summary2.documents_added == 0


def test_pipeline_batch_continues_after_failure(monkeypatch):
    graph, db, vectors = FakeGraphWriter(), FakeDB(), FakeVectors()

    from app.extraction.schemas import Entity
    from app.trust.source_weights import entity_id_for

    def exploding_entities(text):
        if "Leike" in text:
            raise LLMError("malformed LLM extraction")
        return [
            Entity(entity_id=entity_id_for(n), name=n, type="Person" if n == "David Luan" else "Organization")
            for n in ("Amazon", "Adept AI", "David Luan") if n in text
        ]

    def relations(text, names, allowed):
        if "Amazon" not in text:
            return []
        return [
            {"subject": "Amazon", "relation": "ACQUIRED", "object": "Adept AI", "extraction_confidence": 0.9},
            {"subject": "David Luan", "relation": "WORKED_AT", "object": "Amazon", "extraction_confidence": 0.9},
        ]

    monkeypatch.setattr("app.ingestion.pipeline.extract_entities", exploding_entities)
    monkeypatch.setattr("app.ingestion.pipeline.extract_relations", relations)

    good = make_doc("good", "Amazon acquired Adept AI in April 2025. David Luan now works at Amazon. " * 3)
    bad = make_doc("bad", "Jan Leike joined Anthropic after leaving OpenAI. " * 3, url="https://example.com/b")
    summary = ingest_documents([bad, good], graph=graph, db=db, vectors=vectors, run_id="r1")

    assert summary.documents_failed == 1
    assert summary.documents_added == 1
    assert any("bad" in e for e in summary.errors)
    assert summary.relationships_added == 2


def test_content_hash_stable():
    assert content_hash("abc", "u1") == content_hash("abc", "u1")
    assert content_hash("abc", "u1") != content_hash("abcd", "u1")
