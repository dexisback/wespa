from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone

from ..config import EVAL_RESULTS_PATH, RAW_DIR
from ..evaluation.metrics import (
    answer_keyword_score,
    fact_key,
    hit_at_k,
    norm,
    passage_hit,
    recall_at_k,
    temporal_check,
)
from ..ingestion.pipeline import ingest_documents, load_documents_from_file
from ..rag.hybrid_retriever import answer_question
from ..trust.confidence import confidence as compute_confidence

MODES = ["vector", "graph", "hybrid"]


def _expected_fact_tuples(q):
    out = []
    for t in q.get("expected_facts", []):
        if isinstance(t, (list, tuple)) and len(t) == 3:
            out.append(tuple(t))
    return out


def run_evaluation(questions_path=None) -> dict:
    questions_path = questions_path or (RAW_DIR.parent / "eval" / "questions.json")
    questions = json.loads(questions_path.read_text())["questions"]

    latencies: list[float] = []
    per_mode = {m: {"hits": [], "recalls": []} for m in MODES}
    multi_hop_results: list[float] = []
    answers: dict[str, str] = {}
    debug: list[dict] = []

    for q in questions:
        for mode in MODES:
            result = answer_question(q["question"], mode)
            latencies.append(result.latency_ms)
            exp_facts = _expected_fact_tuples(q)
            if mode == "vector":
                kws = q.get("expected_passage_keywords", q.get("expected_keywords", []))
                per_mode[mode]["hits"].append(passage_hit(kws, result.passages))
                matched_kws = sum(
                    1 for kw in kws
                    if any(norm(kw) in norm(p.text) or norm(kw) in norm(p.title) for p in result.passages[:5])
                )
                per_mode[mode]["recalls"].append(matched_kws / len(kws) if kws else 0.0)
            else:
                per_mode[mode]["hits"].append(hit_at_k(exp_facts, result.facts, k=5))
                per_mode[mode]["recalls"].append(recall_at_k(exp_facts, result.facts, k=5))
            if mode == "hybrid":
                answers[q["id"]] = result.answer
                got = sorted({f_key for f in result.facts[:5] if (f_key := fact_key(f))})
                debug.append({
                    "id": q["id"], "question": q["question"], "answer": result.answer,
                    "top_facts": got[:5], "confidence": result.confidence,
                })

        if q.get("multi_hop"):
            score = answer_keyword_score(q.get("expected_keywords", []), answers.get(q["id"], ""))
            multi_hop_results.append(1.0 if score >= 0.99 else 0.0)

    hit_at_5 = {m: round(statistics.mean(v["hits"]), 3) if v["hits"] else 0.0 for m, v in per_mode.items()}
    recall_at_5 = {m: round(statistics.mean(v["recalls"]), 3) if v["recalls"] else 0.0 for m, v in per_mode.items()}

    temporal_ok, temporal_detail = _temporal_correctness()
    conf_ok, conf_detail = _confidence_weighting()
    stale_ok, stale_detail, impact_ok, impact_detail = _stale_answer_detection()
    ttravel_ok, ttravel_detail = _temporal_reconstruction()

    results = {
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "questions": len(questions),
        "multi_hop_questions": sum(1 for q in questions if q.get("multi_hop")),
        "hit_at_5": hit_at_5,
        "recall_at_5": recall_at_5,
        "hit_at_5_overall": hit_at_5["hybrid"],
        "recall_at_5_overall": recall_at_5["hybrid"],
        "multi_hop_accuracy": round(statistics.mean(multi_hop_results), 3) if multi_hop_results else 0.0,
        "avg_latency_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "temporal_correctness": {"result": "PASS" if temporal_ok else "FAIL", "detail": temporal_detail},
        "confidence_weighting": {"result": "PASS" if conf_ok else "FAIL", "detail": conf_detail},
        "stale_answer_detection": {"result": "PASS" if stale_ok else "FAIL", "detail": stale_detail},
        "impact_analysis": {"result": "PASS" if impact_ok else "FAIL", "detail": impact_detail},
        "temporal_reconstruction": {"result": "PASS" if ttravel_ok else "FAIL", "detail": ttravel_detail},
        "details": debug,
    }
    EVAL_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVAL_RESULTS_PATH.write_text(json.dumps(results, indent=2))
    return results


