"""Repo hygiene: no personal identifiers in tracked files (outside exempt set).

The pattern list is itself personal, so it is **not** stored in this repo — it
lives in the config layer at ``${CONFIG}/hygiene-patterns.txt``. When that file
is absent (a fresh clone, CI without the operator's config), the test skips with
a loud warning so the repo stays cloneable and green. When present, it greps
every tracked file (``git ls-files``) except the exempt set and must find zero
matches.

Exempt set (:data:`EXEMPT_PREFIXES`):

* ``specs/`` — the accepted pre-split exception (CLAUDE.md "No personal data in
  this repo": specs/ predates the split and contains real domains).
* the cwft-managed workflow scaffolding enumerated in CLAUDE.md's "Generated
  files" section: ``.cwft-settings.yaml`` (cwft source config),
  ``.cwft-ai-manifest.json``, ``.claude/``, ``.cursor/``, ``.vscode/``,
  ``AGENTS.md``, ``GEMINI.md``.

Rationale: those cwft files hold repo-identity / commit-attribution values
(github org, coauthor email), not the personal *data* this guarantee protects.
They are cwft-generated or cwft-source and must never be hand-edited (changing
them means editing the upstream template + ``cwft ai refresh``), so scrubbing
them here is neither possible nor the point. The hygiene guarantee therefore
covers the hand-authored tooling surface — ``mclaw_core/``, ``bin/``,
``prompts/``, ``tests/``, ``docs/``, ``pyproject.toml``, ``CLAUDE.md``,
``README`` — and anything else not in the exempt set. CLAUDE.md's "Generated
files" list is the authority for the exempt set.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from mclaw_core import paths

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Path prefixes / exact paths excluded from the hygiene grep. A prefix ending
#: in ``/`` matches a directory subtree; a bare filename matches that exact file.
#: The cwft entries mirror CLAUDE.md's "Generated files" list (the authority).
EXEMPT_PREFIXES: tuple[str, ...] = (
    "specs/",
    ".cwft-settings.yaml",
    ".cwft-ai-manifest.json",
    ".claude/",
    ".cursor/",
    ".vscode/",
    "AGENTS.md",
    "GEMINI.md",
)


def is_exempt(path: str) -> bool:
    """Return True if ``path`` is in the exempt (non-scanned) set."""
    return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def find_violations(
    rel_paths: Iterable[str],
    compiled_patterns: list[re.Pattern[str]],
    read_content: Callable[[str], str],
) -> list[str]:
    """Return ``"<path>: matched /<pattern>/"`` for each non-exempt hit.

    Pure and content-injectable so the exemption and matching logic are unit
    testable without touching the real working tree. Each file's relative path
    *and* its text content are matched against every pattern.
    """
    violations: list[str] = []
    for rel in rel_paths:
        if is_exempt(rel):
            continue
        haystacks = [rel, read_content(rel)]
        for pat in compiled_patterns:
            if any(pat.search(h) for h in haystacks):
                violations.append(f"{rel}: matched /{pat.pattern}/")
                break
    return violations


def _patterns_file() -> Path:
    return paths.config_root() / "hygiene-patterns.txt"


def _load_patterns(path: Path) -> list[str]:
    patterns: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in out.stdout.splitlines() if p]


def _read_repo_file(rel: str) -> str:
    try:
        return (REPO_ROOT / rel).read_text(errors="ignore")
    except (OSError, ValueError):
        return ""


def test_no_personal_identifiers_in_repo() -> None:
    patterns_path = _patterns_file()
    if not patterns_path.is_file():
        pytest.skip(
            "LOUD SKIP: hygiene patterns file not found at "
            f"{patterns_path}. The personal-identifier pattern list lives in the "
            "config layer, not this repo. Provide it (config issue DEV-13/B2) to "
            "run the hygiene grep. Skipping keeps the repo cloneable and CI green."
        )

    patterns = _load_patterns(patterns_path)
    assert patterns, f"{patterns_path} contains no usable patterns"
    compiled = [re.compile(p) for p in patterns]

    violations = find_violations(_tracked_files(), compiled, _read_repo_file)
    assert not violations, "personal identifiers found in tracked files:\n" + "\n".join(
        violations
    )


# --- unit tests for the exemption + matching logic -------------------------


def test_exempt_prefixes_cover_cwft_surface() -> None:
    # Guard against silent shrinkage of the exempt set (CLAUDE.md authority).
    for expected in (
        "specs/",
        ".cwft-settings.yaml",
        ".cwft-ai-manifest.json",
        ".claude/",
        ".cursor/",
        ".vscode/",
        "AGENTS.md",
        "GEMINI.md",
    ):
        assert expected in EXEMPT_PREFIXES
    assert is_exempt(".claude/agents/common-developer.md")
    assert is_exempt(".cwft-settings.yaml")
    assert is_exempt("specs/plans/PHASE-1-PLAN.md")
    assert not is_exempt("mclaw_core/paths.py")


# Neutral placeholder tokens for the injected-content tests. These stand in for
# a real personal identifier so that this test file itself stays clean of the
# real values (it is a non-exempt, scanned surface). The exempt-vs-non-exempt
# behavior is independent of which literal token is used.
_FAKE_IDENT = "zzplaceholderident"
_FAKE_ORG = "zzplaceholderorg"


def test_matches_in_exempt_files_are_ignored() -> None:
    # A personal identifier living only in the cwft scaffolding is exempt.
    compiled = [re.compile(_FAKE_IDENT)]
    content = {
        ".cwft-settings.yaml": f"github_org: {_FAKE_IDENT}",
        ".claude/agents/common-developer.md": f"noreply@{_FAKE_ORG}.example",
        "mclaw_core/paths.py": "DEFAULT_PROFILE = 'mark'",
    }
    violations = find_violations(content.keys(), compiled, lambda r: content[r])
    assert violations == []


def test_match_in_non_exempt_file_is_flagged() -> None:
    # Exemption must not neuter the check: a token in a hand-authored file fails.
    compiled = [re.compile(_FAKE_IDENT)]
    content = {
        "mclaw_core/leaky.py": f"USER = '{_FAKE_IDENT}'",
        ".cwft-settings.yaml": f"github_org: {_FAKE_IDENT}",
    }
    violations = find_violations(content.keys(), compiled, lambda r: content[r])
    assert violations == [f"mclaw_core/leaky.py: matched /{_FAKE_IDENT}/"]


def test_match_in_non_exempt_file_path_is_flagged() -> None:
    # The path itself is a haystack, not just the content.
    compiled = [re.compile(_FAKE_ORG)]
    content = {f"docs/{_FAKE_ORG}-notes.md": "nothing sensitive in body"}
    violations = find_violations(content.keys(), compiled, lambda r: content[r])
    assert violations == [f"docs/{_FAKE_ORG}-notes.md: matched /{_FAKE_ORG}/"]
