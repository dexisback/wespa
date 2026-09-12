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


_RELEVANCE_STOP = {
    # question words
    "what", "which", "who", "when", "where", "how", "about", "does", "did", "didnt", "give",
    "show", "tell", "tellall", "list", "please", "there", "their", "with", "that", "this",
    "from", "have", "after", "before", "people", "company", "companies", "they", "them",
    "many", "much", "some", "into", "onto", "were", "was", "are", "been", "being", "also",
    "the", "and", "for", "his", "her", "its", "him", "she", "our", "own", "out", "off",
    "per", "via", "can", "will", "why", "yet", "nor", "but", "one", "two", "not", "get",
    "has", "had", "over", "under", "more", "most", "very", "than", "then", "them", "they",
    # months / time
    "september", "october", "august", "january", "february", "march", "april", "may",
    "june", "july", "november", "december", "current", "currently", "latest", "recent",
    "today", "yesterday", "year", "month",
    # generic category words — they appear in off-topic evidence too
    "model", "models", "lineup", "listings", "full", "complete", "entire", "all", "every",
    "top", "best", "popular", "main", "major", "new", "brand", "brands", "range", "series",
    "price", "prices", "sale", "sell", "market", "segment", "type", "types", "kind", "kinds",
    "name", "names", "known", "know", "info", "information", "details", "detail",
    "car", "cars", "motor", "motors", "vehicle", "vehicles", "truck", "trucks",
}


def _salient_tokens(question: str) -> list[str]:
    """Content words that identify the requested topic/entity — category words
    ('model', 'lineup', 'cars') are excluded because they appear in off-topic
    evidence (e.g. a Maruti passage also contains 'model')."""
    words = [w.lower().strip(".,?!'\"") for w in question.split()]
    return [w for w in words if len(w) >= 3 and w not in _RELEVANCE_STOP]


def _token_hit(token: str, hay: str) -> bool:
    # suffix-tolerant word-boundary match: "found" hits "founded", "raise" hits "raised"
    return re.search(rf"\b{re.escape(token)}(?:s|es|ed|d|ing)?\b", hay) is not None


def _relevance_report(question: str, facts: list[Fact], passages: list) -> dict:
    """Per-item topical relevance: does the evidence actually mention the
    requested entities/topics? Word-boundary matching, no fuzzy substring hits."""
    salient = _salient_tokens(question)
    fact_texts = [f"{f.subject_name} {f.relation.replace('_', ' ')} {f.object_name}" for f in facts]
    passage_texts = [p.text for p in passages]
    hay = " ".join(fact_texts + passage_texts).lower()

    hits = [t for t in salient if _token_hit(t, hay)]
    relevant_facts = sum(
        1 for text in fact_texts if any(_token_hit(t, text.lower()) for t in salient)
    )
    relevant_passages = sum(
        1 for text in passage_texts if any(_token_hit(t, text.lower()) for t in salient)
    )
    relevance = (len(hits) / len(salient)) if salient else 1.0
    return {
        "salient": salient,
        "hits": hits,
        "relevance": relevance,
        "relevant_facts": relevant_facts,
        "relevant_passages": relevant_passages,
        "irrelevant_passages": len(passage_texts) - relevant_passages,
    }


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


