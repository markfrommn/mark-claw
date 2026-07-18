"""Exclusion gate — the fetch gate (design §5.2, the hard guarantee).

These tests build throwaway config trees under ``tmp_path`` and never touch the
real ``~/.config/mark-claw/`` profile. They cover the B3/DEV-14 acceptance
criteria from ``specs/plans/PHASE-1-PLAN.md`` §B3:

- blocked / ephemeral / allow for each source type (chat, drive, meetings, local)
- drive path-prefix inheritance to subfolders + segment-boundary correctness
  (``/HR`` must NOT block ``/HR-secret``)
- chat ID + name (case-insensitive) + ``also_match`` alias matching
- meetings ``series_id`` match + event-in-series match + title-pattern fallback
- local whitelist inversion: unlisted root → BLOCKED; whitelisted root + subdir
  → ALLOW; segment-boundary (``/root`` does NOT whitelist ``/root-evil``)
- config reload by re-calling ``ExclusionGate.load`` after a disk mutation
- default ALLOW for unlisted source_id / non-matching item
- ``ExclusionConfigError`` on an unknown ``tier`` value at load
- ``local_scan_roots()`` returns exactly the whitelisted roots
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mclaw_core.exclusion import (
    ChatRef,
    Decision,
    DriveRef,
    ExclusionConfigError,
    ExclusionGate,
    LocalRef,
    MeetingRef,
)

# --- helpers ---------------------------------------------------------------


def _write_exclusions(root: Path, data: object) -> Path:
    """Write ``data`` as exclusions.yaml under ``root``; return the path."""
    path = root / "exclusions.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _write_whitelist(root: Path, scan_roots: list[str]) -> Path:
    """Write ``scan_roots`` as local-whitelist.yaml under ``root``."""
    path = root / "local-whitelist.yaml"
    path.write_text(yaml.safe_dump({"scan_roots": scan_roots}), encoding="utf-8")
    return path


def _gate_with(
    tmp_path: Path,
    *,
    exclusions: object | None = None,
    scan_roots: list[str] | None = None,
) -> ExclusionGate:
    """Build a gate from a config tree under ``tmp_path``.

    A ``None`` exclusions means: don't write the file (test missing-file path).
    ``scan_roots`` defaults to ``[]`` (whitelist present but empty).
    """
    if exclusions is not None:
        _write_exclusions(tmp_path, exclusions)
    if scan_roots is not None:
        _write_whitelist(tmp_path, scan_roots)
    return ExclusionGate.load(tmp_path)


# --- Decision enum ---------------------------------------------------------


def test_decision_enum_members() -> None:
    """The spec writes the decision set exactly as ALLOW | EPHEMERAL | BLOCKED."""
    assert {d.name for d in Decision} == {"ALLOW", "EPHEMERAL", "BLOCKED"}


# --- chat ---------------------------------------------------------------


def test_chat_blocked_by_id(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {"id": "C0HRCHAN", "name": "#people-private", "tier": "blocked"}
                ]
            }
        },
    )
    assert gate.check("slack-work", ChatRef(id="C0HRCHAN")) == Decision.BLOCKED


def test_chat_ephemeral_by_id(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {"id": "D024JD", "name": "DM: Jane Doe", "tier": "ephemeral"}
                ]
            }
        },
    )
    assert gate.check("slack-work", ChatRef(id="D024JD")) == Decision.EPHEMERAL


def test_chat_allow_when_no_match(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={
            "chat": {"slack-work": [{"id": "C0HRCHAN", "tier": "blocked"}]}
        },
    )
    assert gate.check("slack-work", ChatRef(id="C0UNRELATED")) == Decision.ALLOW


def test_chat_blocked_by_name_case_insensitive(tmp_path: Path) -> None:
    """Name match is case-insensitive exact (spec §5.2)."""
    gate = _gate_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {"id": "C0X", "name": "#People-Private", "tier": "blocked"}
                ]
            }
        },
    )
    assert gate.check("slack-work", ChatRef(name="#people-private")) == Decision.BLOCKED
    assert gate.check("slack-work", ChatRef(name="#PEOPLE-private")) == Decision.BLOCKED


def test_chat_blocked_by_also_match_alias(tmp_path: Path) -> None:
    """``also_match`` aliases are matched against ``ref.name`` (case-insensitive)."""
    gate = _gate_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {
                        "id": "C0HRCHAN",
                        "name": "#people-private",
                        "tier": "blocked",
                        "also_match": ["people-private", "hr-private"],
                    }
                ]
            }
        },
    )
    assert gate.check("slack-work", ChatRef(name="PEOPLE-PRIVATE")) == Decision.BLOCKED
    assert gate.check("slack-work", ChatRef(name="hr-private")) == Decision.BLOCKED


def test_chat_default_allow_for_unlisted_source(tmp_path: Path) -> None:
    """An unknown source_id → ALLOW (§5.3 default-fully-processed)."""
    gate = _gate_with(tmp_path, exclusions={"chat": {"slack-work": []}})
    assert gate.check("other-source", ChatRef(id="C0X")) == Decision.ALLOW


def test_chat_first_matching_entry_wins_block_over_ephemeral(tmp_path: Path) -> None:
    """If two entries match the same ref, the first one's tier is returned.

    A blocked entry listed before an ephemeral entry for the same id wins as
    blocked — fail-closed ordering is the operator's responsibility, but the
    gate must be deterministic (first-match), not silently pick the weaker.
    """
    gate = _gate_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {"id": "C0X", "tier": "blocked"},
                    {"id": "C0X", "tier": "ephemeral"},
                ]
            }
        },
    )
    assert gate.check("slack-work", ChatRef(id="C0X")) == Decision.BLOCKED


# --- drive --------------------------------------------------------------


def test_drive_blocked_by_exact_path(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR", "tier": "blocked"}]}},
    )
    assert gate.check("gdrive-work", DriveRef(path="/HR")) == Decision.BLOCKED


def test_drive_prefix_inherits_to_subfolder(tmp_path: Path) -> None:
    """Drive folder exclusion is path-prefix: inherits to all descendants."""
    gate = _gate_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR", "tier": "blocked"}]}},
    )
    assert gate.check("gdrive-work", DriveRef(path="/HR/payroll")) == Decision.BLOCKED
    assert gate.check("gdrive-work", DriveRef(path="/HR/sub/deep")) == Decision.BLOCKED


def test_drive_segment_boundary_does_not_block_sibling(tmp_path: Path) -> None:
    """Critical: ``/HR`` must NOT block ``/HR-secret`` or ``/HRsecret``.

    This is the correctness-critical reason the check is done with ``Path``
    ancestry, not string ``startswith`` — a string prefix would let ``/HR``
    match ``/HR-secret`` and silently widen the exclusion.
    """
    gate = _gate_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR", "tier": "blocked"}]}},
    )
    assert gate.check("gdrive-work", DriveRef(path="/HR-secret")) == Decision.ALLOW
    assert gate.check("gdrive-work", DriveRef(path="/HRsecret")) == Decision.ALLOW


def test_drive_entry_trailing_slash_inherits(tmp_path: Path) -> None:
    """``/HR/`` normalizes to ``/HR`` and inherits to subfolders (§6.1 example)."""
    gate = _gate_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR/", "tier": "blocked"}]}},
    )
    assert gate.check("gdrive-work", DriveRef(path="/HR")) == Decision.BLOCKED
    assert gate.check("gdrive-work", DriveRef(path="/HR/payroll")) == Decision.BLOCKED


def test_drive_ephemeral_tier(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/Eph", "tier": "ephemeral"}]}},
    )
    assert gate.check("gdrive-work", DriveRef(path="/Eph/thing")) == Decision.EPHEMERAL


def test_drive_default_allow_unlisted_source(tmp_path: Path) -> None:
    gate = _gate_with(tmp_path, exclusions={"drive": {}})
    assert gate.check("other-acct", DriveRef(path="/HR")) == Decision.ALLOW


def test_drive_paths_case_sensitive(tmp_path: Path) -> None:
    """Drive paths from the provider are case-sensitive (spec examples exact).

    ``/HR`` must NOT match ``/hr`` — the operator authoring an exclusion for
    ``/HR`` did not intend to block ``/hr`` (a different folder on a
    case-sensitive store). String equality, not case-fold.
    """
    gate = _gate_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR", "tier": "blocked"}]}},
    )
    assert gate.check("gdrive-work", DriveRef(path="/hr/payroll")) == Decision.ALLOW


# --- meetings -----------------------------------------------------------


def test_meeting_blocked_by_series_id(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc123", "title": "Comp review", "tier": "blocked"}
            ]
        },
    )
    assert gate.check("any-cal", MeetingRef(series_id="abc123")) == Decision.BLOCKED


def test_meeting_blocked_by_event_in_series(tmp_path: Path) -> None:
    """An event whose ``event_id`` equals a blocked series's id is blocked."""
    gate = _gate_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc123", "title": "Comp review", "tier": "blocked"}
            ]
        },
    )
    assert gate.check("any-cal", MeetingRef(event_id="abc123")) == Decision.BLOCKED


