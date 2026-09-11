CONTEXT.md — AI Knowledge Memory Engine

Build handover for autonomous coding agents (opencode)

Read this entire file before writing any code. This is the single source of truth for
architecture, scope, file layout, data contracts, and acceptance criteria. Do not deviate
from the scope boundaries in Section 6 — they exist because this is a 24-hour build with a
hard grading deadline, not a research project.

1. What we are building (one paragraph)

A system that behaves like persistent, explainable memory instead of a one-shot search
tool. It continuously ingests trusted sources, extracts structured facts (entities +
relationships), stores them in a knowledge graph (Neo4j) with confidence scores and
timestamps, stores raw text as embeddings (ChromaDB) for semantic recall, and answers
natural-language questions by combining graph traversal + vector search into one LLM-grounded
answer with visible sources, confidence, and — critically — a timeline showing how facts
changed over time. When a fact changes, the old value is never deleted; it's versioned.

The product is not "a chatbot with a vector DB." It is a memory system that can say: "as of
March, source X said this; as of June, source Y updated it; here's both, and here's how
confident I am in each."

2. Non-negotiable end-to-end pipeline

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

Every stage in this pipeline must be demoable. A stage that exists but produces no visible
demo output is not done.

3. Tech stack (locked — do not substitute without team sign-off)

Layer

Choice

Notes

Language

Python 3.11+



Graph DB

Neo4j (Docker, local)

Entity nodes + typed relationship edges

Vector DB

ChromaDB

chunk + entity embeddings

Relational

PostgreSQL

sources, documents, ingestion_logs, fact_audit, queries

API

FastAPI

single service, app/main.py

UI

Streamlit or a lightweight React/HTML graph view (see handover-team.md for the visual upgrade plan)

must render an interactive graph, not just text

LLM

ONE provider only (pick one: OpenAI / Anthropic / local via Ollama — decide in hour 0 and do not switch)

used for extraction AND answer generation

Orchestration

Docker Compose for Neo4j + Postgres + Chroma (if not embedded)



4. Repository structure (create exactly this — agents should not invent alternate layouts)

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

5. Shared data contracts (lock these before writing any store/retrieve code)

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

6. Scope boundaries — what NOT to build

Agents must refuse to expand scope into any of the following, even if it seems easy:

No general internet crawler — bounded technology-news domain, 20–50 seed documents only

No custom-trained models

No learned/ML credibility classifier — use the deterministic formula in Section 7

No community detection / Leiden clustering

No auth, user accounts, multi-tenancy

Only ONE LLM provider

No multi-agent swarm / agentic loops for extraction — single-pass LLM extraction with a strict schema is sufficient

7. Confidence formula (implement exactly, do not "improve" without discussion)

confidence = 0.50 * source_reliability + 0.30 * cross_source_agreement + 0.20 * extraction_confidence

Clamp to [0,1]. Single-source fact → agreement = 0.5. Corroborated fact → agreement increases.
On conflict: retain both versions, flag the conflict, lower effective answer confidence.
Never silently overwrite.

8. Neo4j schema

(:Entity {id, name, type})
(:Source {id, name, reliability})
(:Document {id, title, url, published_at})

(Entity)-[:RELATION {
    fact_id, confidence, source_id,
    valid_from, valid_to, observed_at
}]->(Entity)

Example: (OpenAI)-[:DEVELOPED {confidence:0.94}]->(GPT-5)

Do not store full article text in Neo4j — keep graph facts compact; text lives in Chroma/Postgres.

9. PostgreSQL tables

sources(source_id, name, domain, reliability_weight)

documents(document_id, source_id, url, title, published_at, retrieved_at, content_hash)

ingestion_logs(run_id, started_at, completed_at, documents_seen, documents_added, errors)

fact_audit(fact_id, action, old_value, new_value, timestamp)

queries(query_id, question, retrieval_mode, latency_ms, created_at)

10. Ownership map (for agent task-splitting / parallel branches)

Branch

Owner

Scope

mihika/graph-memory

Mihika

Neo4j client/writer, temporal versioning, confidence, contradiction detection, graph retrieval

adrija/ingestion

Adrija

RSS/URL ingestion, cleaning/chunking, entity/relation extraction, ChromaDB

amaan/rag-api-ui

Amaan

Hybrid retriever, prompts, answer generation, FastAPI, UI, evaluation

Agents working on one branch should treat the data contracts in Section 5 as a hard interface
and not reach into another branch's internal implementation.

11. Definition of done (acceptance criteria per component)

