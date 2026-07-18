"""Keychain secret wrapper — argv safety, stdin handling, parsing, export.

Most tests mock the ``security`` and ``age`` subprocesses (asserting argv never
carries a secret value) and never touch the real login keychain. The one
exception is :func:`test_security_real_roundtrip`, which does a real
create/read/delete under a throwaway service so nothing collides with real items
and everything is cleaned up — it proves ``security add-generic-password -w``
reads the value from stdin (our double-feed assumption), not the tty.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from mclaw_core import secret

SECRET_VALUE = "sup3r-s3cret-token-value"


def test_service_and_account_slug() -> None:
    assert secret.service_name("mark") == "mark-claw-mark"
    assert secret.account_slug("google", "client_secret") == "google-client_secret"


def test_account_slug_requires_both_parts() -> None:
    with pytest.raises(secret.SecretError):
        secret.account_slug("google", "")
    with pytest.raises(secret.SecretError):
        secret.account_slug("", "field")


def test_account_slug_rejects_dash_in_field() -> None:
    # A '-' in <field> would make distinct (item, field) pairs collide.
    with pytest.raises(secret.SecretError, match="delimiter"):
        secret.account_slug("a", "b-c")


def test_account_slug_allows_dash_in_item() -> None:
    # <item> may contain '-'; the last '-' splits item/field.
    assert secret.account_slug("entra-app", "client_id") == "entra-app-client_id"


def test_add_argv_never_contains_value() -> None:
    argv = secret._add_argv("mark-claw-mark", "google-token")
    assert SECRET_VALUE not in argv
    assert argv[0] == "security"
    assert "add-generic-password" in argv
    assert "-A" in argv  # always-allow, per SECRETS.md
    assert argv[-1] == "-w"  # -w last, no value follows it
    assert "-U" in argv  # idempotent set


def test_find_argv_shape() -> None:
    argv = secret._find_argv("mark-claw-mark", "google-token")
    assert argv == [
        "security",
        "find-generic-password",
        "-a",
        "google-token",
        "-s",
        "mark-claw-mark",
        "-w",
    ]


def test_set_secret_feeds_value_via_stdin_twice_not_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, input=None, text=None, capture_output=None):
        captured["argv"] = argv
        captured["input"] = input
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    secret.set_secret("google", "token", profile="mark", value=SECRET_VALUE)

    argv = captured["argv"]
    assert isinstance(argv, list)
    # The secret must appear nowhere in argv (would leak via `ps`).
    assert all(SECRET_VALUE not in str(part) for part in argv)
    # security prompts twice (entry + retype): value written twice on stdin.
    assert captured["input"] == f"{SECRET_VALUE}\n{SECRET_VALUE}\n"


def test_set_secret_reads_from_stdin_when_no_value(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, input=None, text=None, capture_output=None):
        captured["input"] = input
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    fake_stdin = SimpleNamespace(
        isatty=lambda: False,
        readline=lambda: f"{SECRET_VALUE}\n",
    )
    monkeypatch.setattr("mclaw_core.secret.sys.stdin", fake_stdin)
    secret.set_secret("slack", "xoxp", profile="mark")
    assert captured["input"] == f"{SECRET_VALUE}\n{SECRET_VALUE}\n"


def test_set_secret_rejects_empty_value(monkeypatch) -> None:
    # An explicit empty value must be rejected before any subprocess runs.
    def boom(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("security must not be invoked for an empty secret")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(secret.SecretError, match="empty"):
        secret.set_secret("a", "b", profile="mark", value="")


def test_set_secret_rejects_empty_stdin(monkeypatch) -> None:
    # A piped empty stdin / EOF must not silently store a blank secret.
    def boom(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("security must not be invoked for an empty secret")

    monkeypatch.setattr(subprocess, "run", boom)
    fake_stdin = SimpleNamespace(isatty=lambda: False, readline=lambda: "")
    monkeypatch.setattr("mclaw_core.secret.sys.stdin", fake_stdin)
    with pytest.raises(secret.SecretError, match="empty"):
        secret.set_secret("a", "b", profile="mark")


def test_set_secret_raises_on_failure(monkeypatch) -> None:
    def fake_run(argv, input=None, text=None, capture_output=None):
        return SimpleNamespace(returncode=45, stdout="", stderr="item exists")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(secret.SecretError):
        secret.set_secret("a", "b", profile="mark", value="x")


def test_get_secret_returns_stdout(monkeypatch) -> None:
    def fake_run(argv, text=None, capture_output=None):
        assert "find-generic-password" in argv
        return SimpleNamespace(returncode=0, stdout=f"{SECRET_VALUE}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert secret.get_secret("google", "token", profile="mark") == SECRET_VALUE


def test_get_secret_raises_when_missing(monkeypatch) -> None:
    def fake_run(argv, text=None, capture_output=None):
        return SimpleNamespace(returncode=44, stdout="", stderr="not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(secret.SecretError):
        secret.get_secret("google", "token", profile="mark")


DUMP_SAMPLE = """\
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="google-token"
    "svce"<blob>="mark-claw-mark"
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="slack-xoxp"
    "svce"<blob>="mark-claw-mark"
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="unrelated"
    "svce"<blob>="some-other-service"
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>=<NULL>
    "svce"<blob>="mark-claw-mark"
