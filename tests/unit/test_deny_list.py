"""Runtime dependency allowlist + framework deny-list (MARK-CLAW-TOOLS.md §12).

Two complementary guards on ``pyproject.toml``:

* :func:`test_runtime_dependencies_are_allowlisted` — the runtime surface is
  audited-minimal and **allowlisted**. Every runtime dep must be in
  :data:`ALLOWED_RUNTIME_DEPS`. A smuggled non-allowlisted dep fails here.
  (Replaces the former empty-deps assertion; ``pyyaml`` is now admitted for
  config parsing — Python ships no stdlib YAML — per B2/DEV-13.)
* :func:`test_no_agent_framework_dependencies` — no declared dep (runtime, dev,
  build-system) matches an agent-framework deny-glob. A smuggled framework is
  caught here even if it were (mistakenly) added to the runtime allowlist.

Prompt instructions do not enforce "no agent frameworks"; these tests do.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

DENY_GLOBS = [
    "openclaw*",
    "zeroclaw*",
    "ironclaw*",
    "picoclaw*",
    "nemoclaw*",
    "hermes*",
    "vellum*",
]

#: Runtime dependencies the tooling layer is permitted to declare. The runtime
#: surface stays audited-minimal; adding a dependency requires a deliberate edit
#: here (preserving the property the former empty-deps assertion protected).
#: ``pyyaml`` is admitted for YAML config parsing — Python has no stdlib YAML.
ALLOWED_RUNTIME_DEPS: frozenset[str] = frozenset({"pyyaml"})

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Split a PEP 508 requirement string down to its distribution name.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")


def _dist_name(requirement: str) -> str:
    match = _NAME_RE.match(requirement.strip())
    return (match.group(0) if match else requirement).lower()


def _all_declared_dependencies() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    names: list[str] = []

    project = data.get("project", {})
    for req in project.get("dependencies", []):
        names.append(_dist_name(req))
    for group in project.get("optional-dependencies", {}).values():
        names.extend(_dist_name(r) for r in group)

    for group in data.get("dependency-groups", {}).values():
        names.extend(_dist_name(r) for r in group if isinstance(r, str))

    build = data.get("build-system", {})
    names.extend(_dist_name(r) for r in build.get("requires", []))

    return names


def test_pyproject_exists() -> None:
    assert PYPROJECT.is_file()


def test_runtime_dependencies_are_allowlisted() -> None:
    """Every runtime dep must be in the approved allowlist.

    The runtime surface is audited-minimal, now expressed as an allowlist so
    ``pyyaml`` (config parsing) can be admitted without weakening the guarantee
    that the former empty-deps assertion protected. A smuggled non-allowlisted
    runtime dep fails here; a smuggled framework is additionally caught by
    :func:`test_no_agent_framework_dependencies`.
    """
    data = tomllib.loads(PYPROJECT.read_text())
    declared = {_dist_name(r) for r in data["project"].get("dependencies", [])}
    extra = declared - ALLOWED_RUNTIME_DEPS
    assert not extra, (
        f"non-allowlisted runtime dependencies declared: {sorted(extra)} "
        "(extend ALLOWED_RUNTIME_DEPS only after review)"
    )


def test_no_agent_framework_dependencies() -> None:
    declared = _all_declared_dependencies()
    for name in declared:
        for pattern in DENY_GLOBS:
            assert not fnmatch.fnmatch(name, pattern), (
                f"denied agent-framework dependency '{name}' matches '{pattern}' "
                "(MARK-CLAW-TOOLS.md §12)"
            )


def test_deny_globs_would_catch_a_framework() -> None:
    # Guard the guard: the matcher must actually fire on a denied name.
    assert any(
        fnmatch.fnmatch("openclaw-sdk", pattern) for pattern in DENY_GLOBS
    )