def test_meeting_blocked_by_title_substring_case_insensitive(tmp_path: Path) -> None:
    """Title-pattern fallback (§5.2): entry title is a case-insensitive substring."""
    gate = _gate_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc", "title": "Comp Review", "tier": "blocked"}
            ]
        },
    )
    assert (
        gate.check("any-cal", MeetingRef(title="Quarterly comp review sync"))
        == Decision.BLOCKED
    )


def test_meeting_ephemeral_tier(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "xyz", "title": "Private 1:1", "tier": "ephemeral"}
            ]
        },
    )
    assert gate.check("any-cal", MeetingRef(series_id="xyz")) == Decision.EPHEMERAL


def test_meeting_default_allow_when_no_match(tmp_path: Path) -> None:
    gate = _gate_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc", "title": "Comp review", "tier": "blocked"}
            ]
        },
    )
    assert gate.check("any-cal", MeetingRef(series_id="unrelated")) == Decision.ALLOW
    assert (
        gate.check("any-cal", MeetingRef(title="totally different"))
        == Decision.ALLOW
    )


# --- local (whitelist inversion) ----------------------------------------


def test_local_whitelisted_root_allowed(tmp_path: Path) -> None:
    """Whitelist inversion: a ref AT a whitelisted root → ALLOW."""
    scan_root = tmp_path / "scanroot"
    scan_root.mkdir()
    gate = _gate_with(tmp_path, exclusions={}, scan_roots=[str(scan_root)])
    assert gate.check("local", LocalRef(path=str(scan_root))) == Decision.ALLOW


