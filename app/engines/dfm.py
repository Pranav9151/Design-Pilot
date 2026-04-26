"""
DesignPilot MECH — DFM (Design for Manufacturing) Rule Engine
Rules sourced from Machinery's Handbook + industry best practices.
All rules are data-driven (not hardcoded logic) for easy extension.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DFMIssue:
    rule_id: str
    severity: str          # "critical", "warning", "info"
    parameter: str         # Which parameter failed
    actual_value: float
    limit_value: float
    unit: str
    message: str
    recommendation: str
    source: str


@dataclass
class DFMResult:
    score: int             # 0-100
    issues: List[DFMIssue]
    process: str
    material_category: str
    status: str            # "PASS", "WARNING", "FAIL"


# ═══════════════════════════════════════════════════════════════
# CNC MACHINING RULES
# ═══════════════════════════════════════════════════════════════

CNC_RULES = {
    "wall_thickness_min": {
        "aluminum": 0.8, "steel": 0.5, "stainless": 0.5,
        "titanium": 1.0, "brass": 0.5, "polymer": 1.5,
    },
    "fillet_radius_min": 0.5,             # mm absolute minimum
    "fillet_to_depth_ratio_min": 0.25,    # fillet ≥ 25% of pocket depth
    "hole_depth_to_dia_max": 10,          # deeper needs special tooling
    "hole_diameter_min": 1.0,             # mm
    "standard_hole_sizes": [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 6.5, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 25, 28, 30],
    "tool_access_min_width": 3.0,         # mm (smallest end mill)
    "max_aspect_ratio_wall": 8,           # height/thickness
    "draft_angle_not_needed": True,       # CNC doesn't need draft
}


class DFMEngine:
    """Check design geometry against manufacturing rules."""
    
    def check_cnc(
        self,
        wall_thicknesses_mm: List[float],
        fillet_radii_mm: List[float],
        hole_diameters_mm: List[float],
        hole_depths_mm: List[float],
        pocket_depths_mm: List[float],
        material_category: str,
        wall_heights_mm: List[float] = None,
    ) -> DFMResult:
        """Check all CNC machining rules. Returns score and issues."""
        issues: List[DFMIssue] = []
        
        # ── WALL THICKNESS ────────────────────────────────────
        min_wall = CNC_RULES["wall_thickness_min"].get(material_category, 1.0)
        for i, t in enumerate(wall_thicknesses_mm):
            if t < min_wall:
                issues.append(DFMIssue(
                    rule_id="CNC-001",
                    severity="critical",
                    parameter=f"wall_thickness[{i}]",
                    actual_value=t,
                    limit_value=min_wall,
                    unit="mm",
                    message=f"Wall thickness {t}mm is below CNC minimum {min_wall}mm for {material_category}",
                    recommendation=f"Increase wall thickness to at least {min_wall}mm, recommended {min_wall * 2}mm",
                    source="Machinery's Handbook 31st ed, CNC Design Guidelines"
                ))
        
        # ── FILLET RADII ──────────────────────────────────────
        for i, r in enumerate(fillet_radii_mm):
            if r < CNC_RULES["fillet_radius_min"]:
                issues.append(DFMIssue(
                    rule_id="CNC-003",
                    severity="critical",
                    parameter=f"fillet_radius[{i}]",
                    actual_value=r,
                    limit_value=CNC_RULES["fillet_radius_min"],
                    unit="mm",
                    message=f"Fillet radius {r}mm is too small for standard end mills",
                    recommendation=f"Use fillet radius ≥ {CNC_RULES['fillet_radius_min']}mm (tool radius constraint)",
                    source="Machinery's Handbook 31st ed, Milling Guidelines"
                ))
        
        # ── POCKET DEPTH vs FILLET ────────────────────────────
        if pocket_depths_mm and fillet_radii_mm:
            for i, (depth, fillet) in enumerate(zip(pocket_depths_mm, fillet_radii_mm)):
                if depth > 0 and fillet / depth < CNC_RULES["fillet_to_depth_ratio_min"]:
                    min_fillet = depth * CNC_RULES["fillet_to_depth_ratio_min"]
                    issues.append(DFMIssue(
                        rule_id="CNC-004",
                        severity="warning",
                        parameter=f"fillet_to_depth_ratio[{i}]",
                        actual_value=round(fillet / depth, 2),
                        limit_value=CNC_RULES["fillet_to_depth_ratio_min"],
                        unit="ratio",
                        message=f"Fillet radius {fillet}mm too small for {depth}mm pocket depth (ratio={fillet/depth:.2f})",
                        recommendation=f"Increase fillet to ≥ {min_fillet:.1f}mm (≥ 1/3 of pocket depth for standard tooling)",
                        source="Industry best practice: fillet ≥ 1/3 × pocket depth"
                    ))
        
        # ── HOLES ─────────────────────────────────────────────
        for i, d in enumerate(hole_diameters_mm):
            if d < CNC_RULES["hole_diameter_min"]:
                issues.append(DFMIssue(
                    rule_id="CNC-005",
                    severity="critical",
                    parameter=f"hole_diameter[{i}]",
                    actual_value=d,
                    limit_value=CNC_RULES["hole_diameter_min"],
                    unit="mm",
                    message=f"Hole diameter {d}mm is below minimum {CNC_RULES['hole_diameter_min']}mm",
                    recommendation=f"Increase hole diameter to ≥ {CNC_RULES['hole_diameter_min']}mm",
                    source="Machinery's Handbook 31st ed, Drilling Guidelines"
                ))
            
            # Check standard sizes (info only)
            if d not in CNC_RULES["standard_hole_sizes"]:
                closest = min(CNC_RULES["standard_hole_sizes"], key=lambda x: abs(x - d))
                issues.append(DFMIssue(
                    rule_id="CNC-006",
                    severity="info",
                    parameter=f"hole_diameter[{i}]",
                    actual_value=d,
                    limit_value=closest,
                    unit="mm",
                    message=f"Hole diameter {d}mm is non-standard. Closest standard size: {closest}mm",
                    recommendation=f"Consider using standard drill size {closest}mm to reduce cost",
                    source="Standard drill size chart (ISO 235)"
                ))
        
        # ── HOLE DEPTH ────────────────────────────────────────
        for i, (depth, dia) in enumerate(zip(hole_depths_mm, hole_diameters_mm)):
            ratio = depth / dia if dia > 0 else 0
            if ratio > CNC_RULES["hole_depth_to_dia_max"]:
                issues.append(DFMIssue(
                    rule_id="CNC-007",
                    severity="warning",
                    parameter=f"hole_depth_ratio[{i}]",
                    actual_value=round(ratio, 1),
                    limit_value=CNC_RULES["hole_depth_to_dia_max"],
                    unit="L/D ratio",
                    message=f"Hole depth/diameter ratio {ratio:.1f} exceeds {CNC_RULES['hole_depth_to_dia_max']}. Special tooling required.",
                    recommendation=f"Reduce hole depth or increase diameter. Ratio > {CNC_RULES['hole_depth_to_dia_max']} significantly increases cost.",
                    source="Machinery's Handbook 31st ed, Deep Hole Drilling"
                ))
        
        # ── WALL ASPECT RATIO ─────────────────────────────────
        if wall_heights_mm and wall_thicknesses_mm:
            for i, (h, t) in enumerate(zip(wall_heights_mm, wall_thicknesses_mm)):
                ratio = h / t if t > 0 else 0
                if ratio > CNC_RULES["max_aspect_ratio_wall"]:
                    issues.append(DFMIssue(
                        rule_id="CNC-010",
                        severity="warning",
                        parameter=f"wall_aspect_ratio[{i}]",
                        actual_value=round(ratio, 1),
                        limit_value=CNC_RULES["max_aspect_ratio_wall"],
                        unit="H/t ratio",
                        message=f"Wall height/thickness ratio {ratio:.1f} is high. Risk of chatter and deflection during machining.",
                        recommendation=f"Increase wall thickness or reduce height. Keep ratio ≤ {CNC_RULES['max_aspect_ratio_wall']}.",
                        source="CNC machining best practice: thin wall chatter prevention"
                    ))
        
        # ── CALCULATE SCORE ───────────────────────────────────
        critical_count = sum(1 for i in issues if i.severity == "critical")
        warning_count = sum(1 for i in issues if i.severity == "warning")
        info_count = sum(1 for i in issues if i.severity == "info")
        
        score = 100 - (critical_count * 25) - (warning_count * 10) - (info_count * 2)
        score = max(0, min(100, score))
        
        if critical_count > 0:
            status = "FAIL"
        elif warning_count > 0:
            status = "WARNING"
        else:
            status = "PASS"
        
        return DFMResult(
            score=score,
            issues=issues,
            process="CNC Machining",
            material_category=material_category,
            status=status
        )
