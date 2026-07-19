"""Scope-neutral safety tests for provider authentication helpers."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mclaw_core import auth


def _xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("MCLAW_PROFILE", "test")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state / "mark-claw" / "test"


def test_write_secret_file_creates_private_tree_and_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _xdg(monkeypatch, tmp_path)

    target = auth.write_secret_file("google/account/token.json", b"not-a-token")

    assert target == root / "secrets/google/account/token.json"
    assert target.read_bytes() == b"not-a-token"
    assert stat.S_IMODE((root / "secrets").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "secrets/google").stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_secret_file_rejects_escape_and_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _xdg(monkeypatch, tmp_path)
    with pytest.raises(auth.AuthError, match="invalid secret state path"):
        auth.write_secret_file("../outside", b"x")

    secrets = root / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "google").symlink_to(tmp_path)
    with pytest.raises(auth.AuthError, match="unsafe secret state directory"):
        auth.write_secret_file("google/token.json", b"x")


def test_write_secret_file_does_not_preserve_wide_permissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _xdg(monkeypatch, tmp_path)
    target = root / "secrets/msal/account/token.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    os.chmod(target, 0o644)

    auth.write_secret_file("msal/account/token.json", b"new")

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_token_cache_path_accepts_only_state_secrets_descendants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _xdg(monkeypatch, tmp_path)
    assert auth.token_cache_path("$STATE/secrets/google/work/", "token.json") == (
        root / "secrets/google/work/token.json"
    )
    with pytest.raises(auth.AuthError, match="state secrets"):
        auth.token_cache_path("$STATE/spool/work", "token.json")
    with pytest.raises(auth.AuthError, match="relative"):
        auth.token_cache_path("/tmp/cache", "token.json")


def test_keychain_ref_is_profile_bound() -> None:
    auth.validate_keychain_ref(
        "keychain://mark-claw-test/google-oauth-client-credential", "test"
    )
    with pytest.raises(auth.AuthError, match="profile keychain"):
        auth.validate_keychain_ref("keychain://mark-claw-other/item-field", "test")
