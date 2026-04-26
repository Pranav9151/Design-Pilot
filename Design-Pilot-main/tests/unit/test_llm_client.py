"""
Unit tests for the Claude client safety contract.

We do NOT call the real Anthropic API here. We inject a fake client
that returns whatever tool-use payload or text we want, and assert that
the safety contract holds:

  1. Valid tool-use → validated BracketDesignRequest
  2. Response with forbidden material property → rejected, retry fires
  3. Two consecutive rejections → LLMError
  4. LLM returns an unknown material slug → rejected (allowlist defense)
  5. No tool-use block at all → rejected
  6. QA synthesis: same contract applied to the narrative schema
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.llm_client import ClaudeClient, LLMError
from app.services.llm_schemas import BracketDesignRequest, QASynthesis


# ─────────────────────────────────────────────────────────────────────
# Fake Anthropic helpers
# ─────────────────────────────────────────────────────────────────────


def _tool_use_block(name: str, payload: dict):
    """Build a fake 'tool_use' content block shaped like the real Anthropic SDK."""
    return SimpleNamespace(type="tool_use", name=name, input=payload)


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _fake_message(content_blocks, model: str = "claude-sonnet-4-20250514"):
    """Shape matches Anthropic's Message type well enough for our client."""
    return SimpleNamespace(
        content=content_blocks,
        model=model,
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=380,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=0,
        ),
    )


def _valid_bracket_payload(material_slug: str = "aluminum_6061_t6") -> dict:
    return {
        "part_type": "bracket",
        "material_slug": material_slug,
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
            "Chose 6061-T6 for good machinability and strength-to-weight ratio. "
            "Dimensions sized to comfortably exceed the safety factor target."
        ),
    }


def _valid_qa_payload() -> dict:
    return {
        "recommended_variant": "B",
        "summary": (
            "Three variants considered. Variant B chosen for a balanced compromise "
            "between safety factor and manufacturability."
        ),
        "why_recommended": (
            "Variant B offers a comfortable safety factor with minimum weight and "
            "a shape that's easy to CNC in one setup."
        ),
        "why_not_a": "Variant A is overbuilt; added mass doesn't buy meaningful safety margin.",
        "why_not_b": "Variant B is the recommendation.",
        "why_not_c": "Variant C is too thin; marginal for the cyclic load case.",
        "senior_engineer_questions": [
            "Is the mounting surface truly flat, or should we account for tolerance?",
            "Any corrosive environment exposure?",
        ],
        "assumptions": [
            "Static load at the centre of the lever arm",
            "Room-temperature operation (20 C)",
        ],
    }


@pytest.fixture
def client_with_fake_anthropic():
    """ClaudeClient whose AsyncAnthropic is a mock with a .messages.create() coroutine."""

    def build(messages_create_behavior) -> ClaudeClient:
        fake_anthropic = SimpleNamespace(
            messages=SimpleNamespace(create=messages_create_behavior)
        )
        return ClaudeClient(anthropic=fake_anthropic)

    return build


AVAILABLE_SLUGS = [
    "aluminum_6061_t6", "aluminum_7075_t6", "steel_1018",
    "steel_4140", "stainless_304", "titanium_grade5",
]


# ─────────────────────────────────────────────────────────────────────
# parse_bracket_prompt happy path
# ─────────────────────────────────────────────────────────────────────


async def test_parse_bracket_prompt_happy_path(client_with_fake_anthropic):
    async def create(**kwargs):
        return _fake_message([_tool_use_block("design_bracket", _valid_bracket_payload())])

    client = client_with_fake_anthropic(create)
    request, meta = await client.parse_bracket_prompt(
        prompt="L-bracket for 50kg load in aluminum",
        available_material_slugs=AVAILABLE_SLUGS,
    )
    assert isinstance(request, BracketDesignRequest)
    assert request.material_slug == "aluminum_6061_t6"
    assert meta.retries == 0
    assert meta.input_tokens == 120
    assert meta.cache_read_tokens == 80


# ─────────────────────────────────────────────────────────────────────
# Material-property leakage defense
# ─────────────────────────────────────────────────────────────────────


async def test_parse_retries_when_response_leaks_property_then_succeeds(
    client_with_fake_anthropic,
):
    """First response has yield_strength_mpa (leakage). Second is clean.
    Client must retry and succeed with retries=1."""
    call_count = {"n": 0}

    async def create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            payload = _valid_bracket_payload()
            payload["yield_strength_mpa"] = 276   # LEAK — must be caught
            return _fake_message([_tool_use_block("design_bracket", payload)])
        return _fake_message([_tool_use_block("design_bracket", _valid_bracket_payload())])

    client = client_with_fake_anthropic(create)
    request, meta = await client.parse_bracket_prompt(
        prompt="L-bracket",
        available_material_slugs=AVAILABLE_SLUGS,
    )
    assert isinstance(request, BracketDesignRequest)
    assert meta.retries == 1
    assert call_count["n"] == 2


async def test_parse_fails_after_two_leakage_attempts(client_with_fake_anthropic):
    async def create(**kwargs):
        bad = _valid_bracket_payload()
        bad["density_kg_m3"] = 2710
        return _fake_message([_tool_use_block("design_bracket", bad)])

    client = client_with_fake_anthropic(create)
    with pytest.raises(LLMError) as exc_info:
        await client.parse_bracket_prompt(
            prompt="L-bracket",
            available_material_slugs=AVAILABLE_SLUGS,
        )
    assert "2 attempts" in str(exc_info.value)


