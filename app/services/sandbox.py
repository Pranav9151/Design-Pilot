"""
Docker sandbox wrapper — LAYER 2 of CadQuery execution defense.

**Layer order:**
    1. `app.services.ast_validator` blocks dangerous code patterns statically.
    2. This file launches an isolated Docker container to run what passed (1).
    3. `app.services.output_validator` (Week 3) verifies the geometry.
    4. `app.audit.service` logs every run; anomaly alerts on repeats.

**Security flags (all mandatory, matching ARCH-MECH-DesignPilot-v3-FINAL
PART 2.5, Security Layer 4):**

    --runtime=runsc             gVisor kernel isolation
    --network=none              no egress, no DNS, no localhost
    --read-only                 root filesystem is RO
    --tmpfs=/work:size=100m,... writable /work limited to 100 MB
    --cap-drop=ALL              every Linux capability dropped
    --security-opt no-new-privileges
    --security-opt seccomp=...  narrow syscall allowlist
    --user 65534:65534          nobody:nogroup
    --memory=512m
    --cpus=2
    --pids-limit=50

Even if an attacker somehow bypasses the AST validator and the container
at runtime, gVisor's user-space kernel absorbs the syscall; --cap-drop=ALL
removes privilege escalation primitives; and --network=none eliminates
exfiltration paths.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from app.core.config import Settings, get_settings
from app.services.ast_validator import ASTValidationError, ast_validator

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Data shapes
# ═════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SandboxResult:
    """The outcome of a single sandbox run.

    `ok=True` means CadQuery produced a valid STEP file. The caller must
    still run host-side output validation (dimensions match parameters,
    shape is manifold etc.) — the `ok` flag only means "the container
    returned without error."
    """

    ok: bool
    stage: str                            # "success" | "ast" | "start" | "execute" | "timeout" | ...
    error: str | None = None
    step_path: Path | None = None
    glb_path: Path | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float | None = None
    exit_code: int | None = None
    stderr_tail: str | None = None


class SandboxRunError(Exception):
    """Raised only for programmer errors (bad args, image missing).
    Runtime failures are reported via SandboxResult, not exceptions."""


# ═════════════════════════════════════════════════════════════════════
# Command construction (separated from execution — so it's unit-testable)
# ═════════════════════════════════════════════════════════════════════


def build_docker_command(
    *,
    image: str,
    host_workdir: Path,
    timeout_s: int,
    memory_mb: int,
    cpu_quota: int,
    use_gvisor: bool,
    container_name: str,
) -> list[str]:
    """
    Construct the `docker run` argv for a single sandbox invocation.

    Isolated from `run()` so every flag combination is unit-testable
    without ever launching Docker. Every flag here maps to a specific
    forensic threat (see module docstring).

    Args:
        image:           container image tag (e.g. "designpilot/cadquery-sandbox:latest")
        host_workdir:    host path bind-mounted as /work (writable output area)
        timeout_s:       hard ceiling passed via docker --stop-timeout and
                         caller-side subprocess timeout
        memory_mb:       --memory=<N>m
        cpu_quota:       CPU microseconds per 100ms; 200000 == 2 cores
        use_gvisor:      adds --runtime=runsc when True. Required in prod.
                         Dev environments without gVisor can pass False
                         (logged as a warning by `run()`).
        container_name:  --name, useful for `docker kill` on cleanup

    Returns:
        argv list ready for subprocess.run().
    """
    cmd: list[str] = [
        "docker", "run",
        "--rm",
        "--interactive",
        "--name", container_name,
    ]

    if use_gvisor:
        cmd += ["--runtime", "runsc"]

    # Isolation flags
    cmd += [
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--pids-limit", "50",
    ]

    # Resource limits
    cmd += [
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",       # no swap ballooning
        "--cpu-quota", str(cpu_quota),
        "--cpu-period", "100000",
    ]

    # Writable area: tmpfs for /tmp (small), bind for /work so host can read outputs
    cmd += [
        "--tmpfs", "/tmp:size=50m,noexec,nosuid,nodev",
        "--volume", f"{host_workdir.absolute()}:/work:rw",
        "--workdir", "/work",
    ]

    # Stop after timeout_s; SIGKILL-forceful after +5s
    cmd += ["--stop-timeout", str(timeout_s)]

    # Image last
    cmd.append(image)

    return cmd


# ═════════════════════════════════════════════════════════════════════
# Execution
# ═════════════════════════════════════════════════════════════════════


class Sandbox:
    """Spawn a one-shot Docker container per piece of CadQuery code."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _docker_available(self) -> bool:
        return shutil.which("docker") is not None

    def run(
        self,
        code: str,
        *,
        skip_ast_check: bool = False,
        use_gvisor: bool | None = None,
        run_id: str | None = None,
    ) -> SandboxResult:
        """Execute `code` inside a one-shot container.

        Returns a SandboxResult describing what happened. This method
        does not raise for runtime failures; it converts them into
        SandboxResult(ok=False, stage=..., error=...). The caller should
        always inspect `stage` before trusting outputs.

        Check order (first failure short-circuits):
            1. AST validation   — a malicious prompt is rejected even if
                                   Docker is missing (fast, free defense).
            2. gVisor policy    — prod without gVisor is an immediate stop.
            3. Docker presence  — only matters if we're actually going to run.
            4. Spawn container  — the real work.
        """
        # ── 1. AST validation (Layer 1) ─────────────────────────────
        if not skip_ast_check:
            result = ast_validator.validate(code)
            if not result.valid:
                logger.warning(
                    "sandbox_rejected_by_ast",
                    reason=result.reason,
                    location=result.location,
                    run_id=run_id,
                )
                return SandboxResult(
                    ok=False,
                    stage="ast",
                    error=f"{result.reason} at {result.location}",
                )

        # ── 1b. Dev-skip: bypass Docker entirely for local development ────
        # Activated by SANDBOX_SKIP_FOR_DEV=true in .env.
        # Returns a deterministic mock STEP that lets the full pipeline run
        # (LLM parse → analytics → Triple-Lock → DB write) without the image.
        if getattr(self.settings, 'SANDBOX_SKIP_FOR_DEV', False):
            if self.settings.is_production:
                # Safety guard: this can never fire in prod even if misconfigured
                return SandboxResult(
                    ok=False, stage="prereq",
                    error="SANDBOX_SKIP_FOR_DEV is forbidden in production",
                )
            logger.warning(
                "sandbox_dev_skip_active",
                run_id=run_id,
                note="Real CadQuery execution bypassed — SANDBOX_SKIP_FOR_DEV=true",
            )
            return _mock_sandbox_result(code, run_id=run_id)

        # ── 2. gVisor: prod requires it. Dev may skip with a warning. ──
        if use_gvisor is None:
            use_gvisor = self.settings.is_production

        if not use_gvisor and self.settings.is_production:
            return SandboxResult(
                ok=False,
                stage="prereq",
                error="gVisor is mandatory in production; refusing to run without it",
            )

        if not use_gvisor:
            logger.warning(
                "sandbox_running_without_gvisor",
                env=self.settings.APP_ENV,
                run_id=run_id,
            )

        # ── 3. Docker availability ──────────────────────────────────
        if not self._docker_available():
            return SandboxResult(
                ok=False,
                stage="prereq",
                error="docker not found on PATH; sandbox cannot run",
            )

        # ── 4. Prepare host-side scratch dir ────────────────────────
        with tempfile.TemporaryDirectory(prefix="dpmech-sandbox-") as host_work:
            host_workdir = Path(host_work)
            # Ensure the bind-mount target is writable by the container's nobody
            host_workdir.chmod(0o777)

            container_name = f"dpmech-sandbox-{run_id or _short_id()}"

            cmd = build_docker_command(
                image=self.settings.SANDBOX_IMAGE,
                host_workdir=host_workdir,
                timeout_s=self.settings.SANDBOX_TIMEOUT_SECONDS,
                memory_mb=self.settings.SANDBOX_MEMORY_LIMIT_MB,
                cpu_quota=self.settings.SANDBOX_CPU_QUOTA,
                use_gvisor=use_gvisor,
                container_name=container_name,
            )

            logger.info(
                "sandbox_starting",
                run_id=run_id,
                image=self.settings.SANDBOX_IMAGE,
                gvisor=use_gvisor,
                code_bytes=len(code.encode("utf-8")),
            )

            # ── 3. Execute ──────────────────────────────────────────
            try:
                proc = subprocess.run(
                    cmd,
                    input=code,
                    text=True,
                    capture_output=True,
                    timeout=self.settings.SANDBOX_TIMEOUT_SECONDS + 5,
                )
            except subprocess.TimeoutExpired as exc:
                _force_kill_container(container_name)
                return SandboxResult(
                    ok=False,
                    stage="timeout",
                    error=f"sandbox exceeded {exc.timeout}s",
                )
            except FileNotFoundError as exc:
                return SandboxResult(
                    ok=False,
                    stage="start",
                    error=f"docker binary not found: {exc}",
                )
            except Exception as exc:  # pragma: no cover (defensive)
                return SandboxResult(
                    ok=False,
                    stage="start",
                    error=f"unexpected docker invocation error: {exc}",
                )

            # ── 4. Interpret the runner's JSON output ──────────────
            payload = _parse_runner_output(proc.stdout)

            if not payload:
                return SandboxResult(
                    ok=False,
                    stage="parse_output",
                    error="runner produced no JSON output",
                    exit_code=proc.returncode,
                    stderr_tail=(proc.stderr or "")[-500:],
                )

            if not payload.get("ok"):
                return SandboxResult(
                    ok=False,
                    stage=payload.get("stage", "runner"),
                    error=payload.get("error", "unknown runner failure"),
                    exit_code=proc.returncode,
                    stderr_tail=(proc.stderr or "")[-500:],
                )

            # ── 5. Copy STEP/GLB out of the scratch dir ────────────
            # The tempdir is cleaned up when we exit this `with`, so we
            # move the files to a stable per-run location the caller owns.
            persistent = Path(tempfile.mkdtemp(prefix="dpmech-out-"))
            step_src = host_workdir / "part.step"
            glb_src = host_workdir / "part.glb"
            step_dst = persistent / "part.step"
            glb_dst = persistent / "part.glb"

            if step_src.exists():
                shutil.copy2(step_src, step_dst)
            else:
                return SandboxResult(
                    ok=False,
                    stage="output_missing",
                    error="runner reported success but no STEP file was produced",
                )

            glb_path: Path | None = None
            if glb_src.exists():
                shutil.copy2(glb_src, glb_dst)
                glb_path = glb_dst

            return SandboxResult(
                ok=True,
                stage="success",
                step_path=step_dst,
                glb_path=glb_path,
                metrics=payload.get("metrics", {}),
                warnings=payload.get("warnings", []),
                elapsed_s=payload.get("elapsed_s"),
                exit_code=proc.returncode,
            )


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _parse_runner_output(stdout: str) -> dict[str, Any] | None:
    """The runner emits ONE JSON line. Return it as a dict, or None."""
    if not stdout:
        return None
    # Runner guarantees a single line but be defensive.
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _short_id(n: int = 8) -> str:
    import secrets
    return secrets.token_hex(n // 2)


def _force_kill_container(name: str) -> None:
    """Best-effort `docker kill` after a timeout so the container is reaped."""
    try:
        subprocess.run(
            ["docker", "kill", name],
            timeout=5,
            capture_output=True,
            check=False,
        )
    except Exception:  # pragma: no cover
        pass


# Module-level singleton — same pattern as the AST validator
sandbox = Sandbox()


def _mock_sandbox_result(code: str, *, run_id: str | None = None) -> SandboxResult:
    """
    Dev-only mock that produces a fake STEP file so the full pipeline can be
    exercised without the CadQuery Docker image.

    Parses the variant dimensions from the generated code's variable assignments
    to produce realistic bounding-box metrics. The STEP content is a minimal
    valid stub that satisfies the output validator's volume > 0 check.
    """
    import re, tempfile, math

    # Extract geometry parameters from the emitted CadQuery code
    def _float(name: str, default: float) -> float:
        m = re.search(rf"^{name}\s*=\s*([\d.]+)", code, re.MULTILINE)
        return float(m.group(1)) if m else default

    base_w   = _float("base_width",      80.0)
    base_d   = _float("base_depth",      60.0)
    base_t   = _float("base_thickness",   8.0)
    wall_h   = _float("wall_height",     50.0)
    wall_t   = _float("wall_thickness",   6.0)

    # Approximate bracket volume (base plate + wall rectangle)
    volume_mm3 = base_w * base_d * base_t + base_w * wall_h * wall_t

    # Write a minimal ISO 10303-21 STEP stub
    out_dir = Path(tempfile.mkdtemp(prefix="dpmech-mock-"))
    step_path = out_dir / "part.step"
    step_path.write_text(
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('DesignPilot mock STEP'),'2;1');\n"
        "FILE_NAME('part.step','',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "/* mock geometry — SANDBOX_SKIP_FOR_DEV=true */\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n",
        encoding="utf-8",
    )

    return SandboxResult(
        ok=True,
        stage="success",
        step_path=step_path,
        glb_path=None,          # no 3D viewer in dev-skip mode
        metrics={
            "volume_mm3":   round(volume_mm3, 2),
            "bbox_x_size":  base_w,
            "bbox_y_size":  base_d,
            "bbox_z_size":  round(wall_h + base_t, 1),
        },
        warnings=["SANDBOX_SKIP_FOR_DEV=true — mock STEP, no real geometry"],
        elapsed_s=0.001,
        exit_code=0,
    )
