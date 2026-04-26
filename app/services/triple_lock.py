"""
Triple-Lock accuracy verification — the heart of the accuracy contract.

Every calculation the product shows an engineer passes through all three
locks. A wrong result here doesn't just confuse a user; it could lead to
a part that fails in service. The confidence score we emit is honest —
when we're unsure, we say so.

Lock 1 — DETERMINISTIC (always on, v1.0):
    Pure hand-written engineering formulas from app.engines.formulas.
    Unit-tested against Shigley's examples. No ML, no LLM, no guessing.
    This is the ground truth.

Lock 2 — RAG CROSS-CHECK (v1.0 stub; full in Week 4):
    Query the knowledge base for 3 similar historical problems. Compare
    Lock 1's result to their range. Flag if the result is > 2 standard
    deviations from the cluster. In v1.0 the knowledge base is mostly
    empty, so Lock 2 returns an informational "insufficient data"
    signal that does not downgrade confidence. Once the corpus grows
    we flip a flag and Lock 2 starts contributing.

Lock 3 — FEA SANITY (deferred to v1.5):
    For safety-critical geometries, spin up CalculiX with a coarse mesh
    and compare analytical stress against FEA. Should agree within ~15%
    for standard geometries. In v1.0 this is off.

Confidence rubric:
    95-100 — all active locks agree within tolerance
    80-94  — Lock 1 + 2 agree; Lock 3 diverges (or not run)
    50-79  — Lock 1 and Lock 2 disagree
    <50    — Lock 1 internally inconsistent (should not ship at all)

The confidence score is ALWAYS accompanied by a human-readable rationale.
We never show a number without telling the engineer why.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Data model
# ═════════════════════════════════════════════════════════════════════


class LockStatus(StrEnum):
    AGREE = "agree"
    DIVERGE = "diverge"
    INSUFFICIENT_DATA = "insufficient_data"  # e.g. empty RAG
    NOT_RUN = "not_run"                      # e.g. Lock 3 off in v1.0
    ERROR = "error"


@dataclass(frozen=True)
class LockOutcome:
    """Per-lock result. All three get bundled into TripleLockResult."""

    status: LockStatus
    value: float | None = None
    reference_values: list[float] = field(default_factory=list)
    deviation_pct: float | None = None
    note: str = ""


@dataclass(frozen=True)
class TripleLockResult:
    """The overall verdict the product shows the engineer."""

    lock1: LockOutcome
    lock2: LockOutcome
    lock3: LockOutcome
    confidence_score: float          # 0-100
    confidence_band: Literal["high", "good", "review", "do_not_use"]
    explanation: str

    @property
    def should_ship(self) -> bool:
        """If False, the UI shows a 'DO NOT USE without manual review' banner."""
        return self.confidence_score >= 50.0


# ═════════════════════════════════════════════════════════════════════
# Lock runners
# ═════════════════════════════════════════════════════════════════════


def run_lock1_deterministic(value: float) -> LockOutcome:
    """Lock 1: accept the result from the deterministic formula engine.

    The formula functions themselves (app.engines.formulas) are the
    ground truth — they're unit-tested against textbook. Lock 1 simply
    records that value and, if the value is non-finite or nonsensical,
    reports ERROR.
    """
    if value is None:
        return LockOutcome(status=LockStatus.ERROR, note="no Lock 1 value provided")
    if not _is_finite(value):
        return LockOutcome(
            status=LockStatus.ERROR,
            value=value,
            note=f"Lock 1 result is non-finite ({value})",
        )
    return LockOutcome(status=LockStatus.AGREE, value=value, note="deterministic formula")


def run_lock2_rag_crosscheck(
    lock1_value: float,
    historical_values: list[float],
    *,
    sigma_threshold: float = 2.0,
    min_samples: int = 3,
) -> LockOutcome:
    """Lock 2: compare Lock 1 against historical similar problems.

    Returns INSUFFICIENT_DATA if fewer than `min_samples` historical
    values are known. This is the common case in v1.0 (empty RAG),
    and it does NOT downgrade overall confidence — Lock 2 is treated
    as "not a signal" rather than "disagreement."

    When we do have enough data, we compute the mean + stdev of the
    historical cluster and flag if Lock 1's value is more than
    `sigma_threshold` stdevs away.
    """
    if len(historical_values) < min_samples:
        return LockOutcome(
            status=LockStatus.INSUFFICIENT_DATA,
            reference_values=list(historical_values),
            note=f"only {len(historical_values)} similar problems in knowledge base "
                 f"(need >= {min_samples})",
        )

    if not _is_finite(lock1_value):
        return LockOutcome(
            status=LockStatus.ERROR,
            value=lock1_value,
            note="Lock 1 value is non-finite; cross-check skipped",
        )

    mean = statistics.mean(historical_values)
    stdev = statistics.pstdev(historical_values)

    if stdev == 0:
        # All historical values identical; agreement means equality.
        if abs(lock1_value - mean) <= 1e-9 * max(abs(mean), 1.0):
            return LockOutcome(
                status=LockStatus.AGREE,
                value=lock1_value,
                reference_values=list(historical_values),
                deviation_pct=0.0,
                note="matches historical consensus exactly",
            )
        return LockOutcome(
            status=LockStatus.DIVERGE,
            value=lock1_value,
            reference_values=list(historical_values),
            deviation_pct=_pct_deviation(lock1_value, mean),
            note=f"historical values all = {mean}; Lock 1 differs",
        )

    sigmas = abs(lock1_value - mean) / stdev
    dev_pct = _pct_deviation(lock1_value, mean)

    if sigmas <= sigma_threshold:
        return LockOutcome(
            status=LockStatus.AGREE,
            value=lock1_value,
            reference_values=list(historical_values),
            deviation_pct=dev_pct,
            note=f"within {sigma_threshold}σ of {len(historical_values)} similar problems",
        )
    return LockOutcome(
        status=LockStatus.DIVERGE,
        value=lock1_value,
        reference_values=list(historical_values),
        deviation_pct=dev_pct,
        note=f"{sigmas:.1f}σ from historical mean {mean:.3g} "
             f"(Δ = {dev_pct:.1f}%); review recommended",
    )


def run_lock3_fea_sanity(
    lock1_value: float | None = None,
    fea_value: float | None = None,
    *,
    tolerance_pct: float = 15.0,
    enabled: bool = False,
) -> LockOutcome:
    """Lock 3: coarse FEA cross-check.

    In v1.0 Lock 3 is DISABLED by default (`enabled=False`). Running a
    real FEA job per design would add 20-60s of latency, and CalculiX
    integration lands in Week 20 per the build plan. When we flip this
    on we still only invoke FEA for safety-critical requests.

    For now this function returns NOT_RUN. The signature and tolerance
    are in place so the orchestrator already understands the three-lock
    shape, and flipping the flag requires zero interface change.
    """
    if not enabled:
        return LockOutcome(status=LockStatus.NOT_RUN, note="FEA sanity disabled in v1.0")

    if fea_value is None or lock1_value is None:
        return LockOutcome(
            status=LockStatus.ERROR,
            note="Lock 3 enabled but no FEA or analytical value provided",
        )

    dev_pct = _pct_deviation(lock1_value, fea_value)
    if abs(dev_pct) <= tolerance_pct:
        return LockOutcome(
            status=LockStatus.AGREE,
            value=lock1_value,
            reference_values=[fea_value],
            deviation_pct=dev_pct,
            note=f"analytical within {tolerance_pct}% of FEA",
        )
    return LockOutcome(
        status=LockStatus.DIVERGE,
        value=lock1_value,
        reference_values=[fea_value],
        deviation_pct=dev_pct,
        note=f"analytical differs from FEA by {dev_pct:.1f}% (> {tolerance_pct}%)",
    )


# ═════════════════════════════════════════════════════════════════════
# Confidence scoring
# ═════════════════════════════════════════════════════════════════════


def compute_confidence(
    lock1: LockOutcome,
    lock2: LockOutcome,
    lock3: LockOutcome,
) -> tuple[float, Literal["high", "good", "review", "do_not_use"], str]:
    """Map the three lock outcomes to a score + band + honest explanation.

    The score is bucketed into four bands per the architecture doc:
        95-100 -> high          (all active locks agree)
        80-94  -> good          (Lock 1 + 2 agree; Lock 3 off or mildly off)
        50-79  -> review        (one active lock disagrees)
        <50    -> do_not_use    (Lock 1 internally inconsistent)

    The explanation string is what we show the engineer. It must be
    truthful — if we don't have RAG data, we say so rather than claim
    spurious agreement.
    """
    # Lock 1 broken = we can't trust anything downstream.
    if lock1.status == LockStatus.ERROR:
        return (
            0.0,
            "do_not_use",
            f"Lock 1 (deterministic formula) errored: {lock1.note}. "
            "Do not use this result without manual verification.",
        )

    # Tally which locks actively contributed a signal vs were skipped.
    active: list[tuple[str, LockOutcome]] = []
    skipped: list[tuple[str, LockOutcome]] = []
    for label, outcome in (("Lock 1", lock1), ("Lock 2", lock2), ("Lock 3", lock3)):
        if outcome.status in (LockStatus.AGREE, LockStatus.DIVERGE):
            active.append((label, outcome))
        else:
            skipped.append((label, outcome))

    # Any active disagreement pulls us down into "review" territory.
    disagreements = [(lbl, o) for lbl, o in active if o.status == LockStatus.DIVERGE]

    if disagreements:
        # More disagreements = lower score, but never below 30 if Lock 1 was fine
        # (it's still a valid formula-based answer, just uncorroborated).
        score = max(30.0, 75.0 - 15.0 * len(disagreements))
        band: Literal["high", "good", "review", "do_not_use"] = "review"
        reasons = "; ".join(f"{lbl}: {o.note}" for lbl, o in disagreements)
        explanation = (
            f"Result comes from verified engineering formulas, but "
            f"{len(disagreements)} cross-check(s) flagged deviation. {reasons}. "
            "We recommend manual review before using this result."
        )
        return score, band, explanation

    # All active locks agree. Score depends on how many actually ran.
    active_count = len(active)
    if active_count == 3:
        return (
            98.0,
            "high",
            "All three locks (formula, historical cross-check, FEA) agree within tolerance.",
        )
    if active_count == 2:
        return (
            92.0,
            "good",
            _describe_active_agreement(active, skipped),
        )
    # Only Lock 1 contributed; 2 and 3 were skipped.
    note_parts = [s[1].note for s in skipped if s[1].note]
    return (
        85.0,
        "good",
        "Deterministic formula result. "
        + (" ".join(note_parts) if note_parts else "")
        + " Cross-check coverage will improve as the knowledge base grows.",
    )


def _describe_active_agreement(
    active: list[tuple[str, LockOutcome]],
    skipped: list[tuple[str, LockOutcome]],
) -> str:
    active_labels = [lbl for lbl, _ in active]
    skipped_notes = [f"{lbl}: {o.note}" for lbl, o in skipped if o.note]
    msg = f"{' and '.join(active_labels)} agree."
    if skipped_notes:
        msg += " " + " ".join(skipped_notes) + "."
    return msg


# ═════════════════════════════════════════════════════════════════════
# Orchestrator
# ═════════════════════════════════════════════════════════════════════


class TripleLock:
    """Run all three locks and assemble the final TripleLockResult."""

    def verify(
        self,
        *,
        lock1_value: float,
        historical_values: list[float] | None = None,
        fea_value: float | None = None,
        fea_enabled: bool = False,
    ) -> TripleLockResult:
        lock1 = run_lock1_deterministic(lock1_value)
        lock2 = run_lock2_rag_crosscheck(
            lock1_value=lock1_value,
            historical_values=historical_values or [],
        )
        lock3 = run_lock3_fea_sanity(
            lock1_value=lock1_value,
            fea_value=fea_value,
            enabled=fea_enabled,
        )
        score, band, explanation = compute_confidence(lock1, lock2, lock3)

        result = TripleLockResult(
            lock1=lock1,
            lock2=lock2,
            lock3=lock3,
            confidence_score=score,
            confidence_band=band,
            explanation=explanation,
        )

        logger.info(
            "triple_lock_complete",
            score=score,
            band=band,
            l1=lock1.status,
            l2=lock2.status,
            l3=lock3.status,
        )
        return result


triple_lock = TripleLock()


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _is_finite(v: float | None) -> bool:
    if v is None:
        return False
    try:
        import math
        return math.isfinite(v)
    except (TypeError, ValueError):
        return False


def _pct_deviation(observed: float, reference: float) -> float:
    """Signed percent deviation of `observed` from `reference`."""
    if reference == 0:
        return 0.0 if observed == 0 else float("inf")
    return (observed - reference) / reference * 100.0
