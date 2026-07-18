"""CLI dispatch, stubs, and the doctor report."""

from __future__ import annotations

import pytest

from mclaw_core import cli, secret


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
    monkeypatch.setenv("MCLAW_PROFILE", "mark")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "profile: mark" in out
    assert "MISSING" in out  # roots don't exist yet — must not crash


def test_doctor_reports_existing_roots(monkeypatch, capsys, tmp_path) -> None:
    cfg = tmp_path / "cfg"
    st = tmp_path / "st"
    (cfg / "mark-claw" / "mark").mkdir(parents=True)
    (st / "mark-claw" / "mark").mkdir(parents=True)
    monkeypatch.setenv("MCLAW_PROFILE", "mark")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(st))
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "MISSING" not in out


@pytest.mark.parametrize("provider", ["google", "graph", "telegram"])
def test_auth_is_stub_nonzero(capsys, provider) -> None:
    rc = cli.main(["auth", provider])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not implemented" in err
    assert "DEV-31" in err


@pytest.mark.parametrize("command", ["exclusions", "fetch", "ingest", "guard"])
def test_plain_stubs_nonzero(capsys, command) -> None:
    rc = cli.main([command])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not implemented" in err


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
