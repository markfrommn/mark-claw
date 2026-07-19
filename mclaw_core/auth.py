"""Authentication support with a narrow, secret-safe persistence boundary.

Provider flows live in this module rather than the CLI so OAuth credentials and
sessions never pass through an agent-facing command surface.  This initial
portion intentionally contains only scope-neutral configuration and secure
state-file handling; provider-specific protocol choices are layered above it.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from typing import cast

import yaml

from . import config_state, paths
from .fetch import get_secret

GOOGLE_READONLY_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)
GRAPH_SCOPES = ("offline_access", "Mail.Read", "Files.Read", "Calendars.Read")


class AuthError(Exception):
    """A safe-to-display authentication failure.

    Callers must not wrap provider exception text in this exception: OAuth and
    MTProto responses can contain credential-bearing URLs or session material.
    """


@dataclass(frozen=True)
class EmailAccount:
    """The minimal account configuration an auth wrapper may consume."""

    account_id: str
    provider: str
    client_ref: str
    token_cache: str
    tenant_ref: str | None = None


def validate_keychain_ref(ref: str, profile: str) -> None:
    """Verify that a config reference is confined to this profile's Keychain.

    This validates only the location; resolution remains inside the individual
    provider wrapper immediately before constructing its client.
    """
    prefix = f"keychain://mark-claw-{profile}/"
    account = ref.removeprefix(prefix)
    if not ref.startswith(prefix) or not account or "/" in account:
        raise AuthError("credential reference must be a profile keychain reference")


def resolve_keychain_ref(ref: str, profile: str) -> str:
    """Resolve a ref only inside this Tier-1 provider authentication wrapper."""
    validate_keychain_ref(ref, profile)
    try:
        return get_secret(ref, profile=profile)
    except Exception as exc:
        raise AuthError("could not resolve configured credential") from exc


def load_email_account(
    account_id: str, provider: str, *, profile: str | None = None
) -> EmailAccount:
    """Load and validate the one configured mail account for an auth flow."""
    active = paths.resolve_profile() if profile is None else profile
    config = paths.config_root(active) / "accounts.yaml"
    try:
        raw = yaml.safe_load(config.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise AuthError("could not read account configuration") from exc
    entries = raw.get("email") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise AuthError("account configuration has no email accounts")
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict)
            and item.get("id") == account_id
            and item.get("provider") == provider
        ),
        None,
    )
    if not isinstance(entry, dict) or not isinstance(entry.get("auth"), dict):
        raise AuthError("requested provider account is not configured")
    auth = cast(dict[str, object], entry["auth"])
    client_ref, token_cache = auth.get("client_ref"), auth.get("token_cache")
    if not isinstance(client_ref, str) or not isinstance(token_cache, str):
        raise AuthError("requested provider account is not configured")
    validate_keychain_ref(client_ref, active)
    token_cache_path(token_cache, "token.json", profile=active)
    tenant_ref = auth.get("tenant_ref")
    if provider == "graph":
        if not isinstance(tenant_ref, str):
            raise AuthError("requested provider account is not configured")
        validate_keychain_ref(tenant_ref, active)
    return EmailAccount(
        account_id, provider, client_ref, token_cache, cast(str | None, tenant_ref)
    )


def _json_bytes(value: Mapping[str, object]) -> bytes:
    """Serialize opaque provider tokens without ever emitting their values."""
    return json.dumps(value, separators=(",", ":")).encode()


def authenticate_google(
    account_id: str, *, self_test: bool = False, profile: str | None = None
) -> dict[str, object] | None:
    """Run a local-loopback OAuth flow for the Phase-1 readonly Google grant."""
    active = paths.resolve_profile() if profile is None else profile
    account = load_email_account(account_id, "gmail", profile=active)
    credential = resolve_keychain_ref(account.client_ref, active)
    client = _parse_google_client(credential)
    token = _google_browser_flow(client, GOOGLE_READONLY_SCOPES)
    _validate_google_token_scopes(token)
    target = token_cache_path(account.token_cache, "token.json", profile=active)
    write_secret_file(
        str(target.relative_to(paths.state_root(active) / "secrets")),
        _json_bytes(token),
        profile=active,
    )
    return _google_self_test(_required_access_token(token)) if self_test else None


def authenticate_graph(
    account_id: str, *, self_test: bool = False, profile: str | None = None
) -> dict[str, object] | None:
    """Run a Graph device-code flow and retain its private refresh cache."""
    active = paths.resolve_profile() if profile is None else profile
    account = load_email_account(account_id, "graph", profile=active)
    client_id = resolve_keychain_ref(account.client_ref, active)
    tenant_id = resolve_keychain_ref(cast(str, account.tenant_ref), active)
    token = _graph_device_flow(client_id, tenant_id)
    target = token_cache_path(account.token_cache, "token.json", profile=active)
    write_secret_file(
        str(target.relative_to(paths.state_root(active) / "secrets")),
        _json_bytes(token),
        profile=active,
    )
    return _graph_self_test(_required_access_token(token)) if self_test else None


def authenticate_telegram(
    *, self_test: bool = False, profile: str | None = None
) -> dict[str, object] | None:
    """Log in once with Telethon and persist only the StringSession in state."""
    active = paths.resolve_profile() if profile is None else profile
    source = _load_telegram_source(active)
    api_id = resolve_keychain_ref(source["api_id_ref"], active)
    api_hash = resolve_keychain_ref(source["api_hash_ref"], active)
    try:
        numeric_api_id = int(api_id)
    except ValueError as exc:
        raise AuthError("configured Telegram api_id is invalid") from exc
    session_path = secret_state_path(source["session"], profile=active)
    existing = _read_secret_file_if_present(
        str(session_path.relative_to(paths.state_root(active) / "secrets")), active
    )
    session, dialog_count = _telegram_login(numeric_api_id, api_hash, existing)
    relative = str(session_path.relative_to(paths.state_root(active) / "secrets"))
    write_secret_file(relative, session.encode(), profile=active)
    return {"dialogs": dialog_count} if self_test else None


def _load_telegram_source(profile: str) -> dict[str, str]:
    path = paths.config_root(profile) / "sources.yaml"
    try:
        raw = yaml.safe_load(path.read_text())
        chat = raw.get("chat") if isinstance(raw, dict) else None
        telegram = chat.get("telegram") if isinstance(chat, dict) else None
    except (OSError, yaml.YAMLError) as exc:
        raise AuthError("could not read Telegram configuration") from exc
    if not isinstance(telegram, dict):
        raise AuthError("Telegram is not configured")
    values: dict[str, str] = {}
    for key in ("api_id_ref", "api_hash_ref", "session"):
        value = telegram.get(key)
        if not isinstance(value, str):
            raise AuthError("Telegram is not configured")
        values[key] = value
    validate_keychain_ref(values["api_id_ref"], profile)
    validate_keychain_ref(values["api_hash_ref"], profile)
    secret_state_path(values["session"], profile=profile)
    return values


def _telegram_login(api_id: int, api_hash: str, session: str) -> tuple[str, int]:
    """Use Telethon's interactive client without exposing session material."""
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError as exc:  # pragma: no cover - dependency installation guard
        raise AuthError(
            "Telethon is unavailable; install project dependencies"
        ) from exc
    # Telethon prompts only when the persisted session is not authorized. Its
    # StringSession is never printed or passed to the CLI.
    client = TelegramClient(
        StringSession(session),
        api_id,
        api_hash,
        device_model="macOS",
        system_version=os.uname().release,
    )
    try:
        client.start()
        dialogs = client.get_dialogs()
        result = client.session.save()
    except Exception as exc:
        raise AuthError("Telegram authentication did not complete") from exc
    finally:
        client.disconnect()
    return result, len(dialogs)


