# AI Knowledge Memory Engine — Build Report

**Date:** September 12, 2026
**Status:** ✅ Complete — all 10 build phases implemented, APIs verified live, evaluation passing, demo hardened.
**Update (same day):** `next.md` roadmap Phase 1–5 implemented — memory-first live-retrieval fallback, knowledge-impact analysis, stale-answer detection, time-travel queries, contradiction intelligence, and the full product-polish UI pass. Details in §9.

---

## 1. What this is

An explainable, persistent memory engine for AI questions. Instead of a chatbot that
searches text, the system builds, maintains, and explains its own structured memory:

- **Ingests** technology-news documents into three stores
- **Extracts** entities and (subject, relation, object) facts with source provenance
- **Maintains** memory over time: supersession, corroboration, contradiction — nothing is deleted
- **Answers** questions in three retrieval modes with citations, confidence, latency,
  and the **actual graph traversal path** used to find the evidence

**Question that motivates the design:**
> "Which companies did people who left OpenAI go on to found?"

This is unanswerable by pure text search — it needs a graph: person `LEFT` OpenAI →
person `FOUNDED` company.

---

## 2. Stack

| Layer | Technology |
|---|---|
| Language / API | Python 3.12, FastAPI |
| Graph store | Neo4j 5 (Docker, facts as timestamped relationships) |
| Relational store | PostgreSQL 16 (Docker: documents, sources, audit log, query log) |
| Vector store | ChromaDB (embedded, persisted to `data/chroma/`) |
| Embeddings | `all-MiniLM-L6-v2` (sentence-transformers, local) |
| LLM | Groq `openai/gpt-oss-120b` |
| Frontend | Single-screen dark UI (vanilla JS + vendored vis-network) |
| Config | `.env` + `configs/settings.yaml` |

---

## 3. What was built

### 3.1 Ingestion pipeline (`app/ingestion/`, `app/extraction/`)
- **Three ingest modes:** fixture file, raw payload (`POST /ingest`), URL fetch.
- **Pipeline:** clean → chunk (900 chars / 120 overlap) → embed → extract → normalize →
  temporal graph write → vector write → provenance → audit log.
- **Extraction:** LLM-based entity + relation extraction with a strict JSON schema,
  allowed-relation normalization (synonym map), and an explicit rule:
  `FOUNDED` only when the text says founded; "joined / now at" is `WORKED_AT`.
- **Deterministic fallback extraction** (added for offline mode): regex entity
  extraction (capitalized runs, `$N Billion` money amounts, sentence-boundary aware,
  short-name dedup) and sentence-pattern relation extraction — so ingestion works
  with no LLM and still produces corroborations/supersedes.
- **Duplicate protection:** sha256 content hash (`url|text`); re-ingesting the same
  fixture is skipped.

### 3.2 Graph memory (`app/graph/`)
- **Fact = timestamped relationship** in Neo4j with `fact_id`, `source_id`, `document_id`,
  `observed_at`, `valid_from`, `valid_to`, `extraction_confidence`, `confidence`,
  `conflict`, `supersedes`.
- **Temporal classification per new fact:** CORROBORATE (same triple, cross-source
  agreement 0.5 → 0.75 → 1.0), SUPERSEDE (same subject+relation, newer object,
  observed ≤ 7 days — old version closed with `valid_to`, never deleted), CONFLICT
  (different object within the window — both kept and flagged, fact ×0.75).
- **Graph retrieval:** 1–2 hop bidirectional traversal from matched seed entities,
  returns facts + `GraphPath` payload (nodes, edges, highlighted paths) for the UI.
- **Entity matching** allows partials ("david" → "David Luan").

### 3.3 Vector retrieval (`app/vector/`)
- Chroma collection with chunk metadata (source, title, url, published_at),
  similarity-ranked passages.

### 3.4 RAG (`app/rag/`)
- **Three genuinely different modes** — `vector` (passages only), `graph` (facts +
  traversal path only), `hybrid` (merged evidence).
