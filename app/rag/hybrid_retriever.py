from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone

from ..config import SKIP_LLM, TOP_K_PASSAGES
from ..db.postgres import get_db
from ..extraction.schemas import Fact, QueryResult, SourceCard
from ..graph.graph_retriever import retrieve_facts
from ..rag.answer_generator import answer_confidence, generate_answer
from ..trust.confidence import label
from ..trust.source_weights import DEFAULT_RELIABILITY, reliability, source_id_for
from ..vector.vector_retriever import search as vector_search

log = logging.getLogger("rag.hybrid")

QUERY_ENTITY_TMPL = (
    'Extract entity names from this question, JSON only, like {{"entities": ["X"]}}: {q}'
)


def extract_query_entities(question: str) -> list[str]:
    """Small LLM call to find which entities to seed the graph traversal with.
    Falls back to capitalized n-grams when the LLM is unavailable or SKIP_LLM is set."""
    if SKIP_LLM:
        return _fallback_entities(question)
    from ..llm import LLMError, chat_json

    try:
        data = chat_json(
            [
                {"role": "system", "content": "You extract entity names. JSON only."},
                {"role": "user", "content": QUERY_ENTITY_TMPL.format(q=question)},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        names = [str(n).strip() for n in data.get("entities", []) if str(n).strip()]
        if names:
            return names[:6]
    except (LLMError, Exception):
        pass
    return _fallback_entities(question)


_STOP_TOKENS = {
    "which", "what", "who", "when", "where", "how", "company", "companies", "people",
    "person", "startup", "startups", "after", "leaving", "left", "found", "founded",
    "become", "connected", "they", "that", "this", "have", "was", "were", "did",
    "does", "between", "march", "june", "april", "there", "their", "about", "with",
}


def _fallback_entities(question: str) -> list[str]:
    candidates = set()
    words = question.replace("?", " ").replace(",", " ").replace(".", " ").split()
    for i, w in enumerate(words):
        if w[:1].isupper() and len(w) >= 4 and w.lower() not in _STOP_TOKENS:
            pair = f"{w} {words[i + 1]}" if i + 1 < len(words) and words[i + 1][:1].isupper() else None
            candidates.add(w)
            if pair:
                candidates.add(pair)
    return sorted(candidates)[:6]


def retrieve(question: str, mode: str, as_of: str | None = None) -> dict:
    """Three genuinely different retrieval paths."""
    bundle = {"facts": [], "passages": [], "graph_path": None, "matched": [], "chain_fact_ids": [], "used_graph": False, "used_vector": False}

    if mode in ("vector", "hybrid"):
        bundle["passages"] = vector_search(question, k=TOP_K_PASSAGES, as_of=as_of)
        bundle["used_vector"] = True

    if mode in ("graph", "hybrid"):
        seeds = extract_query_entities(question)
        result = retrieve_facts(seeds, as_of=as_of)
        bundle["facts"] = result["facts"]
        bundle["graph_path"] = result["graph_path"]
        bundle["matched"] = result["matched"]
        bundle["chain_fact_ids"] = result.get("chain_fact_ids", [])
        bundle["used_graph"] = True
        if bundle["facts"]:
            bundle["facts"] = _rank_facts(bundle["facts"], question, bundle["chain_fact_ids"])

    if mode == "hybrid":
        bundle["facts"], bundle["passages"] = _merge_rank(bundle["facts"], bundle["passages"], question, bundle["chain_fact_ids"])
    return bundle


_RELEVANCE_STOP = {"what", "which", "who", "when", "where", "how", "about", "does", "did", "didnt",
                   "september", "october", "august", "january", "february", "march", "april", "may",
                   "june", "july", "november", "december", "there", "their", "with", "that", "this",
                   "from", "have", "after", "before", "people", "company"}


def _topical_relevance(question: str, facts: list[Fact], passages: list) -> float:
    """Share of the question's content words that appear in the retrieved evidence.
    Low relevance means memory contains related-but-off-topic material."""
    words = [w.lower().strip(".,?!'\"") for w in question.split()]
    words = [w for w in words if len(w) >= 4 and w not in _RELEVANCE_STOP]
    if not words:
        return 1.0
    hay = " ".join(
        [f"{f.subject_name} {f.relation} {f.object_name}" for f in facts]
        + [p.text for p in passages]
    ).lower()
    hits = sum(1 for w in words if w in hay)
    return hits / len(words)


def _memory_sufficient(facts: list[Fact], passages: list, conf: float, question: str = "") -> bool:
    """Memory-first check: is the persistent memory strong enough to answer?
    Needs 2+ evidence items, at least medium confidence, AND topical relevance —
    memory containing adjacent material is not the same as containing the answer."""
    if (len(facts) + len(passages)) < 2 or conf < 0.45:
        return False
    return _topical_relevance(question, facts, passages) >= 0.35


def _confidence_breakdown(facts: list[Fact], passages: list, conflicts: int) -> dict:
    """Human-readable components of the confidence formula for the Why panel."""
    from ..trust.confidence import confidence as _conf

    if facts:
        rels = [reliability(f.source_name or f.source_id.replace("src_", "").replace("_", " ").title()) for f in facts[:5]]
        exts = [f.extraction_confidence for f in facts[:5]]
        ags = [0.5 + min(getattr(f, "corroborations", 1) - 1, 2) * 0.25 for f in facts[:5]]
        rel, ext, ag = sum(rels) / len(rels), sum(exts) / len(exts), sum(ags) / len(ags)
    elif passages:
        rel = sum(reliability(p.source) for p in passages) / max(1, len(passages))
        ext = 0.8
        ag = 1.0 if len(passages) > 1 else 0.5
    else:
        rel, ext, ag = DEFAULT_RELIABILITY, 0.0, 0.5
    supporting = len({f.source_name or f.source_id for f in facts} | {p.source for p in passages})
    c = _conf(rel, ag, ext)
    if conflicts:
        c *= 0.8
    why = f"Composite = 0.5·source reliability ({rel:.2f}) + 0.3·cross-source agreement ({ag:.2f}) + 0.2·extraction confidence ({ext:.2f})"
    if conflicts:
        why += f", ×0.8 for {conflicts} conflicting claim(s)"
    return {
        "source_reliability": round(rel, 3),
        "cross_source_agreement": round(ag, 3),
        "extraction_confidence": round(ext, 3),
        "supporting_sources": supporting,
        "conflicting_sources": conflicts,
        "explanation": why,
    }


def _question_overlap(fact: Fact, question: str) -> int:
    ql = question.lower()
    n = 0
    for name in (fact.subject_name, fact.object_name):
        if name and name.lower() in ql:
            n += 1
    return n


# question wording -> relation labels the question is asking about
_RELATION_HINTS: list[tuple[str, frozenset[str]]] = [
    (r"\bfound(?:ed)?\b|\bco-?founded\b|\bstarted\b|\bset up\b", frozenset({"FOUNDED"})),
    (r"\bacquir(?:e|ed|es|ing)\b|\bbought\b|\bmerged\b", frozenset({"ACQUIRED"})),
    (r"\binvest(?:e|ed|ing|ment)s?\b|\bbacked\b|\bfunded\b", frozenset({"INVESTED_IN"})),
    (r"\brais(?:e|ed|es|ing)\b|\bfunding\b|\braise\b", frozenset({"RAISED"})),
    (r"\bvalu(?:e|ed|es|ation)\b", frozenset({"VALUED_AT"})),
    (r"\breleas(?:e|ed|es|ing)\b|\blaunche?d?\b|\bshipped\b|\bdebut\b", frozenset({"RELEASED"})),
    (r"\bjoined\b|\bworks?\b|\bworked\b|\bhiring\b|\bhired\b|\bre-?hiring\b", frozenset({"WORKED_AT", "ACQUIRED"})),
    (r"\bleads?\b|\bCEO\b|\bruns?\b|\bhead of\b|\bchief\b", frozenset({"LEADS"})),
]


def _relation_intent(fact: Fact, question: str) -> int:
    ql = question.lower()
    for pattern, relations in _RELATION_HINTS:
        if re.search(pattern, ql) and fact.relation in relations:
            return 1
    return 0


_MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _temporal_window(question: str) -> tuple[str, str] | None:
    """Parse a time anchor like 'April 2025' or 'in 2025' into a YYYY-MM range."""
    ql = question.lower()
    for name, num in _MONTH_NUMBERS.items():
        m = re.search(rf"\b{name}\s+(\d{{4}})\b", ql)
        if m:
            return (f"{m.group(1)}-{num:02d}", f"{m.group(1)}-{num:02d}")
    m = re.search(r"\b(?:in|during)\s+(\d{4})\b", ql)
    if m:
        return (f"{m.group(1)}-01", f"{m.group(1)}-12")
    return None


def _fact_score(f: Fact, question: str, chain_ids: set[str], window) -> float:
    score = f.confidence * 0.8
    if f.active:
        score += 1.0
    if f.fact_id in chain_ids:
        score += 0.7
    score += 0.9 * _relation_intent(f, question)
    score += 0.5 * _question_overlap(f, question)
    if window and f.observed_at and window[0] <= str(f.observed_at)[:7] <= window[1]:
        score += 0.9
    score += 0.05 * getattr(f, "corroborations", 0)
    return score


def _rank_facts(facts: list[Fact], question: str, chain_ids: set[str]) -> list[Fact]:
    """Shared ranking: weighted evidence score (active, chain, intent, overlap, time)."""
    window = _temporal_window(question)
    return sorted(
        facts,
        key=lambda f: -_fact_score(f, question, set(chain_ids or ()), window),
    )


def _merge_rank(facts: list[Fact], passages, question: str, chain_ids: set[str] | None = None) -> tuple[list[Fact], list]:
    """Rank merged evidence: facts re-ranked with the question, passages by similarity."""
    ranked = _rank_facts(sorted(facts or [], key=lambda f: f.fact_id), question, chain_ids or set())
    passages = sorted(passages or [], key=lambda p: -(p.similarity or 0.0))
    return ranked[:16], passages[:TOP_K_PASSAGES]


def source_cards(bundle: dict) -> list[SourceCard]:
    cards: dict[str, SourceCard] = {}

    def add(name, title, url, published, conf):
        if not name or name in cards:
            return
        cards[name] = SourceCard(
            source_name=name, title=title or "", url=url or "",
            published_at=published, confidence=conf,
        )

    for f in bundle["facts"]:
        src_name = f.source_name or f.source_id.replace("src_", "").replace("_", " ").title()
        add(src_name, "", "", f.observed_at, f.confidence)
    for p in bundle["passages"]:
        add(p.source, p.title, p.url, p.published_at, None)
    return list(cards.values())


def answer_question(question: str, mode: str = "hybrid", allow_live: bool = False, as_of: str | None = None) -> QueryResult:
    """Full query operation, timed end-to-end; logs to PostgreSQL.

    Memory-first flow: search persistent memory; if the evidence is insufficient
    AND the caller allows it, fetch live evidence from the web, ingest it into
    memory, and answer from the updated memory."""
    from ..extraction.schemas import ConfidenceBreakdown, PipelineStage

    started = time.perf_counter()
    query_id = f"q_{uuid.uuid4().hex[:10]}"
    pipeline: list[PipelineStage] = []

    bundle = retrieve(question, mode, as_of=as_of)
    bundle["question"] = question
    if as_of:
        pipeline.append(PipelineStage(name="Time travel", detail=f"Memory as of {as_of[:10]}"))
    if bundle["facts"] or bundle["passages"]:
        pipeline.append(PipelineStage(name="Memory found", detail=f"{len(bundle['facts'])} graph facts · {len(bundle['passages'])} passages"))

    facts = bundle["facts"]
    passages = bundle["passages"]
    conf_est, _ = answer_confidence(facts, passages, conflicts=0)
    sufficient = _memory_sufficient(facts, passages, conf_est, question)

    live_fetch: dict | None = None
    if not sufficient and allow_live:
        pipeline.append(PipelineStage(name="Memory insufficient", detail="Not enough reliable evidence in persistent memory"))
        try:
            from ..ingestion.web_search import live_retrieval

            live_fetch = live_retrieval(question)
        except Exception as e:
            log.warning("live retrieval failed: %s", e)
            live_fetch = {"ok": False, "error": str(e)}
        if live_fetch and live_fetch.get("ok"):
            pipeline.append(
                PipelineStage(
                    name="Memory updated",
                    detail=f"+{live_fetch.get('documents_added', 0)} source(s), +{live_fetch.get('relationships_added', 0)} relationships",
                )
            )
            bundle = retrieve(question, mode, as_of=as_of)
            bundle["question"] = question
            facts, passages = bundle["facts"], bundle["passages"]
        else:
            pipeline.append(PipelineStage(name="Live fetch unavailable", detail=str((live_fetch or {}).get("error", "no results"))))

    if facts or passages:
        pipeline.append(PipelineStage(name="Merging evidence", detail=f"{len(facts)} facts · {len(passages)} passages"))
    conflicts = [f"{f.subject_name} {f.relation} {f.object_name}" for f in facts if f.conflict]
    answer, confidence, conf_label = generate_answer(question, facts, passages, mode, as_of=as_of)
    if conflicts:
        confidence = round(confidence * 0.8, 3)
        conf_label = label(confidence)
    if not sufficient and allow_live:
        pipeline.append(PipelineStage(name="Answer grounded in updated memory", detail=f"confidence {confidence} ({conf_label})"))

    sources: list[str] = []
    src_names: dict[str, str] = {}
    try:
        from ..graph.neo4j_client import get_graph

        src_names = {
            s["id"]: s["name"] for s in get_graph().run("MATCH (s:Source) RETURN s.id AS id, s.name AS name")
        }
    except Exception:
        pass
    for f in facts:
        fallback = f.source_id.replace("src_", "").replace("_", " ").title()
        f.source_name = f.source_name or src_names.get(f.source_id, "").replace("_", " ") or fallback
        if f.source_name and f.source_name not in sources:
            sources.append(f.source_name)
    for p in passages:
        if p.source and p.source not in sources:
            sources.append(p.source)

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    dependencies = [f.fact_id for f in facts[:8]]
    result = QueryResult(
        query_id=query_id,
        answer=answer,
        facts=facts,
        passages=passages,
        sources=sources,
        source_cards=source_cards(bundle),
        confidence=confidence,
        confidence_label=conf_label,
        confidence_breakdown=ConfidenceBreakdown(**_confidence_breakdown(facts, passages, len(conflicts))),
        conflicts=conflicts,
        entities_matched=bundle["matched"],
        retrieval_mode=mode,
        latency_ms=latency_ms,
        graph_path=bundle["graph_path"],
        pipeline=pipeline,
        memory_sufficient=sufficient,
        live_fetch=live_fetch,
        answer_dependencies=dependencies,
        as_of=as_of,
    )
    try:
        db = get_db()
        db.log_query(query_id, question, mode, latency_ms)
        db.log_answer_facts(query_id, dependencies)
    except Exception as e:
        log.warning("query logging skipped: %s", e)
    return result