def _parse_google_client(value: str) -> dict[str, str]:
    try:
        raw = json.loads(value)
        client = raw.get("installed")
        if not isinstance(client, dict):
            raise ValueError
        client_id, client_secret = client.get("client_id"), client.get("client_secret")
        if not isinstance(client_id, str) or not isinstance(client_secret, str):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AuthError("configured Google client credential is invalid") from exc
    return {"client_id": client_id, "client_secret": client_secret}


def _required_access_token(token: Mapping[str, object]) -> str:
    value = token.get("access_token")
    if not isinstance(value, str) or not value:
        raise AuthError("provider returned an incomplete token response")
    return value


def _validate_google_token_scopes(token: Mapping[str, object]) -> None:
    """Fail closed if the authorization server granted anything beyond Phase 1."""
    granted = token.get("scope")
    if not isinstance(granted, str):
        raise AuthError("Google token response did not confirm readonly scopes")
    if set(granted.split()) != set(GOOGLE_READONLY_SCOPES):
        raise AuthError("Google token response granted a non-readonly scope")


def _post_form(url: str, fields: Mapping[str, str]) -> dict[str, object]:
    payload = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # RFC 8628 represents authorization_pending as HTTP 400. The parsed
        # protocol code is consumed below and never appears in CLI output.
        raw = exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise AuthError("provider authentication request failed") from exc
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuthError("provider returned an invalid authentication response") from exc
    if not isinstance(value, dict):
        raise AuthError("provider returned an invalid authentication response")
    return cast(dict[str, object], value)


