"""Output guard — fail-closed defense-in-depth on the exclusion guarantee.

(Design §5.4 — the output-side half of the hard guarantee.) The fetch gate
(:mod:`mclaw_core.exclusion`, DEV-14) blocks items at enumeration; this module
scans the *text of every emitted artifact* for blocked/ephemeral identifiers
*before* that artifact is written or sent, and **fails closed** on a hit: the
artifact is moved to ``state/quarantine/`` (mode ``0o700``, outside the vault
and outside any sync), a ``guard.trip`` record is appended to the changelog,
and a needs-sign-off review item is queued. The Telegram system alert is
Phase 2 (the notify layer does not exist yet); the hook is left here as a
clearly-marked no-op stub.

The guard never logs matched content — only the pattern id and artifact name
(§5.4). A :class:`Trip` carries exactly those two fields and nothing else;
the changelog and review-queue records carry only those plus the quarantine
path. The matched text lives on only inside the quarantined artifact file,
which a human reviewer reads.

Two-surface split (§5.4):

* :data:`Surface.PERSISTENCE` — vault notes, briefing markdown, email/draft
  bodies, EOD/weekly output. Matches identifiers from **blocked AND ephemeral**
  entries (ephemeral content must never be summarized into a vault artifact).
* :data:`Surface.ALERT_LOG` — alert texts, log lines. Matches **blocked only**.
  Alerts legitimately name ephemeral sources as pointers ("check Signal —
  urgent message from J."), so ephemeral identifiers must not trip here.

Hard-rule posture (CLAUDE.md): **fail closed on the hard guarantee, fail open
on everything else.** This module is a hard-guarantee module, so any internal
uncertainty surfaces loudly — unreadable ``exclusions.yaml`` raises
:class:`OutputGuardError` at construction (the writer cannot proceed), and a
symlink at the quarantine dir is refused via ``O_NOFOLLOW`` rather than
followed.
"""

from __future__ import annotations

import enum
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import paths

# Reuse the gate's YAML-narrowing helpers and config-error type — same package,
# same config file, same fail-loud contract. The guard's compile is a different
# shape (flat identifier set, not per-source ref-matching tables), but the
# YAML-shape validation is identical and sharing it keeps the two modules
# consistent under config-schema evolution without coupling the guard to the
# gate's private ref-matching dataclasses (``_ChatEntry`` etc.).
from .exclusion import (
    ExclusionConfigError,
    _as_list,
    _as_mapping,
    _opt_str,
    _str_list,
)

#: Closed ``action`` enum value for an output-guard trip in the changelog
#: (design §4.4 — ``…|guard.trip|…``). Hard-coded here rather than reading
#: from a shared vocabulary module because no such module exists yet; when
#: one lands, this constant moves there and call sites stay literal.
ACTION_GUARD_TRIP = "guard.trip"

#: Tier vocabulary (§5.3 — blocked / ephemeral). Mirrors the gate's
#: :data:`mclaw_core.exclusion._ENTRY_TIERS` keys. Stored as plain strings
#: because the guard only needs the tier as a set-membership tag (which
#: surface set the pattern belongs to), not as a tri-state decision.
_BLOCKED = "blocked"
_EPHEMERAL = "ephemeral"
_TIERS: frozenset[str] = frozenset({_BLOCKED, _EPHEMERAL})


class Surface(enum.Enum):
    """Where the artifact is going to be emitted (§5.4 two-surface split).

    The surface selects which deny-pattern set is applied:

    * :data:`PERSISTENCE` uses blocked + ephemeral identifiers — vault notes,
      briefing markdown, email bodies, drafts, EOD, weekly review. Ephemeral
      content must never reach a persistence surface (§5.3).
    * :data:`ALERT_LOG` uses blocked identifiers only — alert texts and log
      lines, which legitimately name ephemeral sources as pointers.
    """

    PERSISTENCE = "persistence"
    ALERT_LOG = "alert_log"


@dataclass(frozen=True)
class Clean:
    """Scan result: the artifact is clean — emit it."""


