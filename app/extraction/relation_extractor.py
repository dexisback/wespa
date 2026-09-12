from __future__ import annotations

import logging
import re

from ..config import should_skip_llm
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
    """Returns list of {subject, relation, object, extraction_confidence, subject_type, object_type}.
    Falls back to deterministic sentence-pattern extraction when the LLM is unavailable."""
    if not should_skip_llm():
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
            out = _parse_relations(data, allowed_relations)
            if out:
                return out
        except LLMError:
            log.warning("relation extraction LLM unavailable, using deterministic fallback")
    return _fallback_relations(text, allowed_relations, entity_names)


def _parse_relations(data: dict, allowed_relations: list[str]) -> list[dict]:
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


_FALLBACK_CONF = 0.55

# verb triggers mapped to relations; SUBJ/OBJ are 1-4 consecutive capitalized words
_TRIGGER_PATTERNS: list[tuple[str, str]] = [
    (r"co-?founded", "FOUNDED"),
    (r"founded", "FOUNDED"),
    (r"joined|went to|now works at|works at|moved to", "WORKED_AT"),
    (r"leads|runs|heads|is CEO of|as chief executive of", "LEADS"),
    (r"acquired|bought", "ACQUIRED"),
    (r"invested", "INVESTED_IN"),
    (r"released|launched|shipped", "RELEASED"),
    (r"left|departed|stepped down from", "LEFT"),
]

_SUBJ = r"([A-Z][\w'\-&\.]*(?:\s+[A-Z][\w'\-&\.]*){0,3})"
_MONEY = r"(\$[\d,.]+\s?[Bb]illion|\$[\d,.]+\s?[Mm]illion)"


def _clean_name(s: str) -> str:
    s = (s or "").strip().strip(".,;:!?\"'()[]")
    if s.endswith("'s"):
        s = s[:-2]
    return s.strip()


def _money_name(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw.strip())
    return raw[0] + raw[1:].lower().replace("billion", "Billion").replace("million", "Million")


def _fallback_relations(text: str, allowed: list[str], entity_names: list[str] | None = None) -> list[dict]:
    # canonicalize short-name mentions (e.g. "Sutskever") to full known names
    canon: dict[str, str] = {}
    for known in entity_names or []:
        parts = known.split()
        if len(parts) > 1:
            canon[parts[-1].lower()] = known
            canon[known.lower()] = known

    def canon_name(name: str) -> str:
        return canon.get(name.lower(), name)

    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 15:
            continue
        candidates: list[tuple[str, str, str]] = []

        for trigger, relation in _TRIGGER_PATTERNS:
            if relation not in allowed:
                continue
            pat = rf"{_SUBJ}\s+(?:the\s+)?(?:{trigger})\s+(?:the\s+)?{_SUBJ}"
            for m in re.finditer(pat, sent):
                subj, obj = _clean_name(m.group(1)), _clean_name(m.group(2))
                if subj and obj and subj.lower() != obj.lower() and not _junk(subj) and not _junk(obj):
                    candidates.append((subj, relation, obj))

        m = re.search(rf"{_SUBJ}\s+invested\s+{_MONEY}\s+in\s+(?:the\s+)?{_SUBJ}", sent)
        if m:
            subj, obj = _clean_name(m.group(1)), _clean_name(m.group(3))
            if subj and obj and subj.lower() != obj.lower() and not _junk(subj) and not _junk(obj):
                candidates.append((subj, "INVESTED_IN", obj))

        m = re.search(rf"{_SUBJ}\s+raised\s+{_MONEY}", sent)
        if m:
            subj = _clean_name(m.group(1))
            if subj and not _junk(subj):
                candidates.append((subj, "RAISED", _money_name(m.group(2))))

        m = re.search(rf"{_SUBJ}\s+valued at\s+{_MONEY}", sent)
        if m:
            subj = _clean_name(m.group(1))
            if subj and not _junk(subj):
                candidates.append((subj, "VALUED_AT", _money_name(m.group(2))))

        # "raised $1 billion ... at a $32 billion valuation" -> VALUED_AT
        m = re.search(rf"{_MONEY}\s+(?:in new funding\s+)?at\s+(?:a|an|the)?\s*{_MONEY}\s+valuation", sent)
        if m:
            first = _first_capitalized(sent)
            if first:
                candidates.append((first, "VALUED_AT", _money_name(m.group(2))))

        for subj, relation, obj in candidates:
            subj, obj = canon_name(subj), canon_name(obj)
            key = (subj.lower(), relation, obj.lower())
            if key in seen or subj.lower() == obj.lower():
                continue
            seen.add(key)
            out.append(
                {
                    "subject": subj,
                    "relation": relation,
                    "object": obj,
                    "extraction_confidence": _FALLBACK_CONF,
                    "subject_type": _infer_type(subj),
                    "object_type": _infer_type(obj),
                }
            )
        if len(out) >= 25:
            break
    return out[:25]


_JUNK = {
    "people", "sources", "company", "companies", "the", "this", "that", "both",
    "two", "either", "way", "chief", "scientist", "executive", "researcher",
    "founder", "cofounder", "report", "reports", "round", "funding", "last",
    "early", "late", "one", "first", "january", "june", "april", "march", "may",
}


def _junk(name: str) -> bool:
    return name.lower() in _JUNK


def _first_capitalized(sent: str) -> str:
    m = re.match(rf"^(?:{_SUBJ})", sent)
    return _clean_name(m.group(1)) if m and not _junk(_clean_name(m.group(1))) else ""


_MAX_CHARS = 6000


def _infer_type(name: str) -> str:
    n = name.strip()
    if n.startswith("$"):
        return "Money"
    return "Organization"


__all__ = ["extract_relations", "normalize_relation", "entity_id_for"]
