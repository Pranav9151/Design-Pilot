"""
Engineering formulas — Lock 1 of the Triple-Lock accuracy system.

Every function here:
  1. Takes unit-typed inputs (Length, Force, Moment, Stress, AreaMoment).
  2. Returns a unit-typed result (never a bare float).
  3. Cites its source in the docstring (book, edition, equation number).
  4. Is unit-tested against textbook hand-calculation values.

**DO NOT** add a formula without a unit test that matches a published
textbook example. "Close enough" is not enough; we match the book.

These formulas are the ground truth for "Lock 1" of the Triple-Lock.
Lock 2 (RAG cross-check) and Lock 3 (FEA sanity) compare their answers
to whatever these return. If these are wrong, everything downstream is wrong.
"""
from __future__ import annotations

import math

from app.core.units import (
    Area,
    AreaMoment,
    Force,
    Length,
    Moment,
    Stress,
)


# ═════════════════════════════════════════════════════════════════════
# Bending stress — σ = M·c / I
# Shigley's Mechanical Engineering Design, 11th ed., Eq. 3-24
# ═════════════════════════════════════════════════════════════════════


def bending_stress(moment: Moment, c: Length, I: AreaMoment) -> Stress:
    """Maximum bending stress in a beam cross-section.

    σ = M·c / I

    Args:
        moment: bending moment at the section (N·mm after conversion)
        c: distance from the neutral axis to the extreme fibre (mm)
        I: second moment of area of the cross-section (mm⁴)

    Returns:
        Stress in MPa (which equals N/mm² — the units work out cleanly in SI).

    Reference: Shigley's 11e, Eq. 3-24 (p. 85).
    """
    m_nmm = moment.to_nmm()
    c_mm = c.to_mm()
    i_mm4 = I.to_mm4()

    # σ [N/mm²] = M [N·mm] × c [mm] / I [mm⁴]
    sigma_mpa = (m_nmm * c_mm) / i_mm4
    return Stress.mpa(sigma_mpa)


# ═════════════════════════════════════════════════════════════════════
# Direct (axial) stress — σ = F / A
# Shigley's 11e, Eq. 3-17
# ═════════════════════════════════════════════════════════════════════


def axial_stress(force: Force, area: Area) -> Stress:
    """Axial stress under a direct load.

    σ = F / A

    Reference: Shigley's 11e, Eq. 3-17.
    """
    f_n = force.to_newton()
    a_mm2 = area.to_mm2()
    return Stress.mpa(f_n / a_mm2)


# ═════════════════════════════════════════════════════════════════════
# Direct shear stress — τ = V / A
# Shigley's 11e, Eq. 3-29
# ═════════════════════════════════════════════════════════════════════


def direct_shear_stress(shear_force: Force, shear_area: Area) -> Stress:
    """Average direct shear stress on a shear plane.

    τ = V / A_shear

    Reference: Shigley's 11e, Eq. 3-29. Typical use: bolt shear, pin shear.
    """
    v_n = shear_force.to_newton()
    a_mm2 = shear_area.to_mm2()
    return Stress.mpa(v_n / a_mm2)


# ═════════════════════════════════════════════════════════════════════
# von Mises (distortion-energy) stress for plane stress
# Shigley's 11e, Eq. 5-14
# ═════════════════════════════════════════════════════════════════════


def von_mises_plane_stress(
    sigma_x: Stress,
    sigma_y: Stress,
    tau_xy: Stress,
) -> Stress:
    """von Mises equivalent stress for a 2D (plane-stress) state.

    σ' = √(σx² - σx·σy + σy² + 3·τxy²)

    Reference: Shigley's 11e, Eq. 5-14. Standard ductile-material failure check.
    """
    x = sigma_x.to_mpa()
    y = sigma_y.to_mpa()
    t = tau_xy.to_mpa()
    sigma_eq = math.sqrt(x * x - x * y + y * y + 3.0 * t * t)
    return Stress.mpa(sigma_eq)


# ═════════════════════════════════════════════════════════════════════
# Cantilever tip deflection — δ = F·L³ / (3·E·I)
# Shigley's 11e, Table A-9 Case 1
# ═════════════════════════════════════════════════════════════════════


