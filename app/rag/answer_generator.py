from __future__ import annotations

import logging
from datetime import datetime, timezone

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


def generate_answer(question: str, facts, passages, mode: str) -> tuple[str, float, str]:
    """Grounded generation: the LLM must answer from supplied evidence only."""
    prompt = build_answer_prompt(question, facts, passages, mode)
    try:
        answer = chat(prompt, temperature=0.2, max_tokens=700).strip()
    except LLMError as e:
        answer = (
            "I could not generate an answer because the LLM backend is unavailable. "
            f"({e})"
        )
    conf, conf_label = answer_confidence(facts, passages, conflicts=0)
    weak = (len(facts) + len(passages)) <= 1
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