@dataclass(frozen=True)
class Trip:
    """Scan result: the artifact tripped the guard — do NOT emit it.

    Carries only :attr:`pattern_id` (a stable string constructed from the
    matching exclusion entry's source and identifier — never the matched
    content) and :attr:`artifact_name` (the logical name the caller passed to
    :meth:`OutputGuard.scan`). The matched text itself is deliberately absent:
    §5.4 forbids logging matched content, and a Trip is what gets logged.
    """

    pattern_id: str
    artifact_name: str


class OutputGuardError(Exception):
    """Raised at construction when the guard cannot compile a fail-closed policy.

    A guarantee module must not silently degrade. Unreadable or malformed
    ``exclusions.yaml`` surfaces here (wrapping the gate's
    :class:`ExclusionConfigError`) rather than compiling a permissive pattern
    set; the writer's flow treats construction failure as "cannot proceed
    safely" and aborts the emit — fail closed on the hard guarantee.
    """


# --- compiled deny-pattern -------------------------------------------------


@dataclass(frozen=True)
class _Pattern:
    """One compiled deny-pattern with the metadata a Trip needs.

    :attr:`pattern_id` is a stable, human-readable string constructed from the
    entry's source + identifier (e.g. ``chat:slack-work[id=C0HRCHAN]``). It
    surfaces in the Trip, changelog, and review-queue so the operator can
    trace a trip back to the exact ``exclusions.yaml`` entry. It carries
    config-side identifiers only — never the matched text from the artifact.

    :attr:`tier` is the spec's tier vocabulary (``"blocked"`` / ``"ephemeral"``)
    and selects which surface set the pattern belongs to (blocked → both
    surfaces; ephemeral → persistence only).
    """

    pattern_id: str
    tier: str
    regex: re.Pattern[str]


def _compile_token_pattern(identifier: str) -> re.Pattern[str]:
    """Compile a word-bounded token regex; match against casefolded scan text.

    The identifier is :meth:`str.casefold`-ed at compile time and the regex is
    **not** ``re.IGNORECASE`` — :meth:`OutputGuard.scan` searches the
    casefolded artifact text, so the casefold happens exactly once per scan
    rather than once per pattern, and the casefold semantics match the fetch
    gate's chat ``name`` / ``also_match`` matching (the gate compiles
    ``name.casefold()`` and compares against ``ref.name.casefold()`` in
    :meth:`mclaw_core.exclusion.ExclusionGate._check_chat`). Casefold, not
    ``re.IGNORECASE`` (Unicode simple-case-folding), is what equates ``ß``↔
    ``ss`` — the two diverge, so an excluded chat named ``Straße`` would trip
    the gate on artifact text ``STRASSE`` but pass the guard under
    ``re.IGNORECASE``. The guard's casing semantics must agree with the gate's
    (§5.4 defense-in-depth); casefold parity closes that gap.

    The boundary is ``(?<!\\w)`` / ``(?!\\w)`` (no word char adjacent on either
    side) rather than ``\\b`` so identifiers that themselves start or end with a
    non-word character (a Slack channel like ``#people-private``, a meeting
    title with punctuation) still anchor correctly — ``\\b`` requires a
    ``\\w``↔``\\W`` transition at the boundary position, so it silently fails
    to match ``#foo`` at the start of a line (no transition between
    start-of-string and ``#``). The lookarounds apply to whatever is *outside*
    the identifier and work in all positions; ``\\w``-ness is preserved under
    casefold (the casefold of a word char is a word char), so a pattern
    anchored once against the casefolded text still anchors correctly.

    Examples anchored correctly:

    * ``C0HRCHAN`` matches in ``"see C0HRCHAN for"`` but NOT in
      ``"C0HRCHANX"`` (longer token).
    * ``#people-private`` matches at the start of a line and after whitespace.
    * ``abc123`` (a meeting series id) matches as a whole token.
    """
    return re.compile(rf"(?<!\w){re.escape(identifier.casefold())}(?!\w)")