- **Grounded answer generation:** the LLM answers only from supplied evidence,
  with inline citations. No invented facts.
- **Confidence:** locked formula `0.5·source_reliability + 0.3·cross_source_agreement
  + 0.2·extraction_confidence`; conflict penalty ×0.75 on facts, ×0.8 on answers;
  weak evidence (≤1 item) forces a low-confidence hedge.
- **Ranking (overhauled):** weighted composite evidence score — active status,
  2-hop chain membership, **relation-intent** (question verbs mapped to relation labels),
  entity-name overlap, **temporal window parsing** ("April 2025" boosts facts observed
  in that window), confidence, corroboration count. This lifted graph Hit@5 from 0.5 → 0.9
  and multi-hop accuracy 0.75 → 1.00.

### 3.5 API (`app/api/`, `app/main.py`)
| Endpoint | Purpose |
|---|---|
| `GET /health` | all stores ready (`postgres`, `neo4j`, `chroma`, `groq_key`) |
| `GET /stats` | entity/fact/document counts |
| `POST /query` | question + `retrieval_mode` → answer, facts, passages, sources, confidence, latency, graph_path |
| `POST /ingest` | fixture / raw / url modes → `IngestionSummary` (added, skipped, superseded, corroborated, conflicts) |
| `GET /facts/{id}` | full temporal version history + audit trail |
| `GET /ingest/fixtures` | list available demo fixtures |
| `POST /eval/run` · `GET /eval/results` | run/read the evaluation suite |

### 3.6 Frontend (`frontend/`)
- Single-screen experience: question box → loading stages → answer with confidence
  badge, source cards, latency; live **graph visualization** of the actual retrieval
  path (path nodes/edges highlighted); mode toggle (Hybrid / Graph / Vector);
  fact-chip → **temporal history drawer**; ingestion panel with toasts; evaluation panel.
- Stages reflect real backend stages; no fake loading sequences.

### 3.7 Trust & confidence (`app/trust/`)
- Source reliability table (Reuters 0.95, TechCrunch 0.88, …) in settings.
- Confidence formula, agreement table, conflict handling — unit-tested to exact values.

### 3.8 PostgreSQL provenance (`app/db/`)
- `documents`, `sources`, `ingestion_logs`, `fact_audit` (SUPERSEDES / CORROBORATES /
  CONFLICT records), `queries` log. Auto-creates schema on startup.

### 3.9 Evaluation (`app/evaluation/`, `data/eval/questions.json`)
- 10 curated questions (4 multi-hop) with expected facts/keywords.
- Measures Hit@5, Recall@5 (per mode), multi-hop accuracy, latency,
  temporal correctness (old version retrievable with `valid_to`), confidence weighting.

---

## 4. The rate-limit problem and how it was solved

The Groq free tier is token-capped (~8,000 tokens/min). Building the full memory with
the LLM pipeline (`scripts/build_memory.py`) kept tripping the cap mid-run, which would
also make the live demo fragile.

**Solution — three layers of graceful degradation:**

1. **`scripts/bootstrap_memory.py`** — deterministic bootstrap that loads the curated
   20-document corpus + hand-verified extraction results (`data/processed/seed_facts.json`)
   directly into all three stores. No LLM. Idempotent. Produces a fully populated,
   demo-ready memory in seconds.
2. **`SKIP_LLM=true` (`.env`)** — runtime switch that skips every Groq call:
   queries use deterministic entity seeding + a templated answer assembled from the
   retrieved facts/passages (still fully cited, with confidence and temporal state).
   Queries drop from ~25 s to ~400 ms.
3. **Fallbacks in every LLM call path** — if the LLM is unavailable at runtime
   (429s, network), entity extraction and answer generation fall back to the same
   deterministic paths. The demo cannot dead-end; the UI stays usable.

LLM mode re-enables with `SKIP_LLM=false` + server restart, and was verified working
(see §6).

---

## 5. Verification performed

