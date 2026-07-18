"""Output guard — fail-closed defense-in-depth on the exclusion guarantee.

These are the B4 / DEV-15 tests. They build throwaway config + state trees
under ``tmp_path`` and never touch the real ``~/.config/mark-claw/`` profile.

Coverage of the §B4 acceptance criteria (per ``specs/plans/PHASE-1-PLAN.md``):

* blocked identifier on a persistence surface → no write, artifact
  quarantined, ``guard.trip`` changelog record appended;
* clean artifact → :class:`Clean` (writer proceeds);
* **two-surface split** (the subtle AC): an ephemeral identifier trips on a
  persistence surface but NOT on an alert/log surface;
* word-boundary + case-insensitive matching;
* no matched content appears in logs / trip record / quarantine sidecar —
  only the pattern id + artifact name;
* fail-closed on the guard's own error paths (unreadable exclusions →
  load raises, writer cannot proceed);
* quarantine dir mode is ``0o700``;
* CLI ``mclaw guard scan-vault`` exists and prints ``0 findings`` on an empty
  vault;
* ``mclaw doctor`` shows quarantine count ≠ 0 as a FAIL (red) line.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mclaw_core import cli
from mclaw_core.output_guard import (
    Clean,
    OutputGuard,
    OutputGuardError,
    Surface,
    Trip,
)

# --- helpers ---------------------------------------------------------------


def _write_exclusions(root: Path, data: object) -> Path:
    path = root / "exclusions.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _guard_with(
    tmp_path: Path,
    *,
    exclusions: object | None = None,
    state_root: Path | None = None,
) -> OutputGuard:
    """Build a guard with config root under ``tmp_path``.

    ``None`` exclusions means: don't write the file (test missing-file path).
    The state root defaults to ``tmp_path/state`` so quarantine + changelog
    writes land inside the test sandbox, never under the real profile.
    """
    if exclusions is not None:
        _write_exclusions(tmp_path, exclusions)
    st = state_root if state_root is not None else tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)
    return OutputGuard(config_root=tmp_path, state_root=st)


# The canonical fixtures used across the two-surface tests: one blocked chat
# id and one ephemeral chat id, in the same source.
_BLOCKED_ID = "C0HRCHAN"
_EPHEMERAL_ID = "D024JD"
_BLOCKED_NAME = "#people-private"
_EPHEMERAL_NAME = "DM: Jane Doe"


def _two_tier_chat_exclusions() -> dict[str, object]:
    return {
        "chat": {
            "slack-work": [
                {
                    "id": _BLOCKED_ID,
                    "name": _BLOCKED_NAME,
                    "tier": "blocked",
                    "also_match": ["people-private"],
                },
                {
                    "id": _EPHEMERAL_ID,
                    "name": _EPHEMERAL_NAME,
                    "tier": "ephemeral",
                },
            ]
        }
    }


# --- Clean path ------------------------------------------------------------


def test_clean_text_returns_clean(tmp_path: Path) -> None:
    """A text with no deny-pattern hit returns :class:`Clean` — writer proceeds."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        "nothing sensitive here, just project chatter",
        surface=Surface.PERSISTENCE,
        artifact_name="wiki/days/2026-07-18.md",
    )
    assert isinstance(result, Clean)


def test_missing_exclusions_file_all_clean(tmp_path: Path) -> None:
    """A pre-init profile (no exclusions.yaml) compiles an empty pattern set.

    Mirrors the gate's missing-file tolerance. All scans return :class:`Clean`
    (the guard adds defense-in-depth once exclusions exist).
    """
    guard = _guard_with(tmp_path, exclusions=None)
    result = guard.scan(
        "anything goes when no exclusions are configured",
        surface=Surface.PERSISTENCE,
    )
    assert isinstance(result, Clean)


# --- Blocked hits ----------------------------------------------------------