def test_local_whitelisted_subdir_allowed(tmp_path: Path) -> None:
    """Whitelist inversion: a ref UNDER a whitelisted root → ALLOW."""
    scan_root = tmp_path / "scanroot"
    sub = scan_root / "src" / "deep"
    sub.mkdir(parents=True)
    gate = _gate_with(tmp_path, exclusions={}, scan_roots=[str(scan_root)])
    assert gate.check("local", LocalRef(path=str(sub))) == Decision.ALLOW


def test_local_unlisted_root_blocked(tmp_path: Path) -> None:
    """Whitelist inversion: an unlisted root → BLOCKED (fail-closed for local)."""
    whitelisted = tmp_path / "listed"
    unlisted = tmp_path / "unlisted"
    whitelisted.mkdir()
    unlisted.mkdir()
    gate = _gate_with(tmp_path, exclusions={}, scan_roots=[str(whitelisted)])
    assert gate.check("local", LocalRef(path=str(unlisted))) == Decision.BLOCKED


def test_local_missing_whitelist_blocks_everything(tmp_path: Path) -> None:
    """Missing local-whitelist.yaml → no scan roots → local all BLOCKED.

    Fail-closed for local: an empty whitelist means nothing is scanned, which
    is the safe failure mode (§5.2: ``mclaw-fetch-local`` takes roots *only*
    from the whitelist).
    """
    gate = _gate_with(tmp_path, exclusions={}, scan_roots=None)
    assert gate.check("local", LocalRef(path=str(tmp_path))) == Decision.BLOCKED
    assert gate.local_scan_roots() == ()


