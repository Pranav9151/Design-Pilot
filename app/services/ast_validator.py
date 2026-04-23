"""
AST static analyzer for CadQuery code snippets — LAYER 1 of the sandbox.

Threat model (from FORENSIC-ANALYSIS-Complete.md, S1):
  An attacker crafts a prompt that steers the LLM into emitting Python that
  does more than build a 3D shape. They might try to open a socket, read
  /etc/passwd, spawn a subprocess, exfiltrate environment variables, etc.

Defense-in-depth:
  Layer 1 (this file):     Static AST + string checks; rejected code NEVER runs.
  Layer 2 (sandbox.py):    Docker + gVisor with no network, read-only FS, caps dropped.
  Layer 3 (output.py):     Validate the STEP output is manifold and matches constraints.
  Layer 4 (monitoring):    Anomaly detection on execution times and memory.

This file alone cannot stop everything — sandbox and output validation are
still mandatory. But for any code to ever reach the sandbox it must first
pass this gate.

**Invariants enforced here:**
1. No import of dangerous modules (os, subprocess, socket, ...)
2. No call to dangerous builtins (eval, exec, compile, open, ...)
3. No access to dunder introspection attrs (__class__, __subclasses__, ...)
4. No string literals matching import-evasion patterns
5. Max code size (10 KB) to bound validation time and prevent DoS
6. Code must parse cleanly (no syntax errors)
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Final

import structlog

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Blocklists — exhaustive per the build plan's PROMPT-MECH-BUILD v3
# ═════════════════════════════════════════════════════════════════════

FORBIDDEN_IMPORTS: Final[frozenset[str]] = frozenset({
    # Process / OS
    "os", "subprocess", "multiprocessing", "threading", "asyncio",
    # Network
    "socket", "ssl", "urllib", "urllib.request", "http", "http.client",
    "requests", "httpx", "aiohttp", "ftplib", "smtplib", "poplib",
    "imaplib", "telnetlib", "xmlrpc",
    # Filesystem
    "pathlib", "shutil", "tempfile", "glob", "fileinput",
    # Interpreter internals
    "sys", "importlib", "ctypes", "cffi", "__main__",
    # Code execution
    "code", "codeop", "compileall", "py_compile",
    # Serialization that can execute code
    "pickle", "shelve", "marshal", "dill",
})

FORBIDDEN_CALLS: Final[frozenset[str]] = frozenset({
    "eval", "exec", "compile", "__import__",
    "open", "input", "breakpoint",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    "help", "exit", "quit",
})

FORBIDDEN_ATTRS: Final[frozenset[str]] = frozenset({
    # Class / MRO introspection (breaks out of any sandbox in 3 lines)
    "__class__", "__subclasses__", "__bases__", "__mro__",
    "__globals__", "__builtins__", "__dict__", "__module__",
    # Code object internals
    "__code__", "__closure__", "__defaults__",
    # Init manipulation
    "__init__", "__new__", "__init_subclass__",
    # Generic dangerous dunders
    "__loader__", "__spec__", "__import__",
})

# Substrings that appear in classic SSTI / jailbreak payloads.
# If the code contains any of these as a *substring of any string literal*,
# reject it — legitimate CadQuery has zero reason to mention them.
FORBIDDEN_STRING_PATTERNS: Final[tuple[str, ...]] = (
    "__import__",
    "__subclasses__",
    "__globals__",
    "__builtins__",
    "__class__",
    "__mro__",
    "subprocess",
    "os.system",
    "os.popen",
    "/etc/passwd",
    "/etc/shadow",
    "eval(",
    "exec(",
)

MAX_CODE_SIZE_BYTES: Final[int] = 10_000


# ═════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════


class ASTValidationError(Exception):
    """Raised when a code snippet fails the AST filter."""


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validate(). `valid=False` means the code MUST NOT run."""

    valid: bool
    reason: str
    location: str | None = None  # "line:col" when applicable

    def __bool__(self) -> bool:
        return self.valid

    def assert_valid(self) -> None:
        if not self.valid:
            raise ASTValidationError(
                f"{self.reason}" + (f" at {self.location}" if self.location else "")
            )


class ASTValidator:
    """Static analyzer that rejects dangerous Python constructs.

    Thread-safe. Re-use a single instance via `ast_validator` singleton.
    """

    def validate(self, code: str) -> ValidationResult:
        """Return a ValidationResult — does NOT raise. Caller decides."""
        # ── 0. Size check ───────────────────────────────────────────
        if not isinstance(code, str):
            return ValidationResult(False, "code must be str")
        if len(code.encode("utf-8")) > MAX_CODE_SIZE_BYTES:
            return ValidationResult(
                False,
                f"code exceeds {MAX_CODE_SIZE_BYTES} bytes "
                f"(got {len(code.encode('utf-8'))})",
            )
        if not code.strip():
            return ValidationResult(False, "empty code")

        # ── 1. Parse. A SyntaxError is automatic rejection. ─────────
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            return ValidationResult(
                False,
                f"syntax error: {exc.msg}",
                location=f"{exc.lineno}:{exc.offset}" if exc.lineno else None,
            )

        # ── 2. Walk every node ──────────────────────────────────────
        for node in ast.walk(tree):
            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if alias.name in FORBIDDEN_IMPORTS or top in FORBIDDEN_IMPORTS:
                        return _reject(
                            f"forbidden import: '{alias.name}'",
                            node,
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".", 1)[0]
                    if node.module in FORBIDDEN_IMPORTS or top in FORBIDDEN_IMPORTS:
                        return _reject(
                            f"forbidden import-from: '{node.module}'",
                            node,
                        )
                # `from . import os` — relative import of forbidden name
                for alias in node.names:
                    if alias.name in FORBIDDEN_IMPORTS:
                        return _reject(
                            f"forbidden import-from target: '{alias.name}'",
                            node,
                        )

            # Calls to bare names: eval(...), exec(...), __import__(...)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    return _reject(
                        f"forbidden call: '{node.func.id}'",
                        node,
                    )

            # Attribute access: x.__class__, x.__subclasses__()
            elif isinstance(node, ast.Attribute):
                if node.attr in FORBIDDEN_ATTRS:
                    return _reject(
                        f"forbidden attribute: '.{node.attr}'",
                        node,
                    )

            # Named access to a forbidden builtin as a variable reference
            # (e.g. `f = eval; f("1+1")`)
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
                # We only forbid loads/aliases, not assignments to names
                # that happen to collide with builtins.
                if isinstance(node.ctx, ast.Load):
                    return _reject(
                        f"forbidden builtin reference: '{node.id}'",
                        node,
                    )

            # String literals — scan for evasion patterns
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for pat in FORBIDDEN_STRING_PATTERNS:
                    if pat in node.value:
                        return _reject(
                            f"forbidden string pattern: '{pat}'",
                            node,
                        )

            # f-strings / JoinedStr: check every string component
            elif isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        for pat in FORBIDDEN_STRING_PATTERNS:
                            if pat in value.value:
                                return _reject(
                                    f"forbidden string pattern in f-string: '{pat}'",
                                    node,
                                )

        # Passed every check.
        return ValidationResult(True, "ok")


def _reject(reason: str, node: ast.AST) -> ValidationResult:
    loc = None
    lineno = getattr(node, "lineno", None)
    col = getattr(node, "col_offset", None)
    if lineno is not None:
        loc = f"{lineno}:{col}" if col is not None else str(lineno)
    logger.warning("ast_rejected", reason=reason, location=loc)
    return ValidationResult(False, reason, location=loc)


# Module-level singleton. Import and use directly.
ast_validator = ASTValidator()
