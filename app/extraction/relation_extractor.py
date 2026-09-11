from __future__ import annotations

import logging

from ..llm import LLMError, chat_json
from ..trust.source_weights import entity_id_for

log = logging.getLogger("extract.relations")

SYSTEM = (
    "You are a precise relation extraction engine. You extract factual subject-relation-object "
    "triples from technology news text. Output strictly valid JSON only."
)

USER_TMPL = """From the text below, extract factual triples about entities.

Relations allowed (use EXACTLY these labels): {relations}
Rules:
- Only relations from the allowed list. Map paraphrases: "joined"/"works at"/"hired by" -> WORKED_AT,
  "co-founded"/"started" -> FOUNDED, "bought"/"acqui-hired" -> ACQUIRED, "is CEO of"/"runs" -> LEADS,
  "invested"/"put money into" -> INVESTED_IN, "launched"/"released"/"shipped" -> RELEASED,
  "raised funding of" -> RAISED, "valued at" -> VALUED_AT, "departed"/"quit"/"stepped down from" -> LEFT.
- Use FOUNDED only when the text explicitly says the person founded or co-founded the company.
  Joining, leading, or working somewhere is NOT founding. Use WORKED_AT for "joined"/"now at".
- Monetary objects must be written like "$12 Billion".
- Use full entity names exactly as they appear in the text.
- extraction_confidence: your own certainty the triple is explicitly stated, between 0.0 and 1.0.
- Do NOT invent triples. Only what the text states.

Known entities in text: {entities}

Text:
{text}

Return JSON exactly like:
{{"relations": [{{"subject": "...", "relation": "FOUNDED", "object": "...", "extraction_confidence": 0.9}}]}}"""

SYNONYMS = {
    "JOINED": "WORKED_AT",
    "WORKS_AT": "WORKED_AT",
    "WORK_AT": "WORKED_AT",
    "EMPLOYED_BY": "WORKED_AT",
    "EMPLOYED_AT": "WORKED_AT",
    "HIRED_BY": "WORKED_AT",
    "CO_FOUNDED": "FOUNDED",
    "STARTED": "FOUNDED",
    "CEO_OF": "LEADS",
    "HEADS": "LEADS",
    "RUNS": "LEADS",
    "LEAD": "LEADS",
    "LEADS_AT": "LEADS",
    "BOUGHT": "ACQUIRED",
    "ACQUI_HIRED": "ACQUIRED",
    "ACQUI-HIRED": "ACQUIRED",
    "ACQUIRED_BY_TEAM_HIRE": "ACQUIRED",
    "INVESTED": "INVESTED_IN",
    "INVESTED_INTO": "INVESTED_IN",
    "LAUNCHED": "RELEASED",
    "SHIP": "RELEASED",
    "SHIPPED": "RELEASED",
    "RELEASE": "RELEASED",
    "RAISE": "RAISED",
    "RAISED_FUNDING": "RAISED",
    "VALUED": "VALUED_AT",
    "VALUED_AT_ABOUT": "VALUED_AT",
}


def normalize_relation(rel: str, allowed: list[str]) -> str | None:
    r = (rel or "").strip().upper().replace(" ", "_")
    if r in allowed:
        return r
    if r in SYNONYMS and SYNONYMS[r] in allowed:
        return SYNONYMS[r]
    for a in allowed:
        if a in r or r in a:
            return a
    return None


def extract_relations(text: str, entity_names: list[str], allowed_relations: list[str]) -> list[dict]:
    """Returns list of {subject, relation, object, extraction_confidence, subject_type, object_type}."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": USER_TMPL.format(
                relations=", ".join(allowed_relations),
                entities=", ".join(entity_names) or "(none pre-identified)",
                text=text[:_MAX_CHARS],
            ),
        },
    ]
    try:
        data = chat_json(messages, temperature=0.0)
    except LLMError:
        data = chat_json(messages, temperature=0.0)
    out = []
    for rel in data.get("relations", []):
        relation = normalize_relation(str(rel.get("relation", "")), allowed_relations)
        subject = str(rel.get("subject", "")).strip()
        obj = str(rel.get("object", "")).strip()
        try:
            ext_conf = max(0.0, min(1.0, float(rel.get("extraction_confidence", 0.8))))
        except (TypeError, ValueError):
            ext_conf = 0.8
        if not relation or not subject or not obj or subject.lower() == obj.lower():
            continue
        out.append(
            {
                "subject": subject,
                "relation": relation,
                "object": obj,
                "extraction_confidence": ext_conf,
                "subject_type": str(rel.get("subject_type") or _infer_type(subject)),
                "object_type": str(rel.get("object_type") or _infer_type(obj)),
            }
        )
    return out


_MAX_CHARS = 6000


def _infer_type(name: str) -> str:
    n = name.strip()
    if n.startswith("$"):
        return "Money"
    return "Organization"


__all__ = ["extract_relations", "normalize_relation", "entity_id_for"]