def test_local_segment_boundary_does_not_whitelist_sibling(tmp_path: Path) -> None:
    """Whitelisted ``/parent/proj`` must NOT whitelist ``/parent/proj-evil``.

    The segment-boundary check is what prevents a whitelist entry from
    accidentally covering a sibling whose name shares a string prefix.
    """
    parent = tmp_path / "parent"
    root = parent / "proj"
    evil = parent / "proj-evil"
    root.mkdir(parents=True)
    evil.mkdir(parents=True)
    gate = _gate_with(tmp_path, exclusions={}, scan_roots=[str(root)])
    assert gate.check("local", LocalRef(path=str(evil))) == Decision.BLOCKED


def test_local_path_resolved_through_symlink_prefix(tmp_path: Path) -> None:
    """A ref and a whitelist entry that resolve to the same absolute path
    compare equal — local resolution normalizes both via ``Path.resolve``."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    gate = _gate_with(tmp_path, exclusions={}, scan_roots=[str(link)])
    # Item ref names the real dir; whitelist named the symlink; both resolve
    # to the same canonical path, so the ref is under the whitelisted root.
    assert gate.check("local", LocalRef(path=str(real))) == Decision.ALLOW


# --- config reload --------------------------------------------------------


def test_reload_picks_up_disk_changes(tmp_path: Path) -> None:
    """``ExclusionGate.load`` always re-reads disk — that IS the reload path."""
    _write_exclusions(
        tmp_path, {"chat": {"slack": [{"id": "C0X", "tier": "blocked"}]}}
    )
    _write_whitelist(tmp_path, [])
    gate1 = ExclusionGate.load(tmp_path)
    assert gate1.check("slack", ChatRef(id="C0X")) == Decision.BLOCKED

    # Remove the entry on disk.
    _write_exclusions(tmp_path, {"chat": {"slack": []}})
    gate2 = ExclusionGate.load(tmp_path)
    assert gate2.check("slack", ChatRef(id="C0X")) == Decision.ALLOW


def test_reload_picks_up_added_whitelist_root(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _write_exclusions(tmp_path, {})
    _write_whitelist(tmp_path, [str(root_a)])
    gate1 = ExclusionGate.load(tmp_path)
    assert gate1.check("local", LocalRef(path=str(root_b))) == Decision.BLOCKED

    _write_whitelist(tmp_path, [str(root_a), str(root_b)])
    gate2 = ExclusionGate.load(tmp_path)
    assert gate2.check("local", LocalRef(path=str(root_b))) == Decision.ALLOW


# --- default ALLOW + missing-file handling -------------------------------


def test_missing_exclusions_yaml_is_all_allow(tmp_path: Path) -> None:
    """Missing exclusions.yaml → no exclusions → all ALLOW (non-local)."""
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert gate.check("any", ChatRef(id="C0X")) == Decision.ALLOW
    assert gate.check("any", DriveRef(path="/x")) == Decision.ALLOW
    assert gate.check("any", MeetingRef(series_id="x")) == Decision.ALLOW


def test_empty_exclusions_yaml_is_all_allow(tmp_path: Path) -> None:
    """An empty exclusions file (skeleton state) parses cleanly to no entries."""
    _write_exclusions(tmp_path, {})
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert gate.check("any", ChatRef(id="C0X")) == Decision.ALLOW


# --- config-error handling ------------------------------------------------


def test_unknown_tier_in_chat_raises_at_load(tmp_path: Path) -> None:
    _write_exclusions(
        tmp_path, {"chat": {"slack": [{"id": "C0X", "tier": "secret"}]}}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="secret"):
        ExclusionGate.load(tmp_path)


def test_unknown_tier_in_drive_raises_at_load(tmp_path: Path) -> None:
    _write_exclusions(
        tmp_path, {"drive": {"acct": [{"path": "/x", "tier": "nope"}]}}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError):
        ExclusionGate.load(tmp_path)


def test_unknown_tier_in_meetings_raises_at_load(tmp_path: Path) -> None:
    _write_exclusions(
        tmp_path, {"meetings": [{"series_id": "x", "tier": "blocked-ish"}]}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError):
        ExclusionGate.load(tmp_path)


def test_inert_chat_entry_raises_at_load(tmp_path: Path) -> None:
    """An entry with no id/name/also_match can never match — fail at load.

    Such an entry is silently ALLOW-for-everything (it compiles but no ref
    ever matches it), which masks an operator typo like ``{tier: blocked}``
    missing its identifier. The guard surfaces it loudly instead.
    """
    _write_exclusions(tmp_path, {"chat": {"slack": [{"tier": "blocked"}]}})
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="no id, name, or also_match"):
        ExclusionGate.load(tmp_path)


def test_chat_entry_with_only_also_match_is_valid(tmp_path: Path) -> None:
    """An entry with only ``also_match`` (no id/name) is NOT inert — aliases
    match. Proves the inert-entry guard is specific to fully-inert entries,
    not entries that have any single identifier."""
    _write_exclusions(
        tmp_path,
        {"chat": {"slack": [{"also_match": ["x"], "tier": "blocked"}]}},
    )
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert gate.check("slack", ChatRef(name="x")) == Decision.BLOCKED


def test_inert_meeting_entry_raises_at_load(tmp_path: Path) -> None:
    """A meetings entry with no series_id/title can never match — fail at load."""
    _write_exclusions(tmp_path, {"meetings": [{"tier": "blocked"}]})
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="no series_id or title"):
        ExclusionGate.load(tmp_path)


def test_meeting_entry_with_only_title_is_valid(tmp_path: Path) -> None:
    """A meetings entry with only ``title`` (no series_id) is NOT inert — the
    title-pattern fallback matches. Proves the inert-entry guard is specific
    to fully-inert entries."""
    _write_exclusions(
        tmp_path, {"meetings": [{"title": "Comp", "tier": "blocked"}]}
    )
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert (
        gate.check("any-cal", MeetingRef(title="annual comp review"))
        == Decision.BLOCKED
    )


def test_malformed_exclusions_yaml_raises(tmp_path: Path) -> None:
    """A YAML parse error surfaces as ExclusionConfigError (never swallowed)."""
    (tmp_path / "exclusions.yaml").write_text(
        "chat: [unclosed\n  - bad\n", encoding="utf-8"
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError):
        ExclusionGate.load(tmp_path)


def test_malformed_whitelist_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "local-whitelist.yaml").write_text(
        "scan_roots: [unclosed\n", encoding="utf-8"
    )
    _write_exclusions(tmp_path, {})
    with pytest.raises(ExclusionConfigError):
        ExclusionGate.load(tmp_path)


def test_top_level_non_mapping_exclusions_raises(tmp_path: Path) -> None:
    """A top-level scalar/list instead of a mapping is a config bug."""
    _write_exclusions(tmp_path, ["not", "a", "mapping"])
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError):
        ExclusionGate.load(tmp_path)


# --- present-but-null section guards (Macroscope review fix 1) ------------


def test_null_chat_section_raises(tmp_path: Path) -> None:
    """A present-but-null ``chat:`` (a YAML dangling key) is malformed — it
    silently means "no chat exclusions" and disables protection the operator
    wrote an (indented) entry for. Fail loud at load."""
    _write_exclusions(tmp_path, {"chat": None})
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="'chat'"):
        ExclusionGate.load(tmp_path)


def test_null_drive_section_raises(tmp_path: Path) -> None:
    """A present-but-null ``drive:`` is malformed — fail loud at load."""
    _write_exclusions(tmp_path, {"drive": None})
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="'drive'"):
        ExclusionGate.load(tmp_path)


def test_null_meetings_section_raises(tmp_path: Path) -> None:
    """A present-but-null ``meetings:`` is malformed — fail loud at load."""
    _write_exclusions(tmp_path, {"meetings": None})
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="'meetings'"):
        ExclusionGate.load(tmp_path)


def test_absent_sections_load_cleanly(tmp_path: Path) -> None:
    """A config with only ``drive:`` (no ``chat:``/``meetings:`` keys) must
    still load — an *absent* section is a valid "no exclusions for that
    section" and must NOT be rejected by the present-but-null guard. This
    test pins that the guard distinguishes absence (valid) from null
    (malformed) and guards against over-tightening."""
    _write_exclusions(
        tmp_path, {"drive": {"gdrive-work": [{"path": "/HR", "tier": "blocked"}]}}
    )
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert gate.check("any", ChatRef(id="x")) == Decision.ALLOW
    assert gate.check("any", MeetingRef(series_id="x")) == Decision.ALLOW
    # Drive exclusions still applied.
    assert gate.check("gdrive-work", DriveRef(path="/HR")) == Decision.BLOCKED


# --- empty-string identifier guards (Macroscope review fix 2) ------------


def test_empty_chat_id_raises(tmp_path: Path) -> None:
    """An empty-string ``id`` is a malformed entry — reject at load rather
    than silently compile an entry that can never match."""
    _write_exclusions(
        tmp_path, {"chat": {"s": [{"id": "", "tier": "blocked"}]}}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="non-empty"):
        ExclusionGate.load(tmp_path)


def test_empty_chat_name_raises(tmp_path: Path) -> None:
    """An empty-string ``name`` is a malformed entry — reject at load."""
    _write_exclusions(
        tmp_path, {"chat": {"s": [{"name": "", "tier": "blocked"}]}}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="non-empty"):
        ExclusionGate.load(tmp_path)


def test_empty_meeting_title_raises(tmp_path: Path) -> None:
    """An empty-string ``title`` is a malformed entry — the global-match
    scenario. ``"" in any_string`` is always True, so one such entry would
    silently block every titled meeting. Reject at load."""
    _write_exclusions(
        tmp_path, {"meetings": [{"title": "", "tier": "blocked"}]}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="non-empty"):
        ExclusionGate.load(tmp_path)


def test_empty_meeting_series_id_raises(tmp_path: Path) -> None:
    """An empty-string ``series_id`` is a malformed entry — reject at load."""
    _write_exclusions(
        tmp_path, {"meetings": [{"series_id": "", "tier": "blocked"}]}
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="non-empty"):
        ExclusionGate.load(tmp_path)


# --- empty-string also_match alias guards (Macroscope review fix 3) --------


def test_empty_also_match_alias_raises(tmp_path: Path) -> None:
    """An empty-string ``also_match`` alias is malformed — reject at load.

    Without this guard, ``aliases=[""]`` is non-empty, so the inert-entry
    guard (``if id is None and name is None and not aliases``) passes the
    entry through — but ``""`` can never match a real chat name, so the entry
    is silently inert. This contradicts the module's fail-loud promise
    (already enforced by ``_opt_str`` for id/name/series_id/title).
    """
    _write_exclusions(
        tmp_path,
        {"chat": {"s": [{"id": "C0X", "tier": "blocked", "also_match": [""]}]}},
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="non-empty"):
        ExclusionGate.load(tmp_path)


def test_empty_alias_among_valid_raises(tmp_path: Path) -> None:
    """A single bad alias fails the whole entry — surfacing the typo loudly."""
    _write_exclusions(
        tmp_path,
        {
            "chat": {
                "s": [
                    {
                        "id": "C0X",
                        "tier": "blocked",
                        "also_match": ["valid", ""],
                    }
                ]
            }
        },
    )
    _write_whitelist(tmp_path, [])
    with pytest.raises(ExclusionConfigError, match="non-empty"):
        ExclusionGate.load(tmp_path)


def test_valid_also_match_still_compiles(tmp_path: Path) -> None:
    """Non-empty aliases still work after the empty-string guard is added."""
    _write_exclusions(
        tmp_path,
        {
            "chat": {
                "s": [
                    {"id": "C0X", "tier": "blocked", "also_match": ["real-alias"]}
                ]
            }
        },
    )
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert gate.check("s", ChatRef(name="real-alias")) == Decision.BLOCKED


# --- local_scan_roots -----------------------------------------------------


def test_local_scan_roots_returns_whitelisted(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _write_exclusions(tmp_path, {})
    _write_whitelist(tmp_path, [str(root_a), str(root_b)])
    gate = ExclusionGate.load(tmp_path)
    roots = gate.local_scan_roots()
    # Order preserved; both resolved to canonical absolute paths.
    assert len(roots) == 2
    assert {p.name for p in roots} == {"a", "b"}
    for r in roots:
        assert r.is_absolute()


def test_local_scan_roots_empty_when_whitelist_missing(tmp_path: Path) -> None:
    _write_exclusions(tmp_path, {})
    gate = ExclusionGate.load(tmp_path)
    assert gate.local_scan_roots() == ()


def test_local_scan_roots_empty_when_whitelist_present_but_empty(
    tmp_path: Path,
) -> None:
    _write_exclusions(tmp_path, {})
    _write_whitelist(tmp_path, [])
    gate = ExclusionGate.load(tmp_path)
    assert gate.local_scan_roots() == ()


# --- the skeleton-exact entry shapes --------------------------------------


def test_skeleton_entry_shapes_parse_cleanly(tmp_path: Path) -> None:
    """The exact entry shapes shipped in config_state._EXCLUSIONS_YAML
    (referenced by §6.1) must parse without error and produce the expected
    decisions — this is the parser-must-accept-this acceptance test."""
    _write_exclusions(
        tmp_path,
        {
            "chat": {
                "slack-work": [
                    {
                        "id": "C0HRCHAN",
                        "name": "#people-private",
                        "tier": "blocked",
                        "also_match": ["people-private"],
                    },
                    {"id": "D024JD", "name": "DM: Jane Doe", "tier": "ephemeral"},
                ],
            },
            "drive": {"gdrive-work": [{"path": "/HR/", "tier": "blocked"}]},
            "meetings": [
                {"series_id": "abc123", "title": "Comp review", "tier": "blocked"}
            ],
        },
    )
    _write_whitelist(tmp_path, ["/Users/you/Projects/your-repo"])
    gate = ExclusionGate.load(tmp_path)

    assert gate.check("slack-work", ChatRef(id="C0HRCHAN")) == Decision.BLOCKED
    assert gate.check("slack-work", ChatRef(name="people-private")) == Decision.BLOCKED
    assert gate.check("slack-work", ChatRef(id="D024JD")) == Decision.EPHEMERAL
    assert gate.check("gdrive-work", DriveRef(path="/HR/payroll")) == Decision.BLOCKED
    assert gate.check("any-cal", MeetingRef(series_id="abc123")) == Decision.BLOCKED
    assert (
        gate.check("any-cal", MeetingRef(title="annual comp review"))
        == Decision.BLOCKED
    )
    # The whitelisted root is included even though the dir doesn't exist here.
    roots = gate.local_scan_roots()
    assert len(roots) == 1
    assert roots[0].name == "your-repo"
