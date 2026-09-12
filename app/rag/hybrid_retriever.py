from __future__ import annotations

import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..config import TOP_K_PASSAGES, reset_skip_llm_override, set_skip_llm_override, should_skip_llm
from ..db.postgres import get_db
from ..extraction.schemas import Fact, GraphPath, QueryResult, SourceCard
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
    if should_skip_llm():
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
    "latest", "update", "updates", "recent", "current", "currently", "right", "today",
    "italian", "prime", "minister",
}


def _fallback_entities(question: str) -> list[str]:
    candidates = set()
    words = re.findall(r"[A-Za-z][\w'\-]*", question)
    for i, w in enumerate(words):
        if w[:1].isupper() and len(w) >= 4 and w.lower() not in _STOP_TOKENS:
            pair = f"{w} {words[i + 1]}" if i + 1 < len(words) and words[i + 1][:1].isupper() else None
            candidates.add(w)
            if pair:
                candidates.add(pair)
        elif len(w) >= 4 and w.lower() not in _STOP_TOKENS:
            # People often type proper names in lowercase. Neo4j performs
            # partial, case-insensitive matching, so these are safe seeds.
            candidates.add(w)
    return sorted(candidates)[:6]


def retrieve(
    question: str,
    mode: str,
    as_of: str | None = None,
    entity_seeds: list[str] | None = None,
    document_ids: list[str] | None = None,
) -> dict:
    """Three genuinely different retrieval paths."""
    bundle = {"facts": [], "passages": [], "graph_path": GraphPath(), "matched": [], "chain_fact_ids": [], "used_graph": False, "used_vector": False}

    if mode in ("vector", "hybrid"):
        bundle["passages"] = vector_search(question, k=TOP_K_PASSAGES, as_of=as_of)
        bundle["used_vector"] = True

    if mode in ("graph", "hybrid"):
        seeds = entity_seeds if entity_seeds is not None else extract_query_entities(question)
        result = retrieve_facts(seeds, as_of=as_of, document_ids=document_ids)
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
    # query framing / generic intent words — these must not make unrelated
    # documents look topical (for example car articles for a bike question).
    "latest", "current", "currently", "right", "now", "today", "recent", "recently",
    "model", "models", "lineup", "launch", "launched", "launches", "launching",
    "give", "all", "list", "listing", "tell", "show", "find", "go",
}


def _salient_tokens(question: str) -> list[str]:
    """Content words that identify the requested topic/entity — category words
    ('model', 'lineup', 'cars') are excluded because they appear in off-topic
    evidence (e.g. a Maruti passage also contains 'model')."""
    words = [w.lower().strip(".,?!'\"") for w in question.split()]
    return [w for w in words if len(w) >= 3 and w not in _RELEVANCE_STOP]


def _topic_phrases(question: str, salient: list[str]) -> list[str]:
    """Return multi-word topic phrases that must survive retrieval together."""
    words = [w.lower().strip(".,?!'\"") for w in question.split()]
    salient_set = set(salient)
    return [
        f"{words[i]} {words[i + 1]}"
        for i in range(len(words) - 1)
        if words[i] in salient_set and words[i + 1] in salient_set
    ]


def _token_hit(token: str, hay: str) -> bool:
    # suffix-tolerant word-boundary match: "found" hits "founded", "raise" hits "raised"
    return re.search(rf"\b{re.escape(token)}(?:s|es|ed|d|ing)?\b", hay) is not None