def test_blocked_id_trips_persistence(tmp_path: Path) -> None:
    """A blocked chat id in artifact text trips the guard on a persistence surface."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    text = f"Hey everyone in {_BLOCKED_ID}, the agenda is attached."
    result = guard.scan(text, surface=Surface.PERSISTENCE, artifact_name="brief.md")
    assert isinstance(result, Trip)
    assert _BLOCKED_ID not in result.pattern_id or "C0HRCHAN" in result.pattern_id
    assert result.artifact_name == "brief.md"


def test_blocked_id_trips_alert_log(tmp_path: Path) -> None:
    """Blocked identifiers trip on the alert/log surface (blocked is in both sets)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"alert: mention of {_BLOCKED_ID}", surface=Surface.ALERT_LOG
    )
    assert isinstance(result, Trip)


def test_blocked_name_trips_persistence(tmp_path: Path) -> None:
    """The human-readable name of a blocked entry also compiles to a pattern."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"see {_BLOCKED_NAME} for context", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


def test_blocked_also_match_alias_trips(tmp_path: Path) -> None:
    """An ``also_match`` alias of a blocked entry compiles to its own pattern."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        "the people-private channel was lively", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


# --- Two-surface split (the AC) --------------------------------------------


def test_ephemeral_id_trips_persistence(tmp_path: Path) -> None:
    """Ephemeral identifiers MUST trip on persistence surfaces (§5.4).

    Ephemeral content must never be summarized into a vault note or briefing —
    the persistence surface scans blocked + ephemeral.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    text = f"DM thread {_EPHEMERAL_ID} had a great exchange today."
    result = guard.scan(text, surface=Surface.PERSISTENCE, artifact_name="day.md")
    assert isinstance(result, Trip)
    assert _EPHEMERAL_ID in result.pattern_id


def test_ephemeral_id_does_not_trip_alert_log(tmp_path: Path) -> None:
    """Ephemeral identifiers MUST NOT trip on alert/log surfaces (§5.4).

    Alerts legitimately *name* ephemeral sources as pointers ("check Signal —
    urgent message from J."), so the alert/log surface scans blocked only.
    This is the subtle half of the two-surface AC.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    text = f"alert: ephemeral pointer — check DM {_EPHEMERAL_ID}"
    result = guard.scan(text, surface=Surface.ALERT_LOG)
    assert isinstance(result, Clean), (
        "ephemeral identifiers must not trip the alert/log surface — alerts "
        "legitimately name ephemeral sources as pointers (§5.4)"
    )


def test_ephemeral_name_does_not_trip_alert_log(tmp_path: Path) -> None:
    """The ephemeral entry's name also must not trip the alert/log surface."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"alert: {_EPHEMERAL_NAME} poked you", surface=Surface.ALERT_LOG
    )
    assert isinstance(result, Clean)


def test_ephemeral_then_blocked_in_same_text_alert_log(tmp_path: Path) -> None:
    """If a single alert text contains both ephemeral and blocked identifiers,
    the blocked one trips (the alert/log set is blocked-only, not empty)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"alert: ephemeral ref {_EPHEMERAL_ID} plus blocked ref {_BLOCKED_ID}",
        surface=Surface.ALERT_LOG,
    )
    assert isinstance(result, Trip)
    assert _BLOCKED_ID in result.pattern_id


# --- Word-boundary + case-insensitive -------------------------------------


def test_case_insensitive_match(tmp_path: Path) -> None:
    """Match is case-insensitive (§5.4)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"see { _BLOCKED_ID.lower() } mention", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


def test_word_boundary_no_substring_false_positive(tmp_path: Path) -> None:
    """``C0HRCHAN`` must not match ``C0HRCHANX`` (a different id).

    Word-boundary anchoring means the identifier appears as a whole token,
    not as a substring of a longer word.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan("C0HRCHANX is a different channel", surface=Surface.PERSISTENCE)
    # Note: also_match "people-private" not present, blocked name not present,
    # ephemeral id not present → clean. The X-suffixed id is NOT a match.
    assert isinstance(result, Clean), (
        "word-boundary anchoring must not substring-match a longer token"
    )


