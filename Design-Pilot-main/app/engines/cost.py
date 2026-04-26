"""
DesignPilot MECH — Cost Estimation Engine
Parametric cost model based on geometry + material + process.
Shows RANGE (not point estimate). All assumptions visible.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class CostEstimate:
    unit_cost_usd: float
    material_cost: float
    machining_cost: float
    setup_cost_per_unit: float
    finishing_cost: float
    quantity: int
    cost_breakdown: Dict[str, float]
    assumptions: list
    
    @property
    def cost_range(self) -> tuple:
        """Return ±20% range for honest uncertainty."""
        return (round(self.unit_cost_usd * 0.8, 2), round(self.unit_cost_usd * 1.2, 2))


# Machine shop hourly rates (USD) — regional defaults
MACHINE_RATES = {
    "standard": 60,    # Basic 3-axis CNC
    "precision": 90,   # Tight tolerance work
    "5_axis": 150,     # 5-axis machining
}

# Material removal rates (mm³/min) — approximate for roughing
REMOVAL_RATES = {
    "aluminum": 8000,
    "steel": 3000,
    "stainless": 2000,
    "titanium": 800,
    "brass": 6000,
    "polymer": 10000,
}


class CostEngine:
    """Parametric cost estimation for CNC machined parts."""
    
    def estimate_cnc(
        self,
        part_volume_mm3: float,
        surface_area_mm2: float,
        feature_count: int,         # Holes, pockets, fillets
        material_density_kg_m3: float,
        material_cost_per_kg: float,
        material_category: str,
        quantity: int = 100,
        tolerance_grade: str = "standard",
    ) -> CostEstimate:
        """
        Estimate CNC machining cost using parametric model.
        """
        # ── MATERIAL COST ─────────────────────────────────────
        # Raw stock volume (bounding box × 1.2 overhead for stock sizing)
        raw_volume_mm3 = part_volume_mm3 * 1.8  # Assume 80% material removal typical for CNC
        raw_mass_kg = raw_volume_mm3 * 1e-9 * material_density_kg_m3
        material_cost = raw_mass_kg * material_cost_per_kg
        
        # ── MACHINING TIME ────────────────────────────────────
        removal_volume = raw_volume_mm3 - part_volume_mm3
        removal_rate = REMOVAL_RATES.get(material_category, 3000)
        
        # Base machining time
        roughing_time_min = removal_volume / removal_rate
        
        # Finishing passes (proportional to surface area)
        finishing_time_min = surface_area_mm2 / 5000  # ~5000 mm²/min finish rate
        
        # Feature time (each hole, pocket adds setup + machining)
        feature_time_min = feature_count * 1.5  # ~1.5 min per feature average
        
        # Complexity factor
        complexity_factor = 1.0 + (feature_count * 0.05)  # More features = more tool changes
        
        total_machining_time = (roughing_time_min + finishing_time_min + feature_time_min) * complexity_factor
        
        # Machine rate
        hourly_rate = MACHINE_RATES.get(tolerance_grade, 60)
        machining_cost = (total_machining_time / 60) * hourly_rate
        
        # ── SETUP COST (amortized over quantity) ──────────────
        setup_cost_total = 75.0  # Fixed setup per batch (fixture, tooling, first article)
        if feature_count > 10:
            setup_cost_total += 25  # Complex setup
        setup_per_unit = setup_cost_total / quantity
        
        # ── FINISHING ─────────────────────────────────────────
        # Deburring + basic cleaning
        finishing_cost = surface_area_mm2 * 0.000015  # ~$0.015 per 1000 mm²
        
        # ── TOTAL ─────────────────────────────────────────────
        unit_cost = material_cost + machining_cost + setup_per_unit + finishing_cost
        
        return CostEstimate(
            unit_cost_usd=round(unit_cost, 2),
            material_cost=round(material_cost, 2),
            machining_cost=round(machining_cost, 2),
            setup_cost_per_unit=round(setup_per_unit, 2),
            finishing_cost=round(finishing_cost, 2),
            quantity=quantity,
            cost_breakdown={
                "material": round(material_cost / unit_cost * 100, 1),
                "machining": round(machining_cost / unit_cost * 100, 1),
                "setup": round(setup_per_unit / unit_cost * 100, 1),
                "finishing": round(finishing_cost / unit_cost * 100, 1),
            },
            assumptions=[
                f"Machine rate: ${hourly_rate}/hr ({tolerance_grade} CNC)",
                f"Material removal rate: {removal_rate} mm³/min for {material_category}",
                f"Raw stock volume estimated at {1.8:.1f}× part volume",
                f"Setup cost: ${setup_cost_total:.0f} amortized over {quantity} units",
                f"Finishing: basic deburring and cleaning only",
                f"No special coatings, anodizing, or plating included",
                f"Prices are estimates ±20%. Get quotes for production.",
            ]
        )
    
    def quantity_sensitivity(
        self, base_estimate: CostEstimate, quantities: list = None
    ) -> Dict[int, float]:
        """Show how unit cost changes with quantity."""
        if quantities is None:
            quantities = [1, 10, 50, 100, 500, 1000, 5000]
        
        variable_cost = base_estimate.material_cost + base_estimate.machining_cost + base_estimate.finishing_cost
        total_setup = base_estimate.setup_cost_per_unit * base_estimate.quantity
        
        return {
            qty: round(variable_cost + total_setup / qty, 2)
            for qty in quantities
        }
