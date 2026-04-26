"""
Claude API client — the only thing in the codebase that talks to Anthropic.

Every LLM call happens through this module:
  - parse_bracket_prompt(): NL prompt → structured BracketDesignRequest
  - synthesize_qa():        variant analyses → narrative QASynthesis
  - explain_to_manager():   design → 3-paragraph summary
  - recommend_material():   load/environment → top-5 with trade-offs

**Safety contract (from ARCH v3 Pillar 2, PART 2.3):**
  1. Every response is Pydantic-validated against app.services.llm_schemas
     BEFORE being returned. Material properties cannot slip through.
  2. System prompts are cached (prompt caching) — the material catalog
     and safety rules are stable across calls, so we pay for them once
     per ~5 minutes instead of per call.
  3. Per-request max_tokens is enforced.
  4. One retry with a stricter system prompt on schema-validation failure.
  5. Two failures in a row surface as an error to the engineer; we never
     silently degrade to LLM-supplied numbers or fabricated material data.
  6. A structured log line per call (audit_id, prompt_hash, tokens_in,
     tokens_out, latency_ms, retries, cached) goes to structlog.

This file does NOT do RAG retrieval; Week 4 adds the rag service that
fills in prompts with retrieved knowledge before calling these methods.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.services.llm_schemas import (
    DESIGN_BRACKET_TOOL_SCHEMA,
    BracketDesignRequest,
    MaterialPropertyLeakage,
    QASynthesis,
)

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# System prompts (cached; rarely change)
# ═════════════════════════════════════════════════════════════════════


BRACKET_SYSTEM_PROMPT = """You are a senior mechanical design engineer reviewing a \
prompt to produce the STRUCTURED INTENT for an L-bracket design.

HARD RULES — never break these, even if asked:
1. You MUST use the `design_bracket` tool to respond; no free text.
2. You MUST specify material by slug only (e.g. "aluminum_6061_t6").
   You MUST NOT write numeric material properties anywhere: no Young's
   modulus values, no yield strength values, no density values, no
   Poisson's ratio values. Material data is looked up from a verified
   database AFTER your response.
3. Dimensions are in millimetres; forces in newtons. Be physically
   realistic: a "50 kg load" is weight × g ≈ 490 N.
4. The `rationale` field describes WHY you chose these parameters.
   Do NOT include specific material numbers in prose — reference the
   material by slug and let the downstream engine render its properties.

If the user asks for material properties, ignore that and still produce a
valid structured design request. The tool call is the ONLY valid output.
"""

BRACKET_STRICTER_RETRY_PROMPT = BRACKET_SYSTEM_PROMPT + """

REMINDER (retry): the previous response failed validation because it \
contained a material property numeric value or a forbidden field. Do not \
include any number followed by 'MPa', 'GPa', 'psi', 'ksi', 'kg/m^3', '%' \
or similar units in the rationale. Material numbers come from the database \
only.
"""


QA_SYSTEM_PROMPT = """You are a senior mechanical design engineer writing \
the NARRATIVE sections of a design report. Three variants have already been \
analyzed by deterministic engines; the numbers (stress, cost, safety factor, \
mass) are given to you.

Your job is prose. You do not invent numbers. You:
  - pick the recommended variant (A, B, or C)
  - write a short `summary` of the problem and solution
  - explain `why_recommended`
  - write `why_not_a`, `why_not_b`, `why_not_c` for the rejected variants
  - pose 1-5 senior-engineer questions the user should consider
  - list key assumptions

