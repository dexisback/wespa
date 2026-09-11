from __future__ import annotations


def norm(s: str) -> str:
    return (s or "").strip().lower()


def fact_key(f) -> tuple[str, str, str]:
    return (norm(f.subject_name or f.subject_id), norm(f.relation), norm(f.object_name or f.object_id))


def expected_match(expected: tuple[str, str, str], fact) -> bool:
    es, er, eo = (norm(x) for x in expected)
    fs, fr, fo = fact_key(fact)
    return (es in fs or fs in es) and (er == fr) and (eo in fo or fo in eo)


def hit_at_k(expected_facts: list[tuple[str, str, str]], retrieved_facts, k: int = 5) -> float:
    top = retrieved_facts[:k]
    return 1.0 if any(expected_match(e, f) for e in expected_facts for f in top) else 0.0


def recall_at_k(expected_facts, retrieved_facts, k: int = 5) -> float:
    if not expected_facts:
        return 0.0
    top = retrieved_facts[:k]
    matched = sum(1 for e in expected_facts if any(expected_match(e, f) for f in top))
    return matched / len(expected_facts)


def passage_hit(expected_keywords: list[str], passages, k: int = 5) -> float:
    top = passages[:k]
    for kw in expected_keywords or []:
        if any(norm(kw) in norm(p.text) or norm(kw) in norm(p.title) for p in top):
            return 1.0
    return 0.0


def answer_keyword_score(expected_keywords: list[str], answer: str) -> float:
    if not expected_keywords:
        return 1.0
    a = norm(answer)
    hits = sum(1 for kw in expected_keywords if norm(kw) in a)
    return hits / len(expected_keywords)


def temporal_check(versions: list[dict]) -> tuple[bool, str]:
    """Old fact must remain retrievable with valid_to set; new fact active."""
    closed = [v for v in versions if v.get("valid_to")]
    active = [v for v in versions if not v.get("valid_to")]
    if len(versions) >= 2 and closed and active:
        return True, f"{len(versions)} versions retained ({len(closed)} historical, {len(active)} active)"
    return False, f"expected >=2 versions with 1 closed + 1 active, got {len(versions)}"