"""


def test_parse_accounts_filters_by_service() -> None:
    accounts = secret.parse_accounts_for_service(DUMP_SAMPLE, "mark-claw-mark")
    assert accounts == ["google-token", "slack-xoxp"]


def test_parse_accounts_other_profile_excluded() -> None:
    accounts = secret.parse_accounts_for_service(DUMP_SAMPLE, "mark-claw-work")
    assert accounts == []


def test_list_accounts_uses_dump(monkeypatch) -> None:
    def fake_run(argv, text=None, capture_output=None):
        assert argv == ["security", "dump-keychain"]
        return SimpleNamespace(returncode=0, stdout=DUMP_SAMPLE, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert secret.list_accounts(profile="mark") == ["google-token", "slack-xoxp"]


def test_list_accounts_fails_closed_on_unreachable_keychain(monkeypatch) -> None:
    # Non-zero exit + empty stdout = enumeration failed; must NOT return [].
    def fake_run(argv, text=None, capture_output=None):
        return SimpleNamespace(returncode=1, stdout="", stderr="keychain locked")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(secret.SecretError, match="enumeration failed"):
        secret.list_accounts(profile="mark")


def test_list_accounts_parses_when_records_printed_despite_nonzero(monkeypatch) -> None:
    # Non-zero exit but records WERE printed → parse what we got (intended).
    def fake_run(argv, text=None, capture_output=None):
        return SimpleNamespace(returncode=1, stdout=DUMP_SAMPLE, stderr="partial")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert secret.list_accounts(profile="mark") == ["google-token", "slack-xoxp"]


def test_age_argv_recipient_and_passphrase_modes() -> None:
    out = Path("/tmp/backup.age")
    assert secret._age_argv(out, "age1recipient") == [
        "age",
        "-r",
        "age1recipient",
        "-o",
        str(out),
    ]
    assert secret._age_argv(out, None) == ["age", "-p", "-o", str(out)]


def test_export_creates_secrets_dir_0700_and_writes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: ["a-b"])
    monkeypatch.setattr(secret, "_get_by_account", lambda service, account: "val")

    captured: dict[str, object] = {}

    def fake_run(argv, input=None, text=None, capture_output=None):
        captured["argv"] = argv
        captured["input"] = input
        Path(argv[-1]).write_text("age-encryption.org/v1\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = tmp_path / "secrets" / "backup.age"
    result = secret.export_backup(profile="mark", out_path=out, recipient="age1r")

    assert result == out
    assert out.exists()
    # secrets/ parent created at 0700.
    assert (out.parent.stat().st_mode & 0o777) == 0o700
    # Plaintext JSON piped on stdin, not written to argv.
    assert '"a-b"' in str(captured["input"])
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert all("val" not in str(p) for p in argv)


def test_export_raises_when_age_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(secret, "build_secret_payload", lambda *, profile: "{}")

    def fake_run(argv, input=None, text=None, capture_output=None):
        raise FileNotFoundError("age")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(secret.SecretError, match="age"):
        secret.export_backup(profile="mark", out_path=tmp_path / "b.age")


@pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None,
    reason="LOUD SKIP: `age`/`age-keygen` not installed; real export round-trip "
    "not exercised. Install age to run this test.",
)
def test_export_real_age_roundtrip(monkeypatch, tmp_path) -> None:
    # Generate a throwaway recipient so age runs non-interactively.
    keygen = subprocess.run(
        ["age-keygen"], capture_output=True, text=True, check=True
    )
    recipient = ""
    for line in keygen.stdout.splitlines():
        if line.startswith("# public key:"):
            recipient = line.split(":", 1)[1].strip()
    assert recipient
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: ["a-b"])
    monkeypatch.setattr(secret, "_get_by_account", lambda service, account: "val")
    out = tmp_path / "secrets" / "backup.age"
    secret.export_backup(profile="mark", out_path=out, recipient=recipient)
    assert out.read_text().startswith("age-encryption.org/v1")


@pytest.mark.skipif(
    shutil.which("security") is None,
    reason="`security` CLI not present (non-macOS); real keychain round-trip "
    "cannot run here.",
)
def test_security_real_roundtrip() -> None:
    """Real create/read/delete against the login keychain, under a throwaway
    service so it can never collide with real items and always cleans up.

    This is the only test that exercises the actual `security` binary. It proves
    `add-generic-password -w` reads the value from stdin (our double-feed), not
    the controlling tty — if the double-feed assumption were wrong this would
    hang or store the wrong value, which is a real bug to surface, not skip.
    """
    # Unique throwaway profile → service `mark-claw-pytest-<short-uuid>`.
    throwaway_profile = f"pytest-{uuid.uuid4().hex[:12]}"
    service = secret.service_name(throwaway_profile)
    assert service.startswith("mark-claw-pytest-")
    item, field = "roundtrip", "token"
    account = secret.account_slug(item, field)
    known = f"value-{uuid.uuid4().hex}"

    try:
        secret.set_secret(item, field, profile=throwaway_profile, value=known)
        # Read it back through the real `security find-generic-password -w`.
        assert secret.get_secret(item, field, profile=throwaway_profile) == known
    finally:
        # Always delete, even if an assertion above failed.
        subprocess.run(
            [
                secret.SECURITY,
                "delete-generic-password",
                "-a",
                account,
                "-s",
                service,
            ],
            capture_output=True,
            text=True,
        )

    # After deletion the item must be gone.
    gone = subprocess.run(
        secret._find_argv(service, account),
        capture_output=True,
        text=True,
    )
    assert gone.returncode != 0
