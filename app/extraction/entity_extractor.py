from __future__ import annotations

import logging

from ..llm import LLMError, chat_json
from ..extraction.schemas import Entity
from ..trust.source_weights import entity_id_for

log = logging.getLogger("extract.entities")

SYSTEM = (
    "You are a precise information extraction engine. You identify named entities "
    "in technology news text. Output strictly valid JSON only."
)

USER_TMPL = """Extract every named entity from the text below.

Entity types allowed: {types}
Rules:
- Monetary values become entities of type Money, written like "$12 Billion" or "$1.5 Billion".
- Company and organization names verbatim (e.g. "OpenAI", "Thinking Machines Lab", "Character.AI").
- People by full name. Products/models by name (e.g. "GPT-4o", "Claude 3").
- No duplicates. No generic nouns.

Text:
{text}

Return JSON exactly like: {{"entities": [{{"name": "...", "type": "..."}}]}}"""


def extract_entities(text: str) -> list[Entity]:
    """Single-pass LLM entity extraction with a strict JSON schema.
    Malformed output is retried once, then raised as LLMError for the caller to log/skip."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER_TMPL.format(types=", ".join(_TYPES), text=text[:_MAX_CHARS])},
    ]
    try:
        data = chat_json(messages, temperature=0.0)
    except LLMError:
        data = chat_json(messages, temperature=0.0)
    entities = []
    seen = set()
    for e in data.get("entities", []):
        name = str(e.get("name", "")).strip()
        etype = str(e.get("type", "Organization")).strip()
        if etype not in _TYPES:
            etype = _infer_type(name)
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        entities.append(Entity(entity_id=entity_id_for(name), name=name, type=etype))
    return entities


_TYPES = ["Person", "Organization", "Product", "Money"]
_MAX_CHARS = 6000


def _infer_type(name: str) -> str:
    n = name.strip()
    if n.startswith("$"):
        return "Money"
    return "Organization"
