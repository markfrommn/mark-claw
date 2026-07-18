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
import os
import stat
from pathlib import Path
from typing import Any

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
    # The blocked entry's id appears in the pattern_id (the trip traces back to
    # the exact exclusions.yaml entry that fired). The prior form
    # ``_BLOCKED_ID not in ... or _BLOCKED_ID in ...`` was a tautology — always
    # True — and never asserted anything.
    assert _BLOCKED_ID in result.pattern_id
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


# --- casefold parity with the fetch gate -----------------------------------
#
# The fetch gate matches chat ``name`` / ``also_match`` and meeting ``title``
# via :meth:`str.casefold` (see ``mclaw_core.exclusion``). Casefold equates
# characters that ``re.IGNORECASE`` (Unicode simple case-folding) does NOT —
# the canonical example is German ``ß`` ↔ ``SS``. Before the casefold-parity
# fix, the guard compiled patterns with ``re.IGNORECASE`` and would return
# ``Clean`` for artifact text ``STRASSE`` against an excluded chat named
# ``Straße``, even though the gate blocks the same identifier at enumeration.
# The guard's casing semantics must agree with the gate's (§5.4
# defense-in-depth). These tests pin that parity.


def test_casefold_parity_sSharfS_matches_ss(tmp_path: Path) -> None:
    """An excluded chat named ``Straße`` trips the guard on artifact text ``STRASSE``.

    This is the ß↔SS case ``re.IGNORECASE`` misses: simple case-folding keeps
    ``ß`` as a single codepoint and does not equate it to the two-codepoint
    ``ss``. Full :meth:`str.casefold` does (``"ß".casefold() == "ss"``), so the
    guard's casefold-both-sides scan must catch the uppercase artifact text
    that the gate would also block.
    """
    guard = _guard_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {"name": "Straße", "tier": "blocked"},
                ]
            }
        },
    )
    result = guard.scan(
        "see the STRASSE channel for details", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip), (
        "casefold (not re.IGNORECASE) must catch ß↔SS — the gate blocks this "
        "identifier, so the guard must too"
    )
    assert "Straße" in result.pattern_id


def test_casefold_parity_clean_ascii_still_clean(tmp_path: Path) -> None:
    """The converse: an unrelated ASCII artifact with no denied identifier
    returns :class:`Clean`. Guards against the casefold fix becoming an
    over-broad match-everything regression (casefold is non-trivial on some
    Unicode classes; this pins the clean baseline)."""
    guard = _guard_with(
        tmp_path,
        exclusions={
            "chat": {
                "slack-work": [
                    {"name": "Straße", "tier": "blocked"},
                ]
            }
        },
    )
    result = guard.scan(
        "standup notes: shipped the onboarding flow, no blockers",
        surface=Surface.PERSISTENCE,
    )
    assert isinstance(result, Clean)


def test_casefold_parity_ascii_behaviour_unchanged(tmp_path: Path) -> None:
    """Casefold == lower for ASCII, so the canonical ASCII fixture must keep
    behaving exactly as before the casefold-parity fix: an upper-case
    occurrence of the blocked id still trips, and a clean text still cleans.

    This is the explicit assertion that the existing ASCII suite stays green
    on purpose (casefold is a strict superset of lower over ASCII), not by
    accident — the ß↔SS fix must not perturb ASCII matching.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    # Upper-case occurrence trips (casefold of "C0HRCHAN" is "c0hrchan";
    # casefold of the upper-case scan text is the same lower-case form).
    assert isinstance(
        guard.scan(f"see {_BLOCKED_ID} now", surface=Surface.PERSISTENCE), Trip
    )
    # Unrelated ASCII text is still clean.
    assert isinstance(
        guard.scan("nothing to see here", surface=Surface.PERSISTENCE), Clean
    )


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


def test_on_trip_refuses_symlink_quarantine_dir(tmp_path: Path) -> None:
    """A symlink at ``state/quarantine`` is refused (fail-closed, §5.4).

    ``_quarantine_dir`` advertises ``O_NOFOLLOW | O_DIRECTORY`` symlink refusal
    as a fail-closed measure: an attacker (or a misconfigured sync tool) who
    can plant a symlink at the quarantine root must not trick the guard into
    writing the tripped artifact through it to an attacker-controlled dir.
    ``on_trip`` must raise :class:`OutputGuardError` and write nothing.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    # Plant a symlink at state/quarantine pointing at an attacker-controlled
    # dir. _guard_with pre-mkdirs state_root but NOT quarantine, so the link
    # is the quarantine root the guard will resolve on its first trip.
    target = tmp_path / "attacker-controlled"
    target.mkdir()
    link = tmp_path / "state" / "quarantine"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlink in test tmp_path here: {exc}")
    # Sanity: the link resolves as a dir (so mkdir(exist_ok=True) will NOT be
    # the branch that catches the attack — O_NOFOLLOW at open must).
    assert link.is_dir()

    trip = Trip(pattern_id="x", artifact_name="y.md")
    with pytest.raises(OutputGuardError):
        guard.on_trip(trip, content="blocked body")

    # The artifact must NOT have been written through the symlink.
    assert not any(target.iterdir()), (
        "guard wrote through the symlinked quarantine dir — O_NOFOLLOW refusal "
        "did not fire"
    )


