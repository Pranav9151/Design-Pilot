"""
Unit-typed engineering quantities.

**CRITICAL:** Every engineering formula in this codebase takes these types,
NEVER bare floats. This is the type-system guardrail that makes
Mars-Climate-Orbiter-class unit conversion errors impossible at compile time.

Canonical SI internals:
    Length   → mm
    Force    → N
    Moment   → N·mm
    Stress   → MPa  (== N/mm²)
    Area     → mm²
    Volume   → mm³
    Mass     → kg
    Temp     → °C
    Density  → kg/m³

All types are Pydantic v2 models, frozen (immutable), and implement
explicit conversion methods. Arithmetic is NOT overloaded — use .to_si()
and do math on raw numbers, then wrap the result in the correct type.
This keeps formulas explicit and auditable.

Example (correct):
    from app.core.units import Force, Length, Stress

    load = Force(value=500, unit="N")
    lever = Length(value=100, unit="mm")
    moment_si = load.to_newton() * lever.to_mm()  # N·mm

Example (the bug this prevents):
    # If formulas took bare floats, a caller passing 500 lbf would
    # silently be treated as 500 N. With Force, construction forces
    # an explicit unit declaration and the bug is caught.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────
# Length
# ─────────────────────────────────────────────────────────────────────

LengthUnit = Literal["mm", "cm", "m", "in", "ft"]
_LENGTH_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
}


class Length(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(..., description="Scalar value in the specified unit")
    unit: LengthUnit = "mm"

    def to_mm(self) -> float:
        return self.value * _LENGTH_TO_MM[self.unit]

    def to_m(self) -> float:
        return self.to_mm() / 1000.0

    def to_in(self) -> float:
        return self.to_mm() / 25.4

    @classmethod
    def mm(cls, v: float) -> Length:
        return cls(value=v, unit="mm")

    @classmethod
    def m(cls, v: float) -> Length:
        return cls(value=v, unit="m")

    @classmethod
    def inch(cls, v: float) -> Length:
        return cls(value=v, unit="in")

    def __str__(self) -> str:
        return f"{self.value}{self.unit}"


# ─────────────────────────────────────────────────────────────────────
# Force
# ─────────────────────────────────────────────────────────────────────

ForceUnit = Literal["N", "kN", "MN", "lbf", "kgf"]
_FORCE_TO_N: dict[str, float] = {
    "N": 1.0,
    "kN": 1000.0,
    "MN": 1_000_000.0,
    "lbf": 4.4482216152605,
    "kgf": 9.80665,
}


class Force(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(..., description="Scalar force")
    unit: ForceUnit = "N"

    def to_newton(self) -> float:
        return self.value * _FORCE_TO_N[self.unit]

    def to_kn(self) -> float:
        return self.to_newton() / 1000.0

    def to_lbf(self) -> float:
        return self.to_newton() / _FORCE_TO_N["lbf"]

    @classmethod
    def newtons(cls, v: float) -> Force:
        return cls(value=v, unit="N")

    @classmethod
    def kn(cls, v: float) -> Force:
        return cls(value=v, unit="kN")

    def __str__(self) -> str:
        return f"{self.value}{self.unit}"


# ─────────────────────────────────────────────────────────────────────
# Moment (torque / bending)
# ─────────────────────────────────────────────────────────────────────

MomentUnit = Literal["N*mm", "N*m", "kN*m", "lbf*in", "lbf*ft"]
_MOMENT_TO_NMM: dict[str, float] = {
    "N*mm": 1.0,
    "N*m": 1000.0,
    "kN*m": 1_000_000.0,
    "lbf*in": 112.9848290276167,      # 4.4482216152605 N/lbf * 25.4 mm/in
    "lbf*ft": 1355.8179483314004,     # 12 * lbf*in
}


class Moment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(..., description="Scalar moment")
    unit: MomentUnit = "N*mm"

    def to_nmm(self) -> float:
        return self.value * _MOMENT_TO_NMM[self.unit]

    def to_nm(self) -> float:
        return self.to_nmm() / 1000.0

    def to_lbf_in(self) -> float:
        return self.to_nmm() / _MOMENT_TO_NMM["lbf*in"]

    @classmethod
    def from_force_and_lever(cls, force: Force, lever: Length) -> Moment:
        return cls(value=force.to_newton() * lever.to_mm(), unit="N*mm")

    def __str__(self) -> str:
        return f"{self.value}{self.unit}"


# ─────────────────────────────────────────────────────────────────────
# Stress
# ─────────────────────────────────────────────────────────────────────

StressUnit = Literal["Pa", "kPa", "MPa", "GPa", "psi", "ksi"]
_STRESS_TO_MPA: dict[str, float] = {
    "Pa": 1e-6,
    "kPa": 1e-3,
    "MPa": 1.0,
    "GPa": 1000.0,
    "psi": 0.00689475729317831,       # 6.89475729317831 kPa/psi = 6.89e-3 MPa
    "ksi": 6.89475729317831,
}


class Stress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(..., description="Scalar stress")
    unit: StressUnit = "MPa"

    def to_mpa(self) -> float:
        return self.value * _STRESS_TO_MPA[self.unit]

    def to_pa(self) -> float:
        return self.to_mpa() * 1e6

    def to_gpa(self) -> float:
        return self.to_mpa() / 1000.0

    def to_psi(self) -> float:
        return self.to_mpa() / _STRESS_TO_MPA["psi"]

    def to_ksi(self) -> float:
        return self.to_mpa() / _STRESS_TO_MPA["ksi"]

    @classmethod
    def mpa(cls, v: float) -> Stress:
        return cls(value=v, unit="MPa")

    @classmethod
    def gpa(cls, v: float) -> Stress:
        return cls(value=v, unit="GPa")

    def __str__(self) -> str:
        return f"{self.value}{self.unit}"


# ─────────────────────────────────────────────────────────────────────
# Area (mm²)
# ─────────────────────────────────────────────────────────────────────

AreaUnit = Literal["mm^2", "cm^2", "m^2", "in^2"]
_AREA_TO_MM2: dict[str, float] = {
    "mm^2": 1.0,
    "cm^2": 100.0,
    "m^2": 1_000_000.0,
    "in^2": 645.16,
}


class Area(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: AreaUnit = "mm^2"

    def to_mm2(self) -> float:
        return self.value * _AREA_TO_MM2[self.unit]

    def to_m2(self) -> float:
        return self.to_mm2() / 1e6


# ─────────────────────────────────────────────────────────────────────
# Area moment of inertia (second moment of area) — mm⁴
# ─────────────────────────────────────────────────────────────────────

AreaMomentUnit = Literal["mm^4", "cm^4", "m^4", "in^4"]
_AM_TO_MM4: dict[str, float] = {
    "mm^4": 1.0,
    "cm^4": 10_000.0,
    "m^4": 1e12,
    "in^4": 25.4**4,  # 416231.4256
}


class AreaMoment(BaseModel):
    """Second moment of area I (bending stiffness geometry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: AreaMomentUnit = "mm^4"

    def to_mm4(self) -> float:
        return self.value * _AM_TO_MM4[self.unit]

    def to_m4(self) -> float:
        return self.to_mm4() / 1e12

    def to_in4(self) -> float:
        return self.to_mm4() / _AM_TO_MM4["in^4"]


# ─────────────────────────────────────────────────────────────────────
# Mass & Density
# ─────────────────────────────────────────────────────────────────────

MassUnit = Literal["g", "kg", "lb"]
_MASS_TO_KG: dict[str, float] = {
    "g": 0.001,
    "kg": 1.0,
    "lb": 0.45359237,
}


class Mass(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: MassUnit = "kg"

    def to_kg(self) -> float:
        return self.value * _MASS_TO_KG[self.unit]


DensityUnit = Literal["kg/m^3", "g/cm^3"]
_DENSITY_TO_KG_M3: dict[str, float] = {
    "kg/m^3": 1.0,
    "g/cm^3": 1000.0,
}


class Density(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: DensityUnit = "kg/m^3"

    def to_kg_m3(self) -> float:
        return self.value * _DENSITY_TO_KG_M3[self.unit]


# ─────────────────────────────────────────────────────────────────────
# Temperature (affine conversion — not a multiplicative factor)
# ─────────────────────────────────────────────────────────────────────

TempUnit = Literal["C", "K", "F"]


class Temperature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: TempUnit = "C"

    def to_c(self) -> float:
        if self.unit == "C":
            return self.value
        if self.unit == "K":
            return self.value - 273.15
        # F
        return (self.value - 32.0) * 5.0 / 9.0

    def to_k(self) -> float:
        return self.to_c() + 273.15