### 5.1 Test suite
`python3 -m pytest tests/ -q` → **27 passed** (confidence formula, ingestion incl.
duplicate-skip and batch-failure isolation, temporal graph behavior, query API contract).

### 5.2 Live API tests (against running server)

| Endpoint | Result |
|---|---|
| `GET /health` | all stores ready |
| `GET /stats` | 51 entities · 86 facts (80 active) · 22 documents (base corpus + live test docs) |
| `POST /query` hybrid | ~400 ms offline mode, deterministic confidence 0.809 High across repeated runs |
| `POST /query` modes | isolation confirmed: vector → no facts; graph → no passages |
| `POST /ingest` (re-fixture) | duplicate skipped (`documents_skipped_duplicate: 1`) |
| `POST /ingest` (new raw doc) | +4 entities, +8 relations, 2 superseded, 3 corroborated — fully offline |
| `GET /facts/{id}` | 3 temporal versions retained; old $20B valuation closed with `valid_to` (June 12) |
| `POST /eval/run` | full suite in ~210 ms |
| Frontend | index/app.js/styles.css/vis-network all 200; graph renders actual retrieval path |

### 5.3 Full rebuild test (from-scratch recovery)
`docker compose down -v` + wiped `data/chroma` + fresh `up` →
`bootstrap_memory.py` → server → all endpoints verified again. The system is
reproducible from empty volumes to demo-ready in ~2 minutes.

### 5.4 Evaluation results (final)

| Metric | vector | graph | hybrid |
|---|---|---|---|
| **Hit@5** | **1.00** | **0.90** | **0.90** |
| **Recall@5** | 0.95 | 0.70 | 0.70 |

- **Multi-hop accuracy: 1.00** (4/4 multi-hop questions fully answered)
- **Temporal correctness: PASS** — superseded fact versions remain retrievable with `valid_to` set
- **Confidence weighting: PASS** — low-reliability sources produce visibly lower confidence
- **Avg latency:** ~210 ms (offline mode); ~10 s with real LLM generation

### 5.5 Real-LLM mode verification
With a working Groq key and `SKIP_LLM=false`, the same demo question returned a proper
prose answer with inline citations, confidence **0.914 "Very high"**, latency ~10 s.

---

## 6. Current state

- Server running at `http://localhost:8000` (`SKIP_LLM=false` — real LLM answers).
- Docker: `memory-neo4j`, `memory-postgres` healthy; volumes `wespa_neo4j_data`, `wespa_pg_data`.
- Data bootstrapped and demo-ready; demo questions return deterministic, stable results.
- Docs: `README.md` (architecture + Mermaid diagrams), `docs/architecture.md`,
  `docs/demo_script.md` (3-minute pitch flow), `.env.example`.

### Operating modes

| Mode | `.env` setting | Behavior |
|---|---|---|
| Real LLM | `SKIP_LLM=false` | Full extraction + prose answers (~10 s/query, free-tier pacing) |
| Offline demo | `SKIP_LLM=true` | Zero Groq calls, ~400 ms, templated but fully-cited answers |
| Automatic fallback | either | Any LLM failure falls back to deterministic paths mid-flight |

### Known trade-offs / limitations
- Free-tier LLM pacing (8 s minimum gap between calls, reset-aware retries) makes
  real-LLM queries noticeably slower than offline mode.
- Deterministic fallback extraction is regex-based — it covers the demo corpus well
  (founded/leads/left/acquired/invested/raised/valued patterns) but is not a substitute
  for the LLM extractor on arbitrary text.
- Corroboration currently creates a new fact row (older row keeps `valid_to` semantics
  via SUPERSEDE-style closure where applicable); confidence accounts for it via the
  agreement term.
- Eval's money-edge question (q07) expects the `INVESTED_IN → $8 Billion` edge form;
  the engine also represents the claim as `INVESTED_IN Anthropic`, so exact-match Hit@5
  is conservative there.