Ingestion: one command ingests the seed corpus; duplicates are skipped; failures on one
document do not crash the batch.

Extraction: LLM output always validates against the strict Pydantic schema; malformed
output is retried once, then logged and skipped (never crashes the pipeline).

Graph: inserting a new triple works; inserting a changed triple preserves history
(valid_to set on old fact, new fact inserted with new valid_from); every returned fact
includes source_id + timestamp + confidence.

Vector store: Chroma contains retrievable evidence with chunk_id/source_id metadata attached.

Hybrid retrieval: comparison mode returns distinguishably different result sets for
vector-only vs graph-only vs hybrid on at least 3 of the demo questions.

API: POST /query returns a QueryResult; POST /ingest triggers ingestion;
GET /facts/{id} returns fact history. Latency is measured and returned.

UI: question box → answer → evidence cards (source, confidence, timestamp) → graph view
of the traversal path → toggle for vector/graph/hybrid comparison → visible latency.

Evaluation: run_evaluation.py outputs Hit@5/Recall@5 on the curated question set, plus
a temporal-correctness check (old fact still retrievable via history after an update) and a
confidence-weighting check (low-reliability source produces visibly lower confidence).

12. Demo question set requirements

8–12 questions total, including at least 3 multi-hop questions requiring two graph edges
(these are the ones vector-only search will visibly fail or give a vague answer to — this
contrast is the single most important demo moment, see handover-team.md).

13. Build order (agent execution plan)

Hour 0: lock .env.example, requirements.txt, docker-compose (Neo4j + Postgres; Chroma
can be embedded/local), shared data contracts as actual Pydantic models in app/extraction/schemas.py and app/db/models.py.

Bring up all three stores and verify health-check connections before any pipeline code.

Build ingestion → extraction → graph write → vector write as one straight-line path first,
proven on 3–5 documents end-to-end, before scaling to the full 20–50 doc seed set.

Build hybrid retrieval + answer generation against that small proven dataset.

Only then build the comparison-mode UI, evaluation harness, and visual polish.

Freeze architecture once end-to-end works — remaining time is bug fixes + UI/demo polish only.

14. Final pitch line agents should keep in mind when naming things/writing docstrings

"Traditional RAG remembers documents only for the current query. This system builds
persistent memory: it continuously learns from trusted sources, turns information into a
connected knowledge graph, tracks how facts change over time, and assigns confidence based on
source reliability and corroboration."

Code comments, docstrings, and log messages should reflect this framing where natural (e.g.
log lines like "preserving prior fact version, not overwriting" rather than generic
"updating record") — it costs nothing and reinforces the narrative if a professor reads the code.

15. PRODUCT EXPERIENCE / FRONTEND — FINAL UI CONTRACT

The application is one coherent answer experience, not a collection of unrelated pages.

The primary screen should communicate this hierarchy:

Question
  ↓
Retrieval mode + latency
  ↓
Answer
  ↓
Confidence
  ↓
Evidence / sources
  ↓
Actual graph path

The graph must appear directly under or beside the answer so the relationship between the answer and its derivation is obvious.

Empty state

Show a clean search experience with a few pre-tested example questions.

AI Knowledge Memory Engine

Ask a question about the knowledge we've built.

[ question                                      ] [ Ask ]

Try:
• Who left OpenAI and later founded another AI company?
• How did company A become connected to company B?
• What changed about X between March and June?

Only advertise questions the seeded corpus can answer reliably.

Answer state

Recommended layout:

[ HYBRID ]                                      312 ms

Two former OpenAI researchers went on to found
Anthropic and Adept, according to corroborated reporting.

[ CONFIDENCE  HIGH ]

┌──────────────────┐  ┌──────────────────┐
│ TechCrunch       │  │ The Verge        │
│ retrieved Jun 12│  │ retrieved Mar 03│
└──────────────────┘  └──────────────────┘

GRAPH PATH

OpenAI → Person A → Anthropic
       → Person B → Anthropic

Everything renders together. Do not make the user navigate to a separate database/graph page to understand the answer.

16. GRAPH VISUALIZATION — THE MAIN WOW MOMENT

The graph is the strongest visual proof that graph traversal is actually happening.

Requirements:

actual nodes from the retrieval response

actual edges/facts from retrieval

entity labels

relationship labels

basic pan/zoom

relevant path highlighted

subtle traversal animation

surrounding graph kept minimal

no unreadable full-database dump

Preferred sequence:

Question submitted
      ↓
Tracing evidence path...
      ↓