def test_on_trip_quarantine_write_anchored_to_validated_dir_fd(tmp_path: Path) -> None:
    """TOCTOU regression: a path-swap after dir validation cannot redirect the write.

    The pre-fix code validated the ``quarantine`` dir with ``O_NOFOLLOW |
    O_DIRECTORY`` but then closed the fd and wrote via path-qualified
    ``tempfile.mkstemp`` + ``os.replace`` — re-resolving the directory after
    validation. Between validation and the write, an attacker who can mutate
    the state tree could swap the ``quarantine`` path for a symlink and
    redirect the (sensitive, guard-tripped) artifact body to an
    attacker-controlled dir.

    The fix holds the validated dir fd open through publication and uses
    ``dir_fd``-qualified (``openat``-style) operations for the temp create,
    write, ``fsync``, ``replace``, ``chmod``, and unlink. To prove the write
    is anchored to the fd (not the path), this test subclasses
    :class:`OutputGuard` and overrides :meth:`_quarantine_dir` to perform the
    swap *between validation and write*: it calls ``super()`` to obtain the
    validated fd, then moves the real dir aside and plants a symlink at the
    original path → attacker-controlled dir. The fd still references the real
    (moved) dir's inode; the path now resolves through the symlink.

    A correct (fd-anchored) implementation writes the body into the moved real
    dir; the symlink target stays empty. A path-qualified regression would
    write through the symlink into the attacker dir.
    """
    _write_exclusions(tmp_path, _two_tier_chat_exclusions())
    st = tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)

    attacker = tmp_path / "attacker-controlled"
    attacker.mkdir()
    # Where the real quarantine dir gets moved aside to during the swap. Must
    # be on the same filesystem as ``st/quarantine`` for ``os.rename`` to be
    # atomic — keeping it under ``st`` guarantees that.
    moved_to = st / "quarantine-real-moved-aside"

    class _SwapGuard(OutputGuard):
        """Swaps the quarantine PATH for a symlink after the fd is validated."""

        def _quarantine_dir(self) -> tuple[Path, int]:
            q, fd = super()._quarantine_dir()
            # TOCTOU interpose: the fd references the real dir's inode; the
            # path no longer does.
            os.rename(q, moved_to)
            q.symlink_to(attacker, target_is_directory=True)
            return q, fd

    guard = _SwapGuard(config_root=tmp_path, state_root=st)
    trip = Trip(pattern_id="x", artifact_name="y.md")

    q_path = guard.on_trip(trip, content="blocked body content")

    # The body MUST have landed in the real (fd-anchored, moved) dir.
    moved_files = [p for p in moved_to.iterdir() if p.is_file()]
    assert len(moved_files) == 1, (
        f"artifact body did not land in the real (fd-anchored) quarantine "
        f"dir; the write was not anchored to the validated fd; got {moved_files}"
    )
    assert moved_files[0].read_text(encoding="utf-8") == "blocked body content"

    # The symlink target MUST be empty — the path-swap did not redirect the
    # write through the symlink.
    attacker_files = [p for p in attacker.iterdir() if p.is_file()]
    assert not attacker_files, (
        f"artifact body was redirected through the swapped symlink into the "
        f"attacker-controlled dir; got {attacker_files}"
    )
    # q_path is the symlinked path (post-swap); the real file is in moved_to,
    # so q_path does not resolve to the body. That is the expected fail-safe
    # behavior under attack: the body is safe, the path recorded for review is
    # the configured (pre-swap) path. This test pins the body-safety
    # property, not q_path's post-attack resolvability.
    assert q_path.name == moved_files[0].name


