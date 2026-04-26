"""
Security + unit tests for LLM response schema validation.

The threat being defended against here is S5 from the forensic analysis:
an LLM hallucinates a wrong material property and an unsafe design ships.
Our defense is to make it schema-level impossible for the LLM to ever
return a material property. These tests prove the validator catches every
variant we can think of.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.llm_schemas import (
    BracketDesignRequest,
    BracketDimensions,
    LoadSpec,
    MaterialPropertyLeakage,
    QASynthesis,
    _scan_for_forbidden_fields,
    _scan_rationale_for_numeric_properties,
)


pytestmark = [pytest.mark.security]


# ─────────────────────────────────────────────────────────────────────
# Fixtures — a minimal valid request we can mutate in each test
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_request_dict() -> dict:
    return {
        "part_type": "bracket",
        "material_slug": "aluminum_6061_t6",
        "process": "cnc",
        "load": {
            "type": "static_point",
            "magnitude_n": 490.5,
            "direction": "down",
            "lever_arm_mm": 100.0,
        },
        "dimensions": {
            "base_width_mm": 80.0,
            "base_depth_mm": 60.0,
            "base_thickness_mm": 8.0,
            "wall_height_mm": 50.0,
            "wall_thickness_mm": 6.0,
            "fillet_radius_mm": 5.0,
            "hole_diameter_mm": 9.0,
            "hole_count_x": 2,
            "hole_count_y": 2,
            "hole_spacing_x_mm": 50.0,
            "hole_spacing_y_mm": 30.0,
        },
        "safety_factor_target": 2.5,
        "rationale": (
            "Using aluminum 6061-T6 for its good strength-to-weight ratio "
            "and excellent machinability. Sizing targets safety factor 2.5."
        ),
    }


def test_valid_request_parses_clean(valid_request_dict):
    """Baseline: a correctly-shaped response must validate."""
    req = BracketDesignRequest.model_validate(valid_request_dict)
    assert req.material_slug == "aluminum_6061_t6"
    assert req.safety_factor_target == 2.5


# ─────────────────────────────────────────────────────────────────────
# S5 DEFENSE — material property leakage
# ─────────────────────────────────────────────────────────────────────


def test_rejects_flat_young_modulus_field(valid_request_dict):
    """The LLM tries to add youngs_modulus_mpa at the top level."""
    bad = dict(valid_request_dict)
    bad["youngs_modulus_mpa"] = 68900
    with pytest.raises((MaterialPropertyLeakage, ValidationError)) as exc_info:
        BracketDesignRequest.model_validate(bad)
    assert "youngs_modulus_mpa" in str(exc_info.value).lower()


def test_rejects_nested_yield_strength(valid_request_dict):
    """LLM smuggles a property into a nested object."""
    bad = dict(valid_request_dict)
    bad["material"] = {"slug": "aluminum_6061_t6", "yield_strength_mpa": 276}
    with pytest.raises((MaterialPropertyLeakage, ValidationError)):
        BracketDesignRequest.model_validate(bad)


def test_rejects_density_field(valid_request_dict):
    bad = dict(valid_request_dict)
    bad["density_kg_m3"] = 2710
    with pytest.raises((MaterialPropertyLeakage, ValidationError)):
        BracketDesignRequest.model_validate(bad)


def test_rejects_variant_field_names():
    """The LLM's paraphrases of property names are also forbidden."""
    for bad_name in ("tensile_strength", "e_modulus", "youngs_modulus",
                     "yield_strength", "density"):
        with pytest.raises(MaterialPropertyLeakage):
            _scan_for_forbidden_fields({bad_name: 123})


def test_rejects_property_value_in_rationale_mpa(valid_request_dict):
    """LLM writes a specific material number in prose."""
    bad = dict(valid_request_dict)
    bad["rationale"] = "Using 6061-T6 which has a yield strength of 276 MPa for safety."
    with pytest.raises((MaterialPropertyLeakage, ValidationError)) as exc_info:
        BracketDesignRequest.model_validate(bad)
    assert "material property" in str(exc_info.value).lower()


def test_rejects_property_value_in_rationale_psi(valid_request_dict):
    """Same defense in imperial units — psi, ksi variants."""
    bad = dict(valid_request_dict)
    bad["rationale"] = "Material ultimate strength 40000 psi is enough for this load."
    with pytest.raises((MaterialPropertyLeakage, ValidationError)):
        BracketDesignRequest.model_validate(bad)


