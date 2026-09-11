# Demo Script (3 minutes)

Prep: `docker compose up -d`, `python scripts/bootstrap_memory.py` (offline; or `build_memory.py` with a
working Groq key), `uvicorn app.main:fastapi_app --port 8000`, open http://localhost:8000. Do NOT
pre-ingest the June fixture. For a rate-limit-proof demo set `SKIP_LLM=true` in `.env` — queries stay
instant (~400ms) and answers are assembled deterministically from the retrieved facts with full citations.

1. **Ask (multi-hop).** Type or click: *"Which companies did people who left OpenAI go on to found?"*
   Point out the loading stages — entity extraction, graph search, semantic search, merge.
2. **Answer surfaces** with confidence badge, source cards, and latency. Note the confidence is
   computed from the locked formula — source trust + corroboration + extraction certainty.
3. **Graph.** Pause on the traversal path: OpenAI ← person → FOUNDED → new company. This path is the
   backend's actual retrieval output, not decoration.
4. **Compare modes.** Toggle **Vector only** — answer gets vague, graph panel says "No graph traversal
   used". Toggle **Graph only** — connected facts, no passages. Back to **Hybrid** — the best of both.
   The toggle re-queries each time; the difference is live.
5. **Update memory.** Click **Ingest new source** → pick the June Reuters fixture. Toast shows
   entities/relationships added and "N facts updated (previous version preserved)".
6. **Prove memory is temporal.** Click the SSI valuation fact chip → the drawer shows both versions:
   $20B (Apr, historical) and $32B (Jun, current belief). The old version was never deleted.
7. **Ask again.** *"What is Safe Superintelligence valued at?"* → $32B from the new memory.
8. **Evaluation.** Open the Evaluation panel → run → real numbers: Hit@5, Recall@5, multi-hop accuracy,
   average latency, temporal correctness PASS, confidence weighting PASS.

Closing line: *"This isn't a chatbot that searches text — it's a system that builds, maintains, and
explains its own memory. That's the difference between retrieval and understanding."*

Fallbacks: if the LLM is rate-limited, wait a beat and retry — errors surface as friendly toasts and the
UI stays usable. If a source dies, ingestion skips it and reports the failure without killing the batch.
