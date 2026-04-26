"""
Security: sandbox command construction.

These tests DO NOT spawn Docker. They assert that the argv we hand to
`subprocess.run(...)` contains exactly the right security flags.

Why this matters: if a refactor accidentally drops `--network=none` or
`--cap-drop=ALL`, every piece of subsequent defense is weakened. This
suite freezes the argv contract.

Integration tests that actually spawn containers live in
`tests/integration/test_sandbox_integration.py` and are skipped when
Docker is not available (e.g. during fast local dev loops).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.sandbox import (
    Sandbox,
    SandboxResult,
    _parse_runner_output,
    build_docker_command,
)


pytestmark = [pytest.mark.security]


# ─────────────────────────────────────────────────────────────────────
# build_docker_command: the argv contract
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def base_args(tmp_path: Path) -> dict:
    return {
        "image": "designpilot/cadquery-sandbox:latest",
        "host_workdir": tmp_path,
        "timeout_s": 30,
        "memory_mb": 512,
        "cpu_quota": 200_000,
        "use_gvisor": True,
        "container_name": "test-container",
    }


def test_cmd_starts_with_docker_run(base_args: dict):
    cmd = build_docker_command(**base_args)
    assert cmd[0] == "docker"
    assert cmd[1] == "run"


def test_cmd_includes_rm_flag(base_args: dict):
    """Container must be removed after run — no persistent state."""
    cmd = build_docker_command(**base_args)
    assert "--rm" in cmd


def test_cmd_includes_gvisor_runtime_when_requested(base_args: dict):
    cmd = build_docker_command(**base_args)
    assert "--runtime" in cmd
    idx = cmd.index("--runtime")
    assert cmd[idx + 1] == "runsc"


def test_cmd_omits_gvisor_runtime_when_disabled(base_args: dict):
    base_args["use_gvisor"] = False
    cmd = build_docker_command(**base_args)
    assert "--runtime" not in cmd
    assert "runsc" not in cmd


def test_cmd_includes_network_none(base_args: dict):
    """CRITICAL: containers must have NO network. Exfiltration defense."""
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--network")
    assert cmd[idx + 1] == "none"


def test_cmd_includes_read_only_rootfs(base_args: dict):
    cmd = build_docker_command(**base_args)
    assert "--read-only" in cmd


def test_cmd_drops_all_capabilities(base_args: dict):
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--cap-drop")
    assert cmd[idx + 1] == "ALL"


def test_cmd_disables_new_privileges(base_args: dict):
    """setuid / filecaps escalation vector closed."""
    cmd = build_docker_command(**base_args)
    # --security-opt can appear multiple times; find the right pair
    security_opts = []
    for i, arg in enumerate(cmd):
        if arg == "--security-opt":
            security_opts.append(cmd[i + 1])
    assert "no-new-privileges" in security_opts


def test_cmd_runs_as_nobody(base_args: dict):
    """uid 65534 = nobody; gid 65534 = nogroup. Non-root container."""
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--user")
    assert cmd[idx + 1] == "65534:65534"


def test_cmd_sets_memory_limit(base_args: dict):
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--memory")
    assert cmd[idx + 1] == "512m"


def test_cmd_disables_memory_swap(base_args: dict):
    """memory-swap == memory means no swap allowed — resists swap-based OOM evasion."""
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--memory-swap")
    assert cmd[idx + 1] == "512m"


def test_cmd_sets_cpu_quota(base_args: dict):
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--cpu-quota")
    assert cmd[idx + 1] == "200000"
    idx2 = cmd.index("--cpu-period")
    assert cmd[idx2 + 1] == "100000"


def test_cmd_limits_pids(base_args: dict):
    """Fork-bomb defense."""
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--pids-limit")
    assert cmd[idx + 1] == "50"


def test_cmd_has_restricted_tmpfs(base_args: dict):
    """tmpfs /tmp must be size-capped, noexec, nosuid, nodev."""
    cmd = build_docker_command(**base_args)
    # Find the --tmpfs arg and its value
    tmpfs_values = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--tmpfs"]
    tmp_mount = next(v for v in tmpfs_values if v.startswith("/tmp"))
    assert "size=50m" in tmp_mount
    assert "noexec" in tmp_mount
    assert "nosuid" in tmp_mount
    assert "nodev" in tmp_mount


def test_cmd_bind_mounts_workdir(base_args: dict, tmp_path: Path):
    cmd = build_docker_command(**base_args)
    # --volume <host>:/work:rw
    volume_args = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--volume"]
    work_mount = next(v for v in volume_args if v.endswith(":/work:rw"))
    assert str(tmp_path.absolute()) in work_mount


def test_cmd_includes_container_name(base_args: dict):
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--name")
    assert cmd[idx + 1] == "test-container"


def test_cmd_image_is_last(base_args: dict):
    """Image name must be the last argv element — Docker CLI contract."""
    cmd = build_docker_command(**base_args)
    assert cmd[-1] == "designpilot/cadquery-sandbox:latest"


def test_cmd_never_includes_privileged_flag(base_args: dict):
    """--privileged bypasses ALL isolation. Must never appear."""
    cmd = build_docker_command(**base_args)
    assert "--privileged" not in cmd


def test_cmd_never_mounts_docker_socket(base_args: dict):
    """Classic container escape via /var/run/docker.sock."""
    cmd = build_docker_command(**base_args)
    joined = " ".join(cmd)
    assert "docker.sock" not in joined


def test_cmd_never_uses_host_network(base_args: dict):
    cmd = build_docker_command(**base_args)
    assert "host" not in [cmd[i + 1] for i, a in enumerate(cmd) if a == "--network"]


def test_cmd_never_uses_host_pid(base_args: dict):
    """--pid=host breaks pid namespacing, exposing host processes."""
    cmd = build_docker_command(**base_args)
    assert "--pid" not in cmd  # we don't set it, which means default (container-private)


def test_cmd_never_uses_host_ipc(base_args: dict):
    cmd = build_docker_command(**base_args)
    assert "--ipc" not in cmd


def test_cmd_timeout_matches_caller(base_args: dict):
    base_args["timeout_s"] = 45
    cmd = build_docker_command(**base_args)
    idx = cmd.index("--stop-timeout")
    assert cmd[idx + 1] == "45"


# ─────────────────────────────────────────────────────────────────────
# Sandbox.run: pre-flight behavior without spawning Docker
# ─────────────────────────────────────────────────────────────────────


def test_run_rejects_ast_invalid_code_without_docker():
    """AST rejection happens in-process — no Docker touch needed."""
    sb = Sandbox()
    result = sb.run("import os\nos.system('ls')", skip_ast_check=False)
    assert isinstance(result, SandboxResult)
    assert not result.ok
    assert result.stage == "ast"
    assert "forbidden" in (result.error or "").lower()


def test_run_refuses_to_run_without_gvisor_in_production(monkeypatch):
    """Production env + use_gvisor=False = immediate refusal."""
    sb = Sandbox()
    monkeypatch.setattr(sb.settings, "APP_ENV", "production")

    result = sb.run("result = 1", skip_ast_check=True, use_gvisor=False)
    assert not result.ok
    assert result.stage == "prereq"
    assert "gvisor" in (result.error or "").lower()


def test_run_reports_missing_docker():
    """If docker binary is absent, return a structured failure, not a traceback."""
    from unittest.mock import patch

    sb = Sandbox()
    with patch.object(sb, "_docker_available", return_value=False):
        result = sb.run("result = 1", skip_ast_check=True, use_gvisor=False)
    assert not result.ok
    assert result.stage == "prereq"
    assert "docker" in (result.error or "").lower()


# ─────────────────────────────────────────────────────────────────────
# _parse_runner_output: the protocol with the container
# ─────────────────────────────────────────────────────────────────────


def test_parse_runner_output_happy_path():
    out = '{"ok": true, "step": "/work/part.step", "metrics": {"volume_mm3": 1000.0}}\n'
    parsed = _parse_runner_output(out)
    assert parsed is not None
    assert parsed["ok"] is True
    assert parsed["step"] == "/work/part.step"


def test_parse_runner_output_handles_multiline_with_trailing_json():
    """Runner might emit a warning on an earlier line, then the JSON result."""
    out = "Some warning text\n{\"ok\": true, \"step\": \"x\"}\n"
    parsed = _parse_runner_output(out)
    assert parsed is not None
    assert parsed["ok"] is True


def test_parse_runner_output_empty_returns_none():
    assert _parse_runner_output("") is None
    assert _parse_runner_output("   \n\n  ") is None


def test_parse_runner_output_invalid_json_returns_none():
    assert _parse_runner_output("this is not json") is None


def test_parse_runner_output_rejects_non_dict_json():
    """Arrays and scalars are valid JSON but not valid runner output."""
    assert _parse_runner_output("[1, 2, 3]") is None
    assert _parse_runner_output("42") is None