def test_on_trip_fsyncs_quarantine_dir_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX durability: fsyncing the file body alone does not persist the
    rename — the new directory entry can be lost on crash/power-loss after
    the method returns, while the caller has already suppressed the original
    write. The quarantine copy is the preserved-for-review artifact (§5.4),
    so the quarantine *directory* fd must be fsynced after the rename, not
    only the temp-file fd.

    A crash cannot be simulated portably, so this test pins the contract by
    spying on ``os.fsync``: it counts fsync calls whose live fd refers to a
    directory. After a trip, at least one directory fd must have been
    fsynced. The directory check runs at fsync time (while the fd is live),
    so the assertion is immune to later fd-integer reuse by the changelog
    and review-queue file writes.
    """
    _write_exclusions(tmp_path, _two_tier_chat_exclusions())
    st = tmp_path / "state"
    st.mkdir(parents=True, exist_ok=True)

    fsynced_dir_count = 0
    real_fsync = os.fsync

    def _spy_fsync(fd: int) -> None:
        nonlocal fsynced_dir_count
        try:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                fsynced_dir_count += 1
        except OSError:
            pass
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy_fsync)

    guard = OutputGuard(config_root=tmp_path, state_root=st)
    trip = Trip(pattern_id="chat:slack-work[id=C0HRCHAN]", artifact_name="d.md")
    guard.on_trip(trip, content="blocked body")

    assert fsynced_dir_count >= 1, (
        "os.fsync was never called on the quarantine directory fd; the "
        "rename is not made durable (file fsync alone does not persist the "
        "directory entry holding the quarantined artifact)"
    )


# --- Quarantine filename NAME_MAX truncation ------------------------------


def _quarantine_records(tmp_path: Path) -> tuple[list[Any], list[Any]]:
    """Collect (changelog records, review-queue items) written for trips."""
    log_path = next((tmp_path / "state" / "changelog").glob("*.jsonl").__iter__())
    changelog: list[Any] = [
        json.loads(line) for line in log_path.read_text().splitlines() if line
    ]
    queue_path = tmp_path / "state" / "review-queue" / "pending.jsonl"
    queue: list[Any] = []
    if queue_path.is_file():
        queue = [
            json.loads(line)
            for line in queue_path.read_text().splitlines()
            if line
        ]
    return changelog, queue


def test_on_trip_long_ascii_stem_truncates_to_name_max(tmp_path: Path) -> None:
    """A source stem that is > 255 ASCII bytes must still quarantine cleanly.

    Regression: ``_quarantine_artifact`` composed the final filename from the
    full (untruncated) slug, so a long source stem produced a
    ``final_name`` longer than the filesystem's 255-byte ``NAME_MAX``.
    ``os.replace`` then raised ``ENAMETOOLONG`` *before* the rename landed,
    which propagated out of ``on_trip`` before the changelog and review-queue
    appends — the tripped artifact was neither written to its destination nor
    preserved in quarantine for review. That is strictly worse than §5.4's
    accepted "lost until reviewed" state, which assumes a quarantine copy
    exists.

    With the fix the slug is truncated to fit ``NAME_MAX`` by UTF-8 encoded
    byte length, ``on_trip`` returns a real path, the quarantined file exists
    at mode 0600 under ``quarantine/``, and the changelog ``guard.trip`` and
    review-queue items are still appended.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    text = f"briefing mentioning {_BLOCKED_ID}"
    # 300-char ASCII stem — encodes to 300 bytes, well over the 255-byte cap
    # after the timestamp/uuid/extension parts are added.
    long_stem = "a" * 300
    artifact_name = f"{long_stem}.md"
    trip = guard.scan(text, surface=Surface.PERSISTENCE, artifact_name=artifact_name)
    assert isinstance(trip, Trip)

    q_path = guard.on_trip(trip, content=text, actor="briefing-assembler")

    # The trip succeeded: a quarantine file exists at the returned path...
    assert q_path.is_file()
    assert q_path.read_text(encoding="utf-8") == text
    assert q_path.is_relative_to(tmp_path / "state" / "quarantine")
    # ...with 0600 permissions like every other quarantined artifact.
    mode = q_path.stat().st_mode & 0o777
    assert mode == 0o600, f"quarantined file expected 0600, got {oct(mode)}"

    # The composed filename fits NAME_MAX (a byte limit) and is valid UTF-8.
    name_bytes = q_path.name.encode("utf-8")
    assert len(name_bytes) <= 255, (
        f"quarantine filename exceeds 255-byte NAME_MAX: {len(name_bytes)} bytes"
    )
    # ``encode`` round-trips only if there are no dangling partial chars.
    assert name_bytes.decode("utf-8") == q_path.name

    # The changelog and review-queue are still appended — the trip is auditable.
    changelog, queue = _quarantine_records(tmp_path)
    assert len(changelog) == 1
    assert changelog[0]["action"] == "guard.trip"
    assert changelog[0]["target"] == artifact_name
    assert changelog[0]["detail"]["quarantine_path"] == str(q_path)
    assert len(queue) == 1
    assert queue[0]["bucket"] == "sign-off"


