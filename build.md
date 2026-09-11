BUILD.md — AI Knowledge Memory Engine

This is the implementation handoff for the coding agent.

Read context.md and handover-team.md first. They define the architecture, scope, contracts, ownership, and demo story. This file turns those decisions into the concrete build plan.

Do not redesign the project. Do not add unrelated features. If something is ambiguous, prefer the simplest implementation that satisfies the existing contracts and makes the demo reliable.

1. What we are building

Build a working AI Knowledge Memory Engine with this flow:

Trusted tech sources
    ↓
ingest → clean/chunk → LLM extraction
    ↓
normalize/dedup → confidence + contradiction check
    ↓
Neo4j + ChromaDB + PostgreSQL
    ↓
user asks question
    ↓
entity extraction
    ↓
graph traversal + vector search
    ↓
hybrid evidence
    ↓
LLM grounded answer
    ↓
answer + sources + confidence + latency + graph path + fact history

The important distinction from ordinary RAG is persistent structured memory:

facts are entities + relationships, not only text chunks

graph traversal can answer multi-hop questions

facts are versioned instead of overwritten

confidence is explicit and deterministic

provenance is visible

the user can see the graph path used for the answer

vector, graph, and hybrid retrieval can be compared on the same question

The current corpus is intentionally bounded to roughly 20–50 trusted technology-news documents.

2. Locked architecture

Use the existing architecture from context.md.

Backend

Python 3.11+

FastAPI

Neo4j

ChromaDB

PostgreSQL

one LLM provider only

Docker Compose for infrastructure

If the team has already locked the LLM provider/model, keep it. Do not switch providers halfway through the build.

Frontend

Use a lightweight web UI rather than a default-looking Streamlit form if the repository already supports the visual upgrade.

The target experience is:

dark, minimal interface

one primary question/search input

retrieval mode toggle

answer area

confidence badge

source cards

graph traversal visualization

fact-history/time-travel interaction

ingestion action

evaluation view

The graph is a core product surface, not an admin/database screen.

3. Repository structure

Keep the structure defined in context.md:

ai-knowledge-memory-engine/
├── README.md
├── context.md
├── handover-team.md
├── build.md
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
│   └── ...
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

Do not create competing architectures or duplicate service layers.

4. Backend requirements

4.1 Shared models

Implement the shared contracts as real Pydantic models.

Document = {
    "document_id": str,
    "title": str,
    "url": str,
    "source": str,
    "published_at": datetime,
    "retrieved_at": datetime,
    "text": str,
    "source_reliability": float,
}

Chunk = {
    "chunk_id": str,
    "document_id": str,
    "text": str,
    "embedding_id": str,
}

Entity = {
    "entity_id": str,
    "name": str,
    "type": str,
}

Fact = {
    "fact_id": str,
    "subject_id": str,
    "relation": str,
    "object_id": str,
    "confidence": float,
    "source_id": str,
    "observed_at": datetime,
    "valid_from": datetime,
    "valid_to": Optional[datetime],
}

QueryResult = {
    "answer": str,
    "facts": list[Fact],
    "passages": list[Chunk],
    "sources": list[str],
    "confidence": float,
    "retrieval_mode": Literal["vector", "graph", "hybrid"],
    "latency_ms": float,
}

Extend QueryResult with graph visualization data if needed. Do not remove the existing fields.

5. Graph retrieval must expose the traversal path

This is important for the UI.

Do not make the frontend reconstruct the traversal from arbitrary Neo4j results.

graph_retriever.py should return both:

evidence facts used for answering

a compact visualization payload describing the relevant graph path

Recommended shape:

GraphPath = {
    "nodes": [
        {
            "id": "entity-id",
            "label": "OpenAI",
            "type": "Organization",
        }
    ],
    "edges": [
        {
            "id": "fact-id",
            "source": "entity-id-1",
            "target": "entity-id-2",
            "label": "LEFT",
            "confidence": 0.88,
            "source_id": "source-id",
        }
    ],
    "path": [
        "entity-id-1",
        "entity-id-2",
        "entity-id-3",
    ],
}

The exact internal representation can differ, but the frontend must receive enough information to render:

OpenAI ──LEFT──> Person A ──FOUNDED──> Anthropic

and highlight that path.

If there are multiple valid paths, return the relevant paths, but keep the demo view visually simple.

