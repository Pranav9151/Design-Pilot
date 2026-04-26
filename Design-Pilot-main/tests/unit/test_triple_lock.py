"""
Unit tests for the Triple-Lock orchestrator.

These are the tests that guarantee the confidence score we show engineers
is honest — a wrong result never gets a high band, and an uncorroborated
but sound result doesn't get artificially inflated.
"""
from __future__ import annotations

import math

import pytest

from app.services.triple_lock import (
    LockOutcome,
    LockStatus,
    TripleLock,
    TripleLockResult,
    compute_confidence,
    run_lock1_deterministic,
    run_lock2_rag_crosscheck,
    run_lock3_fea_sanity,
    triple_lock,
)


# ─────────────────────────────────────────────────────────────────────
# Lock 1 — deterministic
# ─────────────────────────────────────────────────────────────────────


def test_lock1_accepts_finite_value():
    out = run_lock1_deterministic(86.5)
    assert out.status == LockStatus.AGREE
    assert out.value == 86.5


def test_lock1_rejects_none():
    out = run_lock1_deterministic(None)
    assert out.status == LockStatus.ERROR


def test_lock1_rejects_nan():
    out = run_lock1_deterministic(float("nan"))
    assert out.status == LockStatus.ERROR


def test_lock1_rejects_infinity():
    out = run_lock1_deterministic(float("inf"))
    assert out.status == LockStatus.ERROR


# ─────────────────────────────────────────────────────────────────────
# Lock 2 — RAG cross-check
# ─────────────────────────────────────────────────────────────────────


def test_lock2_insufficient_data_when_empty():
    """v1.0 default: empty knowledge base → insufficient data signal."""
    out = run_lock2_rag_crosscheck(lock1_value=100.0, historical_values=[])
    assert out.status == LockStatus.INSUFFICIENT_DATA
    assert "knowledge base" in out.note.lower()


def test_lock2_insufficient_data_when_below_min_samples():
    out = run_lock2_rag_crosscheck(
        lock1_value=100.0,
        historical_values=[98.0, 101.0],  # only 2
        min_samples=3,
    )
    assert out.status == LockStatus.INSUFFICIENT_DATA


def test_lock2_agrees_when_within_2_sigma():
    """Historical cluster mean 100, stdev ~5. Lock 1 = 103 → well under 2σ."""
    out = run_lock2_rag_crosscheck(
        lock1_value=103.0,
        historical_values=[95, 100, 105, 100, 98, 102],
    )
    assert out.status == LockStatus.AGREE
    assert out.deviation_pct is not None
    assert abs(out.deviation_pct) < 10


def test_lock2_diverges_on_outlier():
    """Historical cluster 95-105. Lock 1 = 500 → obviously wrong."""
    out = run_lock2_rag_crosscheck(
        lock1_value=500.0,
        historical_values=[95, 100, 105, 100, 98, 102],
    )
    assert out.status == LockStatus.DIVERGE
    assert "σ" in out.note


def test_lock2_handles_zero_stdev_historical():
    """If every historical value is identical, equality means agree."""
    agree = run_lock2_rag_crosscheck(
        lock1_value=100.0,
        historical_values=[100.0, 100.0, 100.0],
    )
    assert agree.status == LockStatus.AGREE

    diverge = run_lock2_rag_crosscheck(
        lock1_value=105.0,
        historical_values=[100.0, 100.0, 100.0],
    )
    assert diverge.status == LockStatus.DIVERGE


def test_lock2_error_on_non_finite_lock1():
    out = run_lock2_rag_crosscheck(
        lock1_value=float("nan"),
        historical_values=[100, 101, 99],
    )
    assert out.status == LockStatus.ERROR


# ─────────────────────────────────────────────────────────────────────
# Lock 3 — FEA sanity (off in v1.0)
# ─────────────────────────────────────────────────────────────────────


def test_lock3_not_run_by_default_in_v1():
    out = run_lock3_fea_sanity(lock1_value=100.0)
    assert out.status == LockStatus.NOT_RUN


def test_lock3_agree_when_enabled_and_close():
    out = run_lock3_fea_sanity(
        lock1_value=100.0,
        fea_value=110.0,
        enabled=True,
        tolerance_pct=15.0,
    )
    assert out.status == LockStatus.AGREE
    assert out.deviation_pct is not None


def test_lock3_diverge_when_far_off():
    out = run_lock3_fea_sanity(
        lock1_value=100.0,
        fea_value=200.0,
        enabled=True,
        tolerance_pct=15.0,
    )
    assert out.status == LockStatus.DIVERGE


def test_lock3_error_when_enabled_without_values():
    out = run_lock3_fea_sanity(enabled=True)
    assert out.status == LockStatus.ERROR


# ─────────────────────────────────────────────────────────────────────
# Confidence scoring
# ─────────────────────────────────────────────────────────────────────


def test_confidence_do_not_use_when_lock1_errors():
    l1 = LockOutcome(status=LockStatus.ERROR, note="formula errored")
    l2 = LockOutcome(status=LockStatus.NOT_RUN)
    l3 = LockOutcome(status=LockStatus.NOT_RUN)
    score, band, explanation = compute_confidence(l1, l2, l3)
    assert band == "do_not_use"
    assert score == 0.0
    assert "do not use" in explanation.lower()