def test_on_trip_multibyte_stem_truncates_on_char_boundary(tmp_path: Path) -> None:
    """A multibyte stem whose truncation point falls mid-character must still
    quarantine cleanly, with no dangling partial UTF-8 byte tail in the name.

    Same failure mode as the ASCII case: an over-long filename would lose the
    trip. The byte-level truncation additionally has to land on a UTF-8
    character boundary, otherwise ``final_name`` would be invalid UTF-8 and
    (worse) any downstream consumer that re-encodes the path would see a
    surrogate/error. The fix decodes the truncated byte prefix with
    ``errors="ignore"`` so any partial trailing multibyte character is
    dropped whole.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    text = f"briefing mentioning {_BLOCKED_ID}"
    # ``ä`` is 2 UTF-8 bytes; 130 of them = 260 bytes. The slug truncation
    # budget is well under 260, so the cut lands in the middle of a multibyte
    # character — exactly the case ``errors="ignore"`` must defend.
    long_stem = "ä" * 130
    artifact_name = f"{long_stem}.md"
    trip = guard.scan(text, surface=Surface.PERSISTENCE, artifact_name=artifact_name)
    assert isinstance(trip, Trip)

    q_path = guard.on_trip(trip, content=text, actor="briefing-assembler")

    assert q_path.is_file()
    assert q_path.read_text(encoding="utf-8") == text

    name_bytes = q_path.name.encode("utf-8")
    assert len(name_bytes) <= 255, (
        f"quarantine filename exceeds 255-byte NAME_MAX: {len(name_bytes)} bytes"
    )
    # Round-trips cleanly — no partial multibyte tail survived truncation.
    assert name_bytes.decode("utf-8") == q_path.name

    changelog, queue = _quarantine_records(tmp_path)
    assert len(changelog) == 1
    assert changelog[0]["action"] == "guard.trip"
    assert len(queue) == 1


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


def test_doctor_quarantine_counts_all_regular_files(
    monkeypatch, capsys, tmp_path
) -> None:
    """The quarantine count reflects every regular file in ``quarantine/``.

    The guard currently writes NO sidecars — every trip's metadata lives in
    the changelog, and the artifact body is the only file written — so any
    file in ``quarantine/`` is a quarantined artifact, including a
    ``.json``-named one (a real artifact named e.g. ``report.json``). Earlier
    code excluded ``.json`` in anticipation of per-trip sidecars that were
    never added; that undercounted and let a quarantined ``report.json`` hide
    from review. If a future sidecar convention lands it must use a
    distinguishable suffix (``.meta.json``) or ``.meta/`` subdir.
    """
    _cfg, st = _xdg(monkeypatch, tmp_path)
    from mclaw_core import secret
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: [])
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    q = st / "mark-claw" / "mark" / "quarantine"
    (q / "2026-07-18T120000Z--brief--abcd1234.md").write_text("blocked body")
    (q / "2026-07-18T120100Z--report--beef5678.json").write_text("{}")

    cli.main(["doctor"])
    out = capsys.readouterr().out
    q_line = next(
        (line for line in out.splitlines() if "artifact" in line.lower()), ""
    )
    assert "2 artifacts" in q_line  # both regular files count


# --- Macroscope follow-on: fail-closed gap coverage (DEV-15 review) --------


def test_compile_chat_empty_entry_fails_closed(tmp_path: Path) -> None:
    """A chat entry with none of id/name/also_match must fail at construction.

    Without this check the entry would add zero _Pattern objects, the guard
    would compile clean, and every scan would return Clean for that entry —
    the guarantee silently lost. The gate rejects this shape; the guard must
    too. Fail-closed on the silent-zero-pattern config.
    """
    _write_exclusions(
        tmp_path,
        {"chat": {"slack-work": [{"tier": "blocked"}]}},
    )
    (tmp_path / "state").mkdir()
    with pytest.raises(OutputGuardError):
        OutputGuard(config_root=tmp_path, state_root=tmp_path / "state")


def test_scan_rejects_unknown_surface_value(tmp_path: Path) -> None:
    """``scan`` accepts ONLY the two valid Surface members; else raise.

    The typed signature ``surface: Surface`` is not enforced at runtime, so a
    caller bug passing a wrong-type value (a string, None, an int) must not
    silently fall through to the alert-log (blocked-only) pattern set — that
    would let an ephemeral identifier pass as Clean on an intended-
    persistence scan. A guarantee module surfaces caller bugs loudly.
    """
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    # Cast through ``object`` then back to ``Surface`` to defeat mypy without
    # introducing a real Surface member. At runtime this is a plain string,
    # which exercises the else-branch.
    bad_surface: Surface = "persistence"  # type: ignore[assignment]
    with pytest.raises(OutputGuardError):
        guard.scan(
            f"see {_BLOCKED_ID}", surface=bad_surface, artifact_name="x.md"
        )


def test_scan_rejects_none_surface(tmp_path: Path) -> None:
    """``None`` is not a valid surface — fail closed rather than degrade."""
    guard = _guard_with(tmp_path, exclusions=_two_tier_chat_exclusions())
    bad_surface: Surface = None  # type: ignore[assignment]
    with pytest.raises(OutputGuardError):
        guard.scan(f"see {_BLOCKED_ID}", surface=bad_surface)


def test_drive_path_trailing_slash_matches_no_trailing_text(tmp_path: Path) -> None:
    """A configured ``/HR/`` matches text ``"see /HR"`` (no trailing slash).

    The fetch gate normalizes ``/HR/`` → ``/HR``; the output guard must do
    the same so the two layers agree on what's blocked. Before the fix the
    raw ``/HR/`` was compiled verbatim, so a text occurrence without the
    trailing slash ("see /HR for details") passed as Clean.
    """
    guard = _guard_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR/", "tier": "blocked"}]}},
    )
    result = guard.scan("see /HR for the details", surface=Surface.PERSISTENCE)
    assert isinstance(result, Trip)


def test_drive_path_trailing_slash_matches_nested_path(tmp_path: Path) -> None:
    """A configured ``/HR/`` still matches ``"see /HR/payroll"`` after normalization.

    Regression guard on the trailing-slash fix: rstrip must not eat into the
    real path content. ``/HR/`` → ``/HR`` still substring-matches
    ``/HR/payroll``.
    """
    guard = _guard_with(
        tmp_path,
        exclusions={"drive": {"gdrive-work": [{"path": "/HR/", "tier": "blocked"}]}},
    )
    result = guard.scan("see /HR/payroll for salary", surface=Surface.PERSISTENCE)
    assert isinstance(result, Trip)


def test_meeting_title_substring_matches_inflected(tmp_path: Path) -> None:
    """A title ``Comp review`` trips artifact text ``Comp reviews Q3``.

    The fetch gate matches event titles by case-insensitive *containment* —
    ``Comp review`` blocks an event titled ``Comp reviews Q3``. The output
    guard must compile titles with the substring compiler so the two layers
    agree; a word-boundary compile would let the inflected form through
    (trailing ``s`` defeats ``(?!\\w)``) even though the gate blocked it.
    """
    guard = _guard_with(
        tmp_path,
        exclusions={
            "meetings": [
                {"series_id": "abc123", "title": "Comp review", "tier": "blocked"}
            ]
        },
    )
    result = guard.scan(
        "Comp reviews Q3 went long today", surface=Surface.PERSISTENCE
    )
    assert isinstance(result, Trip)


def test_doctor_quarantine_unreadable_dir_is_fail_no_traceback(
    monkeypatch, capsys, tmp_path
) -> None:
    """An unreadable ``quarantine/`` dir renders as a FAIL check, not a traceback.

    Covers the ``iterdir()`` OSError path: ``is_dir()`` reads metadata only,
    so an existing-but-unreadable dir (mode regression) raises at enumeration.
    The doctor must surface it as a FAIL line and keep going, never crash.
    POSIX ``root`` bypasses the ``0o000`` mode so the test skips there.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses mode 0o000 — cannot exercise unreadable dir")
    _cfg, st = _xdg(monkeypatch, tmp_path)
    from mclaw_core import secret
    monkeypatch.setattr(secret, "list_accounts", lambda *, profile: [])
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    q = st / "mark-claw" / "mark" / "quarantine"
    (q / "2026-07-18T120000Z--brief--abcd1234.md").write_text("blocked body")
    os.chmod(q, 0o000)
    try:
        # Must not raise; must render a FAIL line.
        rc = cli.main(["doctor"])
        out = capsys.readouterr().out
    finally:
        # Restore read/write/exec so pytest's tmp_path cleanup can rm it.
        os.chmod(q, stat.S_IRWXU)

    q_line = next(
        (line for line in out.splitlines() if "artifact" in line.lower()), ""
    )
    assert q_line, "expected a quarantine artifacts line in the doctor report"
    assert "[FAIL]" in q_line
    assert "unreadable" in q_line
    _ = rc  # exit code is incidental to the no-traceback contract; the FAIL
            # line is what proves the path was reached without crashing.


