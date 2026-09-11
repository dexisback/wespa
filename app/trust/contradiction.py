from __future__ import annotations

from datetime import datetime

from ..config import SUPERSEDE_WINDOW_DAYS

ACTION_NEW = "NEW"
ACTION_CORROBORATE = "CORROBORATE"
ACTION_SUPERSEDE = "SUPERSEDE"
ACTION_CONFLICT = "CONFLICT"


def norm_name(name: str) -> str:
    return (name or "").strip().lower()


def classify_fact(
    subject_id: str,
    relation: str,
    object_id: str,
    observed_at: datetime,
    existing_facts: list[dict],
    supersede_window_days: int = SUPERSEDE_WINDOW_DAYS,
) -> dict:
    """Decide how an incoming fact relates to existing active facts.

    existing_facts: active facts dicts with fact_id, object_id, relation, subject_id,
    source_id, observed_at (ISO string or datetime), conflict flags.
    Returns {action, corroborate_ids, supersede_ids, conflict_ids}
    """
    corroborate_ids, supersede_ids, conflict_ids = [], [], []
    observed = _as_dt(observed_at)

    for f in existing_facts:
        if f.get("relation") != relation or f.get("subject_id") != subject_id:
            continue
        if f.get("valid_to") not in (None, ""):
            continue
        if norm_name(f.get("object_id")) == norm_name(object_id):
            corroborate_ids.append(f["fact_id"])
        else:
            gap_days = abs((observed - _as_dt(f.get("observed_at"))).days)
            if gap_days > supersede_window_days:
                supersede_ids.append(f["fact_id"])
            else:
                conflict_ids.append(f["fact_id"])

    if corroborate_ids:
        action = ACTION_CORROBORATE
    elif supersede_ids:
        action = ACTION_SUPERSEDE
    elif conflict_ids:
        action = ACTION_CONFLICT
    else:
        action = ACTION_NEW
    return {
        "action": action,
        "corroborate_ids": corroborate_ids,
        "supersede_ids": supersede_ids,
        "conflict_ids": conflict_ids,
    }


def _as_dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return datetime(1970, 1, 1)
    return datetime(1970, 1, 1)
