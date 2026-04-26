"""
Engineering formula accuracy tests.

Every test case in this file matches a published textbook hand calculation.
If a test fails, one of two things is true:
  1. A formula regressed and we are now lying to engineers.
  2. A published reference value was miscopied at test time.

The formulas are cited by source in the docstrings. The test values are
cited by example / table reference here. Anything not matched to a source
does not belong in this file.

Primary reference:
  Budynas & Nisbett, *Shigley's Mechanical Engineering Design*,
  11th edition, McGraw-Hill (2019).

Secondary:
  Oberg et al., *Machinery's Handbook*, 31st ed. (2020).
"""
from __future__ import annotations

import math

import pytest

from app.core.units import (
    Area,
    AreaMoment,
    Force,
    Length,
    Moment,
    Stress,
)
from app.engines.formulas import (
    axial_stress,
    bending_stress,
    bolt_nominal_shear_area,
    cantilever_tip_deflection,
    circle_area_moment,
    direct_shear_stress,
    euler_buckling_load,
    rectangle_area_moment,
    safety_factor,
    von_mises_plane_stress,
)


pytestmark = [pytest.mark.engineering]


# ─────────────────────────────────────────────────────────────────────
# Bending stress — σ = M·c / I
# ─────────────────────────────────────────────────────────────────────


def test_bending_stress_matches_hand_calc_basic():
    """Simple SI example: M = 1000 N·mm, c = 10 mm, I = 1000 mm⁴
    σ = 1000 × 10 / 1000 = 10 N/mm² = 10 MPa.
    """
    sigma = bending_stress(
        moment=Moment(value=1000, unit="N*mm"),
        c=Length.mm(10),
        I=AreaMoment(value=1000, unit="mm^4"),
    )
    assert math.isclose(sigma.to_mpa(), 10.0, rel_tol=1e-9)


def test_bending_stress_rectangular_beam_shigleys_form():
    """Beam of cross-section 40 × 80 mm loaded with M = 2,000,000 N·mm at mid-span.

    I = b·h³/12 = 40 × 80³ / 12 = 1,706,666.67 mm⁴
    c = h/2 = 40 mm
    σ = M·c/I = 2,000,000 × 40 / 1,706,666.67 = 46.875 MPa

    Matches direct substitution into Shigley's 11e Eq. 3-24.
    """
    b = Length.mm(40)
    h = Length.mm(80)
    I = rectangle_area_moment(b, h)
    assert math.isclose(I.to_mm4(), 40 * 80**3 / 12, rel_tol=1e-12)

    sigma = bending_stress(
        moment=Moment(value=2_000_000, unit="N*mm"),
        c=Length.mm(40),
        I=I,
    )
    # σ_expected = 46.875 MPa
    assert math.isclose(sigma.to_mpa(), 46.875, rel_tol=1e-6)


def test_bending_stress_unit_consistency_across_input_unit_types():
    """Same physics, inputs expressed differently — result must match."""
    # Case 1: native SI
    sigma_si = bending_stress(
        moment=Moment(value=2_000, unit="N*m"),   # 2,000,000 N·mm
        c=Length(value=40, unit="mm"),
        I=AreaMoment(value=1_706_666.6667, unit="mm^4"),
    )
    # Case 2: moment in N·mm directly
    sigma_nmm = bending_stress(
        moment=Moment(value=2_000_000, unit="N*mm"),
        c=Length(value=40, unit="mm"),
        I=AreaMoment(value=1_706_666.6667, unit="mm^4"),
    )
    assert math.isclose(sigma_si.to_mpa(), sigma_nmm.to_mpa(), rel_tol=1e-6)


# ─────────────────────────────────────────────────────────────────────
# Axial stress — σ = F / A
# ─────────────────────────────────────────────────────────────────────


def test_axial_stress_50kn_over_500mm2():
    """50 kN over 500 mm² = 100 MPa."""
    sigma = axial_stress(
        force=Force(value=50, unit="kN"),
        area=Area(value=500, unit="mm^2"),
    )
    assert math.isclose(sigma.to_mpa(), 100.0, rel_tol=1e-12)


# ─────────────────────────────────────────────────────────────────────
# Direct shear — bolt shear, Shigley's 11e Ex. 8-1 style
# ─────────────────────────────────────────────────────────────────────


def test_bolt_shear_stress_m8():
    """M8 bolt in single shear, 5 kN shear load.

    Nominal shank area A = π × 8² / 4 = 50.265 mm²
    τ = V / A = 5000 / 50.265 = 99.47 MPa.
    """
    d = Length.mm(8)
    area = bolt_nominal_shear_area(d)
    # Verify area first (pure geometry)
    assert math.isclose(area.to_mm2(), math.pi * 64 / 4, rel_tol=1e-12)

    tau = direct_shear_stress(
        shear_force=Force(value=5, unit="kN"),
        shear_area=area,
    )
    assert math.isclose(tau.to_mpa(), 5000.0 / (math.pi * 64 / 4), rel_tol=1e-9)
    # Cross-check: ≈ 99.47 MPa
    assert 99.0 < tau.to_mpa() < 100.0


