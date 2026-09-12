from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import SKIP_LLM
from ..llm import LLMError, chat
from ..trust.confidence import confidence as compute_confidence
from ..trust.confidence import label
from ..trust.source_weights import reliability
from .prompts import build_answer_prompt

log = logging.getLogger("rag.answer")


def answer_confidence(facts, passages, conflicts: int) -> tuple[float, str]:
    """Deterministic confidence for the overall answer, derived from the locked
    fact-level formula. Vector-only answers use source reliability + relevance."""
    if facts:
        base = sum(f.confidence for f in facts[:5]) / min(len(facts), 5)
        c = base
    elif passages:
        rels = [reliability(p.source) for p in passages]
        sims = [p.similarity if p.similarity is not None else 0.6 for p in passages]
        avg_rel = sum(rels) / len(rels)
        avg_sim = max(0.0, min(1.0, sum(sims) / len(sims)))
        c = compute_confidence(avg_rel, 1, avg_sim)
    else:
        c = 0.15
    if conflicts:
        c *= 0.8
    c = max(0.0, min(1.0, c))
    return round(c, 3), label(c)


def _fallback_answer(question: str, facts, passages, mode: str, as_of: str | None = None) -> str:
    """Templated fallback when the LLM is rate-limited or unavailable.
    Still cites every fact and passage so the demo remains functional."""
    lines = []
    if as_of:
        lines.append(f"Reconstruction of memory as of {as_of[:10]} (LLM unavailable; assembled directly from the historical facts):")
    elif mode == "vector":
        lines.append("Answer assembled from semantic memory only (no graph traversal used).")
    elif mode == "graph":
        lines.append("Answer assembled from the knowledge-graph traversal only.")
    else:
        lines.append("Answer assembled from merged graph + semantic memory.")

    if facts:
        lines.append("\nKnowledge-graph facts:")
        for f in facts[:10]:
            validity = "currently true" if f.active else f"held until {str(f.valid_to)[:10]}"
            flag = " [CONFLICTING CLAIM]" if f.conflict else ""
            lines.append(
                f"- {f.subject_name} → {f.relation} → {f.object_name} "
                f"(source: {f.source_name or f.source_id}, confidence: {f.confidence}, {validity}){flag}"
            )
    if passages:
        lines.append("\nSupporting passages:")
        for p in passages[:4]:
            lines.append(f"- [{p.source}] {p.text[:220].strip()}...")
    if not facts and not passages:
        return "No reliable evidence was found in memory for this question."
    lines.append("\nSources: " + ", ".join(sorted({f.source_name or f.source_id for f in facts} | {p.source for p in passages if p.source})))
    return "\n".join(lines)


def generate_answer(question: str, facts, passages, mode: str, as_of: str | None = None) -> tuple[str, float, str]:
    """Grounded generation: the LLM must answer from supplied evidence only."""
    conf, conf_label = answer_confidence(facts, passages, conflicts=0)
    weak = (len(facts) + len(passages)) <= 1

    prompt = build_answer_prompt(question, facts, passages, mode, as_of=as_of)
    try:
        if SKIP_LLM:
            raise LLMError("SKIP_LLM is enabled")
        answer = chat(prompt, temperature=0.2, max_tokens=700).strip()
    except LLMError as e:
        answer = _fallback_answer(question, facts, passages, mode, as_of=as_of)
        if not as_of:
            answer = f"[LLM unavailable; templated answer from evidence]\n{answer}"
        conf = round(conf * 0.9, 3)
        conf_label = label(conf)

    if weak or _hedges(answer):
        if not _hedges(answer):
            answer = "Evidence is limited, so confidence is low. " + answer
        conf = round(conf * 0.8, 3)
        conf_label = label(conf)
    return answer, conf, conf_label


def _hedges(answer: str) -> bool:
    lowered = answer.lower()
    markers = ["uncertain", "insufficient", "not enough", "cannot determine", "no evidence", "limited evidence", "couldn't"]
    return any(m in lowered for m in markers)