The graph should show the subset of nodes/edges relevant to the current answer, not dump the entire database.

6. Query API

POST /query

Input:

{
  "question": "Which companies did people who left OpenAI go on to found?",
  "retrieval_mode": "hybrid"
}

Supported modes:

vector
graph
hybrid

Response should contain:

{
  "answer": "...",
  "facts": [],
  "passages": [],
  "sources": [],
  "confidence": 0.88,
  "retrieval_mode": "hybrid",
  "latency_ms": 312,
  "graph_path": {
    "nodes": [],
    "edges": [],
    "path": []
  }
}

Measure latency around the complete query operation and return it.

The LLM must answer from supplied evidence. It must not invent facts that are not supported by the retrieved context.

If evidence is weak or contradictory, the answer should express uncertainty.

7. Retrieval modes

Implement three genuinely different paths.

Vector only

question
→ embedding
→ Chroma similarity search
→ passages
→ LLM answer

Do not inject graph facts into this mode.

Graph only

question
→ entity extraction
→ Neo4j traversal
→ graph facts
→ LLM answer

Do not inject vector passages into this mode.

Hybrid

question
→ entity extraction
→ graph traversal
+
vector similarity search
→ merge/rank evidence
→ LLM answer

The UI must make the difference obvious.

The acceptance test is not that all three produce identical answers. The demo needs at least three questions where the returned evidence/result sets are meaningfully different.

8. Confidence

Implement exactly:

confidence =
    0.50 * source_reliability
  + 0.30 * cross_source_agreement
  + 0.20 * extraction_confidence

Clamp to [0, 1].

For a single-source fact:

cross_source_agreement = 0.5

If sources corroborate a fact, agreement increases.

If sources conflict:

keep both versions

flag the conflict

lower effective answer confidence

never silently overwrite the old fact

Do not replace this with a learned scoring system.

9. Temporal memory

A changed fact must create a new version.

Example:

March:
Person A → WORKED_AT → OpenAI
valid_from = March
valid_to = June

June:
Person A → FOUNDED → Anthropic
valid_from = June
valid_to = null

The previous version remains queryable.

When a fact is changed:

identify the existing active fact

set its valid_to

insert the new fact

write the audit event to PostgreSQL

preserve both source/timestamp/provenance records

The UI must eventually be able to show this history.

10. Fact history API

GET /facts/{id}

Return the fact and its history.

The frontend needs enough information to render a timeline such as:

MAR
● Previous belief
  Source: TechCrunch
  Confidence: High

JUN
● Updated belief
  Source: The Verge
  Confidence: Very high

Do not expose raw database rows as the primary UI.

11. Ingestion

Support:

RSS/curated URLs

cleaning

chunking

extraction

normalization/deduplication

graph write

vector write

provenance/audit logging

A single failed document must not kill the entire batch.

Malformed LLM extraction:

retry once

log the failure

skip that document/fact

continue the batch

Do not add a multi-agent extraction workflow.

12. Frontend — target experience

The screenshot supplied with this handoff is the visual reference.

The target is a dark, restrained, research-tool/product interface.

It should feel closer to a polished internal AI research product than a Streamlit assignment.

Main screen

At the top:

┌─────────────────────────────────────────────────────────────┐
│ Which companies did people who left OpenAI go on to found? │
│                                              [ Ask ]        │
└─────────────────────────────────────────────────────────────┘

[ Hybrid ]   [ Graph only ]   [ Vector only ]          312ms

The active mode should be visually obvious.

Under it:

Two former OpenAI researchers went on to found
Anthropic and Adept, according to corroborated
reporting from three sources.

[ Confidence  HIGH ]

Then source cards:

┌────────────────────────────┐  ┌────────────────────────────┐
│ TechCrunch                 │  │ The Verge                  │
│ retrieved Jun 12           │  │ retrieved Mar 03           │
└────────────────────────────┘  └────────────────────────────┘

Then the graph:

             Person A
            /        \
        OpenAI       Anthropic
            \        /
             Person B

The actual layout can differ, but the hierarchy should remain:

question

retrieval mode + latency

answer

confidence

sources

graph path

Everything belongs to one answer render.

Do not send the user to a separate "graph database" page just to prove traversal.

13. Graph visualization

This is the most important visual component.

