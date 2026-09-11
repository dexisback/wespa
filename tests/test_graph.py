from datetime import datetime, timezone

from app.graph.temporal_update import plan_update
from app.trust.contradiction import ACTION_CONFLICT, ACTION_CORROBORATE, ACTION_SUPERSEDE

from .fakes import FakeGraphWriter, utc


def fact(subject_id, relation, object_id, fact_id, observed_at, valid_to=None):
    return {
        "fact_id": fact_id, "subject_id": subject_id, "relation": relation,
        "object_id": object_id, "source_id": "src_x", "observed_at": observed_at,
        "valid_to": valid_to,
    }


def test_new_fact_action():
    plan = plan_update(
        {"subject_id": "s", "relation": "VALUED_AT", "object_id": "o1", "observed_at": utc(2025, 4, 1)},
        [], supersede_window_days=7,
    )
    assert plan["action"] == "NEW"


def test_corroboration_same_triple():
    existing = [fact("s", "VALUED_AT", "o1", "f1", utc(2025, 4, 1))]
    plan = plan_update(
        {"subject_id": "s", "relation": "VALUED_AT", "object_id": "o1", "observed_at": utc(2025, 5, 1)},
        existing, supersede_window_days=7,
    )
    assert plan["action"] == ACTION_CORROBORATE
    assert plan["corroborate_fact_ids"] == ["f1"]


def test_temporal_supersede_sets_valid_to():
    existing = [fact("s", "VALUED_AT", "o1", "f1", utc(2025, 4, 1))]
    plan = plan_update(
        {"subject_id": "s", "relation": "VALUED_AT", "object_id": "o2", "observed_at": utc(2025, 6, 12)},
        existing, supersede_window_days=7,
    )
    assert plan["action"] == ACTION_SUPERSEDE
    assert plan["close_fact_ids"] == ["f1"]
    assert plan["old_valid_to"]


def test_near_simultaneous_conflict():
    existing = [fact("s", "INVESTED_IN", "o1", "f1", utc(2025, 3, 25))]
    plan = plan_update(
        {"subject_id": "s", "relation": "INVESTED_IN", "object_id": "o2", "observed_at": utc(2025, 3, 27)},
        existing, supersede_window_days=7,
    )
    assert plan["action"] == ACTION_CONFLICT
    assert plan["conflict_fact_ids"] == ["f1"]


def test_supersede_preserves_old_version_in_store():
    graph = FakeGraphWriter()
    r1 = graph.ingest_fact(
        subject_id="ent_ssi", subject_name="Safe Superintelligence", relation="VALUED_AT",
        object_id="ent_20b", object_name="$20 Billion", source_id="src_reuters",
        source_name="Reuters", document_id="d17", observed_at=utc(2025, 4, 24),
        extraction_confidence=0.9, reliability_weight=0.95,
    )
    r2 = graph.ingest_fact(
        subject_id="ent_ssi", subject_name="Safe Superintelligence", relation="VALUED_AT",
        object_id="ent_32b", object_name="$32 Billion", source_id="src_bloomberg",
        source_name="Bloomberg", document_id="f21", observed_at=utc(2025, 6, 12),
        extraction_confidence=0.9, reliability_weight=0.90,
    )
    assert r1["action"] == "NEW"
    assert r2["action"] == ACTION_SUPERSEDE

    old = graph.get_fact(r1["fact_id"])
    new = graph.get_fact(r2["fact_id"])
    assert old["valid_to"] is not None
    assert old["valid_to"] == new["valid_from"]
    assert not new["valid_to"]
    versions = graph.get_fact_versions(r2["fact_id"])
    assert {v["fact_id"] for v in versions} >= {r1["fact_id"], r2["fact_id"]}
    assert graph.get_fact(r1["fact_id"])  # old fact still retrievable


def test_conflict_keeps_both_versions():
    graph = FakeGraphWriter()
    a = graph.ingest_fact(
        subject_id="ent_msft", subject_name="Microsoft", relation="INVESTED_IN",
        object_id="ent_10b", object_name="$10 Billion", source_id="src_info",
        source_name="The Information", document_id="d10", observed_at=utc(2025, 3, 25),
        extraction_confidence=0.9, reliability_weight=0.88,
    )
    b = graph.ingest_fact(
        subject_id="ent_msft", subject_name="Microsoft", relation="INVESTED_IN",
        object_id="ent_13b", object_name="$13 Billion", source_id="src_tc",
        source_name="TechCrunch", document_id="d11", observed_at=utc(2025, 3, 27),
        extraction_confidence=0.9, reliability_weight=0.88,
    )
    assert a["action"] == "NEW"
    assert b["action"] == ACTION_CONFLICT
    assert graph.get_fact(a["fact_id"])["valid_to"] is None
    assert graph.get_fact(b["fact_id"])["valid_to"] is None
    assert graph.get_fact(a["fact_id"])["conflict"] is True
    assert graph.get_fact(b["fact_id"])["conflict"] is True


def test_fact_ids_and_confidence_returned():
    graph = FakeGraphWriter()
    r = graph.ingest_fact(
        subject_id="ent_amzn", subject_name="Amazon", relation="INVESTED_IN",
        object_id="ent_8b", object_name="$8 Billion", source_id="src_reuters",
        source_name="Reuters", document_id="d14", observed_at=utc(2025, 4, 22),
        extraction_confidence=0.95, reliability_weight=0.95,
    )
    stored = graph.get_fact(r["fact_id"])
    assert stored["confidence"] == 0.5 * 0.95 + 0.3 * 0.5 + 0.2 * 0.95
    assert stored["source_id"] == "src_reuters"
    assert stored["observed_at"].startswith("2025-04-22")