### Gotchas worth remembering
- `.env` is read **once at server startup** — restart after any change.
- In this environment the binary is `python3` (`python` may not exist).
- `docker compose down -v` wipes both data volumes; rebuild = `up -d` →
  `python3 scripts/bootstrap_memory.py` → start server.
- Server stop/start/check:
  `pkill -f "uvicorn app.main"` · `curl -s localhost:8000/health` ·
  `ps aux | grep "[u]vicorn"` · `tail -f /tmp/opencode/server.log`

---

## 7. Repository map

```
wespa/
├── README.md · report.md · context.md · build.md · handover-team.md
├── .env · .env.example · requirements.txt · docker-compose.yml
├── configs/settings.yaml
├── data/
│   ├── raw/seed_corpus.json          # 20 curated documents (Mar–May 2025)
│   ├── raw/fixtures/                 # live-ingestion demo fixture (June update)
│   ├── processed/seed_facts.json     # hand-verified extraction for bootstrap
│   ├── chroma/                       # vector store (persisted)
│   └── eval/questions.json           # 10 eval questions (4 multi-hop)
├── app/
│   ├── main.py                       # FastAPI app + health/stats/eval + static UI
│   ├── api/                          # routes_query · routes_ingest · routes_facts
│   ├── ingestion/                    # pipeline · cleaner · chunker · rss_ingester · url_ingester
│   ├── extraction/                   # schemas · entity_extractor · relation_extractor (LLM + fallbacks)
│   ├── graph/                        # neo4j_client · graph_writer · graph_retriever · temporal_update
│   ├── vector/                       # chroma_client · vector_retriever
│   ├── trust/                        # source_weights · confidence · contradiction
│   ├── rag/                          # hybrid_retriever · prompts · answer_generator
│   ├── db/                           # postgres · models (DDL)
│   ├── evaluation/                   # evaluate · metrics
│   ├── llm.py                        # Groq client (pacing, retries, reset-aware 429 handling)
│   └── config.py
├── frontend/                         # index.html · app.js · styles.css · vendor/vis-network
├── scripts/                          # bootstrap_memory · build_memory · run_evaluation · run_query · seed_data
├── tests/                            # 27 tests: confidence · ingestion · graph-temporal · query
└── docs/                             # architecture.md · demo_script.md
```

---

## 8. Next-phase implementation (`next.md` roadmap, September 12, 2026)

### 9.1 Memory-First → Live Retrieval Fallback (Phase 2)
- `POST /query` now takes `allow_live` — when memory can't answer, the system says so,
  searches the web (DuckDuckGo HTML, no API key), fetches and ingests the results,
  and answers from the updated memory. Memory-first, never search-first.
- **Sufficiency check** (`app/rag/hybrid_retriever.py`): 2+ evidence items, ≥ 0.45 confidence,
  AND topical relevance ≥ 0.35 (question content-words present in evidence) — memory containing
  adjacent material is not the same as containing the answer.
- Real verified flow: question about "universal basic compute" (not in memory) →
  `Memory found → Memory insufficient → Fetching new evidence → Memory updated (+1 source, +2 relationships, 1 corroboration) → Answer grounded in updated memory`, 33 s end-to-end.
- Pipeline stages are real and returned in the response (`pipeline[]`) and rendered in the UI.

### 9.2 Knowledge Impact Analysis + Stale Answer Detection (Phase 3)
- Every answer now logs its **fact dependencies** (`answer_facts` table in Postgres).
- When a fact is superseded/conflicted, the impact engine (`app/impact/impact.py`) finds:
  previous answers that cited it → marked **CURRENT / POTENTIALLY STALE / INVALIDATED**
  with a human-readable reason; dependent facts sharing an endpoint; the successor fact.
- New endpoints: `GET /impact/fact/{id}`, `GET /impact/recent`, `GET /impact/answer/{query_id}`
  (re-check any previous answer against current memory).
- Verified: the SSI $20B fact → successor $32B, "4 previous answers may be stale ·
  8 related facts depend on this".

