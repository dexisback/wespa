Next — AI Knowledge Memory Engine

Purpose

This is the roadmap for what we want to do next after the current hackathon build.

The current system is already a working persistent-memory prototype:

Ingests curated technology-news documents and arbitrary URLs.

Extracts entities and facts.

Stores graph memory in Neo4j.

Stores semantic memory in ChromaDB.

Stores provenance/audit information in PostgreSQL.

Tracks fact versions, corroboration, supersession, and conflicts.

Answers using Vector, Graph, or Hybrid retrieval.

Returns citations, confidence, latency, and the actual graph path.

Has a single-screen frontend and an evaluation suite.

Has deterministic offline/bootstrap mode because Groq free-tier limits can interfere with demos.

The next phase should NOT turn this into a generic Perplexity clone.

The goal is to evolve it from:

AI that remembers information

into:

AI memory that understands how changing knowledge affects what the AI previously knew, answered, or decided.

1. Highest-Priority Feature: Memory-First → Live Retrieval Fallback

Problem

Right now, the system has a strong persistent memory layer, but it is still fundamentally dependent on what has already been ingested.

If a user asks something that is not in memory, the system should recognize:

"I don't have enough evidence in memory."

Instead of simply failing, it should optionally retrieve the missing information, ingest it into memory, and then answer from the updated memory.

Target flow

User Question
      ↓
Search persistent memory
      ↓
Is there enough evidence?
   ↙          ↘
 YES           NO
 ↓             ↓
Answer      Live retrieval
             ↓
          Clean / extract
             ↓
          Store in memory
             ↓
          Answer from memory

Important constraint

This should be a controlled fallback, not a replacement for the memory system.

The product should remain:

memory-first

rather than:

search-first.

Desired UX

Show stages such as:

Memory found

Memory insufficient

Fetching new evidence...

Memory updated

Answer grounded in updated memory

This makes the system feel like a continuously learning memory layer rather than a static demo dataset.

2. Knowledge Impact Analysis — The Main Differentiator

This is the strongest future product direction.

When a fact changes, the system should not only update the fact.

It should answer:

"What does this change affect?"

Example

Suppose memory contains:

Company X
→ valuation
→ $20B

Later a trusted source says:

Company X
→ valuation
→ $15B

The system already preserves the old $20B fact.

The next step is to calculate the impact:

$20B fact changed
      ↓
Which facts depended on it?
      ↓
Which previous answers used those facts?
      ↓
Which decisions / summaries / workflows depended on those answers?
      ↓
What is now potentially stale?

Future UI

FACT CHANGED

Company X valuation
$20B → $15B

IMPACT
3 previous answers may be stale
2 derived facts depend on this
1 decision used the old value

[Inspect Impact]

Product thesis

Move from:

"I remember."

toward:

"I understand the consequences of changing knowledge."

3. Stale Answer Detection

Build directly on Knowledge Impact Analysis.

Whenever a stored fact is superseded or contradicted:

Find previous answers that cited it.

Determine whether those answers materially depended on the changed fact.

Mark them as:

CURRENT

POTENTIALLY STALE

INVALIDATED

Explain why.

Example:

Previous answer:
"Company X is valued at approximately $20B."

Status:
POTENTIALLY STALE

Reason:
The source supporting the $20B valuation was superseded
by a newer source reporting $15B.

This gives the memory system a concept of answer freshness, not just fact freshness.

4. Decision Replay

Future capability:

"Why did we make this decision?"

The system should reconstruct the knowledge available at the time a decision was made.

Example:

Decision:
Choose Company X

Decision date:
June 12, 2026

Knowledge available then:
- Valuation: $20B
- Revenue: $4B
- Source confidence: High
- Competitor Y: $18B

Decision reasoning:
...

Then allow:

"Would we make the same decision today?"

Compare:

Knowledge then
      VS
Knowledge now

and explain what changed.

This is a natural extension of temporal memory + provenance.

5. "What Did We Know Then?" / Time-Travel Memory

The current system already maintains temporal fact versions.

The next step is to expose that as a first-class product capability.

Allow queries such as:

"What did we know about Company X on June 1?"

"What was the valuation according to our memory then?"

"Which sources supported that belief?"

"What changed afterward?"

Desired mental model

NOW
│
├── Current facts
├── Previous facts
├── Sources
└── Changes over time

The user should be able to move through the timeline and reconstruct the state of memory at a point in time.

6. Contradiction Intelligence

The current system already detects and stores conflicts.

Next step: make contradictions understandable.

Instead of only saying:

CONFLICT

show:

COMPETING CLAIMS

Claim A
$20B valuation
Source: Source A
Date: June 1
Reliability: High

Claim B
$15B valuation
Source: Source B
Date: June 12
Reliability: High

Current belief:
$15B

Why:
- newer source
- high source reliability
- corroborated by 2 sources

The system should explain why one claim currently wins without pretending conflicting information never existed.

7. "Why Do You Believe This?" Panel

Add an explainability layer to the answer UI.

Current confidence logic:

50% source reliability
30% cross-source agreement
20% extraction confidence