def test_word_boundary_id_at_start_of_text(tmp_path: Path) -> None:
    """An identifier at the very start of the text is matched (the boundary
    lookarounds must not require a preceding char)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"{_BLOCKED_ID} is mentioned first", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


def test_word_boundary_id_at_end_of_text(tmp_path: Path) -> None:
    """An identifier at the very end of the text is matched."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(f"mentioned: {_BLOCKED_ID}", surface=Surface.PERSISTENCE)
    assert isinstance(result, Trip)


def test_name_with_punctuation_matches(tmp_path: Path) -> None:
    """A name like ``#people-private`` that starts with a non-word char still
    matches — ``\\b`` would have failed here (it requires a w↔W transition),
    so the guard uses ``(?<!\\w)…(?!\\w)`` lookarounds instead."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan("drop by #people-private later", surface=Surface.PERSISTENCE)
    assert isinstance(result, Trip)


# --- Drive path patterns ---------------------------------------------------


def test_drive_path_trips_persistence(tmp_path: Path) -> None:
    """A blocked drive path appearing in artifact text trips the guard."""
    guard = _guard_with(
        tmp_path,
        exclusions={
            "drive": {
                "gdrive-work": [
                    {"path": "/HR/", "tier": "blocked"}
                ]
            }
        },
    )
    result = guard.scan(
        "see /HR/payroll for the salary details", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


def test_drive_path_blocked_alert_log(tmp_path: Path) -> None:
    """A blocked drive path trips on alert/log surfaces too."""
    guard = _guard_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR/", "tier": "blocked"}]}},
    )
    result = guard.scan("alert: HR doc at /HR/ leaked", surface=Surface.ALERT_LOG)
    assert isinstance(result, Trip)


def test_drive_path_ephemeral_does_not_trip_alert_log(tmp_path: Path) -> None:
    """Ephemeral drive path does NOT trip on alert/log (ephemeral → persistence)."""
    guard = _guard_with(
        tmp_path,
        exclusions={
            "drive": {"gdrive-work": [{"path": "/Ephem/", "tier": "ephemeral"}]}
        },
    )
    result = guard.scan("see /Ephem/pointer", surface=Surface.ALERT_LOG)
    assert isinstance(result, Clean)


# --- Meeting patterns ------------------------------------------------------


def test_meeting_series_id_trips(tmp_path: Path) -> None:
    guard = _guard_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc123", "title": "Comp review", "tier": "blocked"}
            ]
        },
    )
    result = guard.scan(
        "the abc123 series is on the calendar", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


def test_meeting_title_trips(tmp_path: Path) -> None:
    guard = _guard_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc123", "title": "Comp review", "tier": "blocked"}
            ]
        },
    )
    result = guard.scan(
        "today's Comp review went long", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


# --- Trip carries no content ----------------------------------------------


def test_trip_carries_pattern_id_and_artifact_name_only(tmp_path: Path) -> None:
    """A Trip carries only ``pattern_id`` and ``artifact_name`` — no matched text.

    The dataclass has exactly these two fields and nothing else.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    result = guard.scan(
        f"the secret codeword near {_BLOCKED_ID} should not leak",
        surface=Surface.PERSISTENCE,
        artifact_name="day.md",
    )
    assert isinstance(result, Trip)
    # Exactly two fields, by name.
    assert set(result.__dataclass_fields__) == {"pattern_id", "artifact_name"}
    # The matched context (the surrounding sentence) does not appear.
    assert "secret codeword" not in result.pattern_id
    assert "secret codeword" not in result.artifact_name


# --- on_trip: quarantine + changelog + review queue ----------------------


