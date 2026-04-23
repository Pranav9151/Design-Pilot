"""
Unit tests for app.services.storage.

These run in the local-fallback mode — no real R2 credentials needed.
Integration tests against real R2 are guarded by an env flag and run
only in CI where R2_ACCESS_KEY_ID etc. are set.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.services.storage import (
    DesignFilesLocation,
    StorageError,
    StorageService,
    UploadedFile,
    _design_key,
    _local_fallback_url,
)


# ─────────────────────────────────────────────────────────────────────
# Key construction
# ─────────────────────────────────────────────────────────────────────


def test_design_key_is_deterministic():
    uid = uuid4()
    assert _design_key(uid, "step") == f"designs/{uid}/part.step"
    assert _design_key(uid, "glb") == f"designs/{uid}/part.glb"


def test_design_key_different_uuids_never_collide():
    a = _design_key(uuid4(), "step")
    b = _design_key(uuid4(), "step")
    assert a != b


# ─────────────────────────────────────────────────────────────────────
# is_configured — true R2 vs dev fallback
# ─────────────────────────────────────────────────────────────────────


def test_is_configured_false_without_r2_creds(monkeypatch):
    svc = StorageService()
    monkeypatch.setattr(svc.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(svc.settings, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(svc.settings, "R2_ENDPOINT_URL", "")
    monkeypatch.setattr(svc.settings, "R2_ACCOUNT_ID", "")
    assert svc.is_configured is False


def test_is_configured_true_when_all_creds_set(monkeypatch):
    svc = StorageService()
    monkeypatch.setattr(svc.settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(svc.settings, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(svc.settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(svc.settings, "R2_ENDPOINT_URL", "https://r2.example.com")
    assert svc.is_configured is True


def test_is_configured_false_when_partial_creds(monkeypatch):
    svc = StorageService()
    monkeypatch.setattr(svc.settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(svc.settings, "R2_ACCESS_KEY_ID", "")       # missing
    monkeypatch.setattr(svc.settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(svc.settings, "R2_ENDPOINT_URL", "https://r2.example.com")
    assert svc.is_configured is False


# ─────────────────────────────────────────────────────────────────────
# Local-fallback upload (dev mode)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def storage_local(monkeypatch, tmp_path):
    """A StorageService in local-fallback mode, sandboxed in tmp_path."""
    svc = StorageService()
    monkeypatch.setattr(svc.settings, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(svc.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(svc.settings, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(svc.settings, "R2_ENDPOINT_URL", "")
    svc._local_fallback_dir = tmp_path / "dpmech-local-r2"
    return svc


@pytest.fixture
def sample_step_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.step"
    # Minimal STEP header; content validity isn't what we're testing
    path.write_bytes(
        b"ISO-10303-21;\nHEADER;\n"
        b"FILE_DESCRIPTION((''),'2;1');\n"
        b"ENDSEC;\nDATA;\n#1=SHAPE_DEFINITION_REPRESENTATION(...);\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    return path


async def test_upload_local_fallback_writes_file(storage_local, sample_step_file, tmp_path):
    uid = uuid4()
    result = await storage_local.upload_design_files(
        design_id=uid,
        step_path=sample_step_file,
    )
    assert isinstance(result, DesignFilesLocation)
    assert isinstance(result.step, UploadedFile)
    assert result.step.key == f"designs/{uid}/part.step"
    assert result.step.size_bytes > 0
    assert result.step.url.startswith("file://")

    # File actually exists on disk
    landed = storage_local._local_fallback_dir / result.step.key
    assert landed.exists()
    assert landed.read_bytes() == sample_step_file.read_bytes()


async def test_upload_without_glb_returns_none(storage_local, sample_step_file):
    result = await storage_local.upload_design_files(
        design_id=uuid4(),
        step_path=sample_step_file,
        glb_path=None,
    )
    assert result.step is not None
    assert result.glb is None


async def test_upload_with_glb(storage_local, sample_step_file, tmp_path):
    glb = tmp_path / "sample.glb"
    glb.write_bytes(b"glTF\x00" * 50)  # fake GLB

    result = await storage_local.upload_design_files(
        design_id=uuid4(),
        step_path=sample_step_file,
        glb_path=glb,
    )
    assert result.glb is not None
    assert result.glb.key.endswith("/part.glb")
    assert result.glb.size_bytes == 250


async def test_upload_raises_if_step_missing(storage_local, tmp_path):
    with pytest.raises(StorageError) as exc_info:
        await storage_local.upload_design_files(
            design_id=uuid4(),
            step_path=tmp_path / "does-not-exist.step",
        )
    assert "does not exist" in str(exc_info.value).lower()


async def test_upload_skips_missing_glb_silently(storage_local, sample_step_file, tmp_path):
    """A GLB path that doesn't exist on disk should just be ignored, not error."""
    result = await storage_local.upload_design_files(
        design_id=uuid4(),
        step_path=sample_step_file,
        glb_path=tmp_path / "ghost.glb",
    )
    assert result.step is not None
    assert result.glb is None


# ─────────────────────────────────────────────────────────────────────
# Presigned URL — local fallback
# ─────────────────────────────────────────────────────────────────────


async def test_presigned_url_in_local_mode(storage_local):
    url = await storage_local.presigned_url("designs/abc/part.step")
    assert url == "file:///tmp/dpmech-local-r2/designs/abc/part.step"


def test_local_fallback_url_shape():
    assert _local_fallback_url("designs/x/part.step") == (
        "file:///tmp/dpmech-local-r2/designs/x/part.step"
    )


# ─────────────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────────────


async def test_delete_local_files(storage_local, sample_step_file):
    uid = uuid4()
    await storage_local.upload_design_files(
        design_id=uid,
        step_path=sample_step_file,
    )
    # Verify the file landed
    landed = storage_local._local_fallback_dir / f"designs/{uid}/part.step"
    assert landed.exists()

    # Delete
    n = await storage_local.delete_design_files(uid)
    assert n == 1
    assert not landed.exists()


async def test_delete_is_idempotent(storage_local):
    """Deleting a design that has no files returns 0, doesn't error."""
    n = await storage_local.delete_design_files(uuid4())
    assert n == 0