def _compile_substring_pattern(identifier: str) -> re.Pattern[str]:
    """Compile a substring regex for free-text spans; match casefolded scan text.

    Used for two identifier kinds that appear in artifact text as contiguous
    substrings rather than as whole tokens:

    * **drive paths** — ``/HR`` blocks ``/HR/x`` via path-ancestry in the gate,
      but in artifact *text* a path appears as a literal substring (e.g.
      ``"see /HR/payroll"``). Plain substring match catches every textual
      occurrence; applying word boundaries would miss ``/HR/payroll`` against
      an ``/HR/`` entry because the char after ``/HR/`` is ``p`` — a word
      char — and the trailing boundary would reject the match.
    * **meeting titles** — the fetch gate matches event titles by
      case-insensitive *containment* (a title ``Comp review`` blocks an event
      titled ``Comp reviews Q3``). The output guard must apply the same model
      so the two layers agree on what an excluded title is; a word-boundary
      compile here would let ``Comp reviews Q3`` through (trailing ``s``
      defeats ``(?!\\w)``) even though the gate blocked it.

    Like :func:`_compile_token_pattern`, the identifier is casefolded at
    compile time and the regex is **not** ``re.IGNORECASE`` —
    :meth:`OutputGuard.scan` searches the casefolded artifact text. The gate's
    meeting title fallback uses :meth:`str.casefold` containment (see
    :meth:`mclaw_core.exclusion.ExclusionGate._check_meeting`), so casefold
    parity keeps the two layers in agreement on Unicode identifiers where
    ``re.IGNORECASE`` (simple case-folding) would diverge from full casefold.

    Trailing slashes are stripped before escaping (and before casefold) so a
    configured ``/HR/`` matches text ``"see /HR"`` (no trailing slash) — the
    gate normalizes the same way. Substring is broader than token match and
    therefore fail-closed (more quarantine, no leak).
    """
    # ``rstrip("/") or identifier`` keeps a sole-"/" path from collapsing to
    # the empty string (which would match everything); it is an extreme edge
    # case but the guard must not silently match all text on a degenerate
    # config value. Casefold applied after the trailing-slash normalization so
    # the pattern matches the casefolded scan text (see OutputGuard.scan).
    normalized = (identifier.rstrip("/") or identifier).casefold()
    return re.compile(re.escape(normalized))


def _slugify(name: str) -> str:
    """Make a filesystem-safe slug from ``name`` for the quarantine filename.

    Collapses any run of non-[alnum/``.``/``_``/``-``] to a single ``-`` and
    trims leading/trailing dashes. An empty result (e.g. ``name`` was all
    punctuation) falls back to ``"artifact"`` so the filename is never empty.
    """
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return s or "artifact"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso_now() -> str:
    """ISO-8601 UTC timestamp with second precision and ``Z`` suffix.

    Matches the format in the design §4.4 changelog examples
    (``2026-07-05T15:05:12Z``).
    """
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_date_month() -> str:
    """Current UTC ``YYYY-MM`` for the per-month changelog partition (§4.4)."""
    return _utc_now().strftime("%Y-%m")


# --- pattern compilation (per section) -------------------------------------


def _tier_value(entry: dict[str, object], *, context: str) -> str:
    """Read and validate the ``tier`` field, returning it as a plain string.

    Returns the tier vocabulary value (``"blocked"`` or ``"ephemeral"``) rather
    than the gate's :class:`~mclaw_core.exclusion.Decision` enum: the guard
    only needs the tier as a set-membership tag (which surface set the pattern
    belongs to), not as a tri-state decision. An unknown or non-string tier is
    a config bug — fail loud (mirrors :func:`mclaw_core.exclusion._entry_tier`).
    """
    raw = entry.get("tier")
    if not isinstance(raw, str):
        raise ExclusionConfigError(
            f"{context}: 'tier' must be one of {sorted(_TIERS)}, "
            f"got {type(raw).__name__} ({raw!r})"
        )
    if raw not in _TIERS:
        raise ExclusionConfigError(
            f"{context}: unknown tier {raw!r} (expected one of {sorted(_TIERS)})"
        )
    return raw