Relevant nodes appear
      ↓
Relevant edges appear
      ↓
Traversal path highlights
      ↓
Answer/evidence settles on screen

Do not fake a long loading sequence. Animation should correspond to actual retrieval stages where practical.

The visual message is:

See the path. Not just the paragraph.

17. SOURCE + CONFIDENCE PRESENTATION

Never make raw JSON the primary user-facing representation.

Source cards should communicate:

source/domain

article title

date

evidence relationship

confidence where useful

Example:

TechCrunch
Former OpenAI researchers launch...
Retrieved Jun 12
CONFIDENCE  HIGH

Confidence should be presented as a product UI element:

CONFIDENCE  HIGH

or:

CONFIDENCE  88%

Do not make 0.734 the main visual treatment.

18. RETRIEVAL COMPARISON UX

The same question must be executable in three modes:

[ VECTOR ONLY ] [ GRAPH ONLY ] [ HYBRID ]

Changing the mode must perform a real new /query request.

Do not merely relabel the existing answer.

Vector only should use semantic evidence only.

Graph only should use graph evidence only.

Hybrid should combine both.

The purpose is to let a viewer directly observe that retrieval strategy matters.

For vector-only, the UI may explicitly state:

Graph traversal not used

For graph-only:

Graph path shown

For hybrid:

Graph path + semantic evidence

19. TEMPORAL MEMORY UI / TIME TRAVEL

A user should be able to inspect a fact's history from the answer/evidence/graph UI.

Example:

FACT HISTORY

MAR 03
● Person A worked at OpenAI
  Source: TechCrunch
  Confidence: Medium

JUN 12
● Person A founded Anthropic
  Source: The Verge
  Confidence: High

The visual idea is:

OLD BELIEF
     ↓
KNOWLEDGE CHANGE
     ↓
NEW BELIEF

The old fact remains visible. It is not deleted.

Use a compact drawer/modal/panel rather than a disconnected page.

20. LIVE INGESTION UX

The demo should have a small action such as:

[ Ingest new source ]

For the hackathon, this can use a known curated article or local fixture so the demo is deterministic.

After ingestion, show meaningful output:

Source ingested

+12 entities
+18 relationships
3 facts updated
0 facts deleted

Prefer wording such as:

Preserved previous fact version

rather than:

Updated database record

The audience should visibly understand that memory changes without history being destroyed.

21. LOADING + ERROR STATES

Useful loading states:

Extracting entities...
Searching graph...
Searching semantic memory...
Merging evidence...
Generating grounded answer...
Tracing evidence path...

Do not fake long progress animations.

Useful failure states:

No reliable evidence found.

The sources disagree on this fact.

Unable to retrieve this source.

LLM rate limit reached — retrying...

A failed source must not terminate an ingestion batch.

22. DEMO FLOW — FINAL PITCH ORDER

The complete live demo should fit in approximately three minutes.

1. Hook

Frame the problem before mentioning the technology.

Suggested wording:

Every AI chatbot can retrieve information. The problem is that knowledge changes, relationships matter, and most systems don't remember what they believed before.

Then:

We built a memory system that remembers facts, tracks how they change, and can show why it produced an answer.

2. Ask

Use one rehearsed multi-hop question.

3. Answer

Show answer + sources + confidence + latency.

4. Graph

Show the actual traversal path and pause briefly.

This is the main visual wow moment.

5. Compare

Switch:

Hybrid → Graph only → Vector only

Let the audience see the evidence/result difference.

6. Ingest

Trigger the pre-staged source update.

7. Time travel

Open the changed fact and show the old and new versions.

8. Ask again

Use a follow-up depending on the updated knowledge.

9. Evaluation

Show actual metrics.

The demo is evidence stacked in a deliberate order:

Multi-hop understanding
        ↓
Explainable evidence
        ↓
Retrieval comparison
        ↓
Temporal memory
        ↓
Measured performance

23. FUTURE VISION — DO NOT IMPLEMENT UNLESS EXPLICITLY REQUESTED

The future roadmap should extend the same core idea rather than become a random feature list.

Current system:

Remember knowledge

Future system:

Understand how changing knowledge affects everything that depended on it

This distinction should be central to the final hackathon pitch.

24. FUTURE FEATURE — KNOWLEDGE IMPACT ANALYSIS

This is the primary future direction.

Current:

Fact changes
    ↓
Old fact preserved
    ↓
New fact stored

Future:

Fact changes
       ↓
Find everything that depended on the old fact
       ↓
