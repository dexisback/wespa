from __future__ import annotations

WEIGHTS = {"source_reliability": 0.50, "cross_source_agreement": 0.30, "extraction_confidence": 0.20}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def cross_source_agreement(n_sources: int) -> float:
    """Single-source fact -> 0.5; corroborated facts increase."""
    if n_sources <= 1:
        return 0.5
    if n_sources == 2:
        return 0.75
    return 1.0


def confidence(source_reliability: float, n_sources: int, extraction_confidence: float, conflict: bool = False) -> float:
    c = (
        WEIGHTS["source_reliability"] * clamp01(source_reliability)
        + WEIGHTS["cross_source_agreement"] * cross_source_agreement(n_sources)
        + WEIGHTS["extraction_confidence"] * clamp01(extraction_confidence)
    )
    c = clamp01(c)
    if conflict:
        c *= 0.75
    return round(c, 3)


def label(c: float) -> str:
    if c >= 0.85:
        return "Very high"
    if c >= 0.70:
        return "High"
    if c >= 0.55:
        return "Medium"
    return "Low"