def test_guard_scan_vault_relative_path_treated_as_unset(
    monkeypatch, capsys, tmp_path
) -> None:
    """A relative ``vault.path`` is treated as unset → ``0 findings`` / exit 0.

    Without this guard the scan would resolve the relative path against the
    CLI process's cwd (launchd invokes with cwd ``/``), which is not a stable
    anchor for the vault. The scan could report ``0 findings`` while the real
    vault is never scanned (false clean) or scan an unrelated directory.
    """
    cfg, _ = _xdg(monkeypatch, tmp_path)
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    # Relative vault path — would be resolved against cwd if not treated as
    # unset. The test runs from a known cwd, so this assertion is only
    # meaningful if the resolver returns None regardless of cwd contents.
    settings = cfg / "mark-claw" / "mark" / "settings.yaml"
    settings.write_text(yaml.safe_dump({"vault": {"path": "notes"}}))

    # Even if a directory named "notes" happens to exist in cwd, the resolver
    # must treat the relative path as unset.
    rc = cli.main(["guard", "scan-vault"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 findings" in out


def test_guard_scan_vault_unreadable_note_is_nonzero_with_warning(
    monkeypatch, capsys, tmp_path
) -> None:
    """An unreadable vault note surfaces as a warning + nonzero exit.

    Without this the scan would silently skip the note and report ``0
    findings`` — a false clean if the unreadable note was the one carrying
    a blocked identifier. The scan-vault contract distinguishes 0-findings=
    exit-0 from failure=exit-1; an incomplete scan is a failure.

    POSIX ``root`` bypasses the ``0o000`` mode so the test skips there.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses mode 0o000 — cannot exercise unreadable note")
    cfg, _ = _xdg(monkeypatch, tmp_path)
    cli.main(["doctor", "--init"])
    capsys.readouterr()

    vault = tmp_path / "vault"
    (vault / "days").mkdir(parents=True)
    clean_note = vault / "days" / "clean.md"
    clean_note.write_text("# today\n\nnothing sensitive")
    unreadable_note = vault / "days" / "unreadable.md"
    unreadable_note.write_text("# would carry a blocked id if we could read it")
    # Wire the vault into settings so the scan actually reaches the notes
    # (without this the resolver returns None and the scan short-circuits to
    # "0 findings" before enumerating any note — masking the very behavior
    # under test).
    (cfg / "mark-claw" / "mark" / "settings.yaml").write_text(
        yaml.safe_dump({"vault": {"path": str(vault)}})
    )
    os.chmod(unreadable_note, 0o000)
    try:
        rc = cli.main(["guard", "scan-vault"])
        captured = capsys.readouterr()
    finally:
        os.chmod(unreadable_note, stat.S_IRUSR | stat.S_IWUSR)

    assert rc == 1, "an incomplete scan must exit nonzero (not 0-findings exit 0)"
    err = captured.err
    assert "unreadable" in err.lower()
    assert "unreadable.md" in err, "the skipped note's name must surface"