┌──────────────┬──────────────┬──────────────┐
↓              ↓              ↓
Old answers    Decisions      AI agents
↓              ↓              ↓
Documents      Workflows      Actions
       \       |       /
        \      |      /
         ↓     ↓     ↓
       KNOWLEDGE IMPACT
              ↓
       "What is now stale?"

Example:

An API specification changes.

A future version should be able to identify:

previous answers based on the old specification

engineering documents depending on it

decisions made using it

AI agents/workflows configured around it

Potential UI:

KNOWLEDGE IMPACT: HIGH

3 answers may now be stale
2 decisions depend on this fact
1 agent workflow uses the old information

[ Review impact ]

This is future scope, not current implementation.

25. FUTURE FEATURE — DECISION REPLAY

Future system could answer:

Why did we make this decision?

Represent:

Decision
   ↓
Facts available at the time
   ↓
Sources
   ↓
Confidence
   ↓
Evidence / reasoning

Then ask:

Would we make the same decision today?

Compare:

THEN                         NOW

Fact A ✓                     Fact A ✗
Fact B ✓                     Fact B ✓
Fact C ✓                     Fact C changed

Decision X                   Decision X

This turns temporal memory into decision intelligence.

Potential domains:

enterprise decision review

engineering

compliance

research

finance

incident analysis

Future scope only.

26. FUTURE FEATURE — "WHAT DID WE KNOW THEN?"

Allow a user to reconstruct the system's knowledge at a point in time.

Example:

What did the system believe about Company X on March 1?

Potential result:

MARCH 1 SNAPSHOT

Company X
├── CEO → ...
├── Product → ...
├── Partnership → ...
└── Acquisition → ...

Sources available at that time
Confidence at that time
Known contradictions at that time

This is time travel for organizational knowledge.

Potential uses:

audits

compliance

research

journalism

incident investigation

historical analysis

Future scope only.

27. FUTURE FEATURE — STALE ANSWER DETECTION

If an answer depends on a fact and that fact later changes:

Fact #83 changes
      ↓
Answer #142 depends on Fact #83
      ↓
Answer #142 becomes potentially stale

Future UI:

⚠ Potentially stale answer

This answer depends on a fact that changed on June 12.

[ Review updated evidence ]

This connects temporal memory directly to AI reliability.

28. FUTURE FEATURE — SHARED ORGANIZATIONAL MEMORY

The memory layer could eventually become infrastructure shared by multiple AI agents.

                    MEMORY
                      │
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
   Research       Engineering     Legal
     Agent           Agent        Agent
        │             │             │
        └─────────────┼─────────────┘
                      ↓
              Shared Knowledge

Instead of each agent maintaining isolated memory, agents contribute to and consume a common provenance-aware knowledge layer.

Future requirements:

permissions

access controls

tenant boundaries

agent-specific views

conflict resolution

Do not implement these for the current hackathon unless explicitly requested.

29. FUTURE FEATURE — CONTRADICTION INTELLIGENCE

Current prototype preserves contradictions.

Future system can reason over them:

CONFLICT DETECTED

Claim A
Confidence: 0.72
Source: Source A

Claim B
Confidence: 0.48
Source: Source B

Current supported belief
Confidence: 0.86

Why?
• newer source
• higher source reliability
• independent corroboration

The goal is not to pretend uncertainty disappears.

The goal is to make uncertainty explicit and useful.

30. FUTURE PRODUCT EVOLUTION

The long-term progression:

TODAY

Trusted Sources
      ↓
Persistent Knowledge
      ↓
Explainable Answers

NEXT

Knowledge Change Detection
      ↓
Impact Analysis
      ↓
Stale Answer Detection
      ↓
Decision Replay

FUTURE

Shared Organizational Memory
      ↓
Multiple AI Agents
      ↓
Permissions + Provenance
      ↓
Continuously Evolving Institutional Knowledge

Long-term product thesis:

Knowledge infrastructure for AI.

Not merely a chatbot.

31. REAL-WORLD PROBLEM / FUTURE PITCH

The strongest future problem framing is:

AI systems increasingly make decisions using information that changes after the decision is made. There is a missing layer between “the knowledge changed” and “everything depending on that knowledge should be reconsidered.”

Examples:

API specification changes
        ↓
old engineering answers may be stale

Company policy changes
        ↓
old internal guidance may be stale

Regulation changes
        ↓
old compliance reasoning may be stale

Market information changes
        ↓
old analysis may be stale

Research finding changes
        ↓
old conclusions may be stale

