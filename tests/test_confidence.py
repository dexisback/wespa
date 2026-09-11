from app.trust.confidence import confidence, cross_source_agreement, label


def test_exact_formula_single_source():
    # 0.50*0.90 + 0.30*0.50 + 0.20*0.80 = 0.76
    assert confidence(0.90, 1, 0.80) == 0.76


def test_cross_source_agreement_table():
    assert cross_source_agreement(1) == 0.5
    assert cross_source_agreement(2) == 0.75
    assert cross_source_agreement(3) == 1.0
    assert cross_source_agreement(0) == 0.5


def test_corroboration_increases_confidence():
    single = confidence(0.88, 1, 0.9)
    corroborated = confidence(0.88, 2, 0.9)
    assert corroborated > single


def test_clamping():
    assert confidence(1.5, 3, 1.2) <= 1.0
    assert confidence(-1, 1, -5) >= 0.0


def test_conflict_lowers_confidence():
    plain = confidence(0.9, 1, 0.9)
    conflicted = confidence(0.9, 1, 0.9, conflict=True)
    assert abs(plain - conflicted / 0.75) < 1e-6
    assert conflicted < plain


def test_confidence_labels():
    assert label(0.9) == "Very high"
    assert label(0.75) == "High"
    assert label(0.6) == "Medium"
    assert label(0.3) == "Low"
