"""
DesignPilot MECH — Material Database
All properties verified from engineering handbooks.
CRITICAL: AI agents must ONLY select materials by slug. Never generate numeric properties.

Sources:
  SHI  = Shigley's Mechanical Engineering Design, 11th ed.
  ASM  = ASM Metals Handbook Vol. 2
  MH   = Machinery's Handbook 31st ed.
  MW   = MatWeb (cross-checked against handbook values)
"""
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Material:
    name: str
    grade: str
    category: str  # aluminum | steel | stainless | titanium | brass | polymer | cast_iron

    # Mechanical (all SI)
    youngs_modulus_mpa: float       # E (MPa)
    yield_strength_mpa: float       # σ_y (MPa); 0 for brittle materials
    ultimate_strength_mpa: float    # σ_u (MPa)
    density_kg_m3: float            # ρ (kg/m³)
    poissons_ratio: float           # ν
    elongation_percent: float       # break elongation (%)

    # Thermal
    cte: float                      # µm/m/°C
    thermal_conductivity: float     # W/(m·K)
    max_service_temp_c: float       # °C (continuous)

    # Manufacturing
    machinability_rating: float     # 0–100 (AISI 1212 = 100)

    # Cost (approximate USD/kg, 2024 market)
    cost_per_kg_usd: float

    source: str

    def __str__(self) -> str:
        return f"{self.name} {self.grade}"


