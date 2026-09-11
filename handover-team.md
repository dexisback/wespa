# AI Knowledge Memory Engine — What We Built & Why It Matters
### Team doc (Mihika, Adrija, Amaan) — also the source material for the professor pitch

---

## 1. The problem, in plain language

Every RAG (retrieval-augmented generation) chatbot you've seen has the same blind spot: it
treats the world as a pile of documents to search through *right now*. It doesn't remember
what it learned yesterday, it doesn't know when a fact it retrieved has since changed, and it
can't tell you *why* it trusts one answer more than another.

That's a real, well-known problem in AI systems — it's called the "stateless memory problem."
Every AI assistant "forgets" the moment the conversation ends, and every RAG pipeline treats
old and new information as equally true, with no sense of time or source trust.

**We built a system that fixes this on a small scale**: it doesn't just search documents, it
builds and maintains a memory — a knowledge graph that updates itself, keeps history instead
of overwriting it, and scores its own confidence in what it tells you.

## 2. What we actually built (plain-English version)

1. **It reads real sources** (tech news, in our seed set) and pulls out facts — "who did what,
   to what, when" — instead of just storing raw text.
2. **It remembers facts as a connected graph**, not a flat list — so it can answer questions
   that require connecting two or three facts together (multi-hop questions), which plain
   document search cannot do.
3. **It never forgets a fact by overwriting it.** If a source says something changed, the old
   fact stays in history with a timestamp. You can ask "what did we believe before, and when
   did that change?"
4. **It knows how much to trust what it says.** Every fact has a confidence score built from
   source reliability and how many independent sources agree.
5. **It shows its work.** Every answer comes with the sources, the confidence, and — if you
   want — the exact graph path it used to arrive at the answer.

## 3. Why this is more than a course chatbot

Most "AI project" submissions are: upload documents → embed → search → answer. That's one
retrieval mode. We built **three** (vector-only, graph-only, hybrid) and we can show, live,
that hybrid wins on multi-hop questions that vector-only search visibly can't answer well.
That comparison *is* our evidence of technical merit — we're not claiming it's better, we're
showing it.

## 4. Architecture, one diagram (for slides)

```
 Trusted Sources → Ingest → Extract Facts → ┌─ Neo4j (graph memory)
                                              ├─ ChromaDB (semantic memory)
                                              └─ PostgreSQL (provenance/audit)
                                                        │
 User Question → Entity extraction → Graph traversal + Vector search → Hybrid evidence
                                                        │
                                          LLM answer (grounded, cited, confidence-scored)
```

## 5. What each of us owns (say this out loud in the pitch — it shows real division of labor)

- **Mihika** — the graph's memory: how facts are stored, versioned over time, scored for
  confidence, and checked for contradictions.
- **Adrija** — the intake: pulling in sources, cleaning and chunking text, extracting
  structured facts with an LLM, and building the semantic (vector) memory.
- **Amaan** — the front door: combining graph + vector retrieval into one answer, the API,
  the UI, and proving it works with real evaluation numbers.

---

## 6. THE PITCH — step by step walkthrough

### A. Framing (30 seconds, before you touch the keyboard)

Say this, roughly in your own words:

> "Every AI chatbot today has amnesia — it forgets everything between conversations, and it
> treats a fact from six months ago the same as a fact from this morning. We built a system
> that actually *remembers*: it maintains a living knowledge graph that updates itself,
> preserves history instead of erasing it, and tells you how confident it is and why."

Do **not** open with "we used Neo4j and ChromaDB." Lead with the problem, not the stack.

### B. The live demo (aim for 3 minutes, rehearsed, not improvised)

1. **Ask a multi-hop question** in the UI — something like *"Which companies did people who
   left [Company X] go on to found?"* — a question that needs two connected facts, not one
   document. This is deliberately impossible for a plain vector search to answer well.
2. **Show the answer** with its evidence cards: sources, confidence score, timestamps.
3. **Open the graph view.** Show the actual path — the nodes and edges — that the system
   walked to build that answer. This is your visual "wow" moment. Let it sit on screen for a
   couple of seconds before you talk over it.
4. **Flip the retrieval mode toggle**: Vector Only → Graph Only → Hybrid. Show that vector-only
   gives a vague or incomplete answer, and hybrid gives the connected, correct one. This
   side-by-side contrast is your strongest piece of evidence — let the professor see the
   difference themselves, don't just tell them.
5. **Ingest a new article that changes a known fact.** Show the graph does *not* erase the old
   fact — it creates a new version with a timestamp, and the old one is still visible in
   history if you ask for it.
6. **Ask a follow-up question** that depends on the updated fact, and show the answer reflects
   the new information.
7. **Close on the evaluation panel** — Hit@5/Recall@5 numbers, multi-hop accuracy, and latency.
   This is what turns "cool demo" into "we measured it and it works."

### C. Anticipate these questions

- *"How is this different from a normal RAG chatbot?"* → Point at the temporal versioning and
  confidence scoring — a plain RAG system has neither. Show the fact-history view again if asked.
- *"How do you know your confidence scores mean anything?"* → Explain the formula plainly:
  source reliability + cross-source agreement + extraction confidence, clamped to [0,1]. It's
  deliberately simple and transparent, not a black box — that's a design choice, say so.
- *"Does this scale beyond your demo dataset?"* → Be honest: the pipeline architecture scales,
  the current seed corpus (20–50 docs) is a deliberately bounded prototype to prove the concept
  cleanly within 24 hours. Frame the constraint as a decision, not a limitation you ran out of
  time to fix.
- *"What would you build next?"* → Have one real answer ready: e.g. broader source domains,
  a learned (rather than formula-based) credibility model, or multi-tenant deployment.

### D. Closing line

> "This isn't a chatbot that searches text — it's a system that builds, maintains, and
> explains its own memory. That's the difference between retrieval and understanding."

---

## 7. Presentation/visual checklist (do these, they cost little and matter a lot)

- [ ] Graph view is interactive and animates the traversal path during the demo (not a static screenshot)
- [ ] Confidence shown as a colored badge/bar, never a raw decimal like `0.734`
- [ ] Source cards styled like clean search-result cards (favicon/domain + timestamp), not raw JSON
- [ ] One rehearsed multi-hop "aha" question, tested beforehand so it reliably works live
- [ ] A fallback: screenshots/recorded clip of the graph view and comparison mode in case live demo has a hiccup
- [ ] Evaluation numbers on screen at the end, not just claimed verbally
- [ ] Everyone on the team can explain their own component in one sentence if asked directly

## 8. One-sentence resume bullet (for LinkedIn/resume afterward)

> Built an AI Knowledge Memory Engine combining Neo4j Graph RAG, ChromaDB vector retrieval,
> and LLM-based entity/relation extraction; implemented confidence-aware, provenance-linked,
> and temporal fact updates to answer multi-hop questions over continuously ingested trusted sources.