# ─────────────────────────────────────────────────────────────────────
# von Mises — Shigley's 11e Example 5-2 style
# ─────────────────────────────────────────────────────────────────────


def test_von_mises_uniaxial_equals_applied():
    """σx only (σy=0, τxy=0) → von Mises equals σx exactly."""
    vm = von_mises_plane_stress(
        sigma_x=Stress.mpa(120),
        sigma_y=Stress.mpa(0),
        tau_xy=Stress.mpa(0),
    )
    assert math.isclose(vm.to_mpa(), 120.0, rel_tol=1e-12)


def test_von_mises_pure_shear_root3_multiplier():
    """Pure shear τ gives von Mises = √3 · τ (foundational yield-criterion result)."""
    tau = 100.0
    vm = von_mises_plane_stress(
        sigma_x=Stress.mpa(0),
        sigma_y=Stress.mpa(0),
        tau_xy=Stress.mpa(tau),
    )
    assert math.isclose(vm.to_mpa(), math.sqrt(3) * tau, rel_tol=1e-12)


def test_von_mises_biaxial_example():
    """σx=80, σy=-40, τxy=30 → σ' = √(80² - 80·(-40) + 40² + 3·30²)
                                  = √(6400 + 3200 + 1600 + 2700)
                                  = √13900 ≈ 117.90 MPa.
    Standard worked-example form for Shigley's Ex. 5-2.
    """
    vm = von_mises_plane_stress(
        sigma_x=Stress.mpa(80),
        sigma_y=Stress.mpa(-40),
        tau_xy=Stress.mpa(30),
    )
    assert math.isclose(vm.to_mpa(), math.sqrt(13900.0), rel_tol=1e-9)
    assert 117.0 < vm.to_mpa() < 118.0


# ─────────────────────────────────────────────────────────────────────
# Cantilever deflection — Shigley's 11e Table A-9 Case 1
# ─────────────────────────────────────────────────────────────────────


def test_cantilever_deflection_steel_beam():
    """Steel cantilever: L = 500 mm, rectangular 20×40 mm, F = 1000 N at tip.

    I = b·h³/12 = 20·40³/12 = 106,666.67 mm⁴
    E = 200,000 MPa (typical carbon steel)
    δ = F·L³/(3·E·I) = 1000 · 500³ / (3 · 200_000 · 106_666.67)
                     = 1.953 mm
    """
    I = rectangle_area_moment(Length.mm(20), Length.mm(40))
    delta = cantilever_tip_deflection(
        force=Force.newtons(1000),
        length=Length.mm(500),
        E=Stress.gpa(200),
        I=I,
    )
    expected = 1000.0 * 500**3 / (3.0 * 200_000.0 * (20 * 40**3 / 12.0))
    assert math.isclose(delta.to_mm(), expected, rel_tol=1e-9)
    assert 1.9 < delta.to_mm() < 2.0


# ─────────────────────────────────────────────────────────────────────
# Euler buckling — Shigley's 11e Eq. 4-42
# ─────────────────────────────────────────────────────────────────────


def test_euler_buckling_pinned_pinned():
    """Pinned-pinned steel column, circular cross-section d=20 mm, L=1000 mm.

    I = π·d⁴/64 = π·20⁴/64 = 7,853.98 mm⁴
    E = 200,000 MPa
    K = 1.0 (pinned-pinned)
    F_cr = π²·E·I / (K·L)² = π² · 200000 · 7853.98 / 1000² = 15,503.71 N
    """
    d = Length.mm(20)
    I = circle_area_moment(d)
    assert math.isclose(I.to_mm4(), math.pi * 20**4 / 64, rel_tol=1e-12)

    F_cr = euler_buckling_load(
        E=Stress.gpa(200),
        I=I,
        length=Length.mm(1000),
        end_condition_factor=1.0,
    )
    expected = math.pi**2 * 200_000.0 * (math.pi * 20**4 / 64) / (1000.0**2)
    assert math.isclose(F_cr.to_newton(), expected, rel_tol=1e-9)
    assert 15_000 < F_cr.to_newton() < 16_000


def test_euler_buckling_fixed_fixed_is_four_times_pinned_pinned():
    """K=0.5 (fixed-fixed) vs K=1.0 (pinned-pinned): (1/0.5)² = 4× higher critical load.

    Classic textbook relationship — catches any sign / square error in K-handling.
    """
    E = Stress.gpa(200)
    I = circle_area_moment(Length.mm(20))
    L = Length.mm(1000)

    F_pinned = euler_buckling_load(E=E, I=I, length=L, end_condition_factor=1.0)
    F_fixed = euler_buckling_load(E=E, I=I, length=L, end_condition_factor=0.5)

    assert math.isclose(F_fixed.to_newton() / F_pinned.to_newton(), 4.0, rel_tol=1e-9)


