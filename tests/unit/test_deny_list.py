"""Framework deny-list guard (MARK-CLAW-TOOLS.md §12).

Asserts that no dependency declared in ``pyproject.toml`` — runtime, dev group,
or build-system — matches an agent-runtime framework the project evaluated and
declined. Prompt instructions do not enforce "no agent frameworks"; this test
and the empty runtime dependency set do.
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


def test_runtime_dependencies_are_empty() -> None:
    # The tooling layer is stdlib-only; keep the runtime surface empty.
    data = tomllib.loads(PYPROJECT.read_text())
    assert data["project"]["dependencies"] == []


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