def test_on_trip_quarantines_artifact_and_appends_changelog(tmp_path: Path) -> None:
    """On a Trip, ``on_trip`` writes the artifact to quarantine and appends a
    ``guard.trip`` record to the changelog."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    text = f"the {_BLOCKED_ID} briefing content"
    trip = guard.scan(text, surface=Surface.PERSISTENCE, artifact_name="morning.md")
    assert isinstance(trip, Trip)

    q_path = guard.on_trip(trip, content=text, actor="briefing-assembler")

    # Artifact content is in quarantine.
    assert q_path.is_file()
    assert q_path.read_text(encoding="utf-8") == text
    # Quarantine lives under state root, never under any vault.
    assert q_path.is_relative_to(tmp_path / "state" / "quarantine")
    # The filename traces the artifact name.
    assert "morning" in q_path.name

    # Changelog has exactly one guard.trip record with the right shape.
    log_path = tmp_path / "state" / "changelog"
    logs = list(log_path.glob("*.jsonl"))
    assert len(logs) == 1
    records = [json.loads(line) for line in logs[0].read_text().splitlines() if line]
    assert len(records) == 1
    rec = records[0]
    assert rec["action"] == "guard.trip"
    assert rec["actor"] == "briefing-assembler"
    assert rec["target"] == "morning.md"
    assert rec["detail"]["pattern_id"] == trip.pattern_id
    assert rec["detail"]["quarantine_path"] == str(q_path)


def test_on_trip_no_matched_content_in_changelog_or_queue(tmp_path: Path) -> None:
    """Neither changelog nor review-queue records contain the matched content (§5.4).

    The matched content (the artifact body) lives only in the quarantined
    artifact file itself, which is the preserved artifact for human review.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    secret_marker = "SUPERSECRETOKEN"  # not in exclusions; we assert it never leaks
    text = f"{secret_marker} near {_BLOCKED_ID} end"
    trip = guard.scan(text, surface=Surface.PERSISTENCE, artifact_name="x.md")
    assert isinstance(trip, Trip)
    q_path = guard.on_trip(trip, content=text)

    log_text = (tmp_path / "state" / "changelog").glob("*.jsonl").__next__().read_text()
    queue_path = tmp_path / "state" / "review-queue" / "pending.jsonl"
    queue_text = queue_path.read_text()

    # The secret marker (which exists ONLY in the artifact body) appears in
    # the quarantined artifact but NOWHERE in the audit records.
    assert secret_marker in q_path.read_text()
    assert secret_marker not in log_text
    assert secret_marker not in queue_text
    # And the bare blocked id appears only inside pattern_id, never as the
    # matched context. (The blocked id IS in pattern_id by design — it's a
    # config identifier, not artifact content.)


def test_on_trip_appends_review_queue_item(tmp_path: Path) -> None:
    """On a Trip, a needs-sign-off review item is queued (§5.4 / §10.2)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    trip = Trip(pattern_id="chat:slack-work[id=C0HRCHAN]", artifact_name="b.md")
    guard.on_trip(trip, content="body")

    queue_path = tmp_path / "state" / "review-queue" / "pending.jsonl"
    assert queue_path.is_file()
    items = [json.loads(line) for line in queue_path.read_text().splitlines() if line]
    assert len(items) == 1
    item = items[0]
    assert item["bucket"] == "sign-off"
    assert item["kind"] == "proposal"
    assert trip.pattern_id in item["evidence"][0]


def test_on_trip_idempotent_changelog_appends(tmp_path: Path) -> None:
    """Two trips append two changelog records (no overwrite)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    for i in range(2):
        guard.on_trip(
            Trip(pattern_id="chat:slack-work[id=C0HRCHAN]", artifact_name=f"a{i}.md"),
            content=f"body{i}",
        )
    log_path = next((tmp_path / "state" / "changelog").glob("*.jsonl").__iter__())
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    assert len(records) == 2


# --- Quarantine dir mode ---------------------------------------------------


