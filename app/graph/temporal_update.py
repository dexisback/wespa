from __future__ import annotations

from datetime import datetime, timezone

from ..trust.contradiction import (
    ACTION_CONFLICT,
    ACTION_CORROBORATE,
    ACTION_SUPERSEDE,
    classify_fact,
)


def plan_update(new_fact: dict, existing_facts: list[dict], supersede_window_days: int, now: datetime | None = None) -> dict:
    """Pure decision layer for temporal memory. Given a proposed fact and the
    list of active facts, returns the write plan without touching any store."""
    now = now or datetime.now(timezone.utc)
    decision = classify_fact(
        new_fact["subject_id"],
        new_fact["relation"],
        new_fact["object_id"],
        new_fact.get("observed_at", now),
        existing_facts,
        supersede_window_days,
    )
    plan = {"action": decision["action"], "new_fact": new_fact, "now": now.isoformat()}
    if decision["action"] == ACTION_SUPERSEDE:
        plan["close_fact_ids"] = decision["supersede_ids"]
        plan["old_valid_to"] = new_fact.get("valid_from") or now.isoformat()
    elif decision["action"] == ACTION_CORROBORATE:
        plan["corroborate_fact_ids"] = decision["corroborate_ids"]
    elif decision["action"] == ACTION_CONFLICT:
        plan["conflict_fact_ids"] = decision["conflict_ids"]
    return plan