def test_rejects_density_number_in_rationale(valid_request_dict):
    bad = dict(valid_request_dict)
    bad["rationale"] = "6061-T6 aluminum with density 2710 kg/m^3 keeps mass low."
    with pytest.raises((MaterialPropertyLeakage, ValidationError)):
        BracketDesignRequest.model_validate(bad)


def test_accepts_rationale_that_names_property_without_values(valid_request_dict):
    """Engineers want to see 'yield strength' mentioned — just not with numbers.
    The UI re-injects the DB-sourced number beside the prose."""
    valid_request_dict["rationale"] = (
        "Chosen for good yield strength and machinability; matches the "
        "safety factor target under the specified load."
    )
    req = BracketDesignRequest.model_validate(valid_request_dict)
    assert req.rationale  # accepted


def test_accepts_prompt_style_rationale_without_units(valid_request_dict):
    """Narrative without specific numeric+unit combos passes."""
    valid_request_dict["rationale"] = (
        "Chose CNC 6061-T6 for its machinability and corrosion resistance. "
        "Wall thickness 6mm gives margin against the 50kg load."
    )
    req = BracketDesignRequest.model_validate(valid_request_dict)
    assert req.rationale


# ─────────────────────────────────────────────────────────────────────
# Standard Pydantic-level bounds checks (not the main S5 defense but
# important — defense in depth)
# ─────────────────────────────────────────────────────────────────────


def test_rejects_invalid_material_slug(valid_request_dict):
    valid_request_dict["material_slug"] = "6061-T6"  # starts with a digit, has dash
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


def test_rejects_slug_with_sql_injection_pattern(valid_request_dict):
    valid_request_dict["material_slug"] = "'; DROP TABLE materials; --"
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


def test_rejects_negative_load(valid_request_dict):
    valid_request_dict["load"]["magnitude_n"] = -10
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


def test_rejects_absurd_load(valid_request_dict):
    """1 million N is our absolute ceiling — heavier = probably a prompt bug."""
    valid_request_dict["load"]["magnitude_n"] = 10_000_000
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


def test_rejects_fillet_larger_than_thickness(valid_request_dict):
    """Fillet r >= thickness is geometrically impossible."""
    valid_request_dict["dimensions"]["fillet_radius_mm"] = 20.0
    valid_request_dict["dimensions"]["wall_thickness_mm"] = 6.0
    with pytest.raises(ValidationError) as exc_info:
        BracketDesignRequest.model_validate(valid_request_dict)
    assert "fillet" in str(exc_info.value).lower()


def test_rejects_extra_fields(valid_request_dict):
    """extra='forbid' at the model level — defends against schema drift."""
    valid_request_dict["secret_backdoor"] = "x"
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


def test_rejects_hole_spacing_exceeding_base(valid_request_dict):
    valid_request_dict["dimensions"]["hole_spacing_x_mm"] = 100.0
    valid_request_dict["dimensions"]["base_width_mm"] = 80.0
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


def test_rejects_safety_factor_below_1(valid_request_dict):
    valid_request_dict["safety_factor_target"] = 0.5
    with pytest.raises(ValidationError):
        BracketDesignRequest.model_validate(valid_request_dict)


# ─────────────────────────────────────────────────────────────────────
# QASynthesis — narrative-only model
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_qa_dict() -> dict:
    return {
        "recommended_variant": "B",
        "summary": (
            "Three variants considered. Variant B chosen for the best "
            "balance of weight, safety factor, and manufacturability."
        ),
        "why_recommended": (
            "B uses an 8mm base and 6mm wall, giving strong margin to "
            "the target safety factor while staying economical to CNC."
        ),
        "why_not_a": "Variant A is overbuilt — 11mm base adds weight for no safety gain.",
        "why_not_b": "Variant B is the recommendation.",
        "why_not_c": "Variant C is too thin for the expected cyclic load.",
        "senior_engineer_questions": [
            "Is the load actually static, or could it be cyclic in service?",
            "Any corrosion environment we should account for?",
        ],
        "assumptions": [
            "Static load applied at centre of tip",
            "Ambient operating temperature 20 °C",
        ],
    }


def test_qa_synthesis_accepts_narrative_without_numbers(valid_qa_dict):
    qa = QASynthesis.model_validate(valid_qa_dict)
    assert qa.recommended_variant == "B"


def test_qa_synthesis_rejects_property_number_in_summary(valid_qa_dict):
    valid_qa_dict["summary"] += " Yield strength is 276 MPa so we have margin."
    with pytest.raises(ValidationError):
        QASynthesis.model_validate(valid_qa_dict)


def test_qa_synthesis_rejects_property_number_in_question(valid_qa_dict):
    valid_qa_dict["senior_engineer_questions"].append(
        "Given Young's modulus of 68900 MPa, is deflection acceptable?"
    )
    with pytest.raises(ValidationError):
        QASynthesis.model_validate(valid_qa_dict)


