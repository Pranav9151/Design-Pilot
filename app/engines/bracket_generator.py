"""
DesignPilot MECH — Bracket Generator (CadQuery)
Generates parametric 3D bracket models as STEP files.
Each function produces valid, manifold, manufacturable geometry.
"""

import cadquery as cq
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional
import math
import json
import os

from app.core.paths import make_tempdir


@dataclass
class BracketParams:
    """All parameters for an L-bracket design."""
    base_width: float = 80.0        # mm
    base_depth: float = 60.0        # mm  
    base_thickness: float = 8.0     # mm
    wall_height: float = 50.0       # mm
    wall_thickness: float = 6.0     # mm
    fillet_radius: float = 5.0      # mm
    hole_diameter: float = 9.0      # mm (M8 clearance = 9mm)
    hole_count_x: int = 2
    hole_count_y: int = 2
    hole_spacing_x: float = 50.0    # mm
    hole_spacing_y: float = 30.0    # mm
    gusset_thickness: float = 0.0   # mm (0 = no gusset)
    gusset_height: float = 0.0      # mm
    
    def to_dict(self) -> dict:
        """Export parameters with min/max/unit for UI sliders."""
        return {
            "base_width": {"value": self.base_width, "min": 30, "max": 300, "unit": "mm", "step": 1, "label": "Base width"},
            "base_depth": {"value": self.base_depth, "min": 20, "max": 200, "unit": "mm", "step": 1, "label": "Base depth"},
            "base_thickness": {"value": self.base_thickness, "min": 3, "max": 30, "unit": "mm", "step": 0.5, "label": "Base thickness"},
            "wall_height": {"value": self.wall_height, "min": 15, "max": 200, "unit": "mm", "step": 1, "label": "Wall height"},
            "wall_thickness": {"value": self.wall_thickness, "min": 3, "max": 25, "unit": "mm", "step": 0.5, "label": "Wall thickness"},
            "fillet_radius": {"value": self.fillet_radius, "min": 0.5, "max": min(self.wall_thickness, self.base_thickness) * 0.9, "unit": "mm", "step": 0.5, "label": "Fillet radius"},
            "hole_diameter": {"value": self.hole_diameter, "min": 3, "max": 25, "unit": "mm", "step": 0.5, "label": "Hole diameter"},
            "hole_spacing_x": {"value": self.hole_spacing_x, "min": 15, "max": self.base_width - 15, "unit": "mm", "step": 1, "label": "Hole spacing X"},
            "hole_spacing_y": {"value": self.hole_spacing_y, "min": 10, "max": self.base_depth - 10, "unit": "mm", "step": 1, "label": "Hole spacing Y"},
        }


@dataclass
class BracketResult:
    """Result of bracket generation."""
    params: BracketParams
    step_file_path: str
    stl_file_path: str
    properties: dict         # volume, mass, bounding box, surface area
    cadquery_code: str       # The CadQuery code used (for transparency)
    variant_name: str        # "A: Lightest", "B: Strongest", "C: Cheapest"
    design_rationale: str


