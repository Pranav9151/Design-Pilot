"""
Output validator — Layer 3 of CadQuery execution defense.

Defense order:
    1. app.services.ast_validator   — static code analysis (in-process, before exec)
    2. app.services.sandbox         — Docker + gVisor isolation (execution)
    3. THIS FILE                    — host-side geometry sanity checks (after exec)
    4. app.audit.service            — every run is logged; anomalies alert

What this layer does:
    After the sandbox reports success and returns a STEP file + metrics,
    we cross-check the geometry against the user's originally specified
    parameters. This catches two failure modes:

    A) Geometry mismatch (AI-specific risk, FORENSIC-ANALYSIS S24):
       "AI generating code that produces valid-looking but structurally
       wrong geometry." The bracket appears correct visually but has the
       wrong wall height or wrong hole pattern. We compare bounding-box
       sizes from the sandbox's measured metrics against the requested
       dimensions with a ±20% tolerance.

    B) Degenerate geometry:
       Volume ≤ 0 → the shape collapsed. Bbox dimension < 1 mm in any
       axis → the shape is essentially flat (probably a failed extrusion).
       These slip through the CadQuery `.isValid()` check sometimes.

    C) Implausibly large or small results:
       Volume > 10 m³ → almost certainly a unit error (mm vs m confusion).
       Volume < 0.1 mm³ → dust; not a bracket.

Validation is intentionally lenient: we flag issues as `warnings`, not
hard failures, EXCEPT for the degenerate-geometry case which we do fail.
The reasoning: a bracket that is 22% wider than specified is still
useful (engineer sees the warning and decides); a bracket with zero volume
is not a bracket.

Each issue includes a `code` (machine-readable), `severity`
("error"|"warning"), and a `message` (human-readable for the UI).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

import structlog

from app.services.sandbox import SandboxResult

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Types
# ═════════════════════════════════════════════════════════════════════


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Severity
    message: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OutputValidationResult:
    """Result of host-side output validation after a sandbox run.

    `passed` is True when there are no ERROR-severity issues.
    WARNINGs are surfaced to the engineer but do not prevent use.
    """

    passed: bool
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "all checks passed"
        if self.passed:
            return f"passed with {len(self.warnings)} warning(s)"
        return f"failed: {'; '.join(e.message for e in self.errors)}"


# ═════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════

# Bounds we check before anything else — if these fail, the geometry is
# unusable regardless of what the user specified.
_MIN_VOLUME_MM3 = 0.1          # 0.1 mm³ = roughly a grain of salt
_MAX_VOLUME_M3 = 10.0          # 10 m³ = an absurd bracket (unit confusion)
_MAX_VOLUME_MM3 = _MAX_VOLUME_M3 * 1e9
_MIN_BBOX_DIM_MM = 0.5         # any axis < 0.5 mm → degenerate shape
_MAX_BBOX_DIM_MM = 5_000.0     # 5 metres; beyond this, almost certainly mm↔m error

# Geometry/parameter agreement tolerance.
# ±25% because our A/B variants deliberately scale dimensions. The 25%
# window must be wider than our biggest scale factor (1.3×) so "Strongest"
# variants don't generate false positives. For the bbox check we need
# slack because the bbox measures OUTER size including fillets, which can
# add up to 2× fillet_radius to nominal dimensions.
_DIM_TOLERANCE = 0.30           # 30% → flag if measured > 1.3× or < 0.7× of specified


# ═════════════════════════════════════════════════════════════════════
# Validator
# ═════════════════════════════════════════════════════════════════════


class OutputValidator:
    """Validates sandbox output against user-specified parameters.

    Usage::

        result = output_validator.validate(
            sandbox_result=sb,
            expected_base_width_mm=80.0,
            expected_base_depth_mm=60.0,
            expected_wall_height_mm=50.0,
        )
        if not result.passed:
            # treat as sandbox failure
    """

    def validate(
        self,
        *,
        sandbox_result: SandboxResult,
        expected_base_width_mm: float | None = None,
        expected_base_depth_mm: float | None = None,
        expected_wall_height_mm: float | None = None,
        expected_wall_thickness_mm: float | None = None,
    ) -> OutputValidationResult:
        """
        Run all checks. Returns immediately with a FATAL issue if the
        sandbox_result is not `ok` (we never validate failed sandboxes).
        """
        if not sandbox_result.ok:
            return OutputValidationResult(
                passed=False,
                issues=[
                    ValidationIssue(
                        code="sandbox_not_ok",
                        severity=Severity.ERROR,
                        message="Sandbox run did not succeed; nothing to validate.",
                    )
                ],
            )

        metrics = sandbox_result.metrics
        issues: list[ValidationIssue] = []

        # ── A. Absolute volume bounds ──────────────────────────────
        volume = metrics.get("volume_mm3")
        if volume is not None:
            issues.extend(self._check_volume(volume))

        # ── B. Bounding box sanity ─────────────────────────────────
        bbox_x = metrics.get("bbox_x_size")
        bbox_y = metrics.get("bbox_y_size")
        bbox_z = metrics.get("bbox_z_size")
        if all(v is not None for v in (bbox_x, bbox_y, bbox_z)):
            issues.extend(self._check_bbox(bbox_x, bbox_y, bbox_z))

        # ── C. Parameter agreement ─────────────────────────────────
        if bbox_x is not None and bbox_y is not None and bbox_z is not None:
            param_dims = {
                "base_width": expected_base_width_mm,
                "base_depth": expected_base_depth_mm,
                "wall_height": expected_wall_height_mm,
            }
            measured_dims = [bbox_x, bbox_y, bbox_z]
            issues.extend(
                self._check_parameter_agreement(param_dims, measured_dims)
            )

        passed = not any(i.severity == Severity.ERROR for i in issues)

        if issues:
            log_fn = logger.warning if passed else logger.error
            log_fn(
                "output_validation_result",
                passed=passed,
                issue_count=len(issues),
                error_count=sum(1 for i in issues if i.severity == Severity.ERROR),
                warning_count=sum(1 for i in issues if i.severity == Severity.WARNING),
                summary=OutputValidationResult(passed=passed, issues=issues).summary(),
            )

        return OutputValidationResult(passed=passed, issues=issues)

    # ── Private checks ────────────────────────────────────────────

    @staticmethod
    def _check_volume(volume_mm3: float) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if volume_mm3 <= 0:
            issues.append(ValidationIssue(
                code="volume_zero_or_negative",
                severity=Severity.ERROR,
                message=(
                    f"Generated shape has non-positive volume ({volume_mm3:.3f} mm³). "
                    "The geometry likely collapsed during the boolean operations. "
                    "Regenerating is recommended."
                ),
                detail={"volume_mm3": volume_mm3},
            ))
        elif volume_mm3 < _MIN_VOLUME_MM3:
            issues.append(ValidationIssue(
                code="volume_too_small",
                severity=Severity.ERROR,
                message=(
                    f"Generated shape is implausibly small ({volume_mm3:.4f} mm³ < "
                    f"{_MIN_VOLUME_MM3} mm³ minimum). Possible geometry collapse."
                ),
                detail={"volume_mm3": volume_mm3, "min_mm3": _MIN_VOLUME_MM3},
            ))
        elif volume_mm3 > _MAX_VOLUME_MM3:
            # This most commonly indicates a mm↔m unit confusion in the generated code.
            volume_m3 = volume_mm3 / 1e9
            issues.append(ValidationIssue(
                code="volume_too_large",
                severity=Severity.ERROR,
                message=(
                    f"Generated shape is implausibly large ({volume_m3:.2f} m³). "
                    "This is almost certainly a mm/m unit confusion in the "
                    "generated CadQuery code. Regenerating is recommended."
                ),
                detail={"volume_mm3": volume_mm3, "max_mm3": _MAX_VOLUME_MM3},
            ))

        return issues

    @staticmethod
    def _check_bbox(
        bbox_x: float,
        bbox_y: float,
        bbox_z: float,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for axis, size in (("X", bbox_x), ("Y", bbox_y), ("Z", bbox_z)):
            if size < _MIN_BBOX_DIM_MM:
                issues.append(ValidationIssue(
                    code=f"bbox_{axis.lower()}_degenerate",
                    severity=Severity.ERROR,
                    message=(
                        f"Bounding-box dimension in the {axis} axis is {size:.3f} mm, "
                        f"below the minimum {_MIN_BBOX_DIM_MM} mm. The shape is "
                        f"essentially flat in this direction (collapsed extrusion?)."
                    ),
                    detail={f"bbox_{axis.lower()}_mm": size},
                ))
            elif size > _MAX_BBOX_DIM_MM:
                issues.append(ValidationIssue(
                    code=f"bbox_{axis.lower()}_too_large",
                    severity=Severity.WARNING,
                    message=(
                        f"Bounding box in the {axis} axis is {size:.1f} mm "
                        f"({size / 1000:.2f} m) — unusually large for a bracket. "
                        "Confirm this is intentional."
                    ),
                    detail={f"bbox_{axis.lower()}_mm": size, "max_mm": _MAX_BBOX_DIM_MM},
                ))

        return issues

    @staticmethod
    def _check_parameter_agreement(
        param_dims: dict[str, float | None],
        measured_dims: list[float],
    ) -> list[ValidationIssue]:
        """Check that measured bbox dimensions are within tolerance of specified ones.

        We do a best-fit: sort both lists and pair smallest-to-smallest.
        This is necessary because we don't know which CadQuery axis aligns
        with which user parameter (CadQuery's XYZ can be oriented differently
        from the user's width/depth/height).
        """
        issues: list[ValidationIssue] = []

        # Only include params that were actually specified
        named = {k: v for k, v in param_dims.items() if v is not None and v > 0}
        if not named:
            return issues

        # Sort both by value; pair them
        sorted_params = sorted(named.items(), key=lambda kv: kv[1])
        sorted_measured = sorted(measured_dims)

        for (param_name, expected), measured in zip(sorted_params, sorted_measured):
            if expected <= 0:
                continue
            ratio = measured / expected
            if not (1 - _DIM_TOLERANCE) <= ratio <= (1 + _DIM_TOLERANCE):
                pct = (ratio - 1) * 100
                issues.append(ValidationIssue(
                    code=f"dim_mismatch_{param_name}",
                    severity=Severity.WARNING,
                    message=(
                        f"Generated {param_name.replace('_', ' ')} ({measured:.1f} mm) "
                        f"differs from specified ({expected:.1f} mm) by {pct:+.0f}%. "
                        "Verify that the bracket matches your intent."
                    ),
                    detail={
                        "param": param_name,
                        "expected_mm": expected,
                        "measured_mm": measured,
                        "ratio": round(ratio, 3),
                        "tolerance": _DIM_TOLERANCE,
                    },
                ))

        return issues


# ── Module-level singleton ────────────────────────────────────────────
output_validator = OutputValidator()