Use whichever lightweight graph technology best fits the existing frontend:

React + react-force-graph

vis-network

D3

pyvis/streamlit-agraph only if the current frontend is Streamlit and replacing it would waste too much time

The graph must:

render nodes and typed edges

show entity names

visually distinguish entity types when useful

highlight the path used by the query

animate/highlight the traversal when a result arrives

support basic pan/zoom

not show an unreadable dump of the entire database

Preferred interaction:

query submitted
    ↓
graph container appears
    ↓
nodes fade/enter
    ↓
relevant edges light up
    ↓
answer path is highlighted

This is presentation polish, not fake data.

The highlighted path must come from actual retrieval output.

14. Source cards

Do not render JSON.

Each source card should communicate:

source name/domain

article title if available

retrieved/published date

relationship to the evidence

confidence if useful

Keep cards compact.

Example:

TechCrunch
Former OpenAI researchers launch...
Retrieved Jun 12

████████░░  High confidence

A raw decimal such as 0.734 should not be the primary visual treatment.

15. Confidence badge

Render confidence as a product UI element.

Prefer:

Confidence   HIGH

or:

Confidence   88%

with a visual state.

Avoid:

confidence = 0.88

The underlying API can keep the exact float.

16. Retrieval comparison

The same question should be reusable across modes.

When the user switches:

Hybrid → Graph only → Vector only

make another /query request with the selected mode.

Do not merely change the label on the existing response.

The user should see the actual result change.

Useful UI:

[ Hybrid ] [ Graph only ] [ Vector only ]

Answer
Sources
Confidence
Latency
Graph

For vector-only, the graph section can say:

No graph traversal used

For graph-only, show the actual graph path.

For hybrid, show both evidence sources and the graph path.

This makes the architecture visible without requiring the presenter to explain every backend component.

17. Fact history / time travel UI

A user should be able to click a fact, source, or graph node and inspect its history.

Example:

Fact history

Mar 03
OpenAI → employed → Person A
Source: TechCrunch
Confidence: Medium

Jun 12
Person A → founded → Anthropic
Source: The Verge
Confidence: High

The key visual message:

old fact
   ↓
updated fact

not:

old fact deleted

Keep it as a compact drawer/panel rather than a new page.

18. Live ingestion UI

Add a small ingestion action.

Example:

Memory

[ Ingest new source ]

For the demo, this can point to a known curated article or local fixture.

After ingestion:

Source ingested
+12 entities
+18 relationships
3 facts updated
0 facts deleted

Then asking the same question again should use the new memory.

The visual wording should reinforce:

Preserved previous fact version

rather than:

Updated database record

19. Evaluation view

Include a compact evaluation panel/page.

Show at minimum:

Hit@5              0.xx
Recall@5            0.xx
Multi-hop accuracy  xx%
Avg latency         xxx ms

Also show:

Temporal correctness   PASS
Confidence weighting   PASS

The numbers must come from the evaluation scripts, not hardcoded marketing values.

The evaluation set should contain 8–12 questions, including at least 3 multi-hop questions.

20. Demo flow

The UI must support this exact demo without awkward navigation.

Step 1 — Ask

Use a pre-tested multi-hop question.

Example:

Which companies did people who left OpenAI go on to found?

Step 2 — Answer

Immediately show:

answer

confidence

sources

latency

Step 3 — Graph

Show the actual traversal path.

Pause on it during the pitch.

Step 4 — Compare

Switch:

Vector only
Graph only
Hybrid

The result/evidence should visibly differ.

Step 5 — Update memory

Click:

Ingest new source

Show the new source being processed.

Step 6 — Prove memory is temporal

Open the changed fact.

Show old version + new version.

The old version must still exist.

Step 7 — Ask again

Ask a follow-up that depends on the updated fact.

Step 8 — Evaluation

Show the measured numbers.

This is the complete product story.

21. Example question cards

Put a few tested questions below the empty search box so the viewer immediately understands what the system can do.

Examples:

Who left OpenAI and later founded another AI company?

Which company acquired the startup founded by X?

How did company A become connected to company B?

What changed about X between March and June?

Only include questions that the seeded corpus can answer reliably.

Do not let the UI advertise unsupported capabilities.

22. Loading states

Do not show a blank screen while the backend works.

Use short, meaningful states:

