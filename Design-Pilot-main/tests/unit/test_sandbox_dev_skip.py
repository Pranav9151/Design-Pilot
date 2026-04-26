"""
Unit tests for sandbox dev-skip (SANDBOX_SKIP_FOR_DEV) and mock result.

These run without Docker, without the cadquery image, and without any
external services — pure in-process logic that can be verified anywhere.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.sandbox import (
    Sandbox,
    SandboxResult,
    _mock_sandbox_result,
)


# ─────────────────────────────────────────────────────────────────────
# _mock_sandbox_result
# ─────────────────────────────────────────────────────────────────────

SAMPLE_CODE = """\
import cadquery as cq

base_width = 80.0
base_depth = 60.0
base_thickness = 8.0
wall_height = 50.0
wall_thickness = 6.0
fillet_radius = 5.0
hole_diameter = 9.0
hole_count_x = 2
hole_count_y = 2
hole_spacing_x = 50.0
hole_spacing_y = 30.0
"""


def test_mock_returns_ok_true():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.ok is True


def test_mock_stage_is_success():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.stage == "success"


def test_mock_step_file_exists():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.step_path is not None
    assert result.step_path.exists()
    assert result.step_path.suffix == ".step"


def test_mock_step_file_is_valid_iso():
    result = _mock_sandbox_result(SAMPLE_CODE)
    content = result.step_path.read_text()  # type: ignore[union-attr]
    assert "ISO-10303-21" in content
    assert "END-ISO-10303-21" in content


def test_mock_volume_is_positive():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.metrics["volume_mm3"] > 0


def test_mock_volume_is_approximately_correct():
    """base plate + wall rectangle gives the expected volume."""
    result = _mock_sandbox_result(SAMPLE_CODE)
    # base: 80×60×8 = 38400; wall: 80×50×6 = 24000; total = 62400
    assert 20_000 < result.metrics["volume_mm3"] < 100_000


def test_mock_bbox_dimensions_are_positive():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.metrics["bbox_x_size"] > 0
    assert result.metrics["bbox_y_size"] > 0
    assert result.metrics["bbox_z_size"] > 0


def test_mock_has_dev_skip_warning():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert any("SANDBOX_SKIP_FOR_DEV" in w for w in result.warnings)


def test_mock_glb_path_is_none():
    """No 3D viewer data in dev-skip mode."""
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.glb_path is None


def test_mock_elapsed_is_very_small():
    result = _mock_sandbox_result(SAMPLE_CODE)
    assert result.elapsed_s is not None
    assert result.elapsed_s < 0.1


def test_mock_handles_code_without_dimensions():
    """Falls back to defaults when dimension vars are absent."""
    result = _mock_sandbox_result("import cadquery as cq\nresult = cq.Workplane().box(1,1,1)")
    assert result.ok is True
    assert result.metrics["volume_mm3"] > 0


def test_mock_run_id_accepted():
    """run_id kwarg is accepted and not required."""
    result = _mock_sandbox_result(SAMPLE_CODE, run_id="unit-test-001")
    assert result.ok is True


# ─────────────────────────────────────────────────────────────────────
# Sandbox.run() with SANDBOX_SKIP_FOR_DEV=True
# ─────────────────────────────────────────────────────────────────────

class _MockSettings:
    """Minimal settings stub for Sandbox unit tests."""
    SANDBOX_SKIP_FOR_DEV = True
    APP_ENV = "development"
    SANDBOX_IMAGE = "designpilot/cadquery-sandbox:latest"
    SANDBOX_TIMEOUT_SECONDS = 30
    SANDBOX_MEMORY_LIMIT_MB = 512
    SANDBOX_CPU_QUOTA = 200_000

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


class _ProdSettings(_MockSettings):
    APP_ENV = "production"
    SANDBOX_SKIP_FOR_DEV = True   # misconfigured — should be refused


def test_dev_skip_returns_ok_without_docker():
    """When SANDBOX_SKIP_FOR_DEV=True, Sandbox.run() returns ok=True
    even when Docker is unavailable."""
    sb = Sandbox(settings=_MockSettings())  # type: ignore[arg-type]
    result = sb.run(SAMPLE_CODE)
    assert result.ok is True
    assert result.stage == "success"


def test_dev_skip_blocked_in_production():
    """Safety guard: dev-skip is refused if APP_ENV=production."""
    sb = Sandbox(settings=_ProdSettings())  # type: ignore[arg-type]
    result = sb.run(SAMPLE_CODE)
    assert result.ok is False
    assert "forbidden" in result.error.lower()


def test_dev_skip_still_runs_ast_validation():
    """AST check still fires even in dev-skip mode."""
    sb = Sandbox(settings=_MockSettings())  # type: ignore[arg-type]
    malicious = "import os; os.system('rm -rf /')"
    result = sb.run(malicious)
    assert result.ok is False
    assert result.stage == "ast"


def test_dev_skip_ast_valid_code_succeeds():
    """Valid CadQuery code succeeds in dev-skip mode."""
    sb = Sandbox(settings=_MockSettings())  # type: ignore[arg-type]
    result = sb.run(SAMPLE_CODE)
    assert result.ok is True


def test_dev_skip_step_path_exists():
    sb = Sandbox(settings=_MockSettings())  # type: ignore[arg-type]
    result = sb.run(SAMPLE_CODE)
    assert result.step_path is not None
    assert result.step_path.exists()
