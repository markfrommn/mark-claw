"""macOS Keychain secret wrapper — the only place a secret value is handled.

Implements the ``docs/SECRETS.md`` conventions on top of the built-in
``security`` CLI:

* **Service** is fixed per profile: ``mark-claw-<profile>``.
* **Account** is a flattened ``<item>-<field>`` slug — one keychain item per
  secret value.
* Items are created with ``-A`` (always-allow) so headless launchd runs never
  hit a GUI access prompt.

The secret value is **never** placed in ``argv`` (which would leak via ``ps``).
``security add-generic-password`` prompts for the value twice (entry + retype);
we satisfy both prompts by writing the value twice on the child's stdin, so the
only place the plaintext lives is that pipe. Resolution happens here and nowhere
else — no caller passes a secret into an agent prompt or ``claude -p`` context
(CLAUDE.md hard rule: "Secrets never reach agent context").
"""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from pathlib import Path

from . import paths

SECURITY = "security"
AGE = "age"


class SecretError(Exception):
    """A keychain or backup operation failed."""


def service_name(profile: str) -> str:
    """Return the fixed keychain service for ``profile``."""
    return f"{paths.APP_DIR}-{profile}"


def account_slug(item: str, field: str) -> str:
    """Return the flattened ``<item>-<field>`` account slug.

    ``field`` must not contain the ``-`` delimiter: the slug is ``<item>-<field>``
    and the last ``-`` is what splits item from field, so a ``-`` in ``field``
    makes distinct pairs collide (``item="a-b", field="c"`` and
    ``item="a", field="b-c"`` would both yield ``a-b-c``, and ``set_secret``'s
    ``-U`` would silently overwrite). All real fields use ``_`` (``client_id``,
    ``api_hash``, ``tenant_id``), so this rejects nothing in practice. ``item``
    may still contain ``-`` (e.g. ``entra-app``).

    Control characters are rejected in both components: a newline (or other C0
    control) would survive into the keychain account string, but
    ``parse_accounts_for_service`` reads ``dump-keychain`` output line-by-line, so
    such an account splits across lines and never matches — silently omitting the
    credential from ``list_accounts``/``export_backup``, which promise the full
    set.
    """
    if not item or not field:
        raise SecretError("both <item> and <field> are required")
    if "-" in field:
        raise SecretError(
            f"<field> must not contain the '-' delimiter (use '_'): {field!r}"
        )
    for component, label in ((item, "item"), (field, "field")):
        if any(ord(c) < 0x20 for c in component):
            raise SecretError(
                f"<{label}> must not contain control characters: {component!r}"
            )
    return f"{item}-{field}"


def _add_argv(service: str, account: str) -> list[str]:
    """Build the ``add-generic-password`` argv — never contains the value.

    ``-w`` is passed with no following value so ``security`` prompts on stdin;
    ``-U`` makes ``set`` idempotent (update if the item already exists).
    """
    return [
        SECURITY,
        "add-generic-password",
        "-U",
        "-a",
        account,
        "-s",
        service,
        "-A",
        "-w",
    ]


def _find_argv(service: str, account: str) -> list[str]:
    """Build the ``find-generic-password -w`` argv (prints only the value)."""
    return [SECURITY, "find-generic-password", "-a", account, "-s", service, "-w"]


def _read_secret_value(prompt: str = "Secret value: ") -> str:
    """Read the secret from stdin (piped) or an unechoed prompt (tty)."""
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(prompt)
    data = sys.stdin.readline() if sys.stdin is not None else ""
    return data.rstrip("\n")


