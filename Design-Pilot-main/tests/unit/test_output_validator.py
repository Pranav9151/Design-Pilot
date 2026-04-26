"""
Unit tests for app.services.output_validator — Layer 3 of sandbox defense.

Tests every check independently with injected SandboxResult values;
no Docker, no CadQuery, no DB.
"""
from __future__ import annotations

import pytest

from app.services.output_validator import OutputValidator, Severity
from app.services.sandbox import SandboxResult


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _ok_result(
    volume_mm3: float = 30_000.0,
    bbox_x: float = 80.0,
    bbox_y: float = 60.0,
    bbox_z: float = 56.0,
) -> SandboxResult:
    return SandboxResult(
        ok=True,
        stage="success",
        metrics={
            "volume_mm3": volume_mm3,
            "bbox_x_size": bbox_x,
            "bbox_y_size": bbox_y,
            "bbox_z_size": bbox_z,
        },
    )


def _failed_result() -> SandboxResult:
    return SandboxResult(ok=False, stage="execute", error="container crashed")


V = OutputValidator()


# ─────────────────────────────────────────────────────────────────────
# Sandbox-failed input
# ─────────────────────────────────────────────────────────────────────

def test_failed_sandbox_is_not_ok():
    r = V.validate(sandbox_result=_failed_result())
    assert not r.passed
    assert any(i.code == "sandbox_not_ok" for i in r.errors)


# ─────────────────────────────────────────────────────────────────────
# Volume checks
# ─────────────────────────────────────────────────────────────────────

def test_valid_volume_passes():
    r = V.validate(sandbox_result=_ok_result(volume_mm3=30_000.0))
    assert r.passed
    assert not r.errors


def test_zero_volume_is_error():
    r = V.validate(sandbox_result=_ok_result(volume_mm3=0.0))
    assert not r.passed
    assert any(i.code == "volume_zero_or_negative" for i in r.errors)


def test_negative_volume_is_error():
    r = V.validate(sandbox_result=_ok_result(volume_mm3=-1.0))
    assert not r.passed
    assert any(i.code == "volume_zero_or_negative" for i in r.errors)


def test_tiny_volume_is_error():
    r = V.validate(sandbox_result=_ok_result(volume_mm3=0.01))
    assert not r.passed
    assert any(i.code == "volume_too_small" for i in r.errors)


def test_huge_volume_mm_m_confusion_is_error():
    # 20 m³ in mm³ = 20e9 mm³ — classic mm/m confusion
    r = V.validate(sandbox_result=_ok_result(volume_mm3=20e9))
    assert not r.passed
    assert any(i.code == "volume_too_large" for i in r.errors)


def test_borderline_large_volume_passes():
    # 1 m³ = 1e9 mm³; our limit is 10 m³ = 10e9
    r = V.validate(sandbox_result=_ok_result(volume_mm3=1e9))
    assert r.passed


# ─────────────────────────────────────────────────────────────────────
# Bounding-box degenerate checks
# ─────────────────────────────────────────────────────────────────────

def test_normal_bbox_passes():
    r = V.validate(sandbox_result=_ok_result(bbox_x=80, bbox_y=60, bbox_z=56))
    assert r.passed


def test_flat_z_axis_is_error():
    r = V.validate(sandbox_result=_ok_result(bbox_z=0.2))
    assert not r.passed
    assert any(i.code == "bbox_z_degenerate" for i in r.errors)


def test_all_axes_degenerate_gives_three_errors():
    r = V.validate(sandbox_result=_ok_result(bbox_x=0.1, bbox_y=0.1, bbox_z=0.1))
    assert not r.passed
    codes = {i.code for i in r.errors}
    assert "bbox_x_degenerate" in codes
    assert "bbox_y_degenerate" in codes
    assert "bbox_z_degenerate" in codes


def test_very_large_bbox_is_warning_not_error():
    r = V.validate(sandbox_result=_ok_result(bbox_x=6_000.0))
    assert r.passed          # passed (no errors)
    assert r.warnings        # but has a warning


# ─────────────────────────────────────────────────────────────────────
# Parameter agreement
# ─────────────────────────────────────────────────────────────────────

def test_matching_parameters_passes():
    # spec: 80mm wide, 60mm deep, 50mm wall height
    # measured bbox ≈ those (with fillet rounding)
    r = V.validate(
        sandbox_result=_ok_result(bbox_x=82.0, bbox_y=61.0, bbox_z=58.0),
        expected_base_width_mm=80.0,
        expected_base_depth_mm=60.0,
        expected_wall_height_mm=56.0,
    )
    assert r.passed, f"Unexpected issues: {[i.message for i in r.issues]}"


def test_severely_mismatched_dimension_is_warning():
    # bbox_x is 200 mm but we specified 80 mm — well outside 30% tolerance.
    # We must specify all three expected dims so the sort-pair algorithm
    # lines up the 200mm measurement against the closest expected dim.
    r = V.validate(
        sandbox_result=_ok_result(bbox_x=200.0, bbox_y=60.0, bbox_z=56.0),
        expected_base_width_mm=80.0,
        expected_base_depth_mm=60.0,
        expected_wall_height_mm=56.0,
    )
    # Warnings only (not errors) for dim mismatches
    assert r.passed
    assert any("mismatch" in i.code for i in r.warnings), (
        f"Expected a mismatch warning; got: {[i.code for i in r.warnings]}"
    )


def test_no_params_specified_skips_agreement_check():
    r = V.validate(sandbox_result=_ok_result())
    assert r.passed
    assert not r.issues


def test_b_variant_scale_factor_within_tolerance():
    """Variant B (Strongest) scales all dims by up to 1.30×.
    The 30% tolerance window must not flag this as a mismatch.
    """
    base_width = 80.0
    scaled = base_width * 1.10   # 88 mm — within 30% tolerance
    r = V.validate(
        sandbox_result=_ok_result(bbox_x=scaled, bbox_y=66.0, bbox_z=56.0),
        expected_base_width_mm=base_width,
    )
    # Should pass cleanly
    param_warnings = [i for i in r.warnings if "mismatch" in i.code]
    assert not param_warnings, f"False positive on B-variant scaling: {param_warnings}"


# ─────────────────────────────────────────────────────────────────────
# Summary helpers
# ─────────────────────────────────────────────────────────────────────

def test_summary_all_passed():
    r = V.validate(sandbox_result=_ok_result())
    assert "passed" in r.summary()


def test_summary_shows_error_message_on_failure():
    r = V.validate(sandbox_result=_ok_result(volume_mm3=0.0))
    assert "failed" in r.summary()
    assert "non-positive" in r.summary()


def test_severity_enum_values():
    assert Severity.ERROR.value == "error"
    assert Severity.WARNING.value == "warning"
