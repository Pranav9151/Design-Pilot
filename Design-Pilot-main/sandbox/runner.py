"""
Sandbox runner — executes inside the CadQuery container.

Contract:
    - Reads CadQuery Python code from stdin (up to 10 KB).
    - The code MUST assign its final shape to a global variable `result`.
    - Writes `/work/part.step` and `/work/part.glb`.
    - Emits a single JSON line to stdout: {"ok": true, "step": "...", "glb": "...", ...}
    - Exits 0 on success, non-zero on any failure.

This file is the ONLY Python the container ever runs. It:
    1. Reads stdin as UTF-8 (reject otherwise).
    2. Compiles + execs with a minimal globals dict (cadquery is pre-imported).
    3. Extracts geometry metrics (vertex / face / edge / volume) for the
       output validation layer back on the host.
    4. Serializes to STEP and GLB.

Does NOT implement any security itself beyond sane defaults. Security is the
container wrapper's job (--network=none, read-only FS, cap-drop=ALL, gVisor).
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback


MAX_CODE_BYTES = 10_000
MAX_STEP_BYTES = 50 * 1024 * 1024  # 50 MB
WORK_DIR = "/work"


def _emit(obj: dict) -> None:
    """Single-line JSON to stdout."""
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def main() -> int:
    start = time.perf_counter()

    # 1. Read code ----------------------------------------------------
    try:
        code = sys.stdin.read()
    except Exception as exc:
        _emit({"ok": False, "stage": "read_stdin", "error": str(exc)})
        return 2

    if not code or not code.strip():
        _emit({"ok": False, "stage": "read_stdin", "error": "empty code"})
        return 2

    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        _emit({"ok": False, "stage": "read_stdin", "error": "code too large"})
        return 2

    # 2. Prepare minimal execution globals ----------------------------
    # The host-side AST validator has already approved `code`. We still
    # give it a locked-down namespace: no __builtins__ overrides, no file
    # access patterns, no module loaders beyond `cadquery` and `math`.
    try:
        import cadquery as cq  # noqa: F401  (injected into exec globals)
        import math  # noqa: F401
    except ImportError as exc:
        _emit({"ok": False, "stage": "import_cadquery", "error": str(exc)})
        return 3

    exec_globals: dict = {
        "cq": cq,
        "cadquery": cq,
        "math": math,
        "__builtins__": {
            # A minimum viable builtins dict so common CadQuery patterns work.
            # Anything dangerous has already been rejected by the AST filter.
            name: getattr(__builtins__, name)
            for name in (
                "True", "False", "None",
                "abs", "all", "any", "bool", "dict", "enumerate", "filter",
                "float", "int", "isinstance", "len", "list", "map", "max",
                "min", "pow", "print", "range", "round", "set", "sorted",
                "str", "sum", "tuple", "zip",
            )
        },
    }
    exec_locals: dict = {}

    # 3. Execute ------------------------------------------------------
    try:
        compiled = compile(code, "<user_code>", "exec")
        exec(compiled, exec_globals, exec_locals)
    except Exception:
        _emit({
            "ok": False,
            "stage": "execute",
            "error": traceback.format_exc(limit=3),
        })
        return 4

    # 4. Find the `result` shape --------------------------------------
    result = exec_locals.get("result") or exec_globals.get("result")
    if result is None:
        _emit({
            "ok": False,
            "stage": "extract_result",
            "error": "user code did not assign a `result` variable",
        })
        return 5

    # 5. Validate the shape -------------------------------------------
    # CadQuery produces cq.Workplane whose .val() is a Shape, OR a Shape
    # directly. Normalize.
    try:
        shape = result.val() if hasattr(result, "val") else result
        if not hasattr(shape, "isValid") or not shape.isValid():
            _emit({
                "ok": False,
                "stage": "validate_shape",
                "error": "generated shape is not valid / not manifold",
            })
            return 6
    except Exception as exc:
        _emit({
            "ok": False,
            "stage": "validate_shape",
            "error": f"shape validation failed: {exc}",
        })
        return 6

    # 6. Measure the shape --------------------------------------------
    # These metrics are returned to the host so output validation can
    # cross-check against the user's specified parameters.
    try:
        bbox = shape.BoundingBox()
        metrics = {
            "volume_mm3": float(shape.Volume()),
            "bbox_xmin": float(bbox.xmin),
            "bbox_xmax": float(bbox.xmax),
            "bbox_ymin": float(bbox.ymin),
            "bbox_ymax": float(bbox.ymax),
            "bbox_zmin": float(bbox.zmin),
            "bbox_zmax": float(bbox.zmax),
            "bbox_x_size": float(bbox.xmax - bbox.xmin),
            "bbox_y_size": float(bbox.ymax - bbox.ymin),
            "bbox_z_size": float(bbox.zmax - bbox.zmin),
        }
    except Exception as exc:
        _emit({
            "ok": False,
            "stage": "measure_shape",
            "error": f"measurement failed: {exc}",
        })
        return 7

    # 7. Write STEP ---------------------------------------------------
    step_path = os.path.join(WORK_DIR, "part.step")
    try:
        cq.exporters.export(result, step_path, exportType="STEP")
    except Exception as exc:
        _emit({
            "ok": False,
            "stage": "export_step",
            "error": f"STEP export failed: {exc}",
        })
        return 8

    step_size = os.path.getsize(step_path)
    if step_size > MAX_STEP_BYTES:
        _emit({
            "ok": False,
            "stage": "export_step",
            "error": f"STEP too large: {step_size} bytes",
        })
        return 8

    # 8. Write GLB (for the Three.js viewer) --------------------------
    glb_path = os.path.join(WORK_DIR, "part.glb")
    try:
        stl_path = os.path.join(WORK_DIR, "part.stl")
        cq.exporters.export(result, stl_path, exportType="STL")

        import trimesh
        mesh = trimesh.load(stl_path)
        mesh.export(glb_path, file_type="glb")
    except Exception as exc:
        # GLB is nice-to-have for the viewer; STEP is the deliverable.
        # Don't fail the whole run just because GLB conversion hiccuped.
        _emit({
            "ok": True,
            "warnings": [f"GLB export failed: {exc}"],
            "step": step_path,
            "step_size_bytes": step_size,
            "glb": None,
            "metrics": metrics,
            "elapsed_s": time.perf_counter() - start,
        })
        return 0

    _emit({
        "ok": True,
        "step": step_path,
        "step_size_bytes": step_size,
        "glb": glb_path,
        "glb_size_bytes": os.path.getsize(glb_path),
        "metrics": metrics,
        "elapsed_s": time.perf_counter() - start,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