def _compile_chat_into(
    top: dict[str, object], add: Callable[[_Pattern], None]
) -> None:
    """Walk the ``chat:`` section, compile each entry's identifiers.

    Shape (§6.1)::

        chat:
          <source-id>:
            - {id: "C0...", name: "#...", tier: blocked, also_match: ["alias"]}

    Each entry contributes one pattern per identifier (``id``, ``name``, and
    one per ``also_match`` alias). Reuses the gate's narrowing helpers so a
    schema drift in ``exclusions.yaml`` fails the same shape-validation in both
    modules.
    """
    if "chat" not in top:
        return
    raw = top["chat"]
    if raw is None:
        raise ExclusionConfigError(
            "exclusions.yaml: 'chat' must be a mapping, got null "
            "(a present section must be a mapping; remove the key if unused)"
        )
    chat_map = _as_mapping(raw, context="exclusions.yaml: chat")
    for source_id, entries_raw in chat_map.items():
        ctx = f"exclusions.yaml: chat[{source_id!r}]"
        if entries_raw is None:
            raise ExclusionConfigError(f"{ctx}: expected a list of entries, got null")
        entries_list = _as_list(entries_raw, context=ctx)
        for i, entry_raw in enumerate(entries_list):
            entry_ctx = f"{ctx}[{i}]"
            entry = _as_mapping(entry_raw, context=entry_ctx)
            tier = _tier_value(entry, context=entry_ctx)
            entry_id = _opt_str(entry.get("id"), key="id", context=entry_ctx)
            name = _opt_str(entry.get("name"), key="name", context=entry_ctx)
            aliases = _str_list(
                entry.get("also_match"), key="also_match", context=entry_ctx
            )
            # Fail-closed on the silent-zero-pattern config: an entry with none
            # of id/name/also_match would add no _Pattern, so the guard would
            # compile clean and every scan would return Clean for that entry —
            # the guarantee silently lost. The gate rejects this shape; the
            # guard must too (mirrors gate discipline on the same config file).
            if entry_id is None and name is None and not aliases:
                raise ExclusionConfigError(
                    f"{entry_ctx}: entry has no identifiers "
                    "(need at least one of id, name, also_match)"
                )
            if entry_id is not None:
                add(
                    _Pattern(
                        pattern_id=f"chat:{source_id}[id={entry_id}]",
                        tier=tier,
                        regex=_compile_token_pattern(entry_id),
                    )
                )
            if name is not None:
                add(
                    _Pattern(
                        pattern_id=f"chat:{source_id}[name={name}]",
                        tier=tier,
                        regex=_compile_token_pattern(name),
                    )
                )
            for alias in aliases:
                add(
                    _Pattern(
                        pattern_id=f"chat:{source_id}[also_match={alias}]",
                        tier=tier,
                        regex=_compile_token_pattern(alias),
                    )
                )


def _compile_drive_into(
    top: dict[str, object], add: Callable[[_Pattern], None]
) -> None:
    """Walk the ``drive:`` section, compile each entry's path.

    The path is compiled with :func:`_compile_substring_pattern` (substring
    match, not word-bounded) because path-tree prefixes don't translate to
    word boundaries in free text — see :func:`_compile_substring_pattern`.
    """
    if "drive" not in top:
        return
    raw = top["drive"]
    if raw is None:
        raise ExclusionConfigError(
            "exclusions.yaml: 'drive' must be a mapping, got null "
            "(a present section must be a mapping; remove the key if unused)"
        )
    drive_map = _as_mapping(raw, context="exclusions.yaml: drive")
    for account_id, entries_raw in drive_map.items():
        ctx = f"exclusions.yaml: drive[{account_id!r}]"
        if entries_raw is None:
            raise ExclusionConfigError(f"{ctx}: expected a list of entries, got null")
        entries_list = _as_list(entries_raw, context=ctx)
        for i, entry_raw in enumerate(entries_list):
            entry_ctx = f"{ctx}[{i}]"
            entry = _as_mapping(entry_raw, context=entry_ctx)
            tier = _tier_value(entry, context=entry_ctx)
            path_raw = entry.get("path")
            if not isinstance(path_raw, str) or not path_raw:
                raise ExclusionConfigError(
                    f"{entry_ctx}: 'path' must be a non-empty string, "
                    f"got {type(path_raw).__name__}"
                )
            add(
                _Pattern(
                    pattern_id=f"drive:{account_id}[path={path_raw}]",
                    tier=tier,
                    regex=_compile_substring_pattern(path_raw),
                )
            )