Expose this in human-readable form:

WHY THIS ANSWER IS HIGH CONFIDENCE

Source reliability       ██████████
Cross-source agreement   ████████
Extraction confidence    █████████

Supporting sources: 4
Conflicting sources: 0

Do not make the raw formula the primary UI.

The user should understand the reasoning without needing to understand the implementation.

8. Improve the Graph Visualization

The graph currently renders the actual retrieved graph path.

Next step: make that path visually obvious.

When a question is answered through multi-hop graph traversal:

Entity A
   ↓
Relation
   ↓
Entity B
   ↓
Relation
   ↓
Entity C

animate/highlight the exact path used by the answer.

The graph should visually distinguish:

entities involved in the answer

retrieved edges

supporting facts

unrelated surrounding graph context

The objective is not simply:

"Here is our Neo4j graph."

It should be:

"Here is the exact chain of knowledge that led to this answer."

9. Strengthen the Temporal Timeline UI

The temporal history already exists.

Make it visually important.

For a changing fact:

VALUATION

$20B
│
├── Jun 1
│   Source A
│   ACTIVE
│
└── Jun 12
    Source B
    CURRENT

      ↓

$20B → $15B

The old fact should remain visible.

Do not overwrite history.

This is one of the core conceptual differences from a normal document-retrieval system.

10. Improve Retrieval Mode Comparison

The Vector / Graph / Hybrid toggle should become a clear product demonstration.

For the same question, show:

VECTOR
Answer
Sources
Latency
Evidence count

GRAPH
Answer
Graph path
Sources
Latency
Evidence count

HYBRID
Answer
Graph path + semantic evidence
Sources
Latency
Evidence count

The UI should make the differences obvious.

Do not claim:

Hybrid is always best.

Instead, demonstrate which mode performs better on which type of question.

11. Memory Status / System Health UI

Add a persistent "Memory Status" area.

Example:

MEMORY

51 entities
86 facts
22 sources

Last updated:
2 minutes ago

Status:
● Healthy

Potential indicators:

active facts

historical facts

conflicts

corroborated facts

source count

last ingestion

last graph update

This reinforces that the product is a living memory system rather than a static question-answering page.

12. Better Live Ingestion UX

The ingestion backend already supports:

fixture ingestion

raw payload ingestion

URL ingestion

The next step is making the URL workflow feel like a product feature.

Example:

ADD TO MEMORY

[ Paste URL ]

[ Ingest ]

Fetching...
Cleaning...
Extracting entities...
Checking existing facts...
Updating memory...
Complete.

+4 entities
+8 relations
2 facts superseded
3 facts corroborated

This should visually demonstrate that ingestion changes the memory graph rather than merely adding another document.

13. Better Memory Update / Change Explanation

After ingestion, show what changed.

Instead of only:

Ingestion complete

show:

MEMORY UPDATED

+4 new entities
+8 new relationships

2 facts superseded
3 facts corroborated
1 contradiction detected

Historical versions preserved

This directly demonstrates the temporal-memory thesis.

14. Evaluation Expansion

The current evaluation suite is already working.

Current metrics include:

Hit@5

Recall@5

multi-hop accuracy

latency

temporal correctness

confidence weighting

Current reported results:

Vector Hit@5: 1.00

Graph Hit@5: 0.90

Hybrid Hit@5: 0.90

Vector Recall@5: 0.95

Graph Recall@5: 0.70

Hybrid Recall@5: 0.70

Multi-hop accuracy: 1.00

Temporal correctness: pass

Confidence weighting: pass

Next evaluation dimensions:

A. Stale-answer detection

Can the system correctly identify answers affected by changed facts?

B. Impact-analysis accuracy

Does the system find the correct downstream dependencies?

C. Temporal reconstruction

Can it correctly reconstruct memory as of a historical timestamp?

D. Contradiction resolution

Does it select the better-supported claim and explain why?

E. Live retrieval correctness

When memory is insufficient, does new evidence get correctly incorporated?

F. Provenance completeness

Can every important answer claim be traced back to a source/fact?

15. Production-Quality Reliability

The hackathon build is working, but several areas can be hardened.

Groq

Real-LLM mode is affected by free-tier rate limits and is roughly ~10 seconds/query.

Keep deterministic offline/fallback mode for demos.

Continue improving:

retry behavior

rate-limit handling

request pacing

failure fallback

structured extraction validation

Ingestion

Improve robustness for arbitrary URLs.

Current regex fallback is intentionally limited.

For arbitrary real-world text, the preferred path should be the real LLM extraction pipeline.

Duplicate handling

Continue preserving deterministic SHA256 duplicate protection.

Data safety

Document clearly that:

docker compose down -v

wipes the local database volumes.

16. Source Trust / Provenance Improvements

The system already has source reliability and provenance.

Future improvements:

richer source reliability model

source freshness

source agreement tracking

claim-level provenance

visible provenance chains

source conflict explanations

Desired answer experience:

Answer claim
    ↓
Fact
    ↓
Source
    ↓
Original document / URL
    ↓
Timestamp

The user should be able to trace important claims all the way back to evidence.