def test_quarantine_dir_is_0700(tmp_path: Path) -> None:
    """The quarantine dir is created at mode 0700 (§5.4, §6.2)."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    trip = Trip(pattern_id="chat:slack-work[id=C0HRCHAN]", artifact_name="z.md")
    guard.on_trip(trip, content="body")
    q_dir = tmp_path / "state" / "quarantine"
    mode = q_dir.stat().st_mode & 0o777
    assert mode == 0o700, f"quarantine/ expected 0700, got {oct(mode)}"


def test_quarantine_dir_recreated_lazily_at_0700(tmp_path: Path) -> None:
    """A guard on a fresh profile (no init) creates quarantine/ lazily at 0700."""
    # No state tree pre-created; the guard's first on_trip must make the dir.
    guard = OutputGuard(
        config_root=tmp_path,
        state_root=tmp_path / "fresh-state",
    )
    trip = Trip(pattern_id="x", artifact_name="y.md")
    guard.on_trip(trip, content="z")
    mode = (tmp_path / "fresh-state" / "quarantine").stat().st_mode & 0o777
    assert mode == 0o700


def test_quarantine_files_are_0600(tmp_path: Path) -> None:
    """Quarantined artifact files are written with restrictive permissions.

    The artifact content is by definition sensitive (it tripped the guard);
    the file should not be world/group-readable. The atomic-write temp lives
    in the 0700 dir, and we explicitly chmod the destination to 0600.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    trip = Trip(pattern_id="x", artifact_name="y.md")
    q_path = guard.on_trip(trip, content="secret body")
    mode = q_path.stat().st_mode & 0o777
    assert mode == 0o600, f"quarantined file expected 0600, got {oct(mode)}"


# --- Fail-closed on the guard's own error paths ---------------------------


def test_construction_fails_closed_on_malformed_exclusions(tmp_path: Path) -> None:
    """Malformed exclusions.yaml → construction raises OutputGuardError (fail-closed).

    The writer cannot construct a working guard, so it cannot proceed to emit
    an artifact — the safe outcome on a guarantee module's own error path.
    """
    (tmp_path / "exclusions.yaml").write_text("chat: [unclosed\n", encoding="utf-8")
    (tmp_path / "state").mkdir()
    with pytest.raises(OutputGuardError):
        OutputGuard(config_root=tmp_path, state_root=tmp_path / "state")