def _sufficiency(facts: list[Fact], passages: list, conf: float, question: str = "") -> dict:
    """Deterministic memory-sufficiency verdict with a human-readable reason.

    Distinguishes RETRIEVED SOMETHING from RETRIEVED RELEVANT EVIDENCE:
    evidence must actually mention the requested entities/topics — passages
    about Maruti Suzuki do not answer a question about Kia Motors."""
    rep = _relevance_report(question, facts, passages)
    evidence_count = len(facts) + len(passages)
    conf = round(conf, 3)
    base = {"evidence_count": evidence_count, "confidence": round(conf, 3), **rep}

    if evidence_count == 0:
        return {"sufficient": False, "reason": "no evidence in memory for this question", **base}
    if rep["salient"] and not rep["hits"]:
        topics = ", ".join(rep["salient"][:4])
        return {
            "sufficient": False,
            "reason": (
                f"retrieved evidence does not mention the requested topic ({topics}) — "
                f"{rep['relevant_facts']} relevant graph facts, {rep['relevant_passages']} relevant "
                f"of {evidence_count} evidence item(s)"
            ),
            **base,
        }
    if evidence_count < 2:
        # a single precise, on-topic fact can be enough (e.g. "what is X valued at?");
        # a single weak/off-topic item is not
        if conf >= 0.6 and rep["relevance"] >= 0.5:
            return {
                "sufficient": True,
                "reason": f"{rep['relevant_facts']} relevant graph facts and {rep['relevant_passages']} relevant passages cover the question (confidence {conf:.2f})",
                **base,
            }
        return {"sufficient": False, "reason": f"only {evidence_count} evidence item(s) in memory — too thin to answer reliably", **base}
    if conf < 0.45:
        return {"sufficient": False, "reason": f"memory evidence is low-confidence ({conf:.2f})", **base}
    if rep["salient"] and rep["relevance"] < 0.6:
        missing = [t for t in rep["salient"] if t not in rep["hits"]][:3]
        return {
            "sufficient": False,
            "reason": (
                f"memory contains related material, but not about this topic "
                f"(topical relevance {rep['relevance']:.2f}, missing '{', '.join(missing)}'; "
                f"{rep['relevant_facts']} relevant graph facts, {rep['relevant_passages']} relevant passages)"
            ),
            **base,
        }
    return {
        "sufficient": True,
        "reason": (
            f"{rep['relevant_facts']} relevant graph facts and {rep['relevant_passages']} relevant passages "
            f"cover the question (confidence {conf:.2f})"
        ),
        **base,
    }


