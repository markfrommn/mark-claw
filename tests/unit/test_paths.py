"""Profile and XDG path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from mclaw_core import paths


@pytest.mark.parametrize("good", ["mark", "work-acct", "foo_bar", "DEFAULT.PROFILE"])
def test_validate_profile_accepts_safe_names(good) -> None:
    assert paths.validate_profile(good) == good


def test_default_profile_is_valid() -> None:
    assert paths.validate_profile(paths.DEFAULT_PROFILE) == "mark"


@pytest.mark.parametrize(
    "bad", ["/etc", "../other", "a/b", "..", ".", "", "a\\b", "x\x00y"]
)
def test_validate_profile_rejects_injection(bad) -> None:
    with pytest.raises(ValueError):
        paths.validate_profile(bad)


def test_resolve_profile_rejects_bad_env() -> None:
    with pytest.raises(ValueError):
        paths.resolve_profile({"MCLAW_PROFILE": "/etc"})
    with pytest.raises(ValueError):
        paths.resolve_profile({"MCLAW_PROFILE": "../other"})


def test_config_root_rejects_bad_explicit_profile() -> None:
    with pytest.raises(ValueError):
        paths.config_root("../other", {"XDG_CONFIG_HOME": "/c"})


def test_state_root_rejects_bad_explicit_profile() -> None:
    with pytest.raises(ValueError):
        paths.state_root("/etc", {"XDG_STATE_HOME": "/s"})


def test_default_profile_when_unset() -> None:
    assert paths.resolve_profile({}) == "mark"


def test_default_profile_when_blank() -> None:
    assert paths.resolve_profile({"MCLAW_PROFILE": "   "}) == "mark"


def test_profile_override() -> None:
    assert paths.resolve_profile({"MCLAW_PROFILE": "work"}) == "work"


def test_config_root_default_home(monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/u")))
    root = paths.config_root("mark", {})
    assert root == Path("/home/u/.config/mark-claw/mark")


def test_state_root_default_home(monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/u")))
    root = paths.state_root("mark", {})
    assert root == Path("/home/u/.local/state/mark-claw/mark")


def test_config_root_honors_xdg() -> None:
    env = {"XDG_CONFIG_HOME": "/xdg/cfg"}
    assert paths.config_root("mark", env) == Path("/xdg/cfg/mark-claw/mark")


def test_state_root_honors_xdg() -> None:
    env = {"XDG_STATE_HOME": "/xdg/st"}
    assert paths.state_root("work", env) == Path("/xdg/st/mark-claw/work")


def test_relative_xdg_is_ignored(monkeypatch) -> None:
    # XDG spec: relative paths are invalid and must be ignored.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/u")))
    env = {"XDG_CONFIG_HOME": "relative/path"}
    assert paths.config_root("mark", env) == Path("/home/u/.config/mark-claw/mark")


def test_roots_use_resolved_profile_from_env() -> None:
    env = {"MCLAW_PROFILE": "alt", "XDG_CONFIG_HOME": "/c", "XDG_STATE_HOME": "/s"}
    assert paths.config_root(None, env) == Path("/c/mark-claw/alt")
    assert paths.state_root(None, env) == Path("/s/mark-claw/alt")
