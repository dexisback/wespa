# Architecture

## Components

| Component | Tech | Role |
|---|---|---|
| `app/api` | FastAPI | `/query`, `/ingest`, `/facts/{id}`, `/eval/*`, `/health`, static UI |
| `app/ingestion` | feedparser, httpx, BeautifulSoup | RSS/URL fetch, cleaning, chunking, pipeline orchestration |
| `app/extraction` | Groq (llama-3.3-70b) | strict-JSON entity + relation extraction, retry-once |
| `app/graph` | Neo4j driver | entity nodes, versioned fact edges, traversal, temporal decisions |
| `app/vector` | ChromaDB (embedded) | chunk embeddings + similarity search |
| `app/db` | PostgreSQL (psycopg2) | sources, documents, ingestion_logs, fact_audit, queries |
| `app/trust` | pure Python | source weights, locked confidence formula, contradiction rules |
| `app/rag` | — | three retrieval modes, grounded answer generation |
| `app/evaluation` | — | Hit@5/Recall@5, multi-hop accuracy, temporal + confidence checks |
| `frontend` | vanilla JS + vis-network | dark single-screen product UI |

## Data contracts (Pydantic, `app/extraction/schemas.py`)

`Document`, `Chunk`, `Entity`, `Fact`, `QueryResult`, `GraphPath`, `FactHistory`, `IngestionSummary`.
`QueryResult` extends the locked contract with `graph_path`, `source_cards`, `conflicts`,
`entities_matched`, `confidence_label` — nothing was removed.

## Neo4j model

```
(:Entity {id, name, type})
(:Source {id, name, reliability})
(:Document {id, title, url, published_at})
(:Source)-[:PUBLISHED]->(:Document)
(:Entity)-[:FOUNDED|WORKED_AT|LEFT|LEADS|ACQUIRED|INVESTED_IN|RELEASED|RAISED|VALUED_AT {
    fact_id, relation, confidence, extraction_confidence, source_id, document_id,
    observed_at, valid_from, valid_to, supersedes, corroborates, conflict
}]->(:Entity)
```

No article text is stored in Neo4j — the graph stays compact; text lives in Chroma/Postgres.

## Temporal logic (`trust/contradiction.py` + `graph/temporal_update.py`)

For an incoming fact vs. active facts of the same (subject, relation):

- **same object** → CORROBORATE (new observation edge, agreement ↑, confidence recomputed with n sources)
- **different object, observed > 7 days after existing** → SUPERSEDE (old edge gets `valid_to`, new edge inserted, audit written — history preserved, never deleted)
- **different object, observed ≤ 7 days apart** → CONFLICT (both stay active, both flagged, confidence penalized ×0.75, answer confidence ×0.8)
- otherwise → NEW

## Confidence

Fact level: `0.50*source_reliability + 0.30*cross_source_agreement + 0.20*extraction_confidence`,
clamped to [0,1]; agreement = 0.5 / 0.75 / 1.0 for 1 / 2 / 3+ sources.
Answer level: mean of top fact confidences (vector mode: reliability + relevance via the same weights),
reduced ×0.8 for conflicts and for weak evidence, then labeled High/Medium/Low for the UI.

## Retrieval paths (`rag/hybrid_retriever.py`)

- **vector**: Chroma top-5 passages only.
- **graph**: LLM entity extraction on the question → `graph_retriever.retrieve_facts` (seed match both
  directions, 2-hop expansion, active facts ranked first) → facts + `GraphPath{nodes, edges, paths}`.
- **hybrid**: both, facts ranked (active, confidence) and passages by similarity; the LLM answers from
  the merged evidence and must cite sources; insufficient evidence forces an explicit hedge.

## Frontend

Single screen: question bar → mode toggle (re-issues a real request per mode) → loading stages →
answer + confidence badge → source cards → fact chips (clickable → history drawer) → vis-network graph
with highlighted traversal path. Ingest button runs the live fixture; evaluation modal shows measured
numbers only.
