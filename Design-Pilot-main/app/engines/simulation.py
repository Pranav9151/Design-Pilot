"""
DesignPilot MECH — Simulation Engine (Tier 1: Analytical)
All formulas with textbook source references.
Every function is unit-tested against known hand calculations.
ALL units: mm, N, MPa, kg, °C
"""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class StressResult:
    max_stress_mpa: float
    stress_location: str
    safety_factor: float
    yield_strength_mpa: float
    status: str               # "PASS", "MARGINAL", "FAIL"
    formula_used: str
    source_reference: str
    details: dict


@dataclass
class DeflectionResult:
    max_deflection_mm: float
    deflection_location: str
    allowable_deflection_mm: float
    deflection_ratio: float   # L/delta
    status: str
    formula_used: str
    source_reference: str


@dataclass
class BoltResult:
    shear_stress_mpa: float
    tensile_stress_mpa: float
    bearing_stress_mpa: float
    combined_status: str
    details: dict


class SimulationEngine:
    """Tier 1 Analytical Simulation — instant results, no FEA needed."""
    
    # ── BENDING STRESS ────────────────────────────────────────
    
    def bending_stress_rectangular(
        self, 
        force_n: float,          # Applied force (N)
        length_mm: float,        # Moment arm / cantilever length (mm)
        width_mm: float,         # Section width (mm)
        thickness_mm: float,     # Section thickness (mm)
        yield_strength_mpa: float,
        load_type: str = "cantilever_end"  # "cantilever_end", "simply_supported_center"
    ) -> StressResult:
        """
        Calculate bending stress for a rectangular cross-section.
        σ = M·c / I where c = t/2, I = b·t³/12
        Source: Shigley's 11th ed, Eq. 3-24
        """
        # Moment calculation based on load type
        if load_type == "cantilever_end":
            moment_nmm = force_n * length_mm
            formula = f"M = F×L = {force_n}×{length_mm} = {moment_nmm:.1f} N·mm"
        elif load_type == "simply_supported_center":
            moment_nmm = force_n * length_mm / 4
            formula = f"M = F×L/4 = {force_n}×{length_mm}/4 = {moment_nmm:.1f} N·mm"
        elif load_type == "simply_supported_udl":
            # force_n here is total load (w*L)
            moment_nmm = force_n * length_mm / 8
            formula = f"M = w·L²/8 = {force_n}×{length_mm}/8 = {moment_nmm:.1f} N·mm"
        else:
            moment_nmm = force_n * length_mm
            formula = f"M = F×L = {force_n}×{length_mm} = {moment_nmm:.1f} N·mm"
        
        # Section properties
        c = thickness_mm / 2  # Distance to extreme fiber
        I = width_mm * thickness_mm**3 / 12  # Second moment of area
        
        # Bending stress
        sigma = moment_nmm * c / I  # MPa
        sf = yield_strength_mpa / sigma if sigma > 0 else float('inf')
        
        status = "PASS" if sf >= 2.0 else ("MARGINAL" if sf >= 1.5 else "FAIL")
        
        return StressResult(
            max_stress_mpa=round(sigma, 2),
            stress_location="extreme fiber at fixed end" if "cantilever" in load_type else "extreme fiber at midspan",
            safety_factor=round(sf, 2),
            yield_strength_mpa=yield_strength_mpa,
            status=status,
            formula_used=f"σ = M·c/I; {formula}; I = b·t³/12 = {I:.1f} mm⁴; σ = {moment_nmm:.1f}×{c:.1f}/{I:.1f} = {sigma:.2f} MPa",
            source_reference="Shigley's Mechanical Engineering Design, 11th ed, Eq. 3-24",
            details={
                "moment_nmm": round(moment_nmm, 1),
                "I_mm4": round(I, 1),
                "c_mm": round(c, 1),
                "sigma_mpa": round(sigma, 2),
                "safety_factor": round(sf, 2),
            }
        )

    # ── VON MISES STRESS ──────────────────────────────────────
    
    def von_mises(
        self,
        sigma_x: float,   # Normal stress in x (MPa)
        sigma_y: float,   # Normal stress in y (MPa)  
        tau_xy: float,     # Shear stress (MPa)
        yield_strength_mpa: float
    ) -> StressResult:
        """
        von Mises equivalent stress for plane stress condition.
        σ_vm = √(σ_x² + σ_y² - σ_x·σ_y + 3·τ_xy²)
        Source: Shigley's 11th ed, Eq. 5-13
        """
        sigma_vm = math.sqrt(
            sigma_x**2 + sigma_y**2 - sigma_x * sigma_y + 3 * tau_xy**2
        )
        sf = yield_strength_mpa / sigma_vm if sigma_vm > 0 else float('inf')
        status = "PASS" if sf >= 2.0 else ("MARGINAL" if sf >= 1.5 else "FAIL")
        
        return StressResult(
            max_stress_mpa=round(sigma_vm, 2),
            stress_location="combined stress state",
            safety_factor=round(sf, 2),
            yield_strength_mpa=yield_strength_mpa,
            status=status,
            formula_used=f"σ_vm = √(σx² + σy² - σx·σy + 3·τ²) = √({sigma_x}² + {sigma_y}² - {sigma_x}×{sigma_y} + 3×{tau_xy}²) = {sigma_vm:.2f} MPa",
            source_reference="Shigley's 11th ed, Eq. 5-13 (Distortion Energy Theory)",
            details={"sigma_x": sigma_x, "sigma_y": sigma_y, "tau_xy": tau_xy, "sigma_vm": round(sigma_vm, 2)}
        )

    # ── DEFLECTION ────────────────────────────────────────────
    
    def deflection_cantilever_end_load(
        self,
        force_n: float,
        length_mm: float,
        E_mpa: float,
        I_mm4: float,
        allowable_ratio: float = 200  # L/δ allowable (typically 200-500)
    ) -> DeflectionResult:
        """
        Deflection of cantilever beam with end load.
        δ = P·L³ / (3·E·I)
        Source: Shigley's 11th ed, Table A-9, Case 1
        """
        delta = force_n * length_mm**3 / (3 * E_mpa * I_mm4)
        allowable = length_mm / allowable_ratio
        ratio = length_mm / delta if delta > 0 else float('inf')
        status = "PASS" if delta <= allowable else "FAIL"
        
        return DeflectionResult(
            max_deflection_mm=round(delta, 4),
            deflection_location="free end",
            allowable_deflection_mm=round(allowable, 4),
            deflection_ratio=round(ratio, 1),
            status=status,
            formula_used=f"δ = P·L³/(3·E·I) = {force_n}×{length_mm}³/(3×{E_mpa}×{I_mm4:.1f}) = {delta:.4f} mm",
            source_reference="Shigley's 11th ed, Table A-9, Case 1"
        )

    # ── BOLT ANALYSIS ─────────────────────────────────────────
    
    def bolt_shear(
        self,
        total_force_n: float,
        num_bolts: int,
        bolt_diameter_mm: float,
        bolt_yield_mpa: float = 640,  # Grade 8.8 default
        plate_thickness_mm: float = 10,
        plate_yield_mpa: float = 276,
    ) -> BoltResult:
        """
        Bolt group analysis: shear, tensile, and bearing checks.
        Source: Shigley's 11th ed, Chapter 8
        """
        # Bolt shear area (assuming threads in shear plane)
        A_bolt = math.pi * bolt_diameter_mm**2 / 4
        A_tensile = 0.7854 * bolt_diameter_mm**2  # Approximate tensile area
        
        # Shear stress per bolt
        tau_bolt = total_force_n / (num_bolts * A_bolt)
        
        # Bearing stress on plate
        sigma_bearing = total_force_n / (num_bolts * bolt_diameter_mm * plate_thickness_mm)
        
        # Check against allowables
        tau_allow = 0.577 * bolt_yield_mpa  # von Mises shear = 0.577 × σ_y
        sigma_bearing_allow = 1.5 * plate_yield_mpa  # Typical bearing allowable
        
        shear_ok = tau_bolt < tau_allow / 2.0  # SF ≥ 2
        bearing_ok = sigma_bearing < sigma_bearing_allow / 2.0
        
        status = "PASS" if (shear_ok and bearing_ok) else "FAIL"
        
        return BoltResult(
            shear_stress_mpa=round(tau_bolt, 2),
            tensile_stress_mpa=0,  # No tensile load in shear-only case
            bearing_stress_mpa=round(sigma_bearing, 2),
            combined_status=status,
            details={
                "bolt_shear_area_mm2": round(A_bolt, 1),
                "shear_stress_mpa": round(tau_bolt, 2),
                "shear_allowable_mpa": round(tau_allow, 2),
                "shear_SF": round(tau_allow / tau_bolt, 2) if tau_bolt > 0 else "∞",
                "bearing_stress_mpa": round(sigma_bearing, 2),
                "bearing_allowable_mpa": round(sigma_bearing_allow, 2),
                "bearing_SF": round(sigma_bearing_allow / sigma_bearing, 2) if sigma_bearing > 0 else "∞",
                "formula": f"τ = F/(n×A) = {total_force_n}N / ({num_bolts}×{A_bolt:.1f}mm²) = {tau_bolt:.2f} MPa",
                "source": "Shigley's 11th ed, §8-5 and §8-6"
            }
        )

    # ── BRACKET-SPECIFIC ANALYSIS ─────────────────────────────
    
    def analyze_l_bracket(
        self,
        base_width_mm: float,
        base_depth_mm: float,
        base_thickness_mm: float,
        wall_height_mm: float,
        wall_thickness_mm: float,
        fillet_radius_mm: float,
        force_n: float,               # Load applied at top of wall
        force_direction: str,          # "downward", "horizontal"
        material_yield_mpa: float,
        material_E_mpa: float,
        num_bolts: int = 4,
        bolt_diameter_mm: float = 8,
    ) -> dict:
        """
        Complete L-bracket analysis:
        1. Bending stress at wall-base junction
        2. Bolt shear/bearing
        3. Deflection at load point
        
        Treats the wall as a cantilever from the base.
        """
        results = {}
        
        # 1. Bending at junction (wall acts as cantilever)
        I_wall = base_width_mm * wall_thickness_mm**3 / 12
        bending = self.bending_stress_rectangular(
            force_n=force_n,
            length_mm=wall_height_mm,
            width_mm=base_width_mm,
            thickness_mm=wall_thickness_mm,
            yield_strength_mpa=material_yield_mpa,
            load_type="cantilever_end"
        )
        results["bending"] = bending
        
        # 2. Stress concentration at fillet
        if fillet_radius_mm > 0 and wall_thickness_mm > 0:
            r_d_ratio = fillet_radius_mm / wall_thickness_mm
            # Peterson's Kt approximation for fillet
            Kt = 1 + 2 * (1 / max(r_d_ratio, 0.01))**0.5
            Kt = min(Kt, 5.0)  # Cap at 5
        else:
            Kt = 3.0  # Sharp corner default
        
        actual_stress = bending.max_stress_mpa * Kt
        sf_with_Kt = material_yield_mpa / actual_stress if actual_stress > 0 else float('inf')
        results["stress_concentration"] = {
            "Kt": round(Kt, 2),
            "actual_max_stress_mpa": round(actual_stress, 2),
            "safety_factor_with_Kt": round(sf_with_Kt, 2),
            "status": "PASS" if sf_with_Kt >= 1.5 else "FAIL",
            "source": "Peterson's Stress Concentration Factors, 3rd ed, Fig. 3.1"
        }
        
        # 3. Bolt analysis
        bolts = self.bolt_shear(
            total_force_n=force_n,
            num_bolts=num_bolts,
            bolt_diameter_mm=bolt_diameter_mm,
            plate_thickness_mm=base_thickness_mm,
            plate_yield_mpa=material_yield_mpa
        )
        results["bolts"] = bolts
        
        # 4. Deflection at top of wall
        deflection = self.deflection_cantilever_end_load(
            force_n=force_n,
            length_mm=wall_height_mm,
            E_mpa=material_E_mpa,
            I_mm4=I_wall,
            allowable_ratio=200
        )
        results["deflection"] = deflection
        
        # 5. Overall assessment
        all_pass = (
            bending.status in ("PASS", "MARGINAL") and
            results["stress_concentration"]["status"] == "PASS" and
            bolts.combined_status == "PASS" and
            deflection.status == "PASS"
        )
        
        min_sf = min(
            bending.safety_factor,
            sf_with_Kt,
            bolts.details.get("shear_SF", 999) if isinstance(bolts.details.get("shear_SF"), (int, float)) else 999
        )
        
        results["overall"] = {
            "status": "PASS" if all_pass else "FAIL",
            "min_safety_factor": round(min_sf, 2),
            "critical_location": "wall-base fillet junction",
            "confidence": 85 if all_pass else 60,
            "assumptions": [
                "Load applied statically at top of vertical wall",
                "Wall treated as cantilever beam from base",
                "Stress concentration estimated from Peterson's (approximate)",
                "Bolts in single shear only",
                "No thermal or fatigue loading considered",
            ]
        }
        
        return results
