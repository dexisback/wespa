"""Small helpers shared across modules (hashing, entity-name normalization)."""
from __future__ import annotations

import hashlib
import re

# Legal suffixes that distinguish the same company ("OpenAI" vs "OpenAI Inc.")
_LEGAL_SUFFIX_RE = re.compile(
    r"[,]?\s*\b(?:inc(?:orporated)?|corp(?:oration)?|ltd|llc|plc|gmbh|company|co)\.?\s*$",
    re.IGNORECASE,
)
_PARENS_SUFFIX_RE = re.compile(r"\s*\((?:inc|corp|ltd|llc)\.?\)\s*$", re.IGNORECASE)


def stable_hash(s: str) -> int:
    """Deterministic hash (Python's builtin hash() is salted per process)."""
    import hashlib

    return int(hashlib.sha256(s.encode()).hexdigest()[:16], 16)


def canonical_name(name: str) -> str:
    """Canonical entity name: collapse legal suffixes/whitespace so 'OpenAI Inc.'
    and 'OpenAI' map to the same entity node."""
    n = (name or "").strip()
    n = _PARENS_SUFFIX_RE.sub("", n)
    n = _LEGAL_SUFFIX_RE.sub("", n)
    n = n[: -2] if n.endswith("'s") else n
    n = re.sub(r"\s+", " ", n).strip().strip(".,:;")
    return n


def match_key(value: str) -> str:
    """Normalization for entity-name matching: lowercase alphanumeric only.

    Shared by graph_writer (stored `name_key` property) and graph_retriever
    (seed matching) so both sides always agree."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def cypher_name_key(expr: str) -> str:
    """Cypher approximation of match_key for the common name separators.

    Used only to backfill name_key on entities written before the property
    existed; the retrieval fallback still scans, so partial mismatch is safe."""
    for separator in ("-", " ", ".", "_", "'", "&", "/", ":"):
        escaped = separator.replace("'", "\\'")
        expr = f"replace({expr}, '{escaped}', '')"
    return expr


def infer_entity_type(name: str) -> str:
    """Best-effort entity type for names lacking an explicit type."""
    n = (name or "").strip()
    if n.startswith("$"):
        return "Money"
    return "Organization"