def test_construction_fails_closed_on_unknown_tier(tmp_path: Path) -> None:
    """An entry with an unknown tier fails at construction (not at scan time)."""
    (tmp_path / "exclusions.yaml").write_text(
        yaml.safe_dump(
            {"chat": {"s": [{"id": "X", "tier": "secret"}]}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "state").mkdir()
    with pytest.raises(OutputGuardError):
        OutputGuard(config_root=tmp_path, state_root=tmp_path / "state")


def test_construction_fails_closed_on_unreadable_exclusions(tmp_path: Path) -> None:
    """An unreadable exclusions.yaml (e.g. a directory) fails at construction."""
    (tmp_path / "exclusions.yaml").mkdir()  # a directory, not a file
    (tmp_path / "state").mkdir()
    with pytest.raises(OutputGuardError):
        OutputGuard(config_root=tmp_path, state_root=tmp_path / "state")


# --- CLI: mclaw guard scan-vault ------------------------------------------


def _xdg(monkeypatch, tmp_path) -> tuple[Path, Path]:
    cfg = tmp_path / "cfg"
    st = tmp_path / "st"
    monkeypatch.setenv("MCLAW_PROFILE", "mark")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(st))
    return cfg, st


def test_guard_scan_vault_no_vault_configured(monkeypatch, capsys, tmp_path) -> None:
    """Empty/unset vault → ``0 findings``, exit 0 (the §B4 AC)."""
    _xdg(monkeypatch, tmp_path)
    # No init; settings.yaml may not exist or have empty vault path.
    cli.main(["doctor", "--init"])  # creates skeleton config; vault empty
    capsys.readouterr()

    rc = cli.main(["guard", "scan-vault"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "0 findings" in out


def test_guard_scan_vault_clean_vault(monkeypatch, capsys, tmp_path) -> None:
    """A vault with clean notes → ``0 findings``, exit 0."""
    cfg, _ = _xdg(monkeypatch, tmp_path)
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    vault = tmp_path / "vault"
    (vault / "days").mkdir(parents=True)
    (vault / "days" / "2026-07-18.md").write_text("# today\n\nnothing sensitive")

    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "vault": {"path": str(vault)},
                "timezone": "",
                "profile": {"display_name": ""},
                "runtime": {"billing": "subscription", "models": {"triage-judge": ""}},
                "limits": {
                    "max_backfill_pages": 5,
                    "alert_rate_cap": 5,
                    "relabel_window_days": 14,
                },
            }
        )
    )

    rc = cli.main(["guard", "scan-vault"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 findings" in out


def test_guard_scan_vault_finds_blocked_identifier(
    monkeypatch, capsys, tmp_path
) -> None:
    """A vault note containing a blocked id renders as a finding (count > 0)."""
    cfg, _ = _xdg(monkeypatch, tmp_path)
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    vault = tmp_path / "vault"
    (vault / "briefings").mkdir(parents=True)
    (vault / "briefings" / "2026-07-18-morning.md").write_text(
        f"# morning\n\nrefer to {_BLOCKED_ID} for context"
    )

    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    settings.write_text(
        yaml.safe_dump({"vault": {"path": str(vault)}})
    )
    exclusions = cfg / "mark-claw" / "mark" / "exclusions.yaml"
    exclusions.write_text(yaml.safe_dump(_two_tier_chat_exclusions()))

    rc = cli.main(["guard", "scan-vault"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 findings" not in out
    assert "finding" in out  # e.g. "1 finding(s)"


# --- Doctor: quarantine check ---------------------------------------------


def test_doctor_quarantine_zero_renders_ok(monkeypatch, capsys, tmp_path) -> None:
    """Empty quarantine → doctor renders an ok line (not red)."""
    _xdg(monkeypatch, tmp_path)
    # Stub the keychain probe so tests never touch the real login keychain.
    from mclaw_core import secret
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: [])
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    cli.main(["doctor"])
    out = capsys.readouterr().out
    # The quarantine count line is labeled "quarantine artifacts"; search for
    # "artifact" to avoid matching the perms/ line or the temp dir path (which
    # contains "quarantine" because of the test name).
    q_line = next(
        (line for line in out.splitlines() if "artifact" in line.lower()), ""
    )
    assert q_line, "expected a quarantine artifacts line in the doctor report"
    assert "[FAIL]" not in q_line


def test_doctor_quarantine_nonzero_renders_red(monkeypatch, capsys, tmp_path) -> None:
    """Nonzero quarantined-artifact count renders red ([FAIL]) per §5.4 / §B4."""
    _cfg, st = _xdg(monkeypatch, tmp_path)
    from mclaw_core import secret
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: [])
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    # Drop a fake quarantined artifact (simulating a prior guard trip).
    q = st / "mark-claw" / "mark" / "quarantine"
    (q / "2026-07-18T120000Z--brief--abcd1234.md").write_text("blocked body")

    cli.main(["doctor"])
    out = capsys.readouterr().out
    q_line = next(
        (line for line in out.splitlines() if "artifact" in line.lower()), ""
    )
    assert q_line, "expected a quarantine artifacts line in the doctor report"
    assert "[FAIL]" in q_line
    assert "1" in q_line  # the count surfaces


def test_doctor_quarantine_ignores_sidecar_metadata(
    monkeypatch, capsys, tmp_path
) -> None:
    """The quarantine count must reflect artifacts, not audit sidecars.

    A ``.json`` sidecar (reserved for future per-trip metadata) is not counted
    as a quarantined artifact — only artifact files contribute to the red
    count.
    """
    _cfg, st = _xdg(monkeypatch, tmp_path)
    from mclaw_core import secret
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: [])
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    q = st / "mark-claw" / "mark" / "quarantine"
    (q / "2026-07-18T120000Z--brief--abcd1234.md").write_text("blocked body")
    (q / "ignored.json").write_text("{}")

    cli.main(["doctor"])
    out = capsys.readouterr().out
    q_line = next(
        (line for line in out.splitlines() if "artifact" in line.lower()), ""
    )
    assert "1 artifact" in q_line  # not 2