def test_confidence_high_when_all_three_agree():
    l1 = LockOutcome(status=LockStatus.AGREE, value=100)
    l2 = LockOutcome(status=LockStatus.AGREE, value=100)
    l3 = LockOutcome(status=LockStatus.AGREE, value=100)
    score, band, _ = compute_confidence(l1, l2, l3)
    assert band == "high"
    assert score >= 95


def test_confidence_good_when_lock1_and_lock2_agree_lock3_off():
    """Typical v1.0 scenario once RAG is populated: 2 locks active, both agree."""
    l1 = LockOutcome(status=LockStatus.AGREE, value=100)
    l2 = LockOutcome(status=LockStatus.AGREE, value=100)
    l3 = LockOutcome(status=LockStatus.NOT_RUN)
    score, band, _ = compute_confidence(l1, l2, l3)
    assert band == "good"
    assert 80 <= score < 95


def test_confidence_good_when_only_lock1_ran():
    """Empty RAG + Lock 3 off = only Lock 1. Score ~85, band 'good', honest note."""
    l1 = LockOutcome(status=LockStatus.AGREE, value=100)
    l2 = LockOutcome(status=LockStatus.INSUFFICIENT_DATA, note="empty knowledge base")
    l3 = LockOutcome(status=LockStatus.NOT_RUN, note="FEA off in v1.0")
    score, band, explanation = compute_confidence(l1, l2, l3)
    assert band == "good"
    assert 80 <= score < 95
    # Honesty check: explanation must mention the missing cross-checks
    assert "knowledge base" in explanation.lower() or "cross-check" in explanation.lower()


def test_confidence_review_when_lock2_diverges():
    """Lock 1 says one thing, historical cluster disagrees → demote to 'review'."""
    l1 = LockOutcome(status=LockStatus.AGREE, value=100)
    l2 = LockOutcome(
        status=LockStatus.DIVERGE,
        value=100,
        note="3.2σ from historical mean",
    )
    l3 = LockOutcome(status=LockStatus.NOT_RUN)
    score, band, explanation = compute_confidence(l1, l2, l3)
    assert band == "review"
    assert 50 <= score < 80
    assert "review" in explanation.lower()


def test_confidence_never_claims_high_when_insufficient_data():
    """This is THE regression test — our v1.0 world starts with empty RAG.
    An honest system never promises 'all three locks agreed' when only one ran."""
    l1 = LockOutcome(status=LockStatus.AGREE, value=50.0)
    l2 = LockOutcome(status=LockStatus.INSUFFICIENT_DATA)
    l3 = LockOutcome(status=LockStatus.NOT_RUN)
    score, band, _ = compute_confidence(l1, l2, l3)
    assert band != "high", "NEVER claim 'high' confidence with only 1 active lock"


# ─────────────────────────────────────────────────────────────────────
# End-to-end via the TripleLock class
# ─────────────────────────────────────────────────────────────────────


def test_triple_lock_verify_v1_default_scenario():
    """Most common v1.0 call: just a Lock 1 value, no history, no FEA."""
    tl = TripleLock()
    result = tl.verify(lock1_value=86.5)

    assert isinstance(result, TripleLockResult)
    assert result.lock1.status == LockStatus.AGREE
    assert result.lock2.status == LockStatus.INSUFFICIENT_DATA
    assert result.lock3.status == LockStatus.NOT_RUN
    assert result.confidence_band == "good"
    assert result.should_ship is True


def test_triple_lock_verify_full_three_lock_agreement():
    tl = TripleLock()
    result = tl.verify(
        lock1_value=100.0,
        historical_values=[98, 99, 100, 101, 102],
        fea_value=105.0,
        fea_enabled=True,
    )
    assert result.confidence_band == "high"
    assert result.should_ship is True


def test_triple_lock_verify_catches_formula_error():
    tl = TripleLock()
    result = tl.verify(lock1_value=float("inf"))
    assert result.confidence_band == "do_not_use"
    assert result.should_ship is False


def test_triple_lock_singleton_importable():
    """`triple_lock` is the recommended entry point."""
    result = triple_lock.verify(lock1_value=42.0)
    assert result.lock1.status == LockStatus.AGREE
    assert math.isclose(result.lock1.value, 42.0)


def test_triple_lock_explanation_never_empty():
    """Every result must carry a human-readable rationale."""
    tl = TripleLock()
    for val in [50.0, 0.01, 1e6]:
        r = tl.verify(lock1_value=val)
        assert r.explanation
        assert len(r.explanation) > 20


# ─────────────────────────────────────────────────────────────────────
# Integration with real formula results
# ─────────────────────────────────────────────────────────────────────


def test_triple_lock_wraps_real_bending_stress_result():
    """Connect app.engines.formulas to Triple-Lock: full pipeline."""
    from app.core.units import AreaMoment, Length, Moment
    from app.engines.formulas import bending_stress

    sigma = bending_stress(
        moment=Moment(value=49_050, unit="N*mm"),
        c=Length.mm(4),
        I=AreaMoment(value=2560, unit="mm^4"),
    )
    # σ ≈ 76.64 MPa for the integrated bracket example
    tl = TripleLock()
    result = tl.verify(lock1_value=sigma.to_mpa())

    assert result.lock1.status == LockStatus.AGREE
    assert math.isclose(result.lock1.value, 76.640625, rel_tol=1e-6)
    # With empty RAG, band is "good" not "high" — honest.
    assert result.confidence_band == "good"
