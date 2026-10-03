import re

from ..config import DEFAULT_RELIABILITY, SOURCE_WEIGHTS
from ..util import canonical_name


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def source_id_for(name: str) -> str:
    return f"src_{slugify(name)}"


def entity_id_for(name: str) -> str:
    """Stable entity id from the canonicalized name, so 'OpenAI Inc.' and
    'OpenAI' resolve to the same graph node instead of near-duplicates."""
    return f"ent_{slugify(canonical_name(name))}"


def reliability(source: str) -> float:
    if not source:
        return DEFAULT_RELIABILITY
    return float(SOURCE_WEIGHTS.get(source, DEFAULT_RELIABILITY))