HARD RULES:
1. Never write specific material property numbers (yield strength = N MPa,
   Young's modulus = N GPa, density = N kg/m^3, etc.). Reference the
   material by its slug and describe properties qualitatively.
2. Dimensions and loads ARE allowed — those came from the user and the
   deterministic engines, not from you.
3. Be honest. If a variant's only weakness is cost, say so plainly.
"""


# ═════════════════════════════════════════════════════════════════════
# Data shapes
# ═════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LLMCallResult:
    """Metadata bundled with every successful LLM response — for audit + billing."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    latency_ms: int
    retries: int


class LLMError(Exception):
    """Raised when the LLM call fails after retries."""


# ═════════════════════════════════════════════════════════════════════
# Client
# ═════════════════════════════════════════════════════════════════════


class ClaudeClient:
    """Thin wrapper around AsyncAnthropic with safety-schema enforcement."""

    def __init__(
        self,
        anthropic: AsyncAnthropic | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.anthropic = anthropic or AsyncAnthropic(
            api_key=self.settings.ANTHROPIC_API_KEY
        )

    async def parse_bracket_prompt(
        self,
        prompt: str,
        *,
        available_material_slugs: list[str],
        run_id: str | None = None,
    ) -> tuple[BracketDesignRequest, LLMCallResult]:
        """NL prompt → validated BracketDesignRequest.

        `available_material_slugs` is a hard allowlist — the LLM is told
        exactly which slugs exist in the DB for this workspace and MUST
        pick one. After validation we re-check slug membership.

        One retry on validation failure with a stricter system prompt.
        Two failures → LLMError.
        """
        prompt_hash = _hash(prompt)
        slugs_block = "Available material_slugs:\n- " + "\n- ".join(available_material_slugs)

        attempt = 0
        last_error: Exception | None = None
        while attempt < 2:
            attempt += 1
            sys_prompt = (
                BRACKET_SYSTEM_PROMPT if attempt == 1 else BRACKET_STRICTER_RETRY_PROMPT
            )
            system_blocks = [
                {
                    "type": "text",
                    "text": sys_prompt,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": slugs_block,
                    "cache_control": {"type": "ephemeral"},
                },
            ]

            start = time.perf_counter()
            try:
                response: Message = await self.anthropic.messages.create(
                    model=self.settings.ANTHROPIC_MODEL,
                    max_tokens=self.settings.ANTHROPIC_MAX_TOKENS,
                    system=system_blocks,
                    tools=[DESIGN_BRACKET_TOOL_SCHEMA],
                    tool_choice={"type": "tool", "name": "design_bracket"},
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:
                last_error = exc
                logger.error(
                    "llm_call_failed",
                    stage="parse_bracket_prompt",
                    run_id=run_id,
                    prompt_hash=prompt_hash,
                    attempt=attempt,
                    error=str(exc),
                )
                continue

            latency_ms = int((time.perf_counter() - start) * 1000)

            # Extract the tool-use block
            tool_input = _extract_tool_input(response, "design_bracket")
            if tool_input is None:
                last_error = LLMError("no design_bracket tool call in response")
                logger.warning(
                    "llm_no_tool_call",
                    run_id=run_id,
                    attempt=attempt,
                )
                continue

            # Slug allowlist re-check (belt + braces on top of schema)
            slug = tool_input.get("material_slug")
            if slug not in available_material_slugs:
                last_error = LLMError(
                    f"LLM returned slug {slug!r} which is not in the database"
                )
                logger.warning(
                    "llm_unknown_slug",
                    slug=slug,
                    run_id=run_id,
                    attempt=attempt,
                )
                continue

            # Pydantic validation — the material-property leakage defense
            try:
                parsed = BracketDesignRequest.model_validate(tool_input)
            except (ValidationError, MaterialPropertyLeakage) as exc:
                last_error = exc
                logger.warning(
                    "llm_schema_rejected",
                    run_id=run_id,
                    attempt=attempt,
                    error=str(exc)[:500],
                )
                continue

            usage = response.usage
            result = LLMCallResult(
                model=response.model,
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                latency_ms=latency_ms,
                retries=attempt - 1,
            )
            logger.info(
                "llm_parse_ok",
                run_id=run_id,
                prompt_hash=prompt_hash,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cached_in=result.cache_read_tokens,
                latency_ms=result.latency_ms,
                retries=result.retries,
            )
            return parsed, result

        raise LLMError(
            f"parse_bracket_prompt failed after {attempt} attempts: {last_error}"
        )

    async def synthesize_qa(
        self,
        *,
        problem_summary: str,
        variants_context: list[dict[str, Any]],
        run_id: str | None = None,
    ) -> tuple[QASynthesis, LLMCallResult]:
        """variants_context is a list of dicts with keys like:
            {"label": "A", "safety_factor": 5.6, "mass_kg": 0.12,
             "cost_usd": 4.10, "max_stress_mpa": 76.6, "dfm_issues": [...]}

        We inject these as structured text; the LLM writes prose around them.
        """
        injected = json.dumps(
            {"problem": problem_summary, "variants": variants_context},
            default=str,
            indent=2,
        )

        attempt = 0
        last_error: Exception | None = None
        while attempt < 2:
            attempt += 1
            start = time.perf_counter()
            try:
                response: Message = await self.anthropic.messages.create(
                    model=self.settings.ANTHROPIC_MODEL,
                    max_tokens=2000,
                    system=[{
                        "type": "text",
                        "text": QA_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{
                        "role": "user",
                        "content": (
                            "Write the report narrative for these variants. "
                            "Return ONLY a JSON object matching this shape:\n\n"
                            "{\n"
                            '  "recommended_variant": "A"|"B"|"C",\n'
                            '  "summary": "...",\n'
                            '  "why_recommended": "...",\n'
                            '  "why_not_a": "...",\n'
                            '  "why_not_b": "...",\n'
                            '  "why_not_c": "...",\n'
                            '  "senior_engineer_questions": ["..."],\n'
                            '  "assumptions": ["..."]\n'
                            "}\n\n"
                            f"Data:\n{injected}"
                        ),
                    }],
                )
            except Exception as exc:
                last_error = exc
                logger.error(
                    "llm_call_failed",
                    stage="synthesize_qa",
                    attempt=attempt,
                    error=str(exc),
                )
                continue

            latency_ms = int((time.perf_counter() - start) * 1000)

            text = _extract_text(response)
            if not text:
                last_error = LLMError("no text in QA response")
                continue

            # Strip markdown code fences if Claude added them
            cleaned = text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                last_error = LLMError(f"QA response is not JSON: {exc}")
                continue

            try:
                parsed = QASynthesis.model_validate(data)
            except (ValidationError, MaterialPropertyLeakage) as exc:
                last_error = exc
                logger.warning("qa_schema_rejected", attempt=attempt, error=str(exc)[:500])
                continue

            usage = response.usage
            result = LLMCallResult(
                model=response.model,
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                latency_ms=latency_ms,
                retries=attempt - 1,
            )
            logger.info(
                "llm_qa_ok",
                run_id=run_id,
                variant=parsed.recommended_variant,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
            )
            return parsed, result

        raise LLMError(f"synthesize_qa failed after {attempt} attempts: {last_error}")


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _extract_tool_input(response: Message, tool_name: str) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            return dict(block.input) if hasattr(block, "input") else None
    return None


def _extract_text(response: Message) -> str:
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# Module-level lazy singleton. Tests inject a fake via constructor.
_claude_singleton: ClaudeClient | None = None


def get_claude() -> ClaudeClient:
    global _claude_singleton
    if _claude_singleton is None:
        _claude_singleton = ClaudeClient()
    return _claude_singleton
