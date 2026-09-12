from __future__ import annotations

import logging
import re

from ..config import should_skip_llm
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
    Falls back to deterministic regex extraction when the LLM is unavailable."""
    if not should_skip_llm():
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL.format(types=", ".join(_TYPES), text=text[:_MAX_CHARS])},
        ]
        try:
            data = chat_json(messages, temperature=0.0)
            entities = _parse_entities(data)
            if entities:
                return entities
        except LLMError:
            log.warning("entity extraction LLM unavailable, using deterministic fallback")
    return _fallback_entities(text)


def _parse_entities(data: dict) -> list[Entity]:
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


_OPENERS = {
    "the", "a", "an", "in", "on", "at", "but", "and", "or", "it", "its", "their", "his",
    "her", "he", "she", "they", "we", "our", "this", "that", "these", "those", "there",
    "then", "also", "even", "still", "just", "yet", "only", "more", "less", "most",
    "many", "some", "one", "two", "both", "each", "while", "after", "before", "when",
    "according", "people", "sources", "familiar", "former", "current", "now", "as",
    "if", "so", "not", "no", "all", "any", "by", "for", "from", "with", "about",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "last", "next", "early", "late",
    "information", "reported", "report", "says", "said", "told", "sources", "company",
    "either", "way", "either way", "billion", "million", "valuation", "round",
}
_PRODUCTS = ("gpt-", "claude", "gemini", "llama", "copilot", "o3", "o4-mini")


def _fallback_entities(text: str) -> list[Entity]:
    """Regex-only extraction: money amounts + capitalized word runs (1-4 words)."""
    found: dict[str, Entity] = {}

    def add(name: str, etype: str):
        name = name.strip().strip(".,;:!?\"'()[]")
        if name.endswith("'s"):
            name = name[:-2]
        if len(name) < 3 or len(name) > 48:
            return
        key = name.lower()
        if key in found or key in _OPENERS:
            return
        found[key] = Entity(entity_id=entity_id_for(name), name=name, type=etype)

    for m in re.finditer(r"\$\s?[\d][\d,.]*\s*(?:billion|million)", text, re.I):
        raw = re.sub(r"\s+", " ", m.group(0)).strip()
        name = raw[0] + raw[1:].lower().replace("billion", "Billion").replace("million", "Million")
        add(name, "Money")

    # split on sentence boundaries so capitalized runs cannot span sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    word_re = re.compile(r"[A-Za-z][\w'\-]*")
    runs: list[list[str]] = []
    for sent in sentences:
        cur: list[str] = []
        for w in word_re.findall(sent):
            if w[:1].isupper() and w.lower() not in _OPENERS:
                cur.append(w)
            else:
                if cur:
                    runs.append(cur)
                    cur = []
        if cur:
            runs.append(cur)

    for run in runs:
        if len(run) > 4:
            run = run[:4]
        name = " ".join(run)
        low = name.lower()
        etype = "Organization"
        if low.startswith(_PRODUCTS):
            etype = "Product"
        add(name, etype)

    entities = list(found.values())
    # drop single-word duplicates that are just the last name of a longer candidate
    long_names = {e.name.split()[-1].lower(): e.name for e in entities if len(e.name.split()) > 1}
    return [e for e in entities if not (len(e.name.split()) == 1 and e.name.lower() in long_names)]


_TYPES = ["Person", "Organization", "Product", "Money"]
_MAX_CHARS = 6000


def _infer_type(name: str) -> str:
    n = name.strip()
    if n.startswith("$"):
        return "Money"
    return "Organization"