def _check_temporal_mismatch(question: str, evidence_text: str) -> dict:
    """Detect when the question asks about a specific time period but evidence is from different periods.
    Also detects other specificity mismatches (locations, versions, models, etc.).
    Returns {has_mismatch: bool, requested: str, found: list[str], reason: str}"""
    q_lower = question.lower()
    
    # Extract years from question (4-digit numbers likely to be years)
    question_years = set(re.findall(r'\b(19\d{2}|20\d{2})\b', question))
    
    # Extract temporal references from question
    question_temporal = []
    if re.search(r'\b(latest|current|currently|right now|today|this year|recent|recently)\b', q_lower):
        question_temporal.append("current/recent")
    if re.search(r'\b(last year|previous year|past year)\b', q_lower):
        question_temporal.append("last year")
    if question_years:
        question_temporal.extend(question_years)
    
    # Extract years from evidence
    evidence_years = set(re.findall(r'\b(19\d{2}|20\d{2})\b', evidence_text))
    
    # If question asks for specific year(s) but evidence mentions different years
    if question_years:
        if evidence_years and not (question_years & evidence_years):
            # Evidence has years but none match the requested year
            return {
                "has_mismatch": True,
                "requested": ", ".join(sorted(question_years)),
                "found": sorted(evidence_years),
                "reason": f"evidence mentions {', '.join(sorted(evidence_years)[:5])} but question asks about {', '.join(sorted(question_years))}"
            }
    
    # If question asks for current/recent info, flag if evidence seems dated
    if "current/recent" in question_temporal:
        from datetime import datetime, timezone as tz
        current_year = datetime.now(tz.utc).year
        if evidence_years:
            most_recent = max(int(y) for y in evidence_years)
            if most_recent < current_year - 1:  # Evidence is 2+ years old
                return {
                    "has_mismatch": True,
                    "requested": "current/recent information",
                    "found": sorted(evidence_years),
                    "reason": f"question asks for current/recent info but evidence is from {most_recent}"
                }
    
    # Check for version/model mismatches (e.g., "iPhone 15" vs "iPhone 14")
    version_patterns = [
        (r'\b(version|v\.|ver\.?)\s*([0-9]+(?:\.[0-9]+)*)', 'version'),
        (r'\b([A-Z][a-z]+)\s+([0-9]{1,2})\b', 'model'),  # "iPhone 15", "Formula 1"
        (r'\b(generation|gen)\s+([0-9]+)', 'generation'),
    ]
    for pattern, label in version_patterns:
        q_matches = set(re.findall(pattern, question, re.IGNORECASE))
        e_matches = set(re.findall(pattern, evidence_text, re.IGNORECASE))
        if q_matches and e_matches and not (q_matches & e_matches):
            q_str = ', '.join(f"{m[0]} {m[1]}" for m in sorted(q_matches)[:3])
            e_str = ', '.join(f"{m[0]} {m[1]}" for m in sorted(e_matches)[:3])
            return {
                "has_mismatch": True,
                "requested": q_str,
                "found": [e_str],
                "reason": f"question asks about {q_str} but evidence discusses {e_str}"
            }
    
    return {"has_mismatch": False, "requested": None, "found": [], "reason": ""}


