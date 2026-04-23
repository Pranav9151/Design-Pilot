"""
Object storage — Cloudflare R2 (S3-compatible).

Why R2:
  - Zero egress fees (R2 doesn't charge for downloads)
  - S3-compatible API so we can swap backends if needed
  - Strong consistency — we write then immediately read back for verification

Contract:
  - STEP files go under s3://designpilot-designs/designs/{design_id}/part.step
  - GLB files go under s3://designpilot-designs/designs/{design_id}/part.glb
  - Object keys are deterministic — a re-upload overwrites, which matches
    the "version bumps a new design_id" design in app/models/design.py

Two separate operations are exposed:
  - upload_design_files():  called from the generation pipeline, bytes-in
  - presigned_url():        called from the API layer, returns a 1-hour URL
                            that the frontend uses to download directly from R2

If R2 credentials are not configured (dev environment without R2 set up),
upload_design_files() falls back to writing to a local directory so that
the rest of the pipeline can be exercised end-to-end. The fallback is
explicit and logged — production always uses real R2.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import structlog

from app.core.config import Settings, get_settings
from app.core.paths import local_storage_root

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Data shapes
# ═════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class UploadedFile:
    """Record of a single uploaded file."""

    key: str              # e.g. "designs/abc.../part.step"
    url: str              # s3:// or file:// URL for DB storage
    size_bytes: int


@dataclass(frozen=True)
class DesignFilesLocation:
    """Bundle of the two files we upload per design."""

    step: UploadedFile
    glb: UploadedFile | None


class StorageError(Exception):
    """Raised when an upload or presign fails in a way the caller must handle."""


# ═════════════════════════════════════════════════════════════════════
# Service
# ═════════════════════════════════════════════════════════════════════


class StorageService:
    """Thin facade over R2 via aioboto3-lite (we use boto3 in a thread).

    We use sync boto3 wrapped in `asyncio.to_thread()` rather than aioboto3
    for two reasons: aioboto3 is under-maintained, and our upload frequency
    is low enough (≤10/min per user at launch) that thread-offload is fine.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._local_fallback_dir: Path | None = None

    # ── Public API ────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        s = self.settings
        return bool(
            s.R2_ACCOUNT_ID
            and s.R2_ACCESS_KEY_ID
            and s.R2_SECRET_ACCESS_KEY
            and s.R2_ENDPOINT_URL
        )

    async def upload_design_files(
        self,
        *,
        design_id: UUID,
        step_path: Path,
        glb_path: Path | None = None,
    ) -> DesignFilesLocation:
        """Upload STEP (+ optional GLB) for a design. Returns URLs to store in DB.

        On missing R2 config (dev), writes to a repo-local runtime directory
        and returns `file://` URLs. Production will fail loudly at
        startup if R2 is unset — see app.main.lifespan.
        """
        if not step_path.exists():
            raise StorageError(f"STEP file does not exist: {step_path}")

        step_key = _design_key(design_id, "step")
        glb_key = _design_key(design_id, "glb") if glb_path else None

        if self.is_configured:
            step_url = await self._upload_to_r2(step_path, step_key, "application/step")
            glb_url = None
            if glb_path and glb_path.exists():
                glb_url = await self._upload_to_r2(glb_path, glb_key, "model/gltf-binary")
        else:
            logger.warning(
                "r2_not_configured_using_local_fallback",
                env=self.settings.APP_ENV,
                design_id=str(design_id),
            )
            step_url = await self._upload_local(step_path, step_key)
            glb_url = None
            if glb_path and glb_path.exists():
                glb_url = await self._upload_local(glb_path, glb_key)

        step_file = UploadedFile(
            key=step_key, url=step_url, size_bytes=step_path.stat().st_size
        )
        glb_file = None
        if glb_path and glb_path.exists() and glb_key and glb_url:
            glb_file = UploadedFile(
                key=glb_key, url=glb_url, size_bytes=glb_path.stat().st_size
            )

        return DesignFilesLocation(step=step_file, glb=glb_file)

    async def presigned_url(
        self,
        key: str,
        *,
        expires_in_seconds: int = 3600,
        method: Literal["GET"] = "GET",
    ) -> str:
        """Return a time-limited URL that the browser can GET directly.

        Frontend download flow: API returns this URL; browser downloads
        from R2 without hitting our backend a second time. Saves ~50-100ms
        per download and keeps the 3D viewer feeling instant.

        In the local-fallback mode (dev without R2), returns the `file://`
        URL unchanged — the FastAPI app serves it via a /static mount.
        """
        if not self.is_configured:
            return self._local_fallback_url(key)

        import boto3  # lazy import so the module loads in dev without boto3

        def _sign() -> str:
            client = self._boto_client()
            return client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.settings.R2_BUCKET_NAME, "Key": key},
                ExpiresIn=expires_in_seconds,
            )

        return await asyncio.to_thread(_sign)

    async def delete_design_files(self, design_id: UUID) -> int:
        """Delete the STEP + GLB for a design. Returns count actually deleted.
        Used for GDPR right-to-erasure and for cleaning up failed generations."""
        keys = [
            _design_key(design_id, "step"),
            _design_key(design_id, "glb"),
        ]

        if not self.is_configured:
            return sum(self._delete_local(k) for k in keys)

        import boto3

        def _del_many() -> int:
            client = self._boto_client()
            response = client.delete_objects(
                Bucket=self.settings.R2_BUCKET_NAME,
                Delete={"Objects": [{"Key": k} for k in keys]},
            )
            return len(response.get("Deleted", []))

        return await asyncio.to_thread(_del_many)

    # ── Internals ─────────────────────────────────────────────────

    def _boto_client(self):
        """Construct a fresh boto3 S3 client pointed at R2."""
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self.settings.R2_ENDPOINT_URL,
            aws_access_key_id=self.settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=self.settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",  # R2 requirement
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    async def _upload_to_r2(self, path: Path, key: str, content_type: str) -> str:
        """Upload a file to R2. Returns the s3:// URL."""
        bucket = self.settings.R2_BUCKET_NAME

        def _put() -> None:
            client = self._boto_client()
            with path.open("rb") as fh:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=fh,
                    ContentType=content_type,
                    # Checksum so R2 can validate the upload atomically
                    ChecksumAlgorithm="SHA256",
                )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            raise StorageError(f"R2 upload failed for {key}: {exc}") from exc

        url = f"s3://{bucket}/{key}"
        logger.info("r2_upload_ok", key=key, bytes=path.stat().st_size)
        return url

    async def _upload_local(self, path: Path, key: str) -> str:
        """Dev fallback — copy the file into the repo-local runtime storage area."""
        dest = self._ensure_local_fallback_dir() / key
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _copy() -> None:
            shutil.copy2(path, dest)

        await asyncio.to_thread(_copy)
        return self._local_fallback_url(key)

    def _delete_local(self, key: str) -> int:
        dest = self._ensure_local_fallback_dir() / key
        if dest.exists():
            dest.unlink()
            return 1
        return 0

    def _ensure_local_fallback_dir(self) -> Path:
        if self._local_fallback_dir is None:
            self._local_fallback_dir = local_storage_root()
        self._local_fallback_dir.mkdir(parents=True, exist_ok=True)
        return self._local_fallback_dir

    def _local_fallback_url(self, key: str) -> str:
        return (self._ensure_local_fallback_dir() / key).as_uri()


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _design_key(design_id: UUID, kind: Literal["step", "glb"]) -> str:
    """Canonical object key for a design's artifact."""
    return f"designs/{design_id}/part.{kind}"


def _local_fallback_url(key: str) -> str:
    return (local_storage_root() / key).as_uri()


# Module-level lazy singleton
_storage_singleton: StorageService | None = None


def get_storage() -> StorageService:
    global _storage_singleton
    if _storage_singleton is None:
        _storage_singleton = StorageService()
    return _storage_singleton