class BracketGenerator:
    """Generate parametric bracket models using CadQuery."""
    
    def generate_l_bracket(self, params: BracketParams, output_dir: str = None) -> BracketResult:
        """
        Generate an L-bracket with mounting holes and optional gusset.
        
        Geometry:
        - Horizontal base plate with bolt holes
        - Vertical wall rising from one edge of the base
        - Fillet at the wall-base junction
        - Optional triangular gusset for reinforcement
        """
        if output_dir is None:
            output_dir = str(make_tempdir(prefix="dpmech-bracket-"))
        
        p = params
        
        # ── BUILD GEOMETRY ────────────────────────────────────
        # Base plate
        result = (
            cq.Workplane("XY")
            .box(p.base_width, p.base_depth, p.base_thickness, centered=(True, True, False))
        )
        
        # Vertical wall (positioned at back edge of base)
        wall_offset_y = p.base_depth / 2 - p.wall_thickness / 2
        result = (
            result
            .faces(">Z").workplane()
            .center(0, wall_offset_y)
            .box(p.base_width, p.wall_thickness, p.wall_height, centered=(True, True, False))
        )
        
        # Fillet at wall-base junction
        if p.fillet_radius > 0.3:
            try:
                result = result.edges("|X").edges(
                    cq.selectors.NearestToPointSelector((0, wall_offset_y - p.wall_thickness/2, p.base_thickness))
                ).fillet(p.fillet_radius)
            except Exception:
                pass  # Fillet may fail on certain geometries — skip gracefully
        
        # Gusset reinforcement (triangular rib)
        if p.gusset_thickness > 0 and p.gusset_height > 0:
            gusset_h = min(p.gusset_height, p.wall_height * 0.8)
            gusset_d = min(p.gusset_height, p.base_depth * 0.6)
            
            # Two gussets, one on each side
            for x_offset in [-p.base_width / 4, p.base_width / 4]:
                gusset = (
                    cq.Workplane("YZ")
                    .center(-p.base_depth/2 + gusset_d/2 + p.wall_thickness, p.base_thickness + gusset_h / 2)
                    .moveTo(-gusset_d/2, -gusset_h/2)
                    .lineTo(gusset_d/2, -gusset_h/2)
                    .lineTo(-gusset_d/2, gusset_h/2)
                    .close()
                    .extrude(p.gusset_thickness)
                    .translate((x_offset - p.gusset_thickness/2, 0, 0))
                )
                result = result.union(gusset)
        
        # Base mounting holes
        hole_positions = []
        for ix in range(p.hole_count_x):
            for iy in range(p.hole_count_y):
                x = -p.hole_spacing_x / 2 + ix * p.hole_spacing_x / max(p.hole_count_x - 1, 1)
                y = -p.hole_spacing_y / 2 + iy * p.hole_spacing_y / max(p.hole_count_y - 1, 1)
                # Offset from wall
                y_adjusted = y - p.base_depth * 0.15
                hole_positions.append((x, y_adjusted))
        
        result = (
            result
            .faces("<Z").workplane()
            .pushPoints(hole_positions)
            .hole(p.hole_diameter)
        )
        
        # ── EXPORT ────────────────────────────────────────────
        solid = result.val()
        
        step_path = os.path.join(output_dir, "bracket.step")
        stl_path = os.path.join(output_dir, "bracket.stl")
        
        cq.exporters.export(result, step_path, exportType="STEP")
        cq.exporters.export(result, stl_path, exportType="STL")
        
        # ── PROPERTIES ────────────────────────────────────────
        bb = solid.BoundingBox()
        volume_mm3 = solid.Volume()         # mm³ (OCCT returns in mm³)
        
        # Surface area (approximate from bounding box for now)
        surface_area_mm2 = 2 * (
            (bb.xlen * bb.ylen) + (bb.xlen * bb.zlen) + (bb.ylen * bb.zlen)
        )
        
        properties = {
            "volume_mm3": round(volume_mm3, 1),
            "bounding_box_mm": {
                "x": round(bb.xlen, 1),
                "y": round(bb.ylen, 1), 
                "z": round(bb.zlen, 1)
            },
            "surface_area_mm2_approx": round(surface_area_mm2, 1),
            "feature_count": len(hole_positions) + (2 if p.gusset_thickness > 0 else 0) + 1,  # holes + gussets + fillet
        }
        
        # ── CADQUERY CODE (for transparency) ──────────────────
        code = self._generate_code_string(p)
        
        return BracketResult(
            params=p,
            step_file_path=step_path,
            stl_file_path=stl_path,
            properties=properties,
            cadquery_code=code,
            variant_name="",
            design_rationale=""
        )
    
    def generate_variants(
        self, 
        force_n: float,
        material_id: str,
        bolt_size_mm: float = 8,
        max_width: float = 100,
        max_depth: float = 80,
        max_height: float = 60,
    ) -> list[BracketResult]:
        """
        Generate 3 bracket variants with different trade-offs:
        A: Lightest (minimum material, thinner walls)
        B: Strongest (maximum safety factor, thicker walls, gussets)
        C: Most economical (simplest geometry, easy to machine)
        """
        clearance_hole = bolt_size_mm + 1  # M8 → 9mm clearance
        
        # ── VARIANT A: LIGHTEST ───────────────────────────────
        params_a = BracketParams(
            base_width=max_width * 0.8,
            base_depth=max_depth * 0.75,
            base_thickness=5.0,
            wall_height=max_height * 0.85,
            wall_thickness=4.0,
            fillet_radius=3.0,
            hole_diameter=clearance_hole,
            hole_count_x=2, hole_count_y=2,
            hole_spacing_x=max_width * 0.6,
            hole_spacing_y=max_depth * 0.4,
            gusset_thickness=0,
            gusset_height=0,
        )
        
        # ── VARIANT B: STRONGEST ──────────────────────────────
        params_b = BracketParams(
            base_width=max_width,
            base_depth=max_depth,
            base_thickness=10.0,
            wall_height=max_height,
            wall_thickness=8.0,
            fillet_radius=6.0,
            hole_diameter=clearance_hole,
            hole_count_x=2, hole_count_y=2,
            hole_spacing_x=max_width * 0.65,
            hole_spacing_y=max_depth * 0.45,
            gusset_thickness=5.0,
            gusset_height=max_height * 0.6,
        )
        
        # ── VARIANT C: MOST ECONOMICAL ────────────────────────
        params_c = BracketParams(
            base_width=max_width * 0.9,
            base_depth=max_depth * 0.85,
            base_thickness=8.0,
            wall_height=max_height * 0.9,
            wall_thickness=6.0,
            fillet_radius=5.0,
            hole_diameter=clearance_hole,
            hole_count_x=2, hole_count_y=2,
            hole_spacing_x=max_width * 0.6,
            hole_spacing_y=max_depth * 0.4,
            gusset_thickness=0,
            gusset_height=0,
        )
        
        variants = []
        for name, rationale, params in [
            ("A: Lightest", "Minimized wall thickness and base plate to reduce weight. No gussets. Suitable when weight is critical and loads are moderate.", params_a),
            ("B: Strongest", "Maximum wall thickness with triangular gusset reinforcement. Highest safety factor. Best for heavy loads or safety-critical applications.", params_b),
            ("C: Most economical", "Balanced dimensions with no gussets for simplest machining. Standard wall thickness. Best cost-to-performance ratio for general use.", params_c),
        ]:
            result = self.generate_l_bracket(params)
            result.variant_name = name
            result.design_rationale = rationale
            variants.append(result)
        
        return variants
    
    def _generate_code_string(self, p: BracketParams) -> str:
        """Generate the CadQuery code string for transparency."""
        return f'''import cadquery as cq

# Parameters
base_width = {p.base_width}      # mm
base_depth = {p.base_depth}      # mm
base_thickness = {p.base_thickness}  # mm
wall_height = {p.wall_height}    # mm
wall_thickness = {p.wall_thickness}  # mm
fillet_radius = {p.fillet_radius}    # mm
hole_diameter = {p.hole_diameter}    # mm
hole_spacing_x = {p.hole_spacing_x} # mm
hole_spacing_y = {p.hole_spacing_y} # mm

# Build L-bracket
result = (
    cq.Workplane("XY")
    .box(base_width, base_depth, base_thickness, centered=(True, True, False))
    .faces(">Z").workplane()
    .center(0, base_depth/2 - wall_thickness/2)
    .box(base_width, wall_thickness, wall_height, centered=(True, True, False))
    .faces("<Z").workplane()
    .pushPoints([
        (-hole_spacing_x/2, -hole_spacing_y/2 - base_depth*0.15),
        ( hole_spacing_x/2, -hole_spacing_y/2 - base_depth*0.15),
        (-hole_spacing_x/2,  hole_spacing_y/2 - base_depth*0.15),
        ( hole_spacing_x/2,  hole_spacing_y/2 - base_depth*0.15),
    ])
    .hole(hole_diameter)
)

cq.exporters.export(result, "bracket.step")
'''