def cantilever_tip_deflection(
    force: Force,
    length: Length,
    E: Stress,
    I: AreaMoment,
) -> Length:
    """Deflection at the free end of a cantilever with a point load F at the tip.

    δ = F · L³ / (3 · E · I)

    Reference: Shigley's 11e, Table A-9, Case 1.
    """
    f_n = force.to_newton()
    l_mm = length.to_mm()
    e_mpa = E.to_mpa()         # N/mm²
    i_mm4 = I.to_mm4()

    # Units: N · mm³ / ((N/mm²) · mm⁴) = mm
    delta_mm = (f_n * l_mm**3) / (3.0 * e_mpa * i_mm4)
    return Length.mm(delta_mm)


# ═════════════════════════════════════════════════════════════════════
# Euler critical buckling load — F_cr = π²·E·I / (K·L)²
# Shigley's 11e, Eq. 4-42
# ═════════════════════════════════════════════════════════════════════


def euler_buckling_load(
    E: Stress,
    I: AreaMoment,
    length: Length,
    end_condition_factor: float = 1.0,
) -> Force:
    """Euler critical buckling load for a slender column.

    F_cr = π² · E · I / (K · L)²

    K = 1.0  both ends pinned (default)
    K = 0.5  both ends fixed
    K = 2.0  one fixed, one free (cantilever column)
    K ≈ 0.7  one fixed, one pinned

    Reference: Shigley's 11e, Eq. 4-42. Valid only in the Euler slenderness
    regime; Johnson formula must be used for shorter columns.
    """
    if end_condition_factor <= 0:
        raise ValueError("end_condition_factor must be positive")

    e_mpa = E.to_mpa()
    i_mm4 = I.to_mm4()
    l_mm = length.to_mm()

    # F [N] = π² · (N/mm²) · mm⁴ / mm² = N
    f_cr_n = (math.pi**2 * e_mpa * i_mm4) / (end_condition_factor * l_mm) ** 2
    return Force.newtons(f_cr_n)


# ═════════════════════════════════════════════════════════════════════
# Rectangular cross-section I = b·h³/12
# Shigley's 11e, Table A-18
# ═════════════════════════════════════════════════════════════════════


def rectangle_area_moment(base: Length, height: Length) -> AreaMoment:
    """Second moment of area of a rectangle about its neutral (horizontal) axis.

    I = b · h³ / 12

    Reference: Shigley's 11e, Table A-18.
    """
    b_mm = base.to_mm()
    h_mm = height.to_mm()
    return AreaMoment(value=b_mm * h_mm**3 / 12.0, unit="mm^4")


# ═════════════════════════════════════════════════════════════════════
# Circular cross-section I = π·d⁴/64
# Shigley's 11e, Table A-18
# ═════════════════════════════════════════════════════════════════════


def circle_area_moment(diameter: Length) -> AreaMoment:
    """Second moment of area of a solid circle (diametral).

    I = π · d⁴ / 64

    Reference: Shigley's 11e, Table A-18.
    """
    d_mm = diameter.to_mm()
    return AreaMoment(value=math.pi * d_mm**4 / 64.0, unit="mm^4")


# ═════════════════════════════════════════════════════════════════════
# Factor of safety — n = σ_allow / σ_applied
# Shigley's 11e, §1-10
# ═════════════════════════════════════════════════════════════════════


def safety_factor(allowable: Stress, applied: Stress) -> float:
    """Ratio of material strength to applied stress.

    n = σ_allow / σ_applied

    Typical targets:
        n ≥ 1.5  — well-understood static load, ductile material
        n ≥ 2.0  — general engineering practice
        n ≥ 3-4  — impact / uncertain loads
        n ≥ 5-8  — safety-critical applications
    """
    a = applied.to_mpa()
    if a <= 0:
        raise ValueError("applied stress must be positive")
    return allowable.to_mpa() / a


# ═════════════════════════════════════════════════════════════════════
# Bolt shear area (nominal) — A = π·d²/4
# Shigley's 11e, §8-6
# ═════════════════════════════════════════════════════════════════════


def bolt_nominal_shear_area(nominal_diameter: Length) -> Area:
    """Nominal (full-diameter) cross-section area of a round bolt shank.

    A = π · d² / 4

    This is the correct area for **unthreaded-shank shear planes**.
    If the shear plane passes through the threads, use the tensile-stress area
    (reduced by ~15-20%); see Shigley's Table 8-1.
    """
    d_mm = nominal_diameter.to_mm()
    return Area(value=math.pi * d_mm**2 / 4.0, unit="mm^2")