async def test_parse_rejects_numeric_property_in_rationale_then_succeeds(
    client_with_fake_anthropic,
):
    call_count = {"n": 0}

    async def create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            bad = _valid_bracket_payload()
            bad["rationale"] = "6061-T6 with yield strength of 276 MPa provides margin."
            return _fake_message([_tool_use_block("design_bracket", bad)])
        return _fake_message([_tool_use_block("design_bracket", _valid_bracket_payload())])

    client = client_with_fake_anthropic(create)
    request, meta = await client.parse_bracket_prompt(
        prompt="L-bracket",
        available_material_slugs=AVAILABLE_SLUGS,
    )
    assert meta.retries == 1
    assert isinstance(request, BracketDesignRequest)


# ─────────────────────────────────────────────────────────────────────
# Slug allowlist defense (belt and braces on top of schema)
# ─────────────────────────────────────────────────────────────────────


async def test_parse_rejects_unknown_slug_immediately(client_with_fake_anthropic):
    async def create(**kwargs):
        bad = _valid_bracket_payload(material_slug="unobtainium_grade_x")
        return _fake_message([_tool_use_block("design_bracket", bad)])

    client = client_with_fake_anthropic(create)
    with pytest.raises(LLMError) as exc_info:
        await client.parse_bracket_prompt(
            prompt="L-bracket",
            available_material_slugs=AVAILABLE_SLUGS,
        )
    # Either the allowlist check or its retry will mention the slug
    assert "unobtainium" in str(exc_info.value) or "failed" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────
# Missing tool-use block
# ─────────────────────────────────────────────────────────────────────


async def test_parse_rejects_response_without_tool_call(client_with_fake_anthropic):
    """Claude returns text only — no structured tool call."""
    async def create(**kwargs):
        return _fake_message([_text_block("Here's a nice bracket design!")])

    client = client_with_fake_anthropic(create)
    with pytest.raises(LLMError):
        await client.parse_bracket_prompt(
            prompt="L-bracket",
            available_material_slugs=AVAILABLE_SLUGS,
        )


# ─────────────────────────────────────────────────────────────────────
# API-level failure handling
# ─────────────────────────────────────────────────────────────────────


async def test_parse_retries_on_api_exception(client_with_fake_anthropic):
    call_count = {"n": 0}

    async def create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("anthropic 500")
        return _fake_message([_tool_use_block("design_bracket", _valid_bracket_payload())])

    client = client_with_fake_anthropic(create)
    request, meta = await client.parse_bracket_prompt(
        prompt="L-bracket",
        available_material_slugs=AVAILABLE_SLUGS,
    )
    assert call_count["n"] == 2
    assert meta.retries == 1


async def test_parse_bubbles_persistent_api_error(client_with_fake_anthropic):
    async def create(**kwargs):
        raise RuntimeError("anthropic is down")

    client = client_with_fake_anthropic(create)
    with pytest.raises(LLMError):
        await client.parse_bracket_prompt(
            prompt="L-bracket",
            available_material_slugs=AVAILABLE_SLUGS,
        )


# ─────────────────────────────────────────────────────────────────────
# QA synthesis
# ─────────────────────────────────────────────────────────────────────


async def test_synthesize_qa_happy_path(client_with_fake_anthropic):
    async def create(**kwargs):
        return _fake_message([_text_block(json.dumps(_valid_qa_payload()))])

    client = client_with_fake_anthropic(create)
    qa, meta = await client.synthesize_qa(
        problem_summary="L-bracket for 50kg load",
        variants_context=[
            {"label": "A", "safety_factor": 7.2, "mass_kg": 0.22},
            {"label": "B", "safety_factor": 3.6, "mass_kg": 0.14},
            {"label": "C", "safety_factor": 2.1, "mass_kg": 0.09},
        ],
    )
    assert isinstance(qa, QASynthesis)
    assert qa.recommended_variant == "B"
    assert meta.retries == 0


async def test_synthesize_qa_strips_markdown_fences(client_with_fake_anthropic):
    """Claude sometimes wraps JSON in ```json ... ``` — we must cope."""
    async def create(**kwargs):
        fenced = "```json\n" + json.dumps(_valid_qa_payload()) + "\n```"
        return _fake_message([_text_block(fenced)])

    client = client_with_fake_anthropic(create)
    qa, _ = await client.synthesize_qa(
        problem_summary="p", variants_context=[{}],
    )
    assert qa.recommended_variant == "B"


async def test_synthesize_qa_rejects_property_in_summary(client_with_fake_anthropic):
    """QA narrative that bakes in a yield strength value must be rejected."""
    bad = _valid_qa_payload()
    bad["summary"] += " The yield strength of 276 MPa gives us margin."

    async def create(**kwargs):
        return _fake_message([_text_block(json.dumps(bad))])

    client = client_with_fake_anthropic(create)
    with pytest.raises(LLMError):
        await client.synthesize_qa(problem_summary="p", variants_context=[{}])


async def test_synthesize_qa_rejects_non_json_text(client_with_fake_anthropic):
    async def create(**kwargs):
        return _fake_message([_text_block("Sorry, I cannot respond in JSON right now.")])

    client = client_with_fake_anthropic(create)
    with pytest.raises(LLMError):
        await client.synthesize_qa(problem_summary="p", variants_context=[{}])
