"""Authentication support with a narrow, secret-safe persistence boundary.

Provider flows live in this module rather than the CLI so OAuth credentials and
sessions never pass through an agent-facing command surface.  This initial
portion intentionally contains only scope-neutral configuration and secure
state-file handling; provider-specific protocol choices are layered above it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from . import config_state, paths


class AuthError(Exception):
    """A safe-to-display authentication failure.

    Callers must not wrap provider exception text in this exception: OAuth and
    MTProto responses can contain credential-bearing URLs or session material.
    """


def validate_keychain_ref(ref: str, profile: str) -> None:
    """Verify that a config reference is confined to this profile's Keychain.

    This validates only the location; resolution remains inside the individual
    provider wrapper immediately before constructing its client.
    """
    prefix = f"keychain://mark-claw-{profile}/"
    account = ref.removeprefix(prefix)
    if not ref.startswith(prefix) or not account or "/" in account:
        raise AuthError("credential reference must be a profile keychain reference")


def token_cache_path(
    token_cache: str, filename: str, *, profile: str | None = None
) -> Path:
    """Resolve a configured ``$STATE/secrets/...`` cache location safely."""
    if not token_cache.startswith("$STATE/"):
        raise AuthError("token cache must be a relative $STATE/secrets path")
    relative = token_cache.removeprefix("$STATE/").rstrip("/")
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or parts[0] != "secrets"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise AuthError("token cache must be inside state secrets")
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise AuthError("invalid secret state path")
    return paths.state_root(profile) / Path(*parts) / filename


def _secure_dir(path: Path) -> None:
    """Create one secret directory or reject a symlink at that location."""
    if path.is_symlink():
        raise AuthError("unsafe secret state directory")
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise AuthError("unsafe secret state directory")
    path.chmod(0o700)


def write_secret_file(
    relative: str, content: bytes, *, profile: str | None = None
) -> Path:
    """Atomically write private auth state below ``state/secrets``.

    The supplied bytes are deliberately opaque and never decoded, logged, or
    included in raised errors.  Every created directory is 0700 and the final
    artifact is 0600.
    """
    rel = PurePosixPath(relative)
    if (
        not relative
        or rel.is_absolute()
        or any(part in {"", ".", ".."} for part in rel.parts)
        or len(rel.parts) < 2
    ):
        raise AuthError("invalid secret state path")
    root = paths.state_root(profile)
    # Reuse the state initializer's hardened handling of the top-level secrets
    # directory, then create provider/account subdirectories privately.
    active_profile = paths.resolve_profile() if profile is None else profile
    config_state.init_state_tree(profile=active_profile)
    base = root / "secrets"
    parent = base
    for part in rel.parts[:-1]:
        parent = parent / part
        _secure_dir(parent)
    target = parent / rel.name
    if target.is_symlink():
        raise AuthError("unsafe secret state file")
    fd, temporary_name = tempfile.mkstemp(prefix=".auth-", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AuthError("could not securely write authentication state") from exc
    return target
