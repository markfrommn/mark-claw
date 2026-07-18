"""Config + state tree init — idempotency, perms, non-destructive on config.

All tests point ``XDG_CONFIG_HOME`` / ``XDG_STATE_HOME`` at a tmp path so they
never touch the operator's real config or state.
"""

from __future__ import annotations

import os

from mclaw_core import config_state, paths


def _xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCLAW_PROFILE", "mark")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))


# --- config tree -----------------------------------------------------------


def test_init_creates_all_config_skeletons(monkeypatch, tmp_path) -> None:
    _xdg(monkeypatch, tmp_path)
    result = config_state.init_config_tree(profile="mark")
    root = paths.config_root("mark")
    for name in config_state.CONFIG_SKELETONS:
        assert (root / name).is_file(), f"missing skeleton: {name}"
    assert {p.name for p in result.created} == set(config_state.CONFIG_SKELETONS)
    assert result.reused == []


def test_init_config_is_non_destructive(monkeypatch, tmp_path) -> None:
    """An existing config file must NEVER be overwritten by init."""
    _xdg(monkeypatch, tmp_path)
    root = paths.config_root("mark")
    root.mkdir(parents=True)
    settings = root / "settings.yaml"
    authored = "operator-authored: true\nvault: {path: /real}\n"
    settings.write_text(authored)
    result = config_state.init_config_tree(profile="mark")
    assert settings.read_text() == authored
    assert settings in result.reused
    assert settings not in result.created


def test_init_config_idempotent_second_pass_all_reused(
    monkeypatch, tmp_path
) -> None:
    _xdg(monkeypatch, tmp_path)
    first = config_state.init_config_tree(profile="mark")
    second = config_state.init_config_tree(profile="mark")
    assert len(first.created) == len(config_state.CONFIG_SKELETONS)
    assert second.created == []
    assert {p.name for p in second.reused} == set(config_state.CONFIG_SKELETONS)


def test_skeletons_are_yaml_parseable(monkeypatch, tmp_path) -> None:
    """Every YAML skeleton must parse as a mapping with its required keys.

    Guards against a hand-edited skeleton drifting into invalid YAML or dropping
    a required top-level key (which would make a fresh `doctor --init` red).
    """
    import yaml

    from mclaw_core.doctor import REQUIRED_KEYS

    _xdg(monkeypatch, tmp_path)
    config_state.init_config_tree(profile="mark")
    root = paths.config_root("mark")
    for name in config_state.YAML_CONFIG_FILES:
        with (root / name).open() as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict), f"{name} skeleton is not a mapping"
        for key in REQUIRED_KEYS[name]:
            assert key in data, f"{name} skeleton missing required key {key!r}"


def test_hygiene_skeleton_has_zero_active_patterns(
    monkeypatch, tmp_path
) -> None:
    """The hygiene skeleton ships with NO active patterns (operator-authored)."""
    _xdg(monkeypatch, tmp_path)
    config_state.init_config_tree(profile="mark")
    patterns = config_state.CONFIG_SKELETONS["hygiene-patterns.txt"]
    active = [
        line.strip()
        for line in patterns.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert active == []


# --- state tree ------------------------------------------------------------


def test_init_creates_all_state_dirs(monkeypatch, tmp_path) -> None:
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    root = paths.state_root("mark")
    for rel in config_state.STATE_DIRS:
        assert (root / rel).is_dir(), f"missing state dir: {rel}"


def test_init_state_secure_dirs_are_0700(monkeypatch, tmp_path) -> None:
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    root = paths.state_root("mark")
    for rel in config_state.SECURE_DIRS:
        mode = (root / rel).stat().st_mode & 0o777
        assert mode == 0o700, f"{rel}/ expected 0700, got {oct(mode)}"


def test_init_state_nonsecure_dirs_are_0755(monkeypatch, tmp_path) -> None:
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    root = paths.state_root("mark")
    for rel in config_state.STATE_DIRS:
        if rel in config_state.SECURE_DIRS:
            continue
        mode = (root / rel).stat().st_mode & 0o777
        assert mode == 0o755, f"{rel}/ expected 0755, got {oct(mode)}"


def test_init_state_reasserts_perms_on_widened_secure_dir(
    monkeypatch, tmp_path
) -> None:
    """A widened secure dir must be corrected back to 0700 on re-init."""
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    secrets = paths.state_root("mark") / "secrets"
    assert (secrets.stat().st_mode & 0o777) == 0o700
    os.chmod(secrets, 0o755)  # operator / bad umask widens it
    assert (secrets.stat().st_mode & 0o777) == 0o755
    result = config_state.init_state_tree(profile="mark")
    assert (secrets.stat().st_mode & 0o777) == 0o700
    assert secrets in result.fixed_perms


def test_init_state_idempotent_no_error(monkeypatch, tmp_path) -> None:
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    # Second pass must not raise and must keep the secure dirs at 0700.
    config_state.init_state_tree(profile="mark")
    root = paths.state_root("mark")
    for rel in config_state.SECURE_DIRS:
        assert (root / rel).stat().st_mode & 0o777 == 0o700


def test_init_state_creates_nested_ephemeral(monkeypatch, tmp_path) -> None:
    """spool/ephemeral is nested under spool/ and must end up 0700."""
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    ephemeral = paths.state_root("mark") / "spool" / "ephemeral"
    spool = paths.state_root("mark") / "spool"
    assert ephemeral.is_dir()
    assert spool.is_dir()
    assert (ephemeral.stat().st_mode & 0o777) == 0o700
    assert (spool.stat().st_mode & 0o777) == 0o755