MATERIALS: Dict[str, Material] = {

    # ── ALUMINIUM ──────────────────────────────────────────────────────
    "aluminum_6061_t6": Material(
        name="Aluminium 6061", grade="T6", category="aluminum",
        youngs_modulus_mpa=68_900, yield_strength_mpa=276, ultimate_strength_mpa=310,
        density_kg_m3=2_700, poissons_ratio=0.33, elongation_percent=12.0,
        cte=23.6, thermal_conductivity=167, max_service_temp_c=150,
        machinability_rating=90, cost_per_kg_usd=4.50,
        source="SHI Table A-21; ASM Vol.2 p.102",
    ),
    "aluminum_6061_t4": Material(
        name="Aluminium 6061", grade="T4", category="aluminum",
        youngs_modulus_mpa=68_900, yield_strength_mpa=145, ultimate_strength_mpa=241,
        density_kg_m3=2_700, poissons_ratio=0.33, elongation_percent=22.0,
        cte=23.6, thermal_conductivity=154, max_service_temp_c=150,
        machinability_rating=85, cost_per_kg_usd=4.20,
        source="ASM Vol.2 p.102; MW Al 6061-T4",
    ),
    "aluminum_7075_t6": Material(
        name="Aluminium 7075", grade="T6", category="aluminum",
        youngs_modulus_mpa=71_700, yield_strength_mpa=503, ultimate_strength_mpa=572,
        density_kg_m3=2_810, poissons_ratio=0.33, elongation_percent=11.0,
        cte=23.4, thermal_conductivity=130, max_service_temp_c=120,
        machinability_rating=70, cost_per_kg_usd=8.00,
        source="SHI Table A-21; ASM Vol.2 p.116",
    ),
    "aluminum_5052_h32": Material(
        name="Aluminium 5052", grade="H32", category="aluminum",
        youngs_modulus_mpa=70_300, yield_strength_mpa=193, ultimate_strength_mpa=228,
        density_kg_m3=2_680, poissons_ratio=0.33, elongation_percent=12.0,
        cte=23.8, thermal_conductivity=138, max_service_temp_c=150,
        machinability_rating=65, cost_per_kg_usd=4.00,
        source="ASM Vol.2 p.96; MH 31st p.531",
    ),
    "aluminum_2024_t3": Material(
        name="Aluminium 2024", grade="T3", category="aluminum",
        youngs_modulus_mpa=73_100, yield_strength_mpa=345, ultimate_strength_mpa=483,
        density_kg_m3=2_780, poissons_ratio=0.33, elongation_percent=18.0,
        cte=23.2, thermal_conductivity=121, max_service_temp_c=125,
        machinability_rating=75, cost_per_kg_usd=6.50,
        source="SHI Table A-21; ASM Vol.2 p.88",
    ),
    "aluminum_6082_t6": Material(
        name="Aluminium 6082", grade="T6", category="aluminum",
        youngs_modulus_mpa=70_000, yield_strength_mpa=260, ultimate_strength_mpa=310,
        density_kg_m3=2_710, poissons_ratio=0.33, elongation_percent=10.0,
        cte=23.4, thermal_conductivity=170, max_service_temp_c=150,
        machinability_rating=88, cost_per_kg_usd=4.80,
        source="EN 573-3; MW Al 6082-T6",
    ),

    # ── CARBON STEEL ───────────────────────────────────────────────────
    "steel_a36": Material(
        name="Steel A36", grade="Hot-rolled structural", category="steel",
        youngs_modulus_mpa=200_000, yield_strength_mpa=250, ultimate_strength_mpa=400,
        density_kg_m3=7_850, poissons_ratio=0.26, elongation_percent=23.0,
        cte=11.7, thermal_conductivity=51, max_service_temp_c=260,
        machinability_rating=72, cost_per_kg_usd=0.90,
        source="ASTM A36; SHI Table A-20; AISC SCM",
    ),
    "steel_1018": Material(
        name="Steel 1018", grade="Cold-drawn", category="steel",
        youngs_modulus_mpa=205_000, yield_strength_mpa=370, ultimate_strength_mpa=440,
        density_kg_m3=7_870, poissons_ratio=0.29, elongation_percent=15.0,
        cte=11.7, thermal_conductivity=52, max_service_temp_c=300,
        machinability_rating=78, cost_per_kg_usd=1.20,
        source="SHI Table A-20; ASTM A108",
    ),
    "steel_1045": Material(
        name="Steel 1045", grade="Hot-rolled", category="steel",
        youngs_modulus_mpa=205_000, yield_strength_mpa=530, ultimate_strength_mpa=630,
        density_kg_m3=7_850, poissons_ratio=0.29, elongation_percent=12.0,
        cte=11.7, thermal_conductivity=50, max_service_temp_c=300,
        machinability_rating=65, cost_per_kg_usd=1.40,
        source="SHI Table A-20; ASTM A29",
    ),
    "steel_4140": Material(
        name="Steel 4140", grade="OQT 315°C", category="steel",
        youngs_modulus_mpa=205_000, yield_strength_mpa=655, ultimate_strength_mpa=1_020,
        density_kg_m3=7_850, poissons_ratio=0.29, elongation_percent=17.7,
        cte=12.3, thermal_conductivity=42, max_service_temp_c=400,
        machinability_rating=55, cost_per_kg_usd=2.00,
        source="SHI Table A-22 (4140 OQT 315); ASTM A322",
    ),
    "steel_4340": Material(
        name="Steel 4340", grade="OQT 315°C", category="steel",
        youngs_modulus_mpa=205_000, yield_strength_mpa=1_470, ultimate_strength_mpa=1_620,
        density_kg_m3=7_850, poissons_ratio=0.29, elongation_percent=10.0,
        cte=12.3, thermal_conductivity=38, max_service_temp_c=370,
        machinability_rating=45, cost_per_kg_usd=3.50,
        source="SHI Table A-22 (4340 OQT 315); ASTM A322",
    ),
    "steel_d2_tool": Material(
        name="Tool Steel D2", grade="Hardened 58–62 HRC", category="steel",
        youngs_modulus_mpa=210_000, yield_strength_mpa=1_600, ultimate_strength_mpa=2_000,
        density_kg_m3=7_700, poissons_ratio=0.28, elongation_percent=1.5,
        cte=10.7, thermal_conductivity=20, max_service_temp_c=250,
        machinability_rating=20, cost_per_kg_usd=18.00,
        source="ASM Vol.1 Tool Steels p.766; MH 31st p.502",
    ),

    # ── STAINLESS STEEL ────────────────────────────────────────────────
    "stainless_304": Material(
        name="Stainless 304", grade="Annealed", category="stainless",
        youngs_modulus_mpa=193_000, yield_strength_mpa=215, ultimate_strength_mpa=505,
        density_kg_m3=8_000, poissons_ratio=0.29, elongation_percent=40.0,
        cte=17.2, thermal_conductivity=16, max_service_temp_c=870,
        machinability_rating=45, cost_per_kg_usd=4.00,
        source="SHI Table A-20; ASTM A240; ASM Vol.2 p.197",
    ),
    "stainless_316": Material(
        name="Stainless 316", grade="Annealed", category="stainless",
        youngs_modulus_mpa=193_000, yield_strength_mpa=205, ultimate_strength_mpa=515,
        density_kg_m3=8_000, poissons_ratio=0.29, elongation_percent=40.0,
        cte=16.0, thermal_conductivity=14, max_service_temp_c=870,
        machinability_rating=40, cost_per_kg_usd=5.00,
        source="SHI Table A-20; ASTM A240; ASM Vol.2 p.202",
    ),
    "stainless_17_4ph": Material(
        name="Stainless 17-4PH", grade="H900", category="stainless",
        youngs_modulus_mpa=196_000, yield_strength_mpa=1_170, ultimate_strength_mpa=1_310,
        density_kg_m3=7_780, poissons_ratio=0.27, elongation_percent=10.0,
        cte=10.8, thermal_conductivity=18, max_service_temp_c=315,
        machinability_rating=35, cost_per_kg_usd=12.00,
        source="ASTM A693; ASM Vol.2 p.330; MW 17-4PH H900",
    ),
    "stainless_416": Material(
        name="Stainless 416", grade="Annealed (free-machining)", category="stainless",
        youngs_modulus_mpa=200_000, yield_strength_mpa=275, ultimate_strength_mpa=517,
        density_kg_m3=7_750, poissons_ratio=0.28, elongation_percent=30.0,
        cte=9.9, thermal_conductivity=25, max_service_temp_c=650,
        machinability_rating=85, cost_per_kg_usd=4.80,
        source="ASTM A582; ASM Vol.2 p.210",
    ),

    # ── TITANIUM ───────────────────────────────────────────────────────
    "titanium_grade5": Material(
        name="Titanium Ti-6Al-4V", grade="Grade 5 annealed", category="titanium",
        youngs_modulus_mpa=113_800, yield_strength_mpa=880, ultimate_strength_mpa=950,
        density_kg_m3=4_430, poissons_ratio=0.34, elongation_percent=14.0,
        cte=8.6, thermal_conductivity=6.7, max_service_temp_c=300,
        machinability_rating=25, cost_per_kg_usd=30.00,
        source="SHI Table A-21; ASTM B265; AMS 4928",
    ),
    "titanium_grade2": Material(
        name="Titanium Grade 2", grade="CP (commercially pure)", category="titanium",
        youngs_modulus_mpa=102_700, yield_strength_mpa=275, ultimate_strength_mpa=345,
        density_kg_m3=4_510, poissons_ratio=0.37, elongation_percent=20.0,
        cte=8.6, thermal_conductivity=16.4, max_service_temp_c=250,
        machinability_rating=30, cost_per_kg_usd=22.00,
        source="ASTM B265 Grade 2; ASM Vol.2 p.593",
    ),

    # ── BRASS / COPPER ─────────────────────────────────────────────────
    "brass_c360": Material(
        name="Brass C360", grade="Free-machining half-hard", category="brass",
        youngs_modulus_mpa=97_000, yield_strength_mpa=310, ultimate_strength_mpa=386,
        density_kg_m3=8_500, poissons_ratio=0.34, elongation_percent=25.0,
        cte=20.5, thermal_conductivity=115, max_service_temp_c=150,
        machinability_rating=100, cost_per_kg_usd=6.00,
        source="ASTM B16; ASM Vol.2 p.309; MH 31st p.546",
    ),
    "copper_c110": Material(
        name="Copper C110", grade="ETP", category="brass",
        youngs_modulus_mpa=117_000, yield_strength_mpa=69, ultimate_strength_mpa=220,
        density_kg_m3=8_940, poissons_ratio=0.34, elongation_percent=45.0,
        cte=17.0, thermal_conductivity=391, max_service_temp_c=200,
        machinability_rating=20, cost_per_kg_usd=8.50,
        source="ASTM B152; ASM Vol.2 p.265",
    ),

    # ── POLYMERS ───────────────────────────────────────────────────────
    "delrin_acetal": Material(
        name="Delrin Acetal (POM)", grade="Homopolymer", category="polymer",
        youngs_modulus_mpa=3_100, yield_strength_mpa=70, ultimate_strength_mpa=70,
        density_kg_m3=1_420, poissons_ratio=0.35, elongation_percent=40.0,
        cte=110.0, thermal_conductivity=0.31, max_service_temp_c=90,
        machinability_rating=95, cost_per_kg_usd=5.50,
        source="DuPont Delrin datasheet; ASM Eng. Materials p.163",
    ),
    "nylon_6_6": Material(
        name="Nylon 6/6 (PA66)", grade="Unfilled dry", category="polymer",
        youngs_modulus_mpa=2_800, yield_strength_mpa=83, ultimate_strength_mpa=83,
        density_kg_m3=1_140, poissons_ratio=0.39, elongation_percent=60.0,
        cte=80.0, thermal_conductivity=0.25, max_service_temp_c=105,
        machinability_rating=88, cost_per_kg_usd=4.00,
        source="ISO 527; BASF Ultramid A datasheet",
    ),
    "peek": Material(
        name="PEEK", grade="Unfilled", category="polymer",
        youngs_modulus_mpa=3_600, yield_strength_mpa=91, ultimate_strength_mpa=100,
        density_kg_m3=1_320, poissons_ratio=0.38, elongation_percent=50.0,
        cte=47.0, thermal_conductivity=0.25, max_service_temp_c=250,
        machinability_rating=70, cost_per_kg_usd=90.00,
        source="Victrex PEEK 450G datasheet; ISO 527",
    ),
    "ptfe": Material(
        name="PTFE (Teflon)", grade="Virgin unfilled", category="polymer",
        youngs_modulus_mpa=500, yield_strength_mpa=14, ultimate_strength_mpa=24,
        density_kg_m3=2_200, poissons_ratio=0.46, elongation_percent=300.0,
        cte=112.0, thermal_conductivity=0.25, max_service_temp_c=260,
        machinability_rating=80, cost_per_kg_usd=25.00,
        source="DuPont Teflon datasheet; ASM Eng. Materials p.167",
    ),

    # ── CAST IRON ──────────────────────────────────────────────────────
    "cast_iron_gray": Material(
        name="Cast Iron Gray", grade="ASTM A48 Class 30", category="cast_iron",
        youngs_modulus_mpa=103_000, yield_strength_mpa=0, ultimate_strength_mpa=214,
        density_kg_m3=7_200, poissons_ratio=0.26, elongation_percent=0.5,
        cte=11.0, thermal_conductivity=46, max_service_temp_c=300,
        machinability_rating=80, cost_per_kg_usd=0.70,
        source="SHI Table A-24; ASTM A48",
    ),
    "cast_iron_ductile": Material(
        name="Ductile Iron (SGI)", grade="ASTM A536 65-45-12", category="cast_iron",
        youngs_modulus_mpa=169_000, yield_strength_mpa=310, ultimate_strength_mpa=448,
        density_kg_m3=7_100, poissons_ratio=0.27, elongation_percent=12.0,
        cte=11.0, thermal_conductivity=36, max_service_temp_c=350,
        machinability_rating=65, cost_per_kg_usd=0.80,
        source="SHI Table A-24; ASTM A536",
    ),

    # ── HIGH-PERFORMANCE ───────────────────────────────────────────────
    "inconel_625": Material(
        name="Inconel 625", grade="Annealed", category="steel",
        youngs_modulus_mpa=207_000, yield_strength_mpa=414, ultimate_strength_mpa=827,
        density_kg_m3=8_440, poissons_ratio=0.31, elongation_percent=30.0,
        cte=12.8, thermal_conductivity=9.8, max_service_temp_c=980,
        machinability_rating=18, cost_per_kg_usd=45.00,
        source="ASTM B446; Special Metals Inconel 625 datasheet",
    ),
}

# Convenience alias for system prompts
MATERIAL_SLUGS = sorted(MATERIALS.keys())