def _relevance_report(question: str, facts: list[Fact], passages: list) -> dict:
    """Per-item topical relevance: does the evidence actually mention the
    requested entities/topics? Word-boundary matching, no fuzzy substring hits."""
    salient = _salient_tokens(question)
    anchors = [t for t in salient if t not in _RELEVANCE_STOP]
    if not anchors:
        anchors = salient
    fact_texts = [f"{f.subject_name} {f.relation.replace('_', ' ')} {f.object_name}" for f in facts]
    passage_texts = [p.text for p in passages]
    hay = " ".join(fact_texts + passage_texts).lower()
    phrases = _topic_phrases(question, salient)

    hits = [t for t in salient if _token_hit(t, hay)]
    anchor_hits = [t for t in anchors if _token_hit(t, hay)]
    def item_relevant(text: str) -> bool:
        lower = text.lower()
        if phrases and any(phrase in lower for phrase in phrases):
            return True
        item_hits = sum(1 for t in anchors if _token_hit(t, lower))
        if len(anchors) <= 1:
            return item_hits > 0
        return item_hits >= max(2, (len(anchors) + 1) // 2)

    relevant_facts = sum(1 for text in fact_texts if item_relevant(text))
    relevant_passages = sum(1 for text in passage_texts if item_relevant(text))
    topic_items = fact_texts + passage_texts
    item_coverages = [
        sum(1 for t in anchors if _token_hit(t, text.lower())) / max(1, len(anchors))
        for text in topic_items
    ]
    item_topic_relevance = max(item_coverages, default=0.0)
    if phrases and any(phrase in text.lower() for phrase in phrases for text in topic_items):
        item_topic_relevance = 1.0
    relevance = (len(hits) / len(salient)) if salient else 1.0
    topic_relevance = (len(anchor_hits) / len(anchors)) if anchors else relevance
    
    # Temporal mismatch detection: check if question asks about a specific year/time period
    temporal_mismatch = _check_temporal_mismatch(question, hay)
    
    return {
        "salient": salient,
        "anchors": anchors,
        "hits": hits,
        "anchor_hits": anchor_hits,
        "relevance": relevance,
        "topic_relevance": topic_relevance,
        "item_topic_relevance": item_topic_relevance,
        "topic_phrases": phrases,
        "relevant_facts": relevant_facts,
        "relevant_passages": relevant_passages,
        "irrelevant_passages": len(passage_texts) - relevant_passages,
        "temporal_mismatch": temporal_mismatch,
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
        source_count = len({p.source for p in passages if p.source})
        ag = 0.5 if source_count <= 1 else 0.75 if source_count == 2 else 1.0
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
        if not name:
            return
        # Keep separate article cards even when two results come from the same
        # publisher; live retrieval should visibly show the evidence breadth.
        key = url or f"{name}:{title}"
        if key in cards:
            return
        cards[key] = SourceCard(
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
    
    # Check for temporal mismatch: question asks about 2016, evidence is about 2014/2015/2026
    if rep.get("temporal_mismatch", {}).get("has_mismatch"):
        tm = rep["temporal_mismatch"]
        return {
            "sufficient": False,
            "reason": f"memory contains related material but from wrong time period — {tm['reason']}",
            **base,
        }
    
    if rep["anchors"] and not rep["anchor_hits"]:
        topics = ", ".join(rep["anchors"][:4])
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
    if rep["anchors"] and (
        rep["topic_relevance"] < 0.6
        or (rep.get("topic_phrases") and rep.get("item_topic_relevance", 0.0) < 1.0)
    ):
        missing = [t for t in rep["anchors"] if t not in rep["anchor_hits"]][:3]
        return {
            "sufficient": False,
            "reason": (
                f"memory contains related material, but not about this topic "
                f"(topical relevance {rep['topic_relevance']:.2f}, missing '{', '.join(missing)}'; "
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


def _answer_question_impl(question: str, mode: str = "hybrid", allow_live: bool = False, as_of: str | None = None) -> QueryResult:
    """Full query operation, timed end-to-end; logs to PostgreSQL.

    Memory-first flow: search persistent memory; if the evidence is insufficient
    AND the caller allows it, fetch live evidence from the web, run it through
    the standard ingestion pipeline (dedup, temporal versioning, provenance),
    then re-run retrieval and answer from the updated memory."""
    from ..extraction.schemas import ConfidenceBreakdown, PipelineStage

    started = time.perf_counter()
    query_id = f"q_{uuid.uuid4().hex[:10]}"
    pipeline: list[PipelineStage] = []

    query_entities: list[str] | None = None

    def _re_retrieve():
        nonlocal query_entities
        # Entity extraction is an LLM call. Reusing it across the live-fetch
        # recheck removes one avoidable network round trip.
        if mode in ("graph", "hybrid") and query_entities is None:
            query_entities = extract_query_entities(question)
        if mode == "hybrid":
            # Graph lookup and vector lookup are independent I/O operations.
            # Running them together lowers the critical path for every query.
            with ThreadPoolExecutor(max_workers=2) as ex:
                graph_future = ex.submit(retrieve, question, "graph", as_of, query_entities)
                vector_future = ex.submit(retrieve, question, "vector", as_of)
                initial_graph = graph_future.result()
                vector_bundle = vector_future.result()
            document_ids = list({p.document_id for p in vector_bundle["passages"] if p.document_id})
            graph_path = initial_graph.get("graph_path")
            if document_ids and (not graph_path or not graph_path.nodes):
                # Use the actual retrieved documents as graph seeds. This
                # recovers entities even when query entity extraction is weak.
                b = retrieve(
                    question, "graph", as_of, query_entities, document_ids=document_ids
                )
            else:
                b = initial_graph
            b["passages"] = vector_bundle["passages"]
            b["used_vector"] = True
            b["facts"], b["passages"] = _merge_rank(b["facts"], b["passages"], question, b["chain_fact_ids"])
        else:
            b = retrieve(question, mode, as_of=as_of, entity_seeds=query_entities)
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
    freshness_requested = bool(
        re.search(r"\b(latest|current|currently|right now|today|recent|recently)\b", question.lower())
    ) and not as_of
    if sufficient and allow_live and freshness_requested:
        sufficient = False
        verdict = {
            **verdict,
            "sufficient": False,
            "reason": "the question asks for fresh/current information, so local memory must be refreshed",
        }
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
    verdict2 = verdict  # Initialize verdict2 to verdict in case live retrieval doesn't run

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


def answer_question(question: str, mode: str = "hybrid", allow_live: bool = False, as_of: str | None = None, skip_llm: bool | None = None) -> QueryResult:
    """Run one query with an isolated frontend override for SKIP_LLM."""
    token = set_skip_llm_override(skip_llm)
    try:
        return _answer_question_impl(question, mode, allow_live=allow_live, as_of=as_of)
    finally:
        reset_skip_llm_override(token)
