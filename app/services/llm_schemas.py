"""
LLM response schemas and validators.

**Threat (from FORENSIC-ANALYSIS-Complete.md, S5 — CRITICAL):**
    The LLM hallucinates wrong Young's modulus or yield strength, leading
    to an unsafe design.

**Defense:** Materials come from our database, NEVER from the LLM. The
LLM selects a material by slug (e.g. "aluminum_6061_t6"); it MUST NOT
return numeric mechanical properties. This module enforces that at the
schema layer using Pydantic validators.

Any LLM response we accept has been through:
    1. JSON tool-use schema validation (Claude produces structured output)
    2. This Pydantic validator (rejects material-property leakage)
    3. Field-level bounds checks (dimensions in physical ranges)
    4. Material slug resolution (slug must exist in the materials table)

If any of these fail we retry ONCE with a stricter system prompt. If it
fails again we surface an error to the engineer — we do not "fix up" the
response or fall back to LLM-supplied numbers.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


# ═════════════════════════════════════════════════════════════════════
# Forbidden property field names
# ═════════════════════════════════════════════════════════════════════


# Any top-level or nested field name matching one of these is rejected.
# These are exactly the fields stored in the `materials` table — if the
# LLM is emitting values for them, it has bypassed the slug contract.
FORBIDDEN_MATERIAL_FIELDS: frozenset[str] = frozenset({
    "youngs_modulus_mpa",
    "yield_strength_mpa",
    "ultimate_strength_mpa",
    "density_kg_m3",
    "poissons_ratio",
    "elongation_percent",
    "cte",
    "thermal_conductivity",
    "max_service_temp_c",
    "machinability_rating",
    "cost_per_kg_usd",
    # Common LLM variants we've seen during validation
    "youngs_modulus",
    "yield_strength",
    "ultimate_strength",
    "density",
    "e_modulus",
    "tensile_strength",
})


# Regex that catches numeric material property mentions in free-text
# rationale fields ("Aluminum 6061 has a yield strength of 276 MPa").
# We don't forbid the NAMES (engineers want to see "yield strength"
# discussed) — we forbid NUMERIC-VALUE-BEARING MENTIONS in the LLM-
# generated rationale. The UI re-displays the DB values itself.
#
# Phrase list is deliberately inclusive:
#   - "Young's modulus" (ASCII ' or Unicode ’), also "Youngs modulus"
#   - "modulus of elasticity", "modulus", "E modulus", "elastic modulus"
#   - "yield strength", "tensile strength", "ultimate strength"
#   - "density", "Poisson's ratio" / "Poissons ratio"
#   - "thermal conductivity", "elongation", "CTE", "hardness"
# Then allow up to ~60 characters of intervening text before a
# <number>(optional unit) token. Units include MPa/GPa/Pa/psi/ksi/kN
# but also unit-less properties (Poisson's ratio, elongation %).
_NUMERIC_PROPERTY_REGEX = re.compile(
    r"(?i)"
    # Property phrase alternation (the \b keeps us word-boundaried on the
    # left for phrases that DON'T start with "Young's" where the apostrophe
    # would otherwise poison the boundary).
    r"(?:"
    r"young[’'\u2019]?s?\s+modulus"                       # Young's modulus (any apostrophe / none)
    r"|modulus\s+of\s+elasticity"
    r"|elastic\s+modulus"
    r"|\bE[-_ ]?modulus\b"
    r"|yield\s+strength"
    r"|tensile\s+strength"
    r"|ultimate\s+(?:tensile\s+)?strength"
    r"|\bdensity\b"
    r"|poisson[’'\u2019]?s?\s+ratio"
    r"|thermal\s+conductivity"
    r"|coefficient\s+of\s+thermal\s+expansion"
    r"|\bCTE\b"
    r"|\belongation\b"
    r"|\bhardness\b"
    r")"
    # Up to 60 chars of noise (but not across a sentence boundary)
    r"[^.\n]{0,60}?"
    # A number followed (optionally separated) by a unit OR by '%' OR
    # by the specific unitless Poisson's-ratio pattern (0.xx).
    r"\d[\d.,]*\s*"
    r"(?:mpa|gpa|kpa|pa|psi|ksi|n/mm\^?2|"
    r"kg/m\^?3|g/cm\^?3|g/cc|"
    r"w/m\W?k|"
    r"ppm/°?c|µm/m/°?c|um/m/c|"
    r"%|"
    r"hb|hv|hrc|hra|hrb"
    r")"
    # End-of-unit boundary: either end-of-string, whitespace, or punctuation.
    # Cannot use \b because '%' is non-word.
    r"(?=$|[\s,.;:!?\)\]]|[^a-zA-Z0-9%])",
)


class MaterialPropertyLeakage(ValueError):
    """Raised when the LLM response contains material numeric properties
    outside of an acceptable slug reference."""


def _scan_for_forbidden_fields(obj, path: str = "") -> None:
    """Recursively check a (possibly nested) dict/list for forbidden keys.

    Raises MaterialPropertyLeakage on first offense, with the JSON path.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_MATERIAL_FIELDS:
                here = f"{path}.{key}" if path else key
                raise MaterialPropertyLeakage(
                    f"LLM response contains forbidden material property '{here}'. "
                    "Material data must come from the database, not the LLM."
                )
            _scan_for_forbidden_fields(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_for_forbidden_fields(item, f"{path}[{i}]")


def _scan_rationale_for_numeric_properties(text: str) -> None:
    """Reject narrative text that bakes in specific material numbers.

    We want engineers to see material properties (transparency), but those
    numbers must come from our DB, not from the LLM's prose. If the LLM
    writes "yield strength = 276 MPa" in free text, we reject and retry.
    """
    if not isinstance(text, str):
        return
    if _NUMERIC_PROPERTY_REGEX.search(text):
        match = _NUMERIC_PROPERTY_REGEX.search(text).group(0)
        raise MaterialPropertyLeakage(
            f"LLM rationale contains a hardcoded material property value "
            f"('{match}'). Remove numbers and reference the material by slug instead."
        )


# ═════════════════════════════════════════════════════════════════════
# Bracket design request — what the LLM hands back after parsing a prompt
# ═════════════════════════════════════════════════════════════════════


# Slugs like "aluminum_6061_t6" — lowercase ASCII, underscores, digits.
MaterialSlug = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]{2,99}$",
        min_length=3,
        max_length=100,
    ),
]