Extracting entities...
Searching graph...
Searching semantic memory...
Merging evidence...
Generating grounded answer...

For the graph:

Tracing evidence path...

These should reflect actual backend stages where practical.

Do not fake a long loading sequence just for animation.

23. Error states

The demo must fail gracefully.

Examples:

No reliable evidence found.

The sources disagree on this fact.

Unable to retrieve this source.

LLM rate limit reached — retrying...

A failed document should not terminate an ingestion batch.

A failed query should return a useful API error and leave the frontend usable.

24. What NOT to build

Stay inside scope.

Do not add:

authentication

user accounts

multi-tenancy

general web crawling

custom-trained models

learned credibility models

community detection

agent swarms

elaborate autonomous agents

arbitrary document management systems

a second LLM provider

unnecessary microservices

elaborate admin dashboards

unrelated analytics

If there is extra time, spend it on:

graph animation

source/evidence presentation

fact-history interaction

retrieval comparison

demo reliability

evaluation visibility

Not new backend features.

25. Build order

Follow this order.

Phase 1 — Infrastructure

.env.example

dependencies

Docker Compose

Neo4j connection

PostgreSQL connection

Chroma connection

health checks

Do not continue until all stores are reachable.

Phase 2 — Contracts

Implement:

Pydantic schemas

database models

configuration

source weights

confidence calculation

Phase 3 — Ingestion

Prove:

3–5 documents
→ extraction
→ graph
→ vector
→ provenance

before running the full corpus.

Phase 4 — Graph memory

Implement:

graph writer

deduplication

temporal updates

contradiction handling

graph retrieval

graph path response

Phase 5 — Vector retrieval

Implement Chroma retrieval with source/chunk metadata.

Phase 6 — RAG

Implement:

vector mode

graph mode

hybrid mode

grounded answer generation

citations/evidence

confidence

latency

Phase 7 — API

Wire:

POST /query
POST /ingest
GET /facts/{id}

Phase 8 — Frontend

Build the single-screen answer experience first.

Then add:

graph animation

source cards

mode toggle

history drawer

ingestion action

evaluation panel

Phase 9 — Evaluation

Run the curated questions and verify:

Hit@5

Recall@5

multi-hop accuracy

temporal correctness

confidence weighting

latency

Phase 10 — Demo hardening

Test the exact pitch flow repeatedly.

The demo question must work every time.

Have a local fallback dataset/fixture so a live RSS/source failure does not destroy the presentation.

26. Testing requirements

At minimum test:

Ingestion

valid document ingests

duplicate document skipped

one failed document does not stop batch

Extraction

valid LLM output validates

malformed output retries once

malformed output is skipped after retry

Graph

new triple inserted

changed triple preserves old version

old fact gets valid_to

new fact gets valid_from

source/timestamp/confidence returned

Confidence

Test the exact formula and clamping.

Query

vector mode does not use graph evidence

graph mode does not use vector evidence

hybrid combines both

latency returned

weak evidence produces uncertainty

Temporal

After an update:

old fact still retrievable
new fact retrievable
history contains both

27. Definition of done

The build is done when all of these are true:

Docker services start reliably

seed corpus can be ingested with one command

duplicate documents are skipped

extraction uses strict schemas

Neo4j contains entities + temporal relationships

Chroma contains searchable chunks with metadata

PostgreSQL contains provenance/audit/query records

changed facts preserve their old versions

confidence follows the locked formula

contradiction handling preserves both versions

/query works in vector/graph/hybrid modes

/ingest works

/facts/{id} works

graph traversal path is returned by the backend

frontend renders answer + confidence + sources + latency

frontend renders the actual graph path

graph path is visually highlighted/animated

retrieval mode toggle performs real new queries

fact history is inspectable

live ingestion can demonstrate a memory update

evaluation produces real metrics

at least 3 multi-hop questions are reliable

exact demo flow can be completed without manual database intervention

28. Product principle

The product should communicate one thing clearly:

Traditional RAG retrieves documents. This system builds, maintains, and explains its own memory.

Every implementation decision should support that story.

The UI should not look like:

question → paragraph → citations

It should look like:

question
   ↓
answer + confidence
   ↓
evidence
   ↓
graph path
   ↓
history

The graph is not decoration.

The confidence is not decoration.

The timeline is not decoration.

They are the visible proof of the backend architecture.
