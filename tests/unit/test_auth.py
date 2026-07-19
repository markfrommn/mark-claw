"""Scope-neutral safety tests for provider authentication helpers."""

from __future__ import annotations

import inspect
import json
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


def _accounts_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "cfg" / "mark-claw" / "test"
    cfg.mkdir(parents=True)
    (cfg / "accounts.yaml").write_text(
        """email:
  - id: google-work
    address: user@example.invalid
    provider: gmail
    auth:
      client_ref: keychain://mark-claw-test/google-oauth-client-credential
      token_cache: $STATE/secrets/google/google-work/
  - id: graph-work
    address: user@example.invalid
    provider: graph
    auth:
      client_ref: keychain://mark-claw-test/entra-app-client_id
      tenant_ref: keychain://mark-claw-test/entra-app-tenant_id
      token_cache: $STATE/secrets/msal/graph-work/
"""
    )
    return cfg


def test_google_auth_persists_readonly_token_and_self_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _xdg(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _accounts_config(tmp_path)
    monkeypatch.setattr(
        auth,
        "resolve_keychain_ref",
        lambda ref, profile: (
            '{"installed": {"client_id": "id", "client_secret": "secret"}}'
        ),
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        auth,
        "_google_browser_flow",
        lambda client, scopes: (
            seen.update(client=client, scopes=scopes)
            or {
                "access_token": "never-print",
                "refresh_token": "also-secret",
                "scope": " ".join(auth.GOOGLE_READONLY_SCOPES),
            }
        ),
    )
    monkeypatch.setattr(
        auth,
        "_google_self_test",
        lambda token: {"emailAddress": "user@example.invalid", "messagesTotal": 7},
    )

    summary = auth.authenticate_google("google-work", self_test=True)

    assert seen["scopes"] == auth.GOOGLE_READONLY_SCOPES
    assert summary == {"emailAddress": "user@example.invalid", "messagesTotal": 7}
    token = json.loads(
        (
            tmp_path / "state/mark-claw/test/secrets/google/google-work/token.json"
        ).read_text()
    )
    assert token["access_token"] == "never-print"


def test_google_auth_rejects_a_non_readonly_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _xdg(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _accounts_config(tmp_path)
    monkeypatch.setattr(
        auth,
        "resolve_keychain_ref",
        lambda ref, profile: (
            '{"installed": {"client_id": "id", "client_secret": "secret"}}'
        ),
    )
    monkeypatch.setattr(
        auth,
        "_google_browser_flow",
        lambda client, scopes: {"access_token": "token", "scope": "gmail.modify"},
    )
    with pytest.raises(auth.AuthError, match="non-readonly"):
        auth.authenticate_google("google-work")


@pytest.mark.parametrize(
    "scope",
    [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly extra",
    ],
)
def test_google_scope_validation_requires_the_exact_phase_one_pair(scope: str) -> None:
    with pytest.raises(auth.AuthError, match="non-readonly"):
        auth._validate_google_token_scopes({"scope": scope})


def test_google_client_rejects_web_client() -> None:
    with pytest.raises(auth.AuthError, match="invalid"):
        auth._parse_google_client('{"web": {"client_id": "id", "client_secret": "x"}}')


def test_graph_auth_uses_configured_device_flow_and_private_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _xdg(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _accounts_config(tmp_path)
    refs = {
        "keychain://mark-claw-test/entra-app-client_id": "client",
        "keychain://mark-claw-test/entra-app-tenant_id": "tenant",
    }
    monkeypatch.setattr(auth, "resolve_keychain_ref", lambda ref, profile: refs[ref])
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        auth,
        "_graph_device_flow",
        lambda client_id, tenant_id: (
            seen.update(client_id=client_id, tenant_id=tenant_id)
            or {"access_token": "token"}
        ),
    )
    monkeypatch.setattr(
        auth, "_graph_self_test", lambda token: {"mail": 200, "drive": 200}
    )

    assert auth.authenticate_graph("graph-work", self_test=True) == {
        "mail": 200,
        "drive": 200,
    }
    assert seen == {"client_id": "client", "tenant_id": "tenant"}
    assert (
        tmp_path / "state/mark-claw/test/secrets/msal/graph-work/token.json"
    ).is_file()


def test_account_config_rejects_unbound_or_missing_auth_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _xdg(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = _accounts_config(tmp_path)
    (cfg / "accounts.yaml").write_text(
        "email: [{id: bad, provider: gmail, auth: {}}]\n"
    )
    with pytest.raises(auth.AuthError, match="configured"):
        auth.load_email_account("bad", "gmail")


def test_telegram_auth_reuses_configured_session_for_self_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _xdg(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = tmp_path / "cfg/mark-claw/test"
    cfg.mkdir(parents=True)
    (cfg / "sources.yaml").write_text(
        """chat:
  telegram:
    api_id_ref: keychain://mark-claw-test/telegram-api-api_id
    api_hash_ref: keychain://mark-claw-test/telegram-api-api_hash
    session: $STATE/secrets/telegram/session.string
"""
    )
    monkeypatch.setattr(auth, "resolve_keychain_ref", lambda ref, profile: "123")
    monkeypatch.setattr(
        auth, "_telegram_login", lambda api_id, api_hash, session: ("session", 4)
    )

    assert auth.authenticate_telegram(self_test=True) == {"dialogs": 4}
    assert (
        tmp_path / "state/mark-claw/test/secrets/telegram/session.string"
    ).read_text() == "session"


def test_telegram_session_read_rejects_a_symlinked_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _xdg(monkeypatch, tmp_path)
    target = root / "secrets/telegram/session.string"
    target.parent.mkdir(parents=True)
    target.parent.unlink() if target.parent.is_symlink() else None
    # Replace the provider directory after the safe top-level initialization.
    target.parent.rmdir()
    target.parent.symlink_to(tmp_path)
    with pytest.raises(auth.AuthError, match="unsafe secret state directory"):
        auth._read_secret_file_if_present("telegram/session.string", "test")


def test_telethon_sync_wrapper_has_a_synchronous_get_dialogs_method() -> None:
    from telethon import TelegramClient

    raw_get_dialogs = TelegramClient.get_dialogs
    from telethon.sync import TelegramClient as SyncTelegramClient

    assert inspect.iscoroutinefunction(raw_get_dialogs)
    assert not inspect.iscoroutinefunction(SyncTelegramClient.get_dialogs)


def test_graph_device_flow_retries_authorization_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [
            {
                "device_code": "private",
                "verification_uri_complete": "https://example.invalid/complete",
                "interval": 1,
                "expires_in": 60,
            },
            {"error": "authorization_pending"},
            {"access_token": "private-token"},
        ]
    )
    monkeypatch.setattr(auth, "_post_form", lambda url, fields: next(replies))
    monkeypatch.setattr("mclaw_core.auth.webbrowser.open", lambda url: True)
    monkeypatch.setattr("mclaw_core.auth.time.sleep", lambda delay: None)

    assert auth._graph_device_flow("client", "tenant") == {
        "access_token": "private-token"
    }


def test_graph_device_flow_slows_down_and_honors_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [
            {
                "device_code": "private",
                "verification_uri_complete": "https://example.invalid/complete",
                "interval": 2,
                "expires_in": 10,
            },
            {"error": "slow_down"},
            {"access_token": "private-token"},
        ]
    )
    sleeps: list[int] = []
    monkeypatch.setattr(auth, "_post_form", lambda url, fields: next(replies))
    monkeypatch.setattr("mclaw_core.auth.webbrowser.open", lambda url: True)
    monkeypatch.setattr("mclaw_core.auth.time.sleep", sleeps.append)
    monkeypatch.setattr("mclaw_core.auth.time.monotonic", lambda: 1.0)

    assert auth._graph_device_flow("client", "tenant") == {
        "access_token": "private-token"
    }
    assert sleeps == [7]


def test_graph_device_flow_stops_at_provider_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [
            {
                "device_code": "private",
                "verification_uri_complete": "https://example.invalid/complete",
                "interval": 1,
                "expires_in": 1,
            },
            {"error": "authorization_pending"},
        ]
    )
    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(auth, "_post_form", lambda url, fields: next(replies))
    monkeypatch.setattr("mclaw_core.auth.webbrowser.open", lambda url: True)
    monkeypatch.setattr("mclaw_core.auth.time.sleep", lambda delay: None)
    monkeypatch.setattr("mclaw_core.auth.time.monotonic", lambda: next(ticks))

    with pytest.raises(auth.AuthError, match="timed out"):
        auth._graph_device_flow("client", "tenant")