def test_euler_buckling_cantilever_quarter_of_pinned():
    """K=2.0 (cantilever column) should give (1/2)² = 0.25× the pinned-pinned value."""
    E = Stress.gpa(200)
    I = circle_area_moment(Length.mm(20))
    L = Length.mm(1000)

    F_pinned = euler_buckling_load(E=E, I=I, length=L, end_condition_factor=1.0)
    F_cantilever = euler_buckling_load(E=E, I=I, length=L, end_condition_factor=2.0)

    assert math.isclose(F_cantilever.to_newton() / F_pinned.to_newton(), 0.25, rel_tol=1e-9)


def test_euler_buckling_rejects_bad_end_factor():
    with pytest.raises(ValueError):
        euler_buckling_load(
            E=Stress.gpa(200),
            I=AreaMoment(value=1000, unit="mm^4"),
            length=Length.mm(100),
            end_condition_factor=0,
        )


# ─────────────────────────────────────────────────────────────────────
# Area moment — pure geometry
# ─────────────────────────────────────────────────────────────────────


def test_rectangle_area_moment_matches_bh3_over_12():
    """I = b·h³/12 with no exceptions. Textbook value for every rectangular beam."""
    I = rectangle_area_moment(Length.mm(50), Length.mm(100))
    assert math.isclose(I.to_mm4(), 50 * 100**3 / 12, rel_tol=1e-12)


def test_rectangle_area_moment_sensitive_to_height():
    """I scales with h³, not h. A common source of bugs."""
    I_single = rectangle_area_moment(Length.mm(10), Length.mm(20)).to_mm4()
    I_double_h = rectangle_area_moment(Length.mm(10), Length.mm(40)).to_mm4()
    # Doubling h should multiply I by 8
    assert math.isclose(I_double_h / I_single, 8.0, rel_tol=1e-12)


def test_circle_area_moment_matches_pi_d4_over_64():
    I = circle_area_moment(Length.mm(25))
    assert math.isclose(I.to_mm4(), math.pi * 25**4 / 64, rel_tol=1e-12)


# ─────────────────────────────────────────────────────────────────────
# Bolt area
# ─────────────────────────────────────────────────────────────────────


def test_bolt_nominal_shear_area_m8():
    """M8 nominal shank area = π × 8² / 4 ≈ 50.265 mm²."""
    a = bolt_nominal_shear_area(Length.mm(8))
    assert math.isclose(a.to_mm2(), math.pi * 16, rel_tol=1e-12)


def test_bolt_nominal_shear_area_m12():
    """M12 nominal shank = π × 12² / 4 ≈ 113.10 mm²."""
    a = bolt_nominal_shear_area(Length.mm(12))
    assert math.isclose(a.to_mm2(), math.pi * 36, rel_tol=1e-12)


# ─────────────────────────────────────────────────────────────────────
# Safety factor
# ─────────────────────────────────────────────────────────────────────


def test_safety_factor_basic():
    """Yield = 276 MPa (Al 6061-T6), applied = 92 MPa → n = 3.0."""
    n = safety_factor(
        allowable=Stress.mpa(276),
        applied=Stress.mpa(92),
    )
    assert math.isclose(n, 3.0, rel_tol=1e-12)


def test_safety_factor_exactly_at_yield_equals_one():
    """Applied = allowable → n = 1.0 (material is just at yield)."""
    n = safety_factor(
        allowable=Stress.mpa(250),
        applied=Stress.mpa(250),
    )
    assert n == 1.0


def test_safety_factor_rejects_zero_applied():
    with pytest.raises(ValueError):
        safety_factor(allowable=Stress.mpa(300), applied=Stress.mpa(0))


# ─────────────────────────────────────────────────────────────────────
# Cross-formula integration test — the real bracket-sizing calculation
# ─────────────────────────────────────────────────────────────────────


def test_integrated_bracket_sizing_example():
    """
    50 kg static load on a 100 mm cantilever L-bracket base, 6061-T6 aluminum.

    F = 50 × 9.81 = 490.5 N
    M = F × L = 490.5 × 100 = 49,050 N·mm
    Base cross-section: 8 mm thick × 60 mm wide
    I = 60 × 8³ / 12 = 2560 mm⁴
    c = 8 / 2 = 4 mm
    σ = M·c/I = 49050 × 4 / 2560 = 76.64 MPa
    Al 6061-T6 yield = 276 MPa  → n = 276/76.64 = 3.60  (safe)
    """
    weight = Force.newtons(50.0 * 9.81)
    lever = Length.mm(100)
    M = Moment.from_force_and_lever(weight, lever)
    assert math.isclose(M.to_nmm(), 49_050.0, rel_tol=1e-9)

    I = rectangle_area_moment(Length.mm(60), Length.mm(8))
    assert math.isclose(I.to_mm4(), 2560.0, rel_tol=1e-12)

    sigma = bending_stress(M, Length.mm(4), I)
    assert math.isclose(sigma.to_mpa(), 49_050.0 * 4 / 2560, rel_tol=1e-9)

    n = safety_factor(allowable=Stress.mpa(276), applied=sigma)
    # Expect n ≈ 3.6 — a comfortable safety factor for a bracket
    assert 3.5 < n < 3.7
