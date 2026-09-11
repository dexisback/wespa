# CONTEXT.md — AI Knowledge Memory Engine
### Build handover for autonomous coding agents (opencode)

> Read this entire file before writing any code. This is the single source of truth for
> architecture, scope, file layout, data contracts, and acceptance criteria. Do not deviate
> from the scope boundaries in Section 6 — they exist because this is a 24-hour build with a
> hard grading deadline, not a research project.

---

## 1. What we are building (one paragraph)

A system that behaves like **persistent, explainable memory** instead of a one-shot search
tool. It continuously ingests trusted sources, extracts structured facts (entities +
relationships), stores them in a **knowledge graph (Neo4j)** with confidence scores and
timestamps, stores raw text as **embeddings (ChromaDB)** for semantic recall, and answers
natural-language questions by combining graph traversal + vector search into one LLM-grounded
answer with visible sources, confidence, and — critically — a **timeline showing how facts
changed over time**. When a fact changes, the old value is never deleted; it's versioned.

The product is not "a chatbot with a vector DB." It is a memory system that can say: *"as of
March, source X said this; as of June, source Y updated it; here's both, and here's how
confident I am in each."*

---

## 2. Non-negotiable end-to-end pipeline

```
TRUSTED SOURCES
   → INGEST (RSS/curated URLs)
   → CLEAN/CHUNK
   → EXTRACT ENTITIES + RELATIONS (LLM, strict JSON schema)
   → NORMALIZE/DEDUP
   → CONFIDENCE + CONTRADICTION CHECK
   → WRITE TO: Neo4j (graph) + ChromaDB (vectors) + PostgreSQL (provenance/audit)

USER QUERY
   → QUERY ENTITY EXTRACTION
   → GRAPH TRAVERSAL (1–2 hop) + VECTOR SIMILARITY SEARCH
   → HYBRID CONTEXT MERGE + RANK
   → LLM ANSWER (grounded, must cite evidence, must express uncertainty if evidence is weak)
   → RESPONSE: answer + sources[] + confidence + fact timeline
```

Every stage in this pipeline must be demoable. A stage that exists but produces no visible
demo output is not done.

---

## 3. Tech stack (locked — do not substitute without team sign-off)

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Graph DB | Neo4j (Docker, local) | Entity nodes + typed relationship edges |
| Vector DB | ChromaDB | chunk + entity embeddings |
| Relational | PostgreSQL | sources, documents, ingestion_logs, fact_audit, queries |
| API | FastAPI | single service, `app/main.py` |
| UI | Streamlit **or** a lightweight React/HTML graph view (see `handover-team.md` for the visual upgrade plan) | must render an interactive graph, not just text |
| LLM | ONE provider only (pick one: OpenAI / Anthropic / local via Ollama — decide in hour 0 and do not switch) | used for extraction AND answer generation |
| Orchestration | Docker Compose for Neo4j + Postgres + Chroma (if not embedded) | |

---

## 4. Repository structure (create exactly this — agents should not invent alternate layouts)

```
ai-knowledge-memory-engine/
├── README.md
├── context.md                  # this file
├── handover-team.md            # team/professor-facing doc
├── .env.example
├── requirements.txt
├── docker-compose.yml
├── configs/settings.yaml
├── data/
│   ├── raw/
│   ├── processed/
│   └── eval/questions.json
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── routes_query.py
│   │   ├── routes_ingest.py
│   │   └── routes_facts.py
│   ├── ingestion/
│   │   ├── rss_ingester.py
│   │   ├── url_ingester.py
│   │   ├── cleaner.py
│   │   └── chunker.py
│   ├── extraction/
│   │   ├── schemas.py
│   │   ├── entity_extractor.py
│   │   └── relation_extractor.py
│   ├── graph/
│   │   ├── neo4j_client.py
│   │   ├── graph_writer.py
│   │   ├── graph_retriever.py
│   │   └── temporal_update.py
│   ├── vector/
│   │   ├── chroma_client.py
│   │   └── vector_retriever.py
│   ├── trust/
│   │   ├── source_weights.py
│   │   ├── confidence.py
│   │   └── contradiction.py
│   ├── rag/
│   │   ├── hybrid_retriever.py
│   │   ├── prompts.py
│   │   └── answer_generator.py
│   ├── db/
│   │   ├── postgres.py
│   │   └── models.py
│   └── evaluation/
│       ├── evaluate.py
│       └── metrics.py
├── frontend/
│   └── streamlit_app.py        # or react app if upgraded — see handover-team.md
├── scripts/
│   ├── seed_data.py
│   ├── build_memory.py
│   ├── run_query.py
│   └── run_evaluation.py
├── tests/
│   ├── test_ingestion.py
│   ├── test_graph.py
│   ├── test_confidence.py
│   └── test_query.py
└── docs/
    ├── architecture.md
    └── demo_script.md
```

---

## 5. Shared data contracts (lock these before writing any store/retrieve code)

```python
Document = {
    "document_id": str, "title": str, "url": str, "source": str,
    "published_at": datetime, "retrieved_at": datetime,
    "text": str, "source_reliability": float,
}

Chunk = {"chunk_id": str, "document_id": str, "text": str, "embedding_id": str}

Entity = {"entity_id": str, "name": str, "type": str}

Fact = {
    "fact_id": str, "subject_id": str, "relation": str, "object_id": str,
    "confidence": float, "source_id": str,
    "observed_at": datetime, "valid_from": datetime, "valid_to": Optional[datetime],
}

QueryResult = {
    "answer": str, "facts": list[Fact], "passages": list[Chunk],
    "sources": list[str], "confidence": float,
    "retrieval_mode": Literal["vector", "graph", "hybrid"],
    "latency_ms": float,
}

Source = {"source_id": str, "name": str, "domain": str, "reliability_weight": float}
```