def _compile_meetings_into(
    top: dict[str, object], add: Callable[[_Pattern], None]
) -> None:
    """Walk the flat ``meetings:`` section, compile ``series_id`` + ``title``.

    The ``series_id`` is a stable opaque token (a calendar event series id) so
    it compiles as a word-boundary token. The ``title`` is free text the fetch
    gate matches by case-insensitive *containment* (``Comp review`` blocks
    ``Comp reviews Q3``), so it compiles with the substring compiler — a
    word-boundary compile here would diverge from the gate and let through
    exactly the titles the gate blocks.
    """
    if "meetings" not in top:
        return
    raw = top["meetings"]
    if raw is None:
        raise ExclusionConfigError(
            "exclusions.yaml: 'meetings' must be a list, got null "
            "(a present section must be a list; remove the key if unused)"
        )
    meetings_list = _as_list(raw, context="exclusions.yaml: meetings")
    for i, entry_raw in enumerate(meetings_list):
        entry_ctx = f"exclusions.yaml: meetings[{i}]"
        entry = _as_mapping(entry_raw, context=entry_ctx)
        tier = _tier_value(entry, context=entry_ctx)
        series_id = _opt_str(
            entry.get("series_id"), key="series_id", context=entry_ctx
        )
        title = _opt_str(entry.get("title"), key="title", context=entry_ctx)
        if series_id is not None:
            add(
                _Pattern(
                    pattern_id=f"meetings[series_id={series_id}]",
                    tier=tier,
                    regex=_compile_token_pattern(series_id),
                )
            )
        if title is not None:
            add(
                _Pattern(
                    pattern_id=f"meetings[title={title}]",
                    tier=tier,
                    regex=_compile_substring_pattern(title),
                )
            )


def _compile_patterns_from_config(
    config_root: Path,
) -> tuple[tuple[_Pattern, ...], tuple[_Pattern, ...]]:
    """Read ``exclusions.yaml`` and compile the (persistence, alert/log) sets.

    * persistence set: blocked + ephemeral identifiers.
    * alert/log set: blocked identifiers only.

    The guard reads the YAML itself rather than reaching into the gate's
    private compiled structures because the shape it needs (a flat identifier
    list keyed only by tier) is different from the gate's per-source
    ref-matching tables. Reusing the gate's YAML-narrowing helpers keeps the
    two modules consistent under config-schema evolution without coupling the
    guard to the gate's private ref-matching dataclasses.

    A missing ``exclusions.yaml`` returns two empty tuples (the pre-init
    state — mirrors the gate's missing-file tolerance; all scans return
    :class:`Clean` until exclusions exist).
    """
    excl_path = config_root / "exclusions.yaml"
    try:
        excl_path.lstat()
    except FileNotFoundError:
        return (), ()
    except OSError as exc:
        raise ExclusionConfigError(
            f"cannot inspect {excl_path.name}: {type(exc).__name__}"
        ) from exc
    try:
        with excl_path.open(encoding="utf-8") as f:
            data: object = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ExclusionConfigError(
            f"YAML parse error in {excl_path.name}: {exc}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ExclusionConfigError(
            f"cannot read {excl_path.name}: {type(exc).__name__}"
        ) from exc

    top = _as_mapping(data, context="exclusions.yaml")
    persistence: list[_Pattern] = []
    alert_log: list[_Pattern] = []

    def add(pattern: _Pattern) -> None:
        # persistence = blocked + ephemeral; alert_log = blocked only.
        persistence.append(pattern)
        if pattern.tier == _BLOCKED:
            alert_log.append(pattern)

    _compile_chat_into(top, add)
    _compile_drive_into(top, add)
    _compile_meetings_into(top, add)

    return tuple(persistence), tuple(alert_log)


# --- the guard -------------------------------------------------------------


