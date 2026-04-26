"""
Runtime path helpers.

These keep writable scratch data inside the repo by default so local
development and sandboxed environments do not depend on the host OS temp
directory being writable.
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from secrets import token_hex


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    """Directory for local runtime artifacts."""
    root = PROJECT_ROOT / ".runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def temp_root() -> Path:
    """Directory used for temporary files created by the app."""
    root = runtime_root() / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def local_storage_root() -> Path:
    """Directory used by the local object-storage fallback."""
    root = runtime_root() / "local-r2"
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_tempdir(*, prefix: str) -> Path:
    """Create a unique temp directory under the repo-local temp root."""
    root = temp_root()
    for _ in range(20):
        path = root / f"{prefix}{token_hex(6)}"
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create temp directory under {root}")


@contextmanager
def temporary_directory(*, prefix: str):
    """Create a disposable temp directory under the repo-local temp root."""
    path = make_tempdir(prefix=prefix)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)