def _google_browser_flow(
    client: Mapping[str, str], scopes: tuple[str, ...]
) -> dict[str, object]:
    """Use a loopback callback; OAuth values remain in this wrapper only."""
    state = secrets.token_urlsafe(32)
    callback: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            callback.update({key: values[0] for key, values in query.items() if values})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authentication complete. You may close this window.")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2callback"
    query = urllib.parse.urlencode(
        {
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    try:
        if not webbrowser.open(f"https://accounts.google.com/o/oauth2/v2/auth?{query}"):
            raise AuthError("could not open browser for Google authentication")
        server.timeout = 300
        server.handle_request()
    finally:
        server.server_close()
    if callback.get("state") != state or not callback.get("code"):
        raise AuthError("Google authentication did not complete")
    return _post_form(
        "https://oauth2.googleapis.com/token",
        {
            "code": callback["code"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )


def _graph_device_flow(client_id: str, tenant_id: str) -> dict[str, object]:
    """Open Microsoft’s complete verification URI without printing device codes."""
    quoted_tenant = urllib.parse.quote(tenant_id, safe="")
    base = f"https://login.microsoftonline.com/{quoted_tenant}/oauth2/v2.0"
    device = _post_form(
        f"{base}/devicecode", {"client_id": client_id, "scope": " ".join(GRAPH_SCOPES)}
    )
    complete = device.get("verification_uri_complete")
    device_code = device.get("device_code")
    if not isinstance(complete, str) or not isinstance(device_code, str):
        raise AuthError("provider returned an invalid device authorization response")
    if not webbrowser.open(complete):
        raise AuthError("could not open browser for Graph authentication")
    # The device code remains local; polling has no user-visible credential output.
    interval = device.get("interval", 5)
    wait = interval if isinstance(interval, int) and interval > 0 else 5
    expires_in = device.get("expires_in")
    if not isinstance(expires_in, int) or expires_in <= 0:
        raise AuthError("provider returned an invalid device authorization response")
    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        response = _post_form(
            f"{base}/token",
            {
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
        )
        if "access_token" in response:
            return response
        error = response.get("error")
        if error == "slow_down":
            wait += 5
        elif error != "authorization_pending":
            raise AuthError("Graph authentication did not complete")
        time.sleep(wait)
    raise AuthError("Graph authentication timed out")


def _get_json(url: str, token: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise AuthError("provider self-test request failed") from exc
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuthError("provider self-test returned invalid data") from exc
    if not isinstance(value, dict):
        raise AuthError("provider self-test returned invalid data")
    return cast(dict[str, object], value)


def _google_self_test(token: str) -> dict[str, object]:
    profile = _get_json("https://gmail.googleapis.com/gmail/v1/users/me/profile", token)
    _get_json("https://gmail.googleapis.com/gmail/v1/users/me/labels", token)
    return {
        "emailAddress": profile.get("emailAddress", "unknown"),
        "messagesTotal": profile.get("messagesTotal", 0),
    }


def _graph_self_test(token: str) -> dict[str, object]:
    me = _get_json("https://graph.microsoft.com/v1.0/me", token)
    _get_json("https://graph.microsoft.com/v1.0/me/messages?$top=1", token)
    _get_json("https://graph.microsoft.com/v1.0/me/drive/root", token)
    return {"upn": me.get("userPrincipalName", "unknown"), "mail": 200, "drive": 200}


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


def secret_state_path(value: str, *, profile: str | None = None) -> Path:
    """Resolve one configured ``$STATE/secrets/...`` file path safely."""
    if not value.startswith("$STATE/"):
        raise AuthError("secret state path must be a relative $STATE/secrets path")
    parts = PurePosixPath(value.removeprefix("$STATE/")).parts
    if (
        len(parts) < 2
        or parts[0] != "secrets"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise AuthError("secret state path must be inside state secrets")
    return paths.state_root(profile) / Path(*parts)


def _open_secret_parent(relative: PurePosixPath, profile: str) -> tuple[int, str]:
    """Open a secret parent through no-follow directory descriptors.

    Creating a directory and then resolving it by pathname leaves a swap window.
    Keeping a descriptor for each component means subsequent read/write calls
    remain anchored to the checked directory even if a hostile local process
    changes a pathname concurrently.
    """
    base = paths.state_root(profile) / "secrets"
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(base, flags)
        for part in relative.parts[:-1]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError as exc:
        raise AuthError("unsafe secret state directory") from exc
    return descriptor, relative.name


def _create_secret_parent(relative: PurePosixPath, profile: str) -> tuple[int, str]:
    """Create private child directories through anchored no-follow descriptors."""
    base = paths.state_root(profile) / "secrets"
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(base, flags)
        for part in relative.parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=descriptor)
            # This applies to the opened inode, never a path an attacker can
            # swap between a check and chmod.
            os.fchmod(child, 0o700)
            os.close(descriptor)
            descriptor = child
    except OSError as exc:
        raise AuthError("unsafe secret state directory") from exc
    return descriptor, relative.name


def _read_secret_file_if_present(relative: str, profile: str) -> str:
    """Read an existing private state artifact without following any symlink."""
    rel = PurePosixPath(relative)
    config_state.init_state_tree(profile=profile)
    try:
        parent, name = _open_secret_parent(rel, profile)
    except AuthError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return ""
        raise
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return ""
        try:
            with os.fdopen(descriptor, "r") as handle:
                return handle.read()
        except OSError as exc:
            raise AuthError("could not read Telegram session state") from exc
    except OSError as exc:
        raise AuthError("could not read Telegram session state") from exc
    finally:
        os.close(parent)


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
    directory_fd, target_name = _create_secret_parent(rel, active_profile)
    temporary_name = f".auth-{secrets.token_hex(16)}"
    try:
        fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        # Explicitly assert the final mode: creation mode is filtered by umask.
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except OSError as exc:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise AuthError("could not securely write authentication state") from exc
    finally:
        os.close(directory_fd)
    return root / "secrets" / Path(*rel.parts)