### 9.3 Time-Travel / "What did we know then?" (Phase 4)
- `POST /query` accepts `as_of` — graph traversal and vector search filter to facts/passages
  whose validity/publication window covers that date; the answer prompt reconstructs in past tense.
- Verified: `as_of=2025-06-01` returns the $20B valuation and excludes the future $32B fact.
- The answer card shows an "⏱ Reconstruction as of DATE" tag.

### 9.4 Contradiction Intelligence (Phase 5)
- `GET /facts/{id}` now returns a **resolution** block: every competing claim for the same
  (subject, relation) with source, reliability, date, corroborations, status, plus
  human-readable "why the current belief wins" (newer observation · more reliable source ·
  more corroborations). Rendered in the fact drawer.

### 9.5 Product-polish UI (Phase 1)
- **Memory Status bar** — persistent header strip: entities, facts (active/historical/conflicts),
  sources, corroborated count, last-updated time; refreshes every 30 s. Extended `/stats`.
- **"Why this answer?" panel** — confidence breakdown bars (source reliability /
  cross-source agreement / extraction confidence) + supporting/conflicting source counts.
- **Graph path animation** — the exact answer path lights up edge-by-edge in sequence;
  primary-path nodes/edges render brighter than traversal/context nodes; the note line
  spells out the chain ("OpenAI → Ilya Sutskever → Safe Superintelligence").
- **Temporal timeline** — fact drawer restyled as a vertical timeline (dots + line),
  superseded/current tags, competing-claims section, impact button.
- **Mode comparison modal** — runs the same question through Vector/Graph/Hybrid side by side
  (evidence counts, latency, confidence, answer excerpts, path size); explicitly does not claim
  one mode is always best.
- **Ingestion UX** — URL input ("Add to memory") alongside fixtures; toast shows what changed
  (+entities, +relationships, superseded/corroborated/conflicted, duplicates skipped,
  versions preserved).
- **Impact panel** — header button opens recent knowledge changes with stale-answer counts;
  clicking one shows the full impact breakdown.
- **Time-travel control** — date input next to the mode toggle; "as of" flows into every query
  including mode comparison.

### 9.6 Evaluation expansion (Phase 6)
Three new measured checks (all PASS, computed — not hardcoded):
- **Stale-answer detection**: produce an answer citing the historical fact, then verify the
  impact engine marks it stale after the change.
- **Impact analysis**: verify successor detection + impact headline on a changed fact.
- **Temporal reconstruction**: as-of query must return the old value and exclude the future one.

Full suite now: Hit@5 1.00/0.90/0.90, Recall@5 0.95/0.70/0.70, multi-hop 1.00, temporal PASS,
confidence PASS, stale-answer PASS, impact PASS, reconstruction PASS.

### 9.7 Hardening fixes shipped along the way
- **Deterministic IDs**: fact/document IDs used Python's salted `hash()` (different after every
  rebuild); replaced with SHA-256-based `_stable_hash` — IDs are now reproducible.
- **`/stats` enriched** for the status bar (historical, conflicts, corroborated, sources,
  last ingestion, last document time).
- **Server process management**: start with `setsid` so background servers survive long-running
  command timeouts (recurring demo-ops gotcha).
- Frontend tests updated for the new API parameters; 27/27 passing.

---

## 10. Quickstart (the exact working sequence)

```bash
# 1. infrastructure
docker compose up -d

# 2. build memory (no LLM needed)
python3 scripts/bootstrap_memory.py

# 3. start server
nohup python3 -m uvicorn app.main:fastapi_app --host 0.0.0.0 --port 8000 > /tmp/opencode/server.log 2>&1 &
sleep 4
curl -s localhost:8000/health      # expect {"all_ready": true, ...}

# 4. ask
curl -s -X POST localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question":"Which companies did people who left OpenAI go on to found?","retrieval_mode":"hybrid"}'

# 5. frontend
open http://localhost:8000
```
