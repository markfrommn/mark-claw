"""Profile and XDG path resolution for mark-claw.

The active profile comes from ``MCLAW_PROFILE`` (default ``mark``). The word
``mark`` is only a default *profile name* — no personal value is hard-coded
here. Config and state roots follow the XDG base-directory spec:

    config: ${XDG_CONFIG_HOME:-~/.config}/mark-claw/<profile>/
    state:  ${XDG_STATE_HOME:-~/.local/state}/mark-claw/<profile>/

These are the two roots CLAUDE.md's three-way split reserves for the config and
state layers; the tooling layer (this repo) holds neither.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Env var naming the active profile.
PROFILE_ENV = "MCLAW_PROFILE"

#: Default profile name when ``MCLAW_PROFILE`` is unset or empty. This is a
#: profile *name*, not a personal value.
DEFAULT_PROFILE = "mark"

#: Fixed application directory segment under the XDG roots.
APP_DIR = "mark-claw"


def validate_profile(profile: str) -> str:
    """Return ``profile`` unchanged, or raise ``ValueError`` if unsafe.

    The profile is joined into filesystem paths (config/state roots), so a value
    that is a path separator, a traversal component, or contains a NUL would
    break profile isolation — e.g. ``MCLAW_PROFILE=/etc`` (an absolute component
    replaces the whole prefix on join) or ``MCLAW_PROFILE=../other`` (escapes the
    app dir). Reject those loudly rather than silently resolving outside the
    per-profile subtree.
    """
    if not profile:
        raise ValueError("profile must not be empty")
    if profile in {".", ".."}:
        raise ValueError(f"profile must not be a path traversal component: {profile!r}")
    if "/" in profile or "\\" in profile or "\x00" in profile:
        raise ValueError(
            f"profile must not contain a path separator or NUL: {profile!r}"
        )
    return profile


def resolve_profile(environ: os._Environ[str] | dict[str, str] | None = None) -> str:
    """Return the active profile name from the environment.

    Falls back to :data:`DEFAULT_PROFILE` when the env var is unset or blank, and
    validates the resolved value so a malformed ``MCLAW_PROFILE`` fails loudly
    instead of resolving paths outside the per-profile subtree.
    """
    env = os.environ if environ is None else environ
    value = env.get(PROFILE_ENV, "").strip()
    return validate_profile(value or DEFAULT_PROFILE)


def _xdg_root(
    env_var: str,
    default_relative: str,
    environ: dict[str, str] | os._Environ[str],
) -> Path:
    """Resolve an XDG base directory, honoring the env override.

    Per the XDG spec, a relative value in the env var is ignored (must be
    absolute); we fall back to the home-relative default in that case.
    """
    raw = environ.get(env_var, "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
    return Path.home() / default_relative


def config_root(
    profile: str | None = None,
    environ: os._Environ[str] | dict[str, str] | None = None,
) -> Path:
    """Return the config root for ``profile`` (default: active profile)."""
    env = os.environ if environ is None else environ
    prof = validate_profile(profile) if profile is not None else resolve_profile(env)
    return _xdg_root("XDG_CONFIG_HOME", ".config", env) / APP_DIR / prof


def state_root(
    profile: str | None = None,
    environ: os._Environ[str] | dict[str, str] | None = None,
) -> Path:
    """Return the state root for ``profile`` (default: active profile)."""
    env = os.environ if environ is None else environ
    prof = validate_profile(profile) if profile is not None else resolve_profile(env)
    return _xdg_root("XDG_STATE_HOME", ".local/state", env) / APP_DIR / prof