def set_secret(
    item: str,
    field: str,
    *,
    profile: str,
    value: str | None = None,
) -> None:
    """Create or update the keychain item for ``<item>-<field>``.

    ``value`` is read from stdin/prompt when not supplied. It is written twice
    to ``security``'s stdin (entry + retype) and never appears in ``argv``.
    """
    service = service_name(profile)
    account = account_slug(item, field)
    secret = value if value is not None else _read_secret_value()
    if not secret:
        # Empty stdin/EOF (or an explicit empty value) must not silently store a
        # blank secret — that would look like a success but leave no usable
        # credential. Fail loudly instead.
        raise SecretError(
            f"refusing to store an empty secret for {account} on {service}"
        )
    # security prompts twice: password data, then retype.
    child_input = f"{secret}\n{secret}\n"
    try:
        proc = subprocess.run(
            _add_argv(service, account),
            input=child_input,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - security is built-in
        raise SecretError(f"`{SECURITY}` not found on PATH") from exc
    if proc.returncode != 0:
        raise SecretError(
            f"failed to set {account} on {service}: {proc.stderr.strip()}"
        )


def get_secret(item: str, field: str, *, profile: str) -> str:
    """Return the secret value for ``<item>-<field>`` (stdout of ``find``)."""
    service = service_name(profile)
    account = account_slug(item, field)
    try:
        proc = subprocess.run(
            _find_argv(service, account),
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - security is built-in
        raise SecretError(f"`{SECURITY}` not found on PATH") from exc
    if proc.returncode != 0:
        raise SecretError(
            f"no secret {account} on {service}: {proc.stderr.strip()}"
        )
    return proc.stdout.rstrip("\n")


def _dump_argv() -> list[str]:
    """Build a ``dump-keychain`` argv (attributes only — no ``-d``, no prompt)."""
    return [SECURITY, "dump-keychain"]


def parse_accounts_for_service(dump_output: str, service: str) -> list[str]:
    """Extract account slugs whose ``svce`` matches ``service`` from a dump.

    ``security dump-keychain`` emits one record per item; a record begins with a
    ``keychain: ...`` line and lists ``"acct"<blob>=...`` and ``"svce"<blob>=...``
    attributes. We collect the ``acct`` of every record whose ``svce`` equals
    ``service``.
    """
    accounts: list[str] = []
    cur_acct: str | None = None
    cur_svce: str | None = None

    def flush() -> None:
        if cur_svce == service and cur_acct is not None:
            accounts.append(cur_acct)

    for line in dump_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("keychain:"):
            flush()
            cur_acct = None
            cur_svce = None
            continue
        if '"acct"' in stripped:
            cur_acct = _blob_value(stripped)
        elif '"svce"' in stripped:
            cur_svce = _blob_value(stripped)
    flush()
    return sorted(set(a for a in accounts if a is not None))


def _blob_value(line: str) -> str | None:
    """Parse the value of a ``"attr"<blob>="value"`` dump line (``<NULL>`` → None)."""
    _, _, rhs = line.partition("=")
    rhs = rhs.strip()
    if rhs == "<NULL>" or not rhs:
        return None
    if rhs.startswith('"') and rhs.endswith('"'):
        return rhs[1:-1]
    return rhs


def list_accounts(*, profile: str) -> list[str]:
    """Return the account slugs stored under this profile's service."""
    service = service_name(profile)
    try:
        proc = subprocess.run(_dump_argv(), text=True, capture_output=True)
    except FileNotFoundError as exc:  # pragma: no cover - security is built-in
        raise SecretError(f"`{SECURITY}` not found on PATH") from exc
    # dump-keychain returns 0 on success (it enumerates the unlocked login
    # keychain). Any non-zero exit means enumeration failed (locked / unreachable
    # keychain), so fail closed: returning a partial or empty set here would let
    # export_backup silently write a backup that omits credentials while claiming
    # to hold the "full credential set."
    if proc.returncode != 0:
        raise SecretError(
            f"keychain enumeration failed for {service}: {proc.stderr.strip()}"
        )
    return parse_accounts_for_service(proc.stdout, service)


def secrets_dir(*, profile: str) -> Path:
    """Return ``<state>/secrets`` for ``profile`` (not created here)."""
    return paths.state_root(profile) / "secrets"


def build_secret_payload(*, profile: str) -> str:
    """Serialize the full credential set (account → value) as JSON text."""
    service = service_name(profile)
    payload: dict[str, str] = {}
    for account in list_accounts(profile=profile):
        # Propagate a read failure: a single unreadable item must abort the
        # export, not be silently omitted from a backup that promises the full
        # credential set.
        payload[account] = _get_by_account(service, account)
    return json.dumps({"service": service, "secrets": payload}, indent=2)


def _age_argv(target: Path, recipient: str | None) -> list[str]:
    """Build the ``age`` argv. Passphrase mode (``-p``) prompts on the tty; the
    passphrase never touches ``argv`` or disk. Recipient mode (``-r``) is the
    scriptable path used by tests.
    """
    if recipient is not None:
        return [AGE, "-r", recipient, "-o", str(target)]
    return [AGE, "-p", "-o", str(target)]


def export_backup(
    *,
    profile: str,
    out_path: Path | None = None,
    recipient: str | None = None,
) -> Path:
    """Write an age-encrypted blob of the full credential set to ``backup.age``.

    The credential set is serialized as JSON and piped to ``age`` on stdin — the
    plaintext never lands on disk. By default ``age -p`` prompts the operator for
    a passphrase on the tty, which is **never written to disk** (not in config,
    state, or argv). Passing ``recipient`` selects age recipient mode instead,
    the only non-interactive path (used by tests). ``secrets/`` is created at
    ``0700`` if missing.
    """
    target = (
        out_path
        if out_path is not None
        else secrets_dir(profile=profile) / "backup.age"
    )
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    blob = build_secret_payload(profile=profile)
    try:
        proc = subprocess.run(
            _age_argv(target, recipient),
            input=blob,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise SecretError(
            f"`{AGE}` binary not found on PATH; install age to use export"
        ) from exc
    if proc.returncode != 0:
        raise SecretError(f"age export failed: {proc.stderr.strip()}")
    return target


def _get_by_account(service: str, account: str) -> str:
    """Return the value for a raw account slug under ``service``."""
    try:
        proc = subprocess.run(
            _find_argv(service, account),
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - security is built-in
        raise SecretError(f"`{SECURITY}` not found on PATH") from exc
    if proc.returncode != 0:
        raise SecretError(f"no secret {account} on {service}")
    return proc.stdout.rstrip("\n")