17. Long-Term: Shared Organizational Memory

Future version:

Multiple AI agents should be able to use the same memory layer.

Agent A
   │
Agent B ──→ Shared Memory Layer ←── Agent C
   │
Agent D

The memory layer becomes independent of a single chatbot.

Potential future capabilities:

multiple agents writing memories

permissions

tenant boundaries

agent identity

memory ownership

audit trails

shared organizational knowledge

This is where the project could evolve from a demo into infrastructure.

18. Long-Term: Memory as Infrastructure

The bigger product direction:

LLM
 ↓
Memory Layer
 ↓
 ├── Facts
 ├── Relationships
 ├── Time
 ├── Provenance
 ├── Confidence
 ├── Contradictions
 └── Dependencies

Instead of every AI application implementing memory independently, this system could become reusable memory infrastructure.

Potential consumers:

AI agents

enterprise assistants

research assistants

coding agents

decision-support systems

autonomous workflows

19. Product Positioning

Do NOT position the system as:

"Another RAG system."

Do NOT position it as:

"A Perplexity clone."

Position it as:

"A persistent memory layer for AI that tracks facts, relationships, provenance, confidence, and change over time."

Stronger future positioning:

"AI shouldn't just know. It should remember — and understand what changed."

Eventually:

"Don't just update the memory. Understand what the update breaks."

20. Demo Flow After These Improvements

The ideal future demo:

Step 1 — Ask a multi-hop question

Show answer, confidence, citations, graph path, latency.

Step 2 — Compare retrieval

Toggle Vector → Graph → Hybrid and show how evidence differs.

Step 3 — Inspect the graph

Animate the exact path used to answer.

Step 4 — Inspect temporal memory

Show old fact → new fact without deleting the old version.

Step 5 — Ingest a new URL

Show memory changing live.

Step 6 — Show the impact

Fact changed
↓
Previous answer affected
↓
Answer marked potentially stale

Step 7 — Ask "What did we know then?"

Reconstruct the historical state.

Step 8 — Ask "Would we make the same decision today?"

Run decision replay.

Step 9 — Show evaluation

Demonstrate measured retrieval and temporal performance.

Step 10 — Close on the product thesis

"Traditional RAG retrieves documents.
We built memory that tracks knowledge, change, and the consequences of that change."

21. Recommended Implementation Order

Do not build everything simultaneously.

Phase 1 — Product polish

Improve graph path animation.

Improve temporal timeline.

Improve retrieval comparison UI.

Add Memory Status.

Add "Why do you believe this?" panel.

Improve ingestion result/change UI.

Phase 2 — Memory-first fallback

Detect insufficient memory evidence.

Add controlled live retrieval.

Ingest retrieved evidence.

Re-run memory retrieval.

Answer from updated memory.

Show the complete process in the UI.

Phase 3 — Knowledge impact

Store answer → fact dependencies.

Detect changed facts.

Find dependent answers.

Calculate impact.

Mark stale answers.

Show impact graph/timeline.

Phase 4 — Temporal intelligence

Historical query support.

"What did we know then?"

Historical source reconstruction.

Decision replay.

"Would we make the same decision today?"

Phase 5 — Contradiction intelligence

Better competing-claim representation.

Source comparison.

Confidence comparison.

Resolution explanation.

Contradiction UI.

Phase 6 — Evaluation + hardening

Expand benchmark questions.

Add impact-analysis metrics.

Add stale-answer metrics.

Add temporal reconstruction metrics.

Stress-test ingestion.

Harden LLM failures/rate limits.

22. What Is Already DONE

Do not unnecessarily rebuild these.

Core FastAPI backend

Neo4j graph memory

Chroma semantic memory

PostgreSQL provenance/audit

Local embeddings

Groq integration

Entity extraction

Relation extraction

Normalization

Duplicate protection

Temporal fact versioning

Supersession

Corroboration

Contradiction tracking

Graph retrieval

Vector retrieval

Hybrid retrieval

Grounded answer generation

Confidence scoring

Provenance

Source citations

GraphPath response

URL ingestion

Fixture ingestion

Deterministic bootstrap

LLM fallback behavior

Evaluation suite

Single-screen frontend

Loading states

Error handling

Fact history drawer

Ingestion panel

Evaluation panel

Live graph rendering

The current build should be treated as the foundation, not restarted.

23. Definition of the Next Major Milestone

The next genuinely meaningful milestone is:

A user can ask a question, the system checks persistent memory first, fetches new evidence only when necessary, incorporates that evidence into memory, and then understands which previous knowledge or answers were affected when facts change.

At that point the system stops feeling like:

"a RAG demo with a graph"

and starts feeling like:

"a real persistent AI memory system."

24. Final Engineering Principle

Every new feature should reinforce one of these five properties:

Memory — the system retains knowledge.

Time — the system knows when knowledge was true.

Provenance — the system knows where knowledge came from.

Confidence — the system knows how strongly it should believe knowledge.

Impact — the system understands what changes when knowledge changes.

If a proposed feature does not strengthen one of these, it should probably not be prioritized.