The future system would make those dependencies explicit.

32. PITCH POSITIONING

Do not claim:

nobody has built AI memory

Graph RAG was invented here

confidence scores are objectively correct

the system completely solves hallucinations

hybrid retrieval always wins

the prototype is production-ready enterprise infrastructure

Instead say:

this is a prototype of persistent, explainable AI memory

graph + vector retrieval provide complementary evidence

temporal fact versioning prevents silent loss of historical knowledge

confidence is deterministic and transparent

retrieval modes are experimentally compared

knowledge-impact analysis is the larger future direction

Technical honesty is part of the pitch.

33. FINAL PITCH STRUCTURE — 10 SLIDES

The final presentation should fit into ten slides.

1. The Hook

AI can retrieve information. It still struggles to remember what changed.

2. The Problem

Traditional RAG remembers documents. We want AI to remember knowledge.

3. The Core Idea

What if AI had memory with a sense of time?

4. Architecture

From trusted sources to explainable answers

5. Product / Live Demo

One question. The whole reasoning trail.

Show the actual UI.

6. Experiment

Vector vs Graph vs Hybrid

Show real retrieval differences.

7. Differentiation

Memory that can explain itself.

Show graph + provenance + confidence + temporal history.

8. Bigger Problem

The real problem starts after knowledge changes.

Introduce knowledge-impact analysis.

9. Future

From AI memory → knowledge infrastructure

Show decision replay, stale-answer detection, shared organizational memory, etc. as future scope.

10. Closing

AI shouldn't just know. It should remember.

Then:

And when knowledge changes, it should know what changed — and what that means.

34. VISUAL LANGUAGE FOR THE PITCH

Use a premium dark-mode AI infrastructure aesthetic.

Preferred:

near-black background

white/off-white text

restrained violet/indigo/teal accents

thin borders

rounded cards

subtle graph-path glow

large headlines

whitespace

diagrams and product UI

timelines

graph visualizations

Avoid:

stock photography

robots

brains

generic glowing AI imagery

excessive gradients

fake futuristic imagery

dense paragraphs

The graph, timeline, product UI and knowledge-impact diagram should be the main visual assets.

35. CORE PRODUCT LANGUAGE

Preferred terms:

persistent memory
knowledge graph
temporal knowledge
multi-hop retrieval
provenance
confidence
evidence
knowledge change
knowledge impact
stale knowledge
decision replay

Avoid excessive use of:

revolutionary
groundbreaking
AGI
human-like memory
perfect accuracy
zero hallucinations

The system should sound ambitious but credible.

36. ONE-SENTENCE PRODUCT DESCRIPTION

An AI memory layer that builds persistent knowledge from trusted sources, tracks how facts change over time, and explains the evidence behind every answer.

37. ONE-SENTENCE FUTURE VISION

The next step is not just knowing that knowledge changed — it is understanding which answers, decisions, and AI actions are affected by that change.

38. FINAL PITCH LINE

AI shouldn't just know. It should remember — and when knowledge changes, it should know what changed and what that means.

39. FINAL DEFINITION OF DONE

The prototype is complete when:

Docker services start reliably

Neo4j works

PostgreSQL works

Chroma works

Groq works

seed corpus ingests with one command

duplicates are skipped

one failed document does not kill ingestion

extraction validates against strict schemas

malformed extraction retries once

graph entities/relationships are stored

temporal fact updates preserve old versions

contradictions are preserved

confidence follows the locked formula

vector retrieval works

graph retrieval works

hybrid retrieval works

/query works

/ingest works

/facts/{id} works

latency is returned

sources are returned

confidence is returned

graph path is returned

frontend shows answer

frontend shows evidence/source cards

frontend shows confidence

frontend shows latency

frontend shows actual graph traversal

graph traversal is visually highlighted

retrieval toggle performs real queries

fact history is inspectable

live ingestion can be demonstrated

evaluation produces real metrics

at least 3 multi-hop questions work reliably

exact demo flow works without manual database intervention

40. FINAL ENGINEERING PRINCIPLE

Do not optimize for feature count.

Optimize for one immediate understanding:

This is not simply searching documents. It is maintaining a living, explainable memory.

Every current feature should reinforce that idea.

The future roadmap should extend the same idea naturally:

REMEMBER
    ↓
UNDERSTAND CHANGE
    ↓
TRACE DEPENDENCIES
    ↓
IDENTIFY STALE KNOWLEDGE
    ↓
RECONSIDER DECISIONS
    ↓
POWER AI ORGANIZATIONS
