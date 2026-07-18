"""Config + state tree init — idempotency, perms, non-destructive on config.

All tests point ``XDG_CONFIG_HOME`` / ``XDG_STATE_HOME`` at a tmp path so they
never touch the operator's real config or state.
"""

from __future__ import annotations

import os

import pytest

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


def test_init_config_preserves_existing_bytes_exactly(
    monkeypatch, tmp_path
) -> None:
    """The atomic exclusive-open (``"x"``) must leave an existing file's bytes
    untouched — byte-for-byte preservation across a re-init, even when the
    authored content is longer/shorter than the skeleton would have been."""
    _xdg(monkeypatch, tmp_path)
    root = paths.config_root("mark")
    root.mkdir(parents=True)
    settings = root / "settings.yaml"
    # Non-trivial content unlike the skeleton — its bytes must survive verbatim.
    authored = "# operator\nvault: {path: /x}\n" + ("k: v\n" * 50)
    settings.write_bytes(authored.encode("utf-8"))
    result = config_state.init_config_tree(profile="mark")
    assert settings.read_bytes() == authored.encode("utf-8")
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


@pytest.mark.parametrize("rel", ["secrets", "quarantine", "spool/ephemeral"])
def test_init_state_rejects_symlink_at_secure_dir(
    monkeypatch, tmp_path, rel
) -> None:
    """A symlink placed at a fail-closed secure dir must make init refuse —
    is_dir/mkdir(exist_ok=True)/stat/chmod all follow the link, so trusting it
    would chmod an arbitrary directory outside the state tree. Covers each
    SECURE_DIR (secrets, quarantine, spool/ephemeral)."""
    _xdg(monkeypatch, tmp_path)
    # Build the real layout first so the parent of `rel` exists.
    config_state.init_state_tree(profile="mark")
    root = paths.state_root("mark")
    secure = root / rel
    # `rel` may be nested (spool/ephemeral); remove the real dir, then link.
    secure.rmdir()
    target = tmp_path / "attacker-controlled"
    target.mkdir()
    secure.symlink_to(target)
    with pytest.raises(config_state.StateInitError) as excinfo:
        config_state.init_state_tree(profile="mark")
    assert "symlink" in str(excinfo.value)
    # The link's target must NOT have been chmod'd to 0700 by init.
    assert (target.stat().st_mode & 0o777) != 0o700


def test_init_state_allows_symlink_at_nonsecure_dir(
    monkeypatch, tmp_path
) -> None:
    """Non-secure dirs are out of scope for the fail-closed symlink guard — a
    symlink at a non-secure dir is left untouched (init neither raises nor
    moves the link). Pinning the scope so the guard can't creep wider."""
    _xdg(monkeypatch, tmp_path)
    config_state.init_state_tree(profile="mark")
    root = paths.state_root("mark")
    # `cursors` is a non-secure state dir; replace it with a symlink.
    (root / "cursors").rmdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / "cursors").symlink_to(target)
    # Must not raise (out of scope), and the link is still a link afterwards.
    config_state.init_state_tree(profile="mark")
    assert (root / "cursors").is_symlink()
