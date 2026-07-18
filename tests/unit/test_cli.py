"""CLI dispatch, stubs, and the doctor report."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from mclaw_core import cli, secret

#: A settings.yaml body whose only variable is the vault path. Used to simulate
#: the operator having set their vault after `doctor --init` wrote the skeleton.
_VALID_SETTINGS_BODY = """\
vault:
  path: {vault}
timezone: ""
profile:
  display_name: ""
runtime:
  billing: subscription
  models:
    triage-judge: ""
limits:
  max_backfill_pages: 5
  alert_rate_cap: 5
  relabel_window_days: 14
"""


def _write_valid_settings(path: Path, vault: str) -> None:
    path.write_text(_VALID_SETTINGS_BODY.format(vault=vault))


def _stub_keychain_reachable(monkeypatch) -> None:
    """Stub the keychain probe so tests never touch the real login keychain.

    Patching the canonical ``secret`` module attribute is what counts: the
    doctor imports the module object (``from . import secret``), so its
    ``secret.list_accounts`` lookup resolves to this same patched attribute.
    """
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: [])


def _xdg(monkeypatch, tmp_path) -> tuple[Path, Path]:
    """Point config + state roots at a tmp XDG layout; return (cfg, st) roots."""
    cfg = tmp_path / "cfg"
    st = tmp_path / "st"
    monkeypatch.setenv("MCLAW_PROFILE", "mark")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(st))
    return cfg, st


def test_help_lists_all_subcommands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("doctor", "auth", "secret", "exclusions", "fetch", "ingest", "guard"):
        assert name in out


def test_no_command_prints_help_and_returns_nonzero(capsys) -> None:
    assert cli.main([]) == 1


def test_doctor_runs_and_reports(monkeypatch, capsys, tmp_path) -> None:
    # Roots missing → short-circuit with a MISSING report, exit 0, no keychain
    # touch. This is the pre-init expected state.
    _xdg(monkeypatch, tmp_path)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "profile: mark" in out
    assert "MISSING" in out  # roots don't exist yet — must not crash
    assert "--init" in out  # the hint points at the fix


def test_doctor_flags_missing_config_files_when_roots_exist(
    monkeypatch, capsys, tmp_path
) -> None:
    # Roots exist but are empty: the deep checklist runs and FAILs on the
    # missing config files. (Replaces the old "empty roots → green" assertion;
    # the green path now requires a real init — see test_doctor_green_after_init.)
    cfg, st = _xdg(monkeypatch, tmp_path)
    (cfg / "mark-claw" / "mark").mkdir(parents=True)
    (st / "mark-claw" / "mark").mkdir(parents=True)
    _stub_keychain_reachable(monkeypatch)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1  # settings.yaml missing → hard FAIL
    assert "settings.yaml" in out
    assert "FAIL" in out
    assert "MISSING" not in out  # roots themselves exist


def test_doctor_init_creates_both_trees(monkeypatch, capsys, tmp_path) -> None:
    cfg, st = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    out = capsys.readouterr().out
    cfg_root = cfg / "mark-claw" / "mark"
    st_root = st / "mark-claw" / "mark"
    for name in (
        "settings.yaml",
        "accounts.yaml",
        "sources.yaml",
        "exclusions.yaml",
        "local-whitelist.yaml",
        "hygiene-patterns.txt",
    ):
        assert (cfg_root / name).is_file(), f"init did not create {name}"
    for rel in (
        "cursors",
        "spool",
        "spool/ephemeral",
        "runs",
        "contacts",
        "changelog",
        "quarantine",
        "secrets",
        "locks",
        "logs",
    ):
        assert (st_root / rel).is_dir(), f"init did not create state dir {rel}"
    assert "profile: mark" in out


def test_doctor_init_is_idempotent(monkeypatch, capsys, tmp_path) -> None:
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    capsys.readouterr()
    # Second init must not raise, not duplicate, and not overwrite config.
    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    before = settings.read_text()
    rc = cli.main(["doctor", "--init"])
    # Under --init the deferred vault-unset FAIL is dropped, so exit is
    # deterministically 0; the tighter bound means a regression of the --init
    # exit-code path can't hide behind the loose (0,1).
    assert rc == 0
    assert settings.read_text() == before


def test_doctor_init_does_not_overwrite_operator_config(
    monkeypatch, capsys, tmp_path
) -> None:
    # Config is human-authored; init must never overwrite an existing file.
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    authored = "operator-authored: true\nvault: {path: /real}\n"
    settings.write_text(authored)
    cli.main(["doctor", "--init"])
    assert settings.read_text() == authored


def test_doctor_rebuild_loop(monkeypatch, capsys, tmp_path) -> None:
    """Wipe state → doctor --init → doctor exits 0 green (state rebuildable).

    Simulates the §B2 AC `rm -rf state && mclaw doctor --init && mclaw doctor`:
    config persists (operator has set the vault path), state is wiped and
    re-created by init, then doctor validates green.
    """
    cfg, st = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # skeleton config; vault empty → FAIL on vault
    # Operator sets an absolute vault path (dir need not exist — that's WARN).
    _write_valid_settings(
        cfg / "mark-claw" / "mark" / "settings.yaml", str(tmp_path / "vault")
    )
    capsys.readouterr()
    # Wipe state, re-init (state only; config untouched), doctor green.
    shutil.rmtree(st)
    assert cli.main(["doctor", "--init"]) == 0
    capsys.readouterr()
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "FAIL" not in out  # WARN on the absent vault dir is allowed, not FAIL


def test_doctor_state_dirs_are_0700_after_init(monkeypatch, tmp_path) -> None:
    _cfg, st = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    st_root = st / "mark-claw" / "mark"
    for rel in ("secrets", "quarantine", "spool/ephemeral"):
        mode = (st_root / rel).stat().st_mode & 0o777
        assert mode == 0o700, f"{rel}/ expected 0700, got {oct(mode)}"


def test_doctor_flags_malformed_yaml(monkeypatch, capsys, tmp_path) -> None:
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    settings.write_text("vault: [unclosed\n")  # malformed YAML
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "settings.yaml" in out
    assert "FAIL" in out


def test_doctor_flags_non_mapping_yaml(monkeypatch, capsys, tmp_path) -> None:
    """A config file whose top-level YAML is not a mapping (e.g. a bare list)
    is a hard FAIL with the non-mapping detail — covers the
    ``not isinstance(data, dict)`` branch of ``_check_config_file``."""
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    accounts = cfg / "mark-claw" / "mark" / "accounts.yaml"
    accounts.write_text("- foo\n")  # parses to a list, not a mapping
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1  # non-mapping top level → hard FAIL
    assert "accounts.yaml" in out
    assert "FAIL" in out
    assert "top-level is not a mapping" in out


def test_doctor_flags_mapping_missing_required_keys(
    monkeypatch, capsys, tmp_path
) -> None:
    """A mapping that parses but is missing a required top-level key is a hard
    FAIL with the missing-keys detail — covers the ``missing = [...]`` branch
    of ``_check_config_file``."""
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    # Only `vault` present; timezone/profile/runtime/limits all missing.
    settings.write_text("vault: {}\n")
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1  # missing required keys → hard FAIL
    assert "settings.yaml" in out
    assert "FAIL" in out
    assert "missing keys" in out
    # Each absent required key is named in the detail.
    for key in ("timezone", "profile", "runtime", "limits"):
        assert key in out


def test_doctor_vault_unset_fails(monkeypatch, capsys, tmp_path) -> None:
    _cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # skeleton leaves vault.path empty
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1  # unset vault → hard FAIL
    assert "vault path" in out
    assert "FAIL" in out


def test_doctor_init_exits_0_on_fresh_profile_with_vault_unset(
    monkeypatch, capsys, tmp_path
) -> None:
    """``--init`` is a bootstrap command: scaffolding success is the exit code.

    On a fresh/unconfigured profile the skeleton ``settings.yaml`` leaves
    ``vault.path`` empty, so the validator reports ``vault path [FAIL] unset``.
    That FAIL is a deferred downstream config gap (the vault is owned by
    C1/DEV-18), not a scaffolding failure — ``doctor --init`` must still exit 0.
    The full checklist still prints so the operator sees what's left.
    """
    _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    rc = cli.main(["doctor", "--init"])
    out = capsys.readouterr().out
    assert rc == 0
    # The deferred vault gap is still surfaced as information...
    assert "vault path" in out
    assert "FAIL" in out  # ...it just doesn't fail the bootstrap exit code.


def test_doctor_init_malformed_config_is_nonzero(
    monkeypatch, capsys, tmp_path
) -> None:
    """``--init`` must not mask a malformed config.

    ``settings.yaml`` that fails to parse is a non-deferred hard FAIL, so
    ``doctor --init`` exits nonzero — only the deferred vault-unset failure is
    exempted under ``--init``. Pre-place a malformed settings.yaml so init
    reuses it (non-destructive), then validation must FAIL hard.
    """
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("vault: [unclosed\n")  # malformed YAML
    rc = cli.main(["doctor", "--init"])
    out = capsys.readouterr().out
    assert rc != 0  # malformed config → nonzero, NOT masked by --init
    assert "settings.yaml" in out
    assert "FAIL" in out


def test_doctor_init_missing_security_binary_is_nonzero(
    monkeypatch, capsys, tmp_path
) -> None:
    """``--init`` must not mask a missing ``security`` binary.

    The keychain check probes ``shutil.which(secret.SECURITY)``; a missing
    binary is a non-deferred hard FAIL, so ``doctor --init`` exits nonzero
    even though scaffolding itself succeeded.
    """
    _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: None)
    rc = cli.main(["doctor", "--init"])
    out = capsys.readouterr().out
    assert rc != 0  # missing security → nonzero, NOT masked by --init
    assert "keychain" in out
    assert "FAIL" in out


def test_doctor_init_symlinked_secure_dir_is_nonzero(
    monkeypatch, capsys, tmp_path
) -> None:
    """``--init`` must not mask a symlinked secure dir.

    ``init_state_tree`` refuses the symlink at ``secrets/`` with
    :class:`StateInitError` (fail-closed); the doctor swallows it and the
    validation pass re-flags the symlink via ``_check_perm`` as a
    non-deferred FAIL, so ``doctor --init`` exits nonzero rather than crashing
    or masking the failure.
    """
    _cfg, st = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # scaffold a real state tree
    capsys.readouterr()
    st_root = st / "mark-claw" / "mark"
    shutil.rmtree(st_root / "secrets")
    target = tmp_path / "attacker-controlled"
    target.mkdir()
    (st_root / "secrets").symlink_to(target)
    rc = cli.main(["doctor", "--init"])
    out = capsys.readouterr().out
    assert rc != 0  # symlinked secure dir → nonzero, NOT masked by --init
    assert "perms secrets/" in out
    assert "FAIL" in out


def test_doctor_vault_absolute_but_absent_warns_exit0(
    monkeypatch, capsys, tmp_path
) -> None:
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    # Absolute path that does NOT exist → WARN (C1/DEV-18 creates the vault),
    # not FAIL, so the overall exit stays 0.
    _write_valid_settings(
        cfg / "mark-claw" / "mark" / "settings.yaml",
        str(tmp_path / "does-not-exist-vault"),
    )
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vault path" in out
    assert "WARN" in out


def test_doctor_vault_existing_dir_ok(monkeypatch, capsys, tmp_path) -> None:
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])
    vault_dir = tmp_path / "real-vault"
    vault_dir.mkdir()
    _write_valid_settings(
        cfg / "mark-claw" / "mark" / "settings.yaml", str(vault_dir)
    )
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vault path" in out


def test_doctor_keychain_reachable_never_prints_account(
    monkeypatch, capsys, tmp_path
) -> None:
    # The keychain check must report reachability without leaking any account
    # slug (or value) into the doctor output.
    _xdg(monkeypatch, tmp_path)
    sentinel = "SUPERSECRET-account-slug-never-in-output"
    monkeypatch.setattr(
        secret, "list_accounts", lambda *, profile: [f"google-{sentinel}"]
    )
    cli.main(["doctor", "--init"])
    out = capsys.readouterr().out
    assert "keychain" in out
    assert "[ok]" in out
    assert sentinel not in out


def test_doctor_keychain_missing_security_binary_is_hard_fail(
    monkeypatch, capsys, tmp_path
) -> None:
    """A missing ``security`` binary must FAIL hard and exit nonzero.

    The probe is ``shutil.which(secret.SECURITY)``; ``list_accounts`` catches
    ``FileNotFoundError`` internally and re-raises it as :class:`SecretError`,
    so relying on the exception would let the missing-binary case fall into the
    soft WARN branch and the doctor would exit 0 with keychain integration dead.
    Bare ``doctor`` (not ``--init``) so the FAIL contributes to the exit code.
    """
    _cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # scaffold so the deep checklist runs
    capsys.readouterr()
    # Now hide the binary: which() → None must short-circuit to a hard FAIL
    # without ever calling list_accounts.
    monkeypatch.setattr("shutil.which", lambda name: None)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "keychain" in out
    assert "FAIL" in out


def test_doctor_keychain_locked_is_warn(monkeypatch, capsys, tmp_path) -> None:
    """``security`` present (``which`` returns a path) but the keychain is
    unreachable (``list_accounts`` raises ``SecretError``) stays a soft WARN
    and keeps the doctor exit at 0 — covers the SurvivingPath+SecretError path
    after the dead ``FileNotFoundError`` branch was removed."""
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # green scaffold
    # Set a valid vault so the only non-ok check is the keychain WARN.
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_valid_settings(
        cfg / "mark-claw" / "mark" / "settings.yaml", str(vault_dir)
    )
    capsys.readouterr()
    # Real binary on PATH (deterministic), but keychain enumeration fails.
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/security")

    def _locked(*, profile):
        raise secret.SecretError("keychain locked")

    monkeypatch.setattr(secret, "list_accounts", _locked)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "keychain" in out
    assert "WARN" in out


def test_doctor_config_file_unreadable_perms_is_hard_fail(
    monkeypatch, capsys, tmp_path
) -> None:
    """A config file that exists but cannot be read (PermissionError) must FAIL
    hard, exit nonzero, and not raise a traceback. POSIX ``root`` bypasses the
    ``0o000`` mode, so skip under root."""
    pytest.importorskip("os")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses mode 0o000 — cannot exercise PermissionError")
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # scaffold
    capsys.readouterr()
    accounts = cfg / "mark-claw" / "mark" / "accounts.yaml"
    os.chmod(accounts, 0o000)
    try:
        rc = cli.main(["doctor"])
        out = capsys.readouterr().out
    finally:
        # Restore read/write so pytest's tmp_path cleanup can rm it.
        os.chmod(accounts, stat.S_IRUSR | stat.S_IWUSR)
    assert rc == 1
    assert "accounts.yaml" in out
    assert "FAIL" in out
    assert "unreadable" in out


def test_doctor_config_file_invalid_utf8_is_hard_fail(
    monkeypatch, capsys, tmp_path
) -> None:
    """A config file containing invalid UTF-8 bytes must FAIL hard, exit
    nonzero, and not raise a traceback — covers the ``UnicodeDecodeError``
    branch of the widened handler."""
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # scaffold
    capsys.readouterr()
    accounts = cfg / "mark-claw" / "mark" / "accounts.yaml"
    # 0xFF is invalid in UTF-8 (and any continuation byte without a leader).
    accounts.write_bytes(b"\xff\xfe bad bytes\n")
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "accounts.yaml" in out
    assert "FAIL" in out
    assert "unreadable" in out


def test_doctor_secure_dir_symlink_is_hard_fail(
    monkeypatch, capsys, tmp_path
) -> None:
    """A symlink at a fail-closed secure dir (``secrets/``) must FAIL the
    ``perms secrets/`` check hard and exit nonzero — the doctor must not follow
    the link (no chmod on the target, no treating the target as a valid secure
    dir). The ``O_NOFOLLOW`` anchor refuses the symlink at the final component;
    the mode read never resolves the link, and the target's mode is left
    unchanged."""
    _cfg, st = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # scaffold real state tree
    capsys.readouterr()
    st_root = st / "mark-claw" / "mark"
    # Replace secrets/ with a symlink to an unrelated directory outside the
    # state tree. rmtree is safe — init created it with mode 0700 just above.
    shutil.rmtree(st_root / "secrets")
    target = tmp_path / "attacker-controlled"
    target.mkdir()
    (st_root / "secrets").symlink_to(target)
    # Pin the target to a non-0700 mode explicitly so the unchanged assertion
    # below is rigorous (proves the doctor never fchmod'd the link's target).
    target.chmod(0o755)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "perms secrets/" in out
    assert "FAIL" in out
    assert "symlink" in out
    # The target's mode must be unchanged (still 0o755, NOT mutated to 0700).
    assert (target.stat().st_mode & 0o777) == 0o755
    assert (target.stat().st_mode & 0o777) != 0o700


@pytest.mark.parametrize("provider", ["google", "graph", "telegram"])
def test_auth_is_stub_nonzero(capsys, provider) -> None:
    rc = cli.main(["auth", provider])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not implemented" in err
    assert "DEV-31" in err


@pytest.mark.parametrize("command", ["exclusions", "fetch", "ingest"])
def test_plain_stubs_nonzero(capsys, command) -> None:
    rc = cli.main([command])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not implemented" in err


def test_guard_bare_no_subaction_is_nonzero_stub(capsys) -> None:
    """``mclaw guard`` with no sub-action exits non-zero with the stub message.

    ``guard scan-vault`` is implemented (B4 / DEV-15); other potential actions
    remain stubs, and bare ``guard`` falls through to the stub dispatcher.
    """
    rc = cli.main(["guard"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not implemented" in err


def test_guard_scan_vault_construction_failure_exits_nonzero(
    monkeypatch, capsys, tmp_path
) -> None:
    """``guard scan-vault`` exits 1 on guard construction failure (§B4).

    The scan's own contract distinguishes "0 findings → exit 0" (informational
    spot-check) from "guard construction failure → exit 1" (a broken
    ``exclusions.yaml`` means the guard cannot compile a fail-closed policy —
    the operator must fix config before the spot-check is meaningful). The
    empty/clean/findings paths cover exit 0; this test covers the exit-1 path
    via a malformed ``exclusions.yaml`` (an entry with an unknown tier).
    """
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # skeleton config + state
    capsys.readouterr()

    # A configured vault that exists as a dir — required so the scan reaches
    # guard construction (no vault short-circuits to "0 findings", exit 0).
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_valid_settings(cfg / "mark-claw" / "mark" / "settings.yaml", str(vault))

    # Malformed exclusions: an unknown tier value fails at construction.
    (cfg / "mark-claw" / "mark" / "exclusions.yaml").write_text(
        "chat:\n  s:\n    - {id: X, tier: secret}\n", encoding="utf-8"
    )

    rc = cli.main(["guard", "scan-vault"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "guard scan-vault" in err
    assert "cannot compile guard" in err


def test_guard_scan_vault_enumeration_error_exits_nonzero(
    monkeypatch, capsys, tmp_path
) -> None:
    """``guard scan-vault`` handles a vault-traversal ``OSError`` without a traceback.

    The ``vault.rglob("*.md")`` enumeration is outside the per-note
    ``read_text`` try/except. If the traversal itself hits a filesystem error
    (an unreadable subdir, a broken symlink on a directory entry, a permissions
    flip mid-scan), the loop raises ``OSError`` and — left unhandled — surfaces
    as an uncaught traceback. The scan-vault contract already distinguishes
    0-findings=exit-0 from failure=exit-1; "cannot enumerate the vault" is a
    failure of the same class as the per-note unreadable-skip path and must
    surface as a clean stderr message + exit 1, not a crash.

    Patching ``Path.rglob`` to raise is the portable way to exercise this:
    exercising it via a real unreadable subdir is brittle under root and across
    filesystems, and the contract under test is "an OSError from enumeration is
    handled", whatever its cause.
    """
    cfg, _ = _xdg(monkeypatch, tmp_path)
    _stub_keychain_reachable(monkeypatch)
    cli.main(["doctor", "--init"])  # skeleton config + state
    capsys.readouterr()

    vault = tmp_path / "vault"
    vault.mkdir()
    _write_valid_settings(cfg / "mark-claw" / "mark" / "settings.yaml", str(vault))

    def boom(self, pattern):
        raise OSError("simulated traversal failure")

    monkeypatch.setattr(Path, "rglob", boom)
    rc = cli.main(["guard", "scan-vault"])
    err = capsys.readouterr().err
    assert rc == 1, "an enumeration failure must exit nonzero, not crash"
    assert "cannot enumerate vault" in err
    assert "simulated traversal failure" in err


def test_secret_get_dispatches(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "mclaw_core.secret.get_secret",
        lambda item, field, *, profile: "the-value",
    )
    rc = cli.main(["secret", "get", "google", "token"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "the-value"


def test_secret_list_dispatches(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "mclaw_core.secret.list_accounts",
        lambda *, profile: ["a-b", "c-d"],
    )
    rc = cli.main(["secret", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.split() == ["a-b", "c-d"]


def test_secret_error_returns_nonzero(monkeypatch, capsys) -> None:
    def boom(item, field, *, profile):
        raise secret.SecretError("nope")

    monkeypatch.setattr("mclaw_core.secret.get_secret", boom)
    rc = cli.main(["secret", "get", "x", "y"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "nope" in err


def test_secret_requires_action(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["secret"])
    assert exc.value.code != 0