def answer_question(question: str, mode: str = "hybrid", allow_live: bool = False, as_of: str | None = None) -> QueryResult:
    """Full query operation, timed end-to-end; logs to PostgreSQL.

    Memory-first flow: search persistent memory; if the evidence is insufficient
    AND the caller allows it, fetch live evidence from the web, run it through
    the standard ingestion pipeline (dedup, temporal versioning, provenance),
    then re-run retrieval and answer from the updated memory."""
    from ..extraction.schemas import ConfidenceBreakdown, PipelineStage

    started = time.perf_counter()
    query_id = f"q_{uuid.uuid4().hex[:10]}"
    pipeline: list[PipelineStage] = []

    def _re_retrieve():
        b = retrieve(question, mode, as_of=as_of)
        b["question"] = question
        return b

    bundle = _re_retrieve()
    if as_of:
        pipeline.append(PipelineStage(name="Time travel", detail=f"Memory as of {as_of[:10]}"))

    facts = bundle["facts"]
    passages = bundle["passages"]
    conf_est, _ = answer_confidence(facts, passages, conflicts=0)
    verdict = _sufficiency(facts, passages, conf_est, question)
    sufficient = verdict["sufficient"]
    if sufficient:
        pipeline.append(PipelineStage(name="Memory found", detail=verdict["reason"]))
    elif bundle["facts"] or bundle["passages"]:
        # retrieved something, but it is not relevant enough to answer — say exactly that
        pipeline.append(
            PipelineStage(
                name="Memory insufficient",
                detail=(
                    f"{verdict['relevant_facts']} relevant graph facts · "
                    f"{verdict['relevant_passages']} relevant / {verdict['irrelevant_passages']} "
                    f"low-relevance passages"
                ),
            )
        )
    else:
        pipeline.append(PipelineStage(name="Checking memory", detail="no matching evidence"))

    live_fetch: dict | None = None
    live_retrieval_used = False
    still_insufficient = False

    if not sufficient and allow_live:
        if bundle["facts"] or bundle["passages"]:
            pipeline.append(PipelineStage(name="Fetching new evidence", detail=verdict["reason"]))
        else:
            pipeline.append(PipelineStage(name="Memory insufficient", detail=verdict["reason"]))
        try:
            from ..ingestion.web_search import live_retrieval

            live_fetch = live_retrieval(question)
        except Exception as e:
            log.warning("live retrieval failed: %s", e)
            live_fetch = {"ok": False, "error": f"live retrieval failed: {e}"}

        if live_fetch and live_fetch.get("ok"):
            live_retrieval_used = True
            pipeline.append(
                PipelineStage(
                    name="Memory updated",
                    detail=(
                        f"+{live_fetch.get('documents_added', 0)} source(s), "
                        f"+{live_fetch.get('entities_added', 0)} entities, "
                        f"+{live_fetch.get('relationships_added', 0)} relationships, "
                        f"{live_fetch.get('facts_superseded', 0)} superseded, "
                        f"{live_fetch.get('facts_corroborated', 0)} corroborated"
                    ),
                )
            )
            bundle = _re_retrieve()
            facts, passages = bundle["facts"], bundle["passages"]
            conf_est, _ = answer_confidence(facts, passages, conflicts=0)
            verdict2 = _sufficiency(facts, passages, conf_est, question)
            pipeline.append(PipelineStage(name="Re-checking memory", detail=verdict2["reason"]))
            if not verdict2["sufficient"]:
                still_insufficient = True
                pipeline.append(PipelineStage(name="Evidence still insufficient", detail="new sources were not enough to answer confidently"))
        elif live_fetch and live_fetch.get("ingested_but_empty"):
            live_retrieval_used = True
            pipeline.append(PipelineStage(name="Live fetch found nothing useful", detail=str(live_fetch.get("error", ""))))
        else:
            pipeline.append(PipelineStage(name="Couldn't fetch new evidence", detail=str((live_fetch or {}).get("error", "no results"))))

    if facts or passages:
        pipeline.append(PipelineStage(name="Merging evidence", detail=f"{len(facts)} facts · {len(passages)} passages"))
    conflicts = [f"{f.subject_name} {f.relation} {f.object_name}" for f in facts if f.conflict]
    answer, confidence, conf_label = generate_answer(question, facts, passages, mode, as_of=as_of)
    if conflicts:
        confidence = round(confidence * 0.8, 3)
        conf_label = label(confidence)

    if not sufficient and allow_live:
        if live_retrieval_used:
            pipeline.append(PipelineStage(name="Answer grounded in updated memory", detail=f"confidence {confidence} ({conf_label})"))
            if still_insufficient:
                answer = "I found new sources and added them to memory, but they still weren't sufficient to answer confidently.\n\n" + answer
        elif live_fetch and live_fetch.get("ok"):
            pass
        else:
            # fetch failed or found nothing usable — say so explicitly, don't pretend
            answer = "I couldn't fetch new evidence right now. " + answer
            confidence = round(min(confidence, 0.25), 3)
            conf_label = label(confidence)

    sources: list[str] = []
    src_names: dict[str, str] = {}
    try:
        from ..graph.neo4j_client import get_graph

        src_names = {
            s["id"]: s["name"] for s in get_graph().run("MATCH (s:Source) RETURN s.id AS id, s.name AS name")
        }
    except Exception:
        pass
    fetched_urls = {
        (s.get("url") or "")
        for s in ((live_fetch or {}).get("fetched_sources") or [])
        if isinstance(s, dict)
    }
    cards = source_cards(bundle)
    for c in cards:
        if c.url and c.url in fetched_urls:
            c.is_new = True
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
    memory_updates = None
    if live_retrieval_used and live_fetch:
        memory_updates = {
            "documents_added": live_fetch.get("documents_added", 0),
            "entities_added": live_fetch.get("entities_added", 0),
            "relationships_added": live_fetch.get("relationships_added", 0),
            "facts_superseded": live_fetch.get("facts_superseded", 0),
            "facts_corroborated": live_fetch.get("facts_corroborated", 0),
            "conflicts_flagged": live_fetch.get("conflicts_flagged", 0),
            "documents_skipped_duplicate": live_fetch.get("documents_skipped_duplicate", 0),
        }
    result = QueryResult(
        query_id=query_id,
        answer=answer,
        facts=facts,
        passages=passages,
        sources=sources,
        source_cards=cards,
        confidence=confidence,
        confidence_label=conf_label,
        confidence_breakdown=ConfidenceBreakdown(**_confidence_breakdown(facts, passages, len(conflicts))),
        conflicts=conflicts,
        entities_matched=bundle["matched"],
        retrieval_mode=mode,
        latency_ms=latency_ms,
        graph_path=bundle["graph_path"],
        pipeline=pipeline,
        memory_sufficient=verdict2["sufficient"] if live_retrieval_used and allow_live and not sufficient else sufficient,
        memory_reason=verdict2["reason"] if live_retrieval_used and allow_live and not sufficient else verdict["reason"],
        evidence_count=len(facts) + len(passages),
        live_retrieval_used=live_retrieval_used,
        sources_fetched=(live_fetch or {}).get("fetched_sources") or [],
        memory_updates=memory_updates,
        still_insufficient=still_insufficient,
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