class OutputGuard:
    """Compiled output-guard policy — the fail-closed writer-side gate (§5.4).

    Construct via :meth:`for_profile` (the documented entry point that resolves
    the active profile's config + state roots) or directly with explicit roots.
    Both paths compile the deny-pattern set a single time at construction;
    :meth:`scan` is an O(patterns) regex search, not a disk read.

    A construction failure (unreadable / malformed ``exclusions.yaml``) raises
    :class:`OutputGuardError` — the writer treats this as "cannot proceed
    safely" and aborts the emit. Fail-closed on the hard guarantee, always.
    """

    _persistence_patterns: tuple[_Pattern, ...]
    _alert_log_patterns: tuple[_Pattern, ...]
    _state_root: Path

    def __init__(self, *, config_root: Path, state_root: Path) -> None:
        """Compile a guard from ``config_root``; anchor quarantines at ``state_root``.

        ``<config_root>/exclusions.yaml`` is the sole source of deny-patterns
        (same source file as the gate). ``local-whitelist.yaml`` is NOT read
        here — local-scan enforcement is the gate's job (whitelist inversion at
        enumeration), and the output guard has no per-item local identifier to
        scan for; the local source emits no text artifacts that name scan roots.

        Raises :class:`OutputGuardError` if ``exclusions.yaml`` is malformed
        or unreadable (wraps the gate's :class:`ExclusionConfigError`).
        """
        self._state_root = state_root
        try:
            persistence, alert_log = _compile_patterns_from_config(config_root)
        except ExclusionConfigError as exc:
            raise OutputGuardError(
                f"cannot compile output-guard policy: {exc}"
            ) from exc
        self._persistence_patterns = persistence
        self._alert_log_patterns = alert_log

    @classmethod
    def for_profile(cls, profile: str | None = None) -> OutputGuard:
        """Construct a guard for ``profile`` (default: active profile).

        The documented entry point for writers. Resolves the profile's config
        and state roots via :mod:`mclaw_core.paths` and delegates to
        :meth:`__init__`.
        """
        prof = (
            paths.resolve_profile()
            if profile is None
            else paths.validate_profile(profile)
        )
        return cls(
            config_root=paths.config_root(prof),
            state_root=paths.state_root(prof),
        )

    # --- scan ------------------------------------------------------------

    def scan(
        self,
        text: str,
        *,
        surface: Surface,
        artifact_name: str = "<artifact>",
    ) -> Clean | Trip:
        """Scan ``text`` for deny-patterns appropriate to ``surface`` (§5.4).

        Returns :class:`Clean` if no pattern matches, or :class:`Trip` carrying
        the first matching pattern's id and ``artifact_name`` if one does.

        The caller's emit flow::

            result = guard.scan(
                text,
                surface=Surface.PERSISTENCE,
                artifact_name="wiki/briefings/2026-07-18-morning.md",
            )
            if isinstance(result, Trip):
                guard.on_trip(result, content=text, actor="briefing-assembler")
                # do NOT write the artifact to its intended destination
            else:
                write_the_artifact(...)

        ``artifact_name`` should be the logical path or identifier the writer
        is about to emit — it appears in the Trip, changelog, and review-queue
        item so the operator can trace the trip back to the artifact. It is
        never used as input to the regex match (it is not the matched text).
        """
        # Fail-closed on the surface selector: accept ONLY the two valid enum
        # members. The prior ternary fell through to the alert-log (blocked-
        # only) set for ANY non-PERSISTENCE value (a string, None, an invalid
        # enum) — so an ephemeral identifier could pass as Clean on an
        # intended-persistence scan when the caller passed a wrong surface.
        # The hard guarantee must not hinge on a silent default.
        if surface is Surface.PERSISTENCE:
            patterns = self._persistence_patterns
        elif surface is Surface.ALERT_LOG:
            patterns = self._alert_log_patterns
        else:
            # ``surface: Surface`` is the typed signature, but Python does not
            # enforce it at runtime; a wrong-type value reaching here is a
            # caller bug, and a guarantee module surfaces caller bugs loudly
            # rather than degrading to a permissive default.
            raise OutputGuardError(
                f"unknown surface {surface!r} "
                "(expected Surface.PERSISTENCE or Surface.ALERT_LOG)"
            )
        # Casefold the scan text exactly once and match every pattern against
        # the casefolded form. The compiled patterns are themselves casefolded
        # (see :func:`_compile_token_pattern` / :func:`_compile_substring_pattern`);
        # matching casefolded-pattern against casefolded-text reproduces the
        # fetch gate's :meth:`str.casefold` semantics for chat ``name`` /
        # ``also_match`` and meeting ``title`` (§5.4 defense-in-depth). This
        # closes the ``ß``↔``SS`` gap that ``re.IGNORECASE`` (Unicode simple
        # case-folding) leaves open. ``artifact_name`` is matched-text-agnostic
        # by contract, so it is not casefolded.
        text_cf = text.casefold()
        for pat in patterns:
            if pat.regex.search(text_cf):
                return Trip(pattern_id=pat.pattern_id, artifact_name=artifact_name)
        return Clean()

    # --- fail-closed action ---------------------------------------------

    def on_trip(self, trip: Trip, *, content: str, actor: str = "guard") -> Path:
        """Execute the fail-closed action for a tripped artifact (§5.4).

        1. Move the artifact's content into ``state/quarantine/`` (0700,
           outside the vault and outside any sync).
        2. Append a ``guard.trip`` record to the per-month changelog.
        3. Queue a needs-sign-off review item with the trip context.
        4. Fire the Telegram system alert (Phase 2 — currently a no-op stub;
           the notify layer doesn't exist yet).

        Returns the path to the quarantined artifact. The caller does NOT
        write the artifact to its intended destination.

        ``content`` is the full artifact body; it lands in the quarantined
        file (which a human reviewer reads). No excerpt of it is logged: the
        changelog and review-queue records carry only the pattern id and
        artifact name (plus the quarantine path) — §5.4 forbids logging
        matched content.
        """
        quarantine_path = self._quarantine_artifact(trip, content)
        self._append_changelog_trip(trip, quarantine_path, actor=actor)
        self._queue_review_item(trip, quarantine_path)
        self._notify_guard_trip(trip)
        return quarantine_path

    # --- internals ------------------------------------------------------

    def _quarantine_dir(self) -> Path:
        """Return the quarantine dir, creating it lazily at 0700 if missing.

        ``init_state_tree`` creates this dir at profile init, but a guard may
        run before init (e.g. on a fresh profile during bootstrap, or when a
        test constructs the guard against an empty state root). The lazy-create
        is anchored with ``O_NOFOLLOW | O_DIRECTORY`` to refuse a symlink at
        the final component (mirrors :mod:`mclaw_core.config_state`'s secure-
        dir discipline). Fail-closed: if the dir cannot be created or is a
        symlink, raise :class:`OutputGuardError` — the writer aborts rather
        than emit an ungated artifact.
        """
        q = self._state_root / "quarantine"
        try:
            q.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputGuardError(
                f"cannot create quarantine dir at {q}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        try:
            fd = os.open(str(q), os.O_NOFOLLOW | os.O_DIRECTORY)
        except OSError as exc:
            raise OutputGuardError(
                f"refusing to use quarantine dir at {q}: not a trusted "
                f"directory (symlink or not a directory): {type(exc).__name__}"
            ) from exc
        try:
            current = os.fstat(fd).st_mode & 0o777
            if current != 0o700:
                os.fchmod(fd, 0o700)
        finally:
            os.close(fd)
        return q

    def _quarantine_artifact(self, trip: Trip, content: str) -> Path:
        """Write ``content`` into quarantine; return the file path.

        Filename: ``YYYY-MM-DDThhmmssZ--<slug>--<uuid8><ext>`` — timestamp
        makes trips sortable, slug traces the original artifact name, uuid
        disambiguates same-second same-name trips. The extension is preserved
        from ``artifact_name`` when one is present (``.md``, ``.txt`` …); the
        default is ``.md`` (the dominant vault surface per design §3.3).

        Atomic write (unique temp + ``fsync`` + rename) — an interrupted trip
        leaves at worst a truncated *temp*, never a truncated quarantine file.
        The temp lives in the 0700 quarantine dir, and the published file is
        explicitly ``chmod 0600`` — the artifact content is by definition
        sensitive (it tripped the guard), so the file must not be world- or
        group-readable even though the dir itself is locked down.
        """
        q = self._quarantine_dir()
        ts = _utc_now().strftime("%Y-%m-%dT%H%M%SZ")
        art_path = Path(trip.artifact_name)
        slug = _slugify(art_path.stem or trip.artifact_name)
        ext = art_path.suffix or ".md"
        u = uuid.uuid4().hex[:8]
        dest = q / f"{ts}--{slug}--{u}{ext}"
        fd, tmp_name = tempfile.mkstemp(dir=str(q))
        tmp_path = Path(tmp_name)
        try:
            try:
                buf = content.encode("utf-8")
                off = 0
                while off < len(buf):
                    off += os.write(fd, buf[off:])
                os.fsync(fd)
            finally:
                os.close(fd)
            # ``os.replace`` is atomic on POSIX. ``dest`` does not yet exist
            # (the uuid makes same-second collisions essentially impossible),
            # so this never overwrites a prior trip.
            os.replace(str(tmp_path), str(dest))
        finally:
            # Defensive unlink of the temp (no-op once rename succeeded).
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        # 0600 on the destination. ``tempfile.mkstemp`` already creates files
        # at 0600 by default (independent of the dir's mode); the explicit
        # ``chmod`` makes that guarantee hard rather than relying on the
        # default, so the artifact body is never world/group-readable even on
        # shared systems where a umask or future stdlib change could shift it.
        os.chmod(dest, 0o600)
        return dest

    def _append_changelog_trip(
        self, trip: Trip, quarantine_path: Path, *, actor: str
    ) -> None:
        """Append a ``guard.trip`` record to the per-month changelog (§4.4).

        Schema: ``{"ts", "actor", "action": "guard.trip", "target":
        artifact_name, "detail": {"pattern_id": ..., "quarantine_path": ...}}``.
        The closed ``action`` enum (§4.4) includes ``guard.trip``; we do not
        invent a different vocabulary.

        No matched content is recorded — only the pattern id and artifact name.
        The pattern id is a config-side identifier (which exclusion entry
        tripped), not the matched text from the artifact.
        """
        log_dir = self._state_root / "changelog"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{_utc_date_month()}.jsonl"
        record = {
            "ts": _utc_iso_now(),
            "actor": actor,
            "action": ACTION_GUARD_TRIP,
            "target": trip.artifact_name,
            "detail": {
                "pattern_id": trip.pattern_id,
                "quarantine_path": str(quarantine_path),
            },
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _queue_review_item(self, trip: Trip, quarantine_path: Path) -> None:
        """Append a needs-sign-off item to ``review-queue/pending.jsonl`` (§10.2).

        Match context = pattern id + artifact name + quarantine path. The
        weekly review surfaces open items; the operator inspects the quarantined
        artifact in place and tunes ``exclusions.yaml`` (alias / also_match)
        via the review loop.
        """
        queue_dir = self._state_root / "review-queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        queue_path = queue_dir / "pending.jsonl"
        record = {
            "id": f"G-{_utc_now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            "kind": "proposal",
            "bucket": "sign-off",
            "created": _utc_iso_now(),
            "title": f"Output guard tripped on {trip.artifact_name}",
            "evidence": [
                f"pattern: {trip.pattern_id}",
                f"quarantined: {quarantine_path}",
            ],
            "risk": "artifact blocked — review and tune exclusions or alias",
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with queue_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _notify_guard_trip(self, trip: Trip) -> None:
        """Phase-2 stub: when the notify layer lands, fire a system-severity
        Telegram alert here (§5.4: "exclusion guard tripped on <artifact> —
        artifact quarantined"). The notify layer (:mod:`mclaw_core.notify`)
        does not exist yet (Phase 2 / B5+); until then this is a no-op.

        The trip argument is the only input — never the matched content. When
        wired up, the alert body will name only ``trip.artifact_name`` and a
        pointer to the quarantine dir, never the artifact body or the matched
        snippet.
        """
        # TODO(Phase 2 / notify layer): from .notify import Alert, Channel
        # alert = Alert(
        #     severity="system",
        #     title="exclusion guard tripped",
        #     body=f"artifact {trip.artifact_name!r} quarantined",
        #     dedup_key=f"guard.trip:{trip.pattern_id}",
        # )
        # channels.send(alert)
        return None
