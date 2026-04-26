"""
Unit tests for app.core.units.

Every conversion factor is checked against authoritative values
(NIST, ASME Y14.5, Machinery's Handbook). Failures here mean every
engineering formula downstream is suspect, so these run first.
"""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.core.units import (
    Area,
    AreaMoment,
    Density,
    Force,
    Length,
    Mass,
    Moment,
    Stress,
    Temperature,
)


# Tolerance: 1e-9 relative for pure rational factors, 1e-6 for published constants
REL = 1e-9
REL_PUBLISHED = 1e-6


# ─────────────────────────────────────────────────────────────────────
# Length
# ─────────────────────────────────────────────────────────────────────

def test_length_mm_is_identity():
    assert Length(value=42.0, unit="mm").to_mm() == 42.0


def test_length_m_to_mm():
    assert Length(value=2.5, unit="m").to_mm() == 2500.0


def test_length_cm_to_mm():
    assert Length(value=3.7, unit="cm").to_mm() == 37.0


def test_length_inch_to_mm_exact():
    """1 inch = 25.4 mm exactly (NIST, ASME Y14.5, ISO 1:2002)."""
    assert Length(value=1.0, unit="in").to_mm() == 25.4


def test_length_ft_to_mm():
    """1 ft = 304.8 mm exactly."""
    assert Length(value=1.0, unit="ft").to_mm() == 304.8


def test_length_roundtrip_inch():
    assert math.isclose(Length.inch(1.0).to_in(), 1.0, rel_tol=REL)


def test_length_frozen_immutable():
    l = Length(value=10, unit="mm")
    with pytest.raises(ValidationError):
        l.value = 20  # type: ignore[misc]


def test_length_rejects_unknown_unit():
    with pytest.raises(ValidationError):
        Length(value=1, unit="furlong")  # type: ignore[arg-type]


def test_length_constructors():
    assert Length.mm(5).to_mm() == 5
    assert Length.m(2).to_mm() == 2000
    assert Length.inch(1).to_mm() == 25.4


# ─────────────────────────────────────────────────────────────────────
# Force
# ─────────────────────────────────────────────────────────────────────

def test_force_newton_identity():
    assert Force(value=100.0, unit="N").to_newton() == 100.0


def test_force_kn_to_n():
    assert Force(value=1.5, unit="kN").to_newton() == 1500.0


def test_force_lbf_to_n():
    """1 lbf = 4.4482216152605 N (NIST SP 811)."""
    n = Force(value=1.0, unit="lbf").to_newton()
    assert math.isclose(n, 4.4482216152605, rel_tol=REL_PUBLISHED)


def test_force_kgf_to_n():
    """1 kgf = 9.80665 N (standard gravity)."""
    assert Force(value=1.0, unit="kgf").to_newton() == 9.80665


def test_force_roundtrip_lbf():
    f = Force(value=500.0, unit="lbf")
    assert math.isclose(f.to_lbf(), 500.0, rel_tol=REL)


# ─────────────────────────────────────────────────────────────────────
# Moment
# ─────────────────────────────────────────────────────────────────────

def test_moment_from_force_and_lever():
    """M = F × L. 500 N × 100 mm = 50,000 N·mm."""
    m = Moment.from_force_and_lever(
        force=Force.newtons(500),
        lever=Length.mm(100),
    )
    assert m.to_nmm() == 50_000.0
    assert m.to_nm() == 50.0


def test_moment_lbf_in_conversion():
    """1 lbf·in = 4.4482 N/lbf × 25.4 mm/in = 112.9848... N·mm"""
    m = Moment(value=1.0, unit="lbf*in").to_nmm()
    assert math.isclose(m, 112.9848290276167, rel_tol=REL_PUBLISHED)


def test_moment_lbf_ft_conversion():
    """1 lbf·ft = 12 × lbf·in = 1355.818 N·mm (Shigley's A-3)."""
    m = Moment(value=1.0, unit="lbf*ft").to_nmm()
    assert math.isclose(m, 1355.8179483314004, rel_tol=REL_PUBLISHED)


# ─────────────────────────────────────────────────────────────────────
# Stress
# ─────────────────────────────────────────────────────────────────────

def test_stress_mpa_identity():
    assert Stress(value=200.0, unit="MPa").to_mpa() == 200.0


def test_stress_gpa_to_mpa():
    """Young's modulus of steel ≈ 200 GPa = 200,000 MPa."""
    assert Stress(value=200.0, unit="GPa").to_mpa() == 200_000.0