def _temporal_correctness() -> tuple[bool, str]:
    """Ingest the update fixture (idempotent), then verify the valuation fact kept
    its previous version and the new one is active."""
    fixture_path = RAW_DIR / "fixtures" / "ssi_june_update.json"
    if not fixture_path.exists():
        return False, "fixture missing"
    ingest_documents(load_documents_from_file(fixture_path))
    from ..graph.graph_writer import GraphWriter

    w = GraphWriter()
    rows = w.g.run(
        """MATCH (s:Entity)-[r:VALUED_AT]->(o:Entity)
           WHERE s.id = 'ent_safe_superintelligence' OR o.id = 'ent_safe_superintelligence'
           RETURN r.fact_id AS id, r.valid_to AS valid_to, o.name AS object_name"""
    )
    versions = [{"valid_to": r["valid_to"]} for r in rows]
    return temporal_check(versions)


def _confidence_weighting() -> tuple[bool, str]:
    high = compute_confidence(0.95, 1, 0.9)
    low = compute_confidence(0.60, 1, 0.9)
    ok = high > low
    return ok, f"high-reliability source -> {high}, low-reliability source -> {low} (formula: 0.5*rel + 0.3*agreement + 0.2*extraction)"


def _stale_answer_detection() -> tuple[bool, str, bool, str]:
    """Stale-answer detection + impact analysis:
    1. Produce an answer that cites the SSI $20B fact (query with as_of set to before the June update).
    2. After the June fixture has been ingested (temporal_check does it), ask the impact API
       whether that answer is now stale. The dependency graph must flag it.
    """
    from ..graph.graph_writer import GraphWriter
    from ..impact.impact import answer_freshness, impact_for_fact

    try:
        w = GraphWriter()
        rows = w.g.run(
            """MATCH (s:Entity {id:'ent_safe_superintelligence'})-[r:VALUED_AT]->(o:Entity)
               WHERE r.valid_to IS NOT NULL AND toUpper(o.name) CONTAINS '$20'
               RETURN r.fact_id AS id LIMIT 1"""
        )
        if not rows:
            return False, "no historical $20B valuation fact found", False, "cannot run impact test"
        old_fact_id = rows[0]["id"]

        # 1) answer a question that (deterministically) depends on the old fact
        res = answer_question("What is Safe Superintelligence valued at?", "graph", as_of="2025-06-01")
        deps = res.answer_dependencies or []
        ok_deps = old_fact_id in deps

        # 2) freshness re-check for that answer
        fresh = answer_freshness(res.query_id)
        flagged = fresh.get("verdict") in ("POTENTIALLY STALE", "STALE")

        stale_ok = ok_deps and flagged
        stale_detail = (
            f"answer {res.query_id} cites the historical fact ({old_fact_id}) -> impact engine marks it "
            f"'{fresh.get('verdict')}' with {fresh.get('stale_count')} stale dependency/dependencies"
        )

        # 3) impact analysis on the changed fact itself
        imp = impact_for_fact(old_fact_id)
        imp_ok = bool(imp.get("ok")) and imp.get("successor") is not None
        impact_detail = (
            f"impact for {old_fact_id}: {imp.get('impact', {}).get('headline', '')}"
            if imp.get("ok")
            else str(imp)
        )
        return stale_ok, stale_detail, imp_ok, impact_detail
    except Exception as e:
        return False, f"stale-answer test failed: {e}", False, str(e)


def _temporal_reconstruction() -> tuple[bool, str]:
    """Time-travel: querying as of June 1 must return the $20B valuation, NOT $32B."""
    from ..rag.hybrid_retriever import answer_question as _aq

    try:
        res = _aq("What is Safe Superintelligence valued at?", "graph", as_of="2025-06-01")
        top = [(f.relation, f.object_name) for f in res.facts[:3]]
        got_old = any(rel == "VALUED_AT" and "$20" in obj.upper() for rel, obj in top)
        got_new = any(rel == "VALUED_AT" and "$32" in obj.upper() for rel, obj in top)
        ok = got_old and not got_new
        return ok, (
            f"as-of 2025-06-01 -> top facts {top[:3]}: old value {'retrieved' if got_old else 'MISSING'}, "
            f"future value {'leaked' if got_new else 'correctly excluded'}"
        )
    except Exception as e:
        return False, f"temporal reconstruction test failed: {e}"
