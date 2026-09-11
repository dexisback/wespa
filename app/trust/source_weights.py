import re

from ..config import DEFAULT_RELIABILITY, SOURCE_WEIGHTS


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def source_id_for(name: str) -> str:
    return f"src_{slugify(name)}"


def entity_id_for(name: str) -> str:
    return f"ent_{slugify(name)}"


def reliability(source: str) -> float:
    if not source:
        return DEFAULT_RELIABILITY
    return float(SOURCE_WEIGHTS.get(source, DEFAULT_RELIABILITY))