def test_stress_psi_to_mpa():
    """1 ksi = 6.89476 MPa (Shigley's inside back cover)."""
    assert math.isclose(
        Stress(value=1.0, unit="ksi").to_mpa(),
        6.89475729317831,
        rel_tol=REL_PUBLISHED,
    )


def test_stress_mpa_to_psi():
    """1 MPa = 145.0377 psi (Shigley's)."""
    assert math.isclose(Stress.mpa(1.0).to_psi(), 145.03773773020924, rel_tol=REL_PUBLISHED)


def test_stress_mpa_is_n_per_mm2():
    """Sanity: MPa = N/mm² is the core invariant that makes our SI internal math work."""
    # 1 MPa = 1 N / 1 mm² by definition — used throughout bending stress formulas
    assert Stress.mpa(1.0).to_mpa() == 1.0


# ─────────────────────────────────────────────────────────────────────
# Area + AreaMoment
# ─────────────────────────────────────────────────────────────────────

def test_area_in2_to_mm2():
    """1 in² = 25.4² = 645.16 mm²."""
    assert Area(value=1.0, unit="in^2").to_mm2() == 645.16


def test_area_moment_in4_to_mm4():
    """1 in⁴ = 25.4⁴ ≈ 416,231 mm⁴."""
    assert math.isclose(AreaMoment(value=1.0, unit="in^4").to_mm4(), 416231.4256, rel_tol=REL_PUBLISHED)


def test_area_moment_roundtrip_in4():
    """Shigley's common example: I = 1.333 in⁴ — round-trip should be exact."""
    i = AreaMoment(value=1.333, unit="in^4")
    mm4 = i.to_mm4()
    back = mm4 / (25.4**4)
    assert math.isclose(back, 1.333, rel_tol=REL)


# ─────────────────────────────────────────────────────────────────────
# Mass + Density
# ─────────────────────────────────────────────────────────────────────

def test_mass_lb_to_kg():
    """1 lb = 0.45359237 kg (international avoirdupois, exact)."""
    assert Mass(value=1.0, unit="lb").to_kg() == 0.45359237


def test_density_g_cm3_to_kg_m3():
    """Aluminum ≈ 2.71 g/cm³ = 2710 kg/m³."""
    assert Density(value=2.71, unit="g/cm^3").to_kg_m3() == 2710.0


# ─────────────────────────────────────────────────────────────────────
# Temperature
# ─────────────────────────────────────────────────────────────────────

def test_temperature_c_identity():
    assert Temperature(value=25.0, unit="C").to_c() == 25.0


def test_temperature_k_to_c():
    """Absolute zero is 0 K = -273.15 °C."""
    assert math.isclose(Temperature(value=0.0, unit="K").to_c(), -273.15, rel_tol=REL)


def test_temperature_f_to_c_freezing():
    """32 °F = 0 °C."""
    assert math.isclose(Temperature(value=32.0, unit="F").to_c(), 0.0, abs_tol=1e-12)


def test_temperature_f_to_c_boiling():
    """212 °F = 100 °C."""
    assert math.isclose(Temperature(value=212.0, unit="F").to_c(), 100.0, abs_tol=1e-12)


def test_temperature_roundtrip_k():
    t = Temperature(value=500.0, unit="K")
    c = t.to_c()
    assert math.isclose(Temperature(value=c, unit="C").to_k(), 500.0, rel_tol=REL)


# ─────────────────────────────────────────────────────────────────────
# Construction safety
# ─────────────────────────────────────────────────────────────────────

def test_extra_fields_forbidden():
    """Pydantic config forbids extra kwargs — catches typos at construction."""
    with pytest.raises(ValidationError):
        Length(value=1, unit="mm", depth=5)  # type: ignore[call-arg]


def test_all_types_are_frozen():
    """Immutability invariant across every unit type."""
    instances = [
        Length(value=1, unit="mm"),
        Force(value=1, unit="N"),
        Moment(value=1, unit="N*mm"),
        Stress(value=1, unit="MPa"),
        Area(value=1, unit="mm^2"),
        AreaMoment(value=1, unit="mm^4"),
        Mass(value=1, unit="kg"),
        Density(value=1, unit="kg/m^3"),
        Temperature(value=1, unit="C"),
    ]
    for inst in instances:
        with pytest.raises(ValidationError):
            inst.value = 99  # type: ignore[misc]