---

## 6. Scope boundaries — what NOT to build

Agents must refuse to expand scope into any of the following, even if it seems easy:

- No general internet crawler — bounded technology-news domain, 20–50 seed documents only
- No custom-trained models
- No learned/ML credibility classifier — use the deterministic formula in Section 7
- No community detection / Leiden clustering
- No auth, user accounts, multi-tenancy
- Only ONE LLM provider
- No multi-agent swarm / agentic loops for extraction — single-pass LLM extraction with a strict schema is sufficient

---

## 7. Confidence formula (implement exactly, do not "improve" without discussion)

```
confidence = 0.50 * source_reliability + 0.30 * cross_source_agreement + 0.20 * extraction_confidence
```

Clamp to [0,1]. Single-source fact → agreement = 0.5. Corroborated fact → agreement increases.
On conflict: **retain both versions**, flag the conflict, lower effective answer confidence.
Never silently overwrite.

---

## 8. Neo4j schema

```
(:Entity {id, name, type})
(:Source {id, name, reliability})
(:Document {id, title, url, published_at})

(Entity)-[:RELATION {
    fact_id, confidence, source_id,
    valid_from, valid_to, observed_at
}]->(Entity)
```

Example: `(OpenAI)-[:DEVELOPED {confidence:0.94}]->(GPT-5)`

Do not store full article text in Neo4j — keep graph facts compact; text lives in Chroma/Postgres.

---

## 9. PostgreSQL tables

- `sources(source_id, name, domain, reliability_weight)`
- `documents(document_id, source_id, url, title, published_at, retrieved_at, content_hash)`
- `ingestion_logs(run_id, started_at, completed_at, documents_seen, documents_added, errors)`
- `fact_audit(fact_id, action, old_value, new_value, timestamp)`
- `queries(query_id, question, retrieval_mode, latency_ms, created_at)`

---

## 10. Ownership map (for agent task-splitting / parallel branches)

| Branch | Owner | Scope |
|---|---|---|
| `mihika/graph-memory` | Mihika | Neo4j client/writer, temporal versioning, confidence, contradiction detection, graph retrieval |
| `adrija/ingestion` | Adrija | RSS/URL ingestion, cleaning/chunking, entity/relation extraction, ChromaDB |
| `amaan/rag-api-ui` | Amaan | Hybrid retriever, prompts, answer generation, FastAPI, UI, evaluation |

Agents working on one branch should treat the data contracts in Section 5 as a hard interface
and not reach into another branch's internal implementation.

---

## 11. Definition of done (acceptance criteria per component)

- **Ingestion**: one command ingests the seed corpus; duplicates are skipped; failures on one
  document do not crash the batch.
- **Extraction**: LLM output always validates against the strict Pydantic schema; malformed
  output is retried once, then logged and skipped (never crashes the pipeline).
- **Graph**: inserting a new triple works; inserting a *changed* triple preserves history
  (`valid_to` set on old fact, new fact inserted with new `valid_from`); every returned fact
  includes source_id + timestamp + confidence.
- **Vector store**: Chroma contains retrievable evidence with `chunk_id`/`source_id` metadata attached.
- **Hybrid retrieval**: comparison mode returns distinguishably different result sets for
  vector-only vs graph-only vs hybrid on at least 3 of the demo questions.
- **API**: `POST /query` returns a `QueryResult`; `POST /ingest` triggers ingestion;
  `GET /facts/{id}` returns fact history. Latency is measured and returned.
- **UI**: question box → answer → evidence cards (source, confidence, timestamp) → graph view
  of the traversal path → toggle for vector/graph/hybrid comparison → visible latency.
- **Evaluation**: `run_evaluation.py` outputs Hit@5/Recall@5 on the curated question set, plus
  a temporal-correctness check (old fact still retrievable via history after an update) and a
  confidence-weighting check (low-reliability source produces visibly lower confidence).

---

## 12. Demo question set requirements

8–12 questions total, including **at least 3 multi-hop** questions requiring two graph edges
(these are the ones vector-only search will visibly fail or give a vague answer to — this
contrast is the single most important demo moment, see `handover-team.md`).

---

## 13. Build order (agent execution plan)

1. Hour 0: lock `.env.example`, `requirements.txt`, docker-compose (Neo4j + Postgres; Chroma
   can be embedded/local), shared data contracts as actual Pydantic models in `app/extraction/schemas.py` and `app/db/models.py`.
2. Bring up all three stores and verify health-check connections before any pipeline code.
3. Build ingestion → extraction → graph write → vector write as one straight-line path first,
   proven on 3–5 documents end-to-end, before scaling to the full 20–50 doc seed set.
4. Build hybrid retrieval + answer generation against that small proven dataset.
5. Only then build the comparison-mode UI, evaluation harness, and visual polish.
6. Freeze architecture once end-to-end works — remaining time is bug fixes + UI/demo polish only.

---

## 14. Final pitch line agents should keep in mind when naming things/writing docstrings

*"Traditional RAG remembers documents only for the current query. This system builds
persistent memory: it continuously learns from trusted sources, turns information into a
connected knowledge graph, tracks how facts change over time, and assigns confidence based on
source reliability and corroboration."*

Code comments, docstrings, and log messages should reflect this framing where natural (e.g.
log lines like `"preserving prior fact version, not overwriting"` rather than generic
`"updating record"`) — it costs nothing and reinforces the narrative if a professor reads the code.
