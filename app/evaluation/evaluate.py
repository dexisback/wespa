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