class BracketDimensions(BaseModel):
    """Physical dimensions for an L-bracket. Every field unit-bounded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_width_mm: float = Field(..., gt=10, lt=500)
    base_depth_mm: float = Field(..., gt=10, lt=500)
    base_thickness_mm: float = Field(..., gt=1, lt=50)
    wall_height_mm: float = Field(..., gt=10, lt=500)
    wall_thickness_mm: float = Field(..., gt=1, lt=50)
    fillet_radius_mm: float = Field(..., gt=0, lt=50)
    hole_diameter_mm: float = Field(..., gt=1, lt=40)
    hole_count_x: int = Field(..., ge=1, le=10)
    hole_count_y: int = Field(..., ge=1, le=10)
    hole_spacing_x_mm: float = Field(..., gt=5, lt=500)
    hole_spacing_y_mm: float = Field(..., gt=5, lt=500)

    @model_validator(mode="after")
    def _check_geometric_consistency(self) -> BracketDimensions:
        """Fillet must fit inside the smaller of wall and base thickness, etc."""
        if self.fillet_radius_mm >= min(self.wall_thickness_mm, self.base_thickness_mm):
            raise ValueError(
                "fillet_radius_mm must be smaller than both wall and base thicknesses"
            )
        if self.hole_spacing_x_mm >= self.base_width_mm:
            raise ValueError("hole_spacing_x_mm must fit within base_width_mm")
        if self.hole_spacing_y_mm >= self.base_depth_mm:
            raise ValueError("hole_spacing_y_mm must fit within base_depth_mm")
        return self


class LoadSpec(BaseModel):
    """The loading condition the engineer specified in their prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["static_point", "static_distributed", "cyclic_point"]
    magnitude_n: float = Field(..., gt=0, lt=1_000_000)
    direction: Literal["down", "up", "horizontal", "shear"] = "down"
    lever_arm_mm: float | None = Field(None, ge=0, lt=2000)