def test_qa_synthesis_rejects_forbidden_top_level_field(valid_qa_dict):
    valid_qa_dict["youngs_modulus_mpa"] = 68900
    with pytest.raises((MaterialPropertyLeakage, ValidationError)):
        QASynthesis.model_validate(valid_qa_dict)


# ─────────────────────────────────────────────────────────────────────
# Scanner helpers — the low-level primitives
# ─────────────────────────────────────────────────────────────────────


def test_scan_rationale_catches_yield_strength_mpa():
    with pytest.raises(MaterialPropertyLeakage):
        _scan_rationale_for_numeric_properties("yield strength of 276 MPa")


def test_scan_rationale_catches_case_insensitive():
    with pytest.raises(MaterialPropertyLeakage):
        _scan_rationale_for_numeric_properties("YIELD STRENGTH = 500 ksi")


def test_scan_rationale_allows_pure_narrative():
    # No numbers — totally fine
    _scan_rationale_for_numeric_properties(
        "We selected a high-machinability alloy suitable for CNC production."
    )


def test_scan_rationale_allows_dimension_numbers():
    """Dimensions and loads ARE allowed in prose — those aren't material properties."""
    _scan_rationale_for_numeric_properties(
        "Base is 80 mm wide and carries 500 N at the tip."
    )


def test_scan_for_forbidden_fields_in_deep_nesting():
    """Recursive scan reaches arbitrary depth."""
    with pytest.raises(MaterialPropertyLeakage):
        _scan_for_forbidden_fields({
            "a": {"b": {"c": [{"d": {"density_kg_m3": 2710}}]}}
        })


def test_scan_for_forbidden_fields_case_insensitive():
    """Field keys are checked case-insensitively."""
    with pytest.raises(MaterialPropertyLeakage):
        _scan_for_forbidden_fields({"Youngs_Modulus_MPa": 68900})


def test_scan_for_forbidden_fields_clean_passes():
    _scan_for_forbidden_fields({
        "material_slug": "aluminum_6061_t6",
        "dimensions": {"base_width_mm": 80},
    })


# ─────────────────────────────────────────────────────────────────────
# Regex robustness — phrasings we must catch
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_phrase", [
    "Young's modulus of 68900 MPa",                 # ASCII apostrophe
    "Young\u2019s modulus of 68900 MPa",            # Unicode right-single-quote
    "Youngs modulus of 68900 MPa",                  # missing apostrophe
    "young's modulus = 68900 MPa",                  # lowercase
    "modulus of elasticity is 200 GPa",             # alternate phrasing
    "elastic modulus 200 GPa",                      # compact form
    "E-modulus of 68.9 GPa",                        # hyphen variant
    "yield strength of 276 MPa",
    "yield strength: 276 MPa",
    "ultimate tensile strength 310 MPa",
    "density of 2710 kg/m^3",
    "density = 2.71 g/cm^3",
    "density is 2.71 g/cc",
    "Poisson's ratio of 0.33%",                      # unitless + %
    "Poisson\u2019s ratio of 0.33%",
    "thermal conductivity 167 W/m-K",
    "CTE of 23.6 ppm/°C",
    "elongation of 12%",
    "hardness 95 HRB",
])
def test_regex_catches_varied_phrasings(bad_phrase):
    """Every one of these must raise. If any pass the LLM can still slip
    a material property into its prose."""
    from app.services.llm_schemas import _scan_rationale_for_numeric_properties

    with pytest.raises(MaterialPropertyLeakage):
        _scan_rationale_for_numeric_properties(bad_phrase)


@pytest.mark.parametrize("safe_phrase", [
    "The bracket base is 80mm wide.",
    "Loaded with 500 N at the tip.",
    "We target a safety factor of 2.5.",
    "Aluminum has excellent machinability.",
    "Choose 6061-T6 for corrosion resistance.",
    "Yield strength margin is comfortable.",             # property NAME only, no number
    "Young's modulus matters for stiffness.",            # no number → safe
    "Density contributes to the part's weight.",         # no number → safe
    "Hole diameter 9mm fits an M8 bolt.",
    "Process time is under 3 minutes at quantity 100.",
])
def test_regex_accepts_narrative_without_property_numbers(safe_phrase):
    """Dimensions, loads, and property NAMES without numeric values are fine."""
    from app.services.llm_schemas import _scan_rationale_for_numeric_properties

    _scan_rationale_for_numeric_properties(safe_phrase)   # must not raise