class BracketDesignRequest(BaseModel):
    """Structured intent parsed from a user prompt by Claude tool-use.

    The LLM fills this in; we validate. Material is by slug only — the
    LLM cannot smuggle in numeric properties anywhere in this object.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    part_type: Literal["bracket"] = "bracket"
    material_slug: MaterialSlug
    process: Literal["cnc", "sheet_metal", "casting", "fdm_3dprint"] = "cnc"
    load: LoadSpec
    dimensions: BracketDimensions
    safety_factor_target: float = Field(2.0, ge=1.0, le=10.0)
    rationale: str = Field(..., min_length=10, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def _rationale_no_property_numbers(cls, v: str) -> str:
        _scan_rationale_for_numeric_properties(v)
        return v

    @model_validator(mode="before")
    @classmethod
    def _no_forbidden_fields_anywhere(cls, data):
        """Recursive scan — catches the LLM if it sneaks a property into a
        nested dict, e.g. {..., "material": {"slug": "...", "density": 2710}}."""
        if isinstance(data, dict):
            _scan_for_forbidden_fields(data)
        return data


# ═════════════════════════════════════════════════════════════════════
# QA synthesis output — the narrative the LLM writes AFTER deterministic
# analyses run. Numbers come from our engines; the LLM only composes prose.
# ═════════════════════════════════════════════════════════════════════


class QASynthesis(BaseModel):
    """LLM-authored narrative sections of the final design report.

    Numbers (stress, cost, mass, safety factor) are INJECTED from the
    deterministic engines; the LLM never writes them. All the LLM writes
    is the prose wrapper around them. We validate there are no numeric
    property values hiding in the prose fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommended_variant: Literal["A", "B", "C"]
    summary: str = Field(..., min_length=20, max_length=1500)
    why_recommended: str = Field(..., min_length=20, max_length=1500)
    why_not_a: str = Field(..., min_length=10, max_length=800)
    why_not_b: str = Field(..., min_length=10, max_length=800)
    why_not_c: str = Field(..., min_length=10, max_length=800)
    senior_engineer_questions: list[str] = Field(..., min_length=1, max_length=5)
    assumptions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("summary", "why_recommended", "why_not_a", "why_not_b", "why_not_c")
    @classmethod
    def _prose_no_property_numbers(cls, v: str) -> str:
        _scan_rationale_for_numeric_properties(v)
        return v

    @field_validator("senior_engineer_questions", "assumptions")
    @classmethod
    def _list_no_property_numbers(cls, v: list[str]) -> list[str]:
        for item in v:
            _scan_rationale_for_numeric_properties(item)
        return v

    @model_validator(mode="before")
    @classmethod
    def _no_forbidden_fields_anywhere(cls, data):
        if isinstance(data, dict):
            _scan_for_forbidden_fields(data)
        return data


# ═════════════════════════════════════════════════════════════════════
# The tool-use schema we give Claude
# ═════════════════════════════════════════════════════════════════════


DESIGN_BRACKET_TOOL_SCHEMA: dict = {
    "name": "design_bracket",
    "description": (
        "Propose an L-bracket design from an engineer's natural-language "
        "prompt. You MUST return a material_slug (not properties). All "
        "dimensions are in millimetres. All forces are in newtons."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["material_slug", "process", "load", "dimensions",
                     "safety_factor_target", "rationale"],
        "properties": {
            "material_slug": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{2,99}$",
                "description": "A slug from the materials table, e.g. 'aluminum_6061_t6'.",
            },
            "process": {
                "type": "string",
                "enum": ["cnc", "sheet_metal", "casting", "fdm_3dprint"],
            },
            "load": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "magnitude_n"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["static_point", "static_distributed", "cyclic_point"],
                    },
                    "magnitude_n": {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000},
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "horizontal", "shear"],
                    },
                    "lever_arm_mm": {"type": "number", "minimum": 0, "maximum": 2000},
                },
            },
            "dimensions": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "base_width_mm", "base_depth_mm", "base_thickness_mm",
                    "wall_height_mm", "wall_thickness_mm", "fillet_radius_mm",
                    "hole_diameter_mm", "hole_count_x", "hole_count_y",
                    "hole_spacing_x_mm", "hole_spacing_y_mm",
                ],
                "properties": {
                    "base_width_mm": {"type": "number", "exclusiveMinimum": 10, "exclusiveMaximum": 500},
                    "base_depth_mm": {"type": "number", "exclusiveMinimum": 10, "exclusiveMaximum": 500},
                    "base_thickness_mm": {"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 50},
                    "wall_height_mm": {"type": "number", "exclusiveMinimum": 10, "exclusiveMaximum": 500},
                    "wall_thickness_mm": {"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 50},
                    "fillet_radius_mm": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 50},
                    "hole_diameter_mm": {"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 40},
                    "hole_count_x": {"type": "integer", "minimum": 1, "maximum": 10},
                    "hole_count_y": {"type": "integer", "minimum": 1, "maximum": 10},
                    "hole_spacing_x_mm": {"type": "number", "exclusiveMinimum": 5, "exclusiveMaximum": 500},
                    "hole_spacing_y_mm": {"type": "number", "exclusiveMinimum": 5, "exclusiveMaximum": 500},
                },
            },
            "safety_factor_target": {"type": "number", "minimum": 1.0, "maximum": 10.0},
            "rationale": {
                "type": "string",
                "minLength": 10,
                "maxLength": 2000,
                "description": (
                    "Why you chose these parameters. DO NOT include specific "
                    "material property values — reference the material by slug."
                ),
            },
        },
    },
}
