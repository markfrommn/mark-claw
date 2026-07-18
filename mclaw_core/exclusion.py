"""Exclusion enforcement — the fetch gate (design §5.2, the hard guarantee).

One module, imported by **every** wrapper. Every item identifier passes
through :meth:`ExclusionGate.check` at **enumeration time**, BEFORE content is
fetched or emitted. This is the primary choke point of the two-tier model
(§5.1); the output guard (B4 / DEV-15) is defense-in-depth and lives elsewhere.

Two structural facts live in this module (§11.1 — "allowlist-over-prompt:
every hard constraint is a structural fact, not a prompt instruction"):

* ``ExclusionGate`` is the **sole extractor of scan-root paths** from
  ``local-whitelist.yaml``. The local scanner obtains its scan roots *only*
  via :meth:`ExclusionGate.local_scan_roots` — no constructor, ``check``
  overload, or other API accepts a root argument, so a code path that scans an
  unlisted root does not exist.
* Local refs are matched by **whitelist inversion**: an item not under any
  whitelisted root returns :data:`Decision.BLOCKED`. With no whitelist loaded,
  every local ref is blocked — fail-closed for the local source (§5.2).

Default decision for anything not listed: :data:`Decision.ALLOW` (§5.3 —
"Default for anything not listed: fully processed"). The hard guarantee bites
only on items the operator has explicitly excluded.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

# --- decision ------------------------------------------------------------


class Decision(enum.Enum):
    """The three possible fetch-gate outcomes for an item (§5.2, §5.3).

    The names match the spec's ``ALLOW | EPHEMERAL | BLOCKED`` set exactly.
    The string values mirror the ``tier:`` vocabulary used in
    ``exclusions.yaml`` for the two non-default outcomes.
    """

    ALLOW = "allow"
    EPHEMERAL = "ephemeral"
    BLOCKED = "blocked"


#: The set of ``tier:`` values an entry may declare. A tier outside this set
#: is a config bug and is surfaced at load as :class:`ExclusionConfigError`
#: rather than silently coerced one way or the other (CLAUDE.md: "fail closed
#: on the hard guarantee").
_ENTRY_TIERS: dict[str, Decision] = {
    "blocked": Decision.BLOCKED,
    "ephemeral": Decision.EPHEMERAL,
}


class ExclusionConfigError(Exception):
    """Raised at :meth:`ExclusionGate.load` when the config is malformed.

    Covers: an entry whose ``tier`` is not in :data:`_ENTRY_TIERS`; a YAML
    parse error; a structure that is not the expected mapping/list shape; a
    non-string where a string is required (path, id, alias). Surfacing loudly
    at load is the safe choice for a guarantee module — a malformed exclusion
    is an operator bug, and silently treating it as allow weakens the
    guarantee while silently treating it as block masks the typo.
    """


# --- item references (discriminated union by type) -----------------------
#
# ``item_ref`` is a discriminated union of frozen dataclasses — one per source
# kind. The ref's *type* selects the matching strategy in ``check`` (isinstance
# dispatch), which stays clean under mypy strict via type narrowing. Each ref
# carries exactly the fields its strategy needs (no nulls-for-other-sources).


@dataclass(frozen=True)
class ChatRef:
    """Reference to a chat conversation or contact (Slack/MM/TG/Signal).

    Either an opaque provider ID (channel ID, DM ID, contact ID), a
    human-readable name, or both. Match succeeds if ``id`` equals an entry's
    ``id`` (exact), or ``name`` equals an entry's ``name`` (case-insensitive
    exact), or ``name`` equals one of the entry's ``also_match`` aliases
    (case-insensitive exact — §5.2 "chat matches by conversation/contact ID").
    Setting both fields widens the match surface when the caller has them.
    """

    id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class DriveRef:
    """Reference to a Drive/OneDrive folder or file by provider path.

    Matched by path-prefix inheritance with segment-boundary awareness
    (§5.2 "Drive/OneDrive folder exclusions are path-prefix matches"): a
    ``/HR`` entry matches ``/HR/payroll`` and ``/HR/sub/deep`` but NOT
    ``/HR-secret``. Comparison is via :class:`PurePosixPath` ancestry
    (``==`` or ``in .parents``), never string ``startswith`` — a string prefix
    would let ``/HR`` match ``/HR-secret`` and silently widen the exclusion.
    Drive paths are case-sensitive (spec examples are exact).
    """

    path: str


@dataclass(frozen=True)
class MeetingRef:
    """Reference to a calendar meeting.

    Matched against the flat ``meetings:`` list (§5.2 "meetings match by
    event/series ID with title-pattern fallback"): a hit if ``series_id``
    equals an entry's ``series_id``, OR ``event_id`` equals an entry's
    ``series_id`` (an event belonging to a blocked series), OR the entry's
    ``title`` is a case-insensitive substring of ``ref.title`` (the
    title-pattern fallback for meetings whose series id the caller lacks).
    """

    series_id: str | None = None
    event_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class LocalRef:
    """Reference to a local-filesystem path the scanner proposes to read.

    Matched by **whitelist inversion** (§5.2 "Local drive is
    whitelist-inverted"): ALLOW only when the path is at or under a root in
    ``local-whitelist.yaml``; BLOCKED otherwise. There are no per-entry local
    exclusions — the whitelist IS the policy. Comparison is segment-boundary
    aware via :class:`pathlib.Path` ancestry.
    """

    path: str


# --- compiled entries (internal) -----------------------------------------


@dataclass(frozen=True)
class _ChatEntry:
    """One compiled chat exclusion entry."""

    id: str | None
    #: Entry ``name`` case-folded with :meth:`str.casefold` (Turkic-aware
    #: i-less; case-insensitive exact per spec).
    name_lower: str | None
    #: ``also_match`` aliases case-folded. Matched against ``ref.name`` only.
    aliases_lower: frozenset[str]
    tier: Decision


@dataclass(frozen=True)
class _DriveEntry:
    """One compiled drive exclusion entry."""

    #: Provider path normalized for ancestry (``/HR/`` → ``/HR``).
    path: PurePosixPath
    tier: Decision


@dataclass(frozen=True)
class _MeetingEntry:
    """One compiled meeting exclusion entry."""

    series_id: str | None
    #: Entry ``title`` case-folded for substring containment in ``ref.title``.
    title_lower: str | None
    tier: Decision


# --- YAML narrowing helpers ----------------------------------------------
#
# ``yaml.safe_load`` returns ``Any``. mypy strict requires we narrow to the
# expected shape before use. These helpers either narrow or raise a loud
# :class:`ExclusionConfigError` — they never return ``Any``.


def _as_mapping(value: object, *, context: str) -> dict[str, object]:
    """Narrow ``value`` to a string-keyed mapping; raise on a non-mapping.

    ``None`` (the result of an empty YAML document) is treated as the empty
    mapping, so a freshly-init'd ``exclusions.yaml`` containing ``chat: {}``
    parses cleanly. A non-string key is rejected — our config schema uses
    string source/account IDs only, and a non-string key would silently
    never match a ``check(source_id: str, ...)`` call.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExclusionConfigError(
            f"{context}: expected a mapping, got {type(value).__name__}"
        )
    result: dict[str, object] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            raise ExclusionConfigError(
                f"{context}: mapping key must be a string, "
                f"got {type(k).__name__} ({k!r})"
            )
        result[k] = v
    return result


def _as_list(value: object, *, context: str) -> list[object]:
    """Narrow ``value`` to a list; ``None`` (empty doc) becomes the empty list."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExclusionConfigError(
            f"{context}: expected a list, got {type(value).__name__}"
        )
    return list(value)


def _opt_str(value: object, *, key: str, context: str) -> str | None:
    """Narrow an optional string field; ``None`` (absent key) passes through.

    A present-but-empty string is rejected: an empty ``id`` / ``name`` /
    ``series_id`` compiles an entry that can never usefully match, and an
    empty ``title`` is worse — ``"" in ref.title.casefold()`` is ``True`` for
    every titled meeting, so one such entry would silently become a global
    block. Reject at load rather than degrade the guarantee.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExclusionConfigError(
            f"{context}: '{key}' must be a string, got {type(value).__name__}"
        )
    if not value:
        raise ExclusionConfigError(
            f"{context}: '{key}' must be a non-empty string"
        )
    return value


def _str_list(value: object, *, key: str, context: str) -> list[str]:
    """Narrow a list-of-strings field (e.g. ``also_match``); defaults to empty.

    A present-but-empty string element is rejected, mirroring :func:`_opt_str`:
    an empty ``also_match`` alias can never match a real chat name, so an entry
    whose only identifier is ``[""]`` is silently inert — and an empty alias
    alongside valid ones is a typo the operator should hear about. Reject at
    load rather than degrade the fail-loud promise.
    """
    if value is None:
        return []
    raw_list = _as_list(value, context=f"{context}: '{key}'")
    result: list[str] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, str):
            raise ExclusionConfigError(
                f"{context}: '{key}[{i}]' must be a string, "
                f"got {type(item).__name__}"
            )
        if not item:
            raise ExclusionConfigError(
                f"{context}: '{key}[{i}]' must be a non-empty string"
            )
        result.append(item)
    return result


def _entry_tier(entry: dict[str, object], *, context: str) -> Decision:
    """Read and validate the ``tier`` field of one entry.

    A missing or non-string tier is a config bug. An unknown string tier is
    also a config bug — both surface here as :class:`ExclusionConfigError`
    rather than being coerced, because a guarantee module must not silently
    turn a typo into allow (weakens the guarantee) or block (masks the typo).
    """
    raw = entry.get("tier")
    if not isinstance(raw, str):
        raise ExclusionConfigError(
            f"{context}: 'tier' must be one of {sorted(_ENTRY_TIERS)}, "
            f"got {type(raw).__name__} ({raw!r})"
        )
    tier = _ENTRY_TIERS.get(raw)
    if tier is None:
        raise ExclusionConfigError(
            f"{context}: unknown tier {raw!r} "
            f"(expected one of {sorted(_ENTRY_TIERS)})"
        )
    return tier


def _load_yaml_or_none(path: Path) -> object | None:
    """Load YAML from ``path``; return ``None`` if the file is absent.

    A :class:`yaml.YAMLError` is wrapped in :class:`ExclusionConfigError` so a
    malformed config surfaces as a clear, single exception type from ``load``
    rather than leaking the parser's exception out of the gate. An unreadable
    file (``PermissionError``, non-UTF-8) is treated the same way — the gate
    never silently degrades.
    """
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            # ``yaml.safe_load`` is typed ``Any``; assign through ``object`` so
            # the return type stays the documented ``object | None`` rather
            # than leaking ``Any`` (the narrowing helpers below do the real
            # shape validation).
            data: object = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ExclusionConfigError(
            f"YAML parse error in {path.name}: {exc}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ExclusionConfigError(
            f"cannot read {path.name}: {type(exc).__name__}"
        ) from exc
    return data


# --- section compilers ---------------------------------------------------


def _compile_chat(top: dict[str, object]) -> dict[str, tuple[_ChatEntry, ...]]:
    """Compile the ``chat:`` section into per-source-id lookup tables.

    Shape (§6.1)::

        chat:
          <source-id>:
            - {id: "C0...", name: "#...", tier: blocked, also_match: ["..."]}

    Returns a mapping ``source_id → tuple[_ChatEntry, ...]``. An absent
    ``chat`` key or an empty mapping yields ``{}``. A **present-but-null**
    ``chat:`` (a YAML dangling key — e.g. an indented entry whose parent
    collapsed) is malformed and raises: it would otherwise compile to "no
    chat exclusions" and silently disable protection the operator intended.
    Every entry's tier is validated here, so a malformed tier fails the
    whole load (fail-closed).
    """
    if "chat" not in top:
        return {}
    raw = top["chat"]
    if raw is None:
        raise ExclusionConfigError(
            "exclusions.yaml: 'chat' must be a mapping, got null "
            "(a present section must be a mapping; remove the key if unused)"
        )
    chat_map = _as_mapping(raw, context="exclusions.yaml: chat")
    result: dict[str, tuple[_ChatEntry, ...]] = {}
    for source_id, entries_raw in chat_map.items():
        ctx = f"exclusions.yaml: chat[{source_id!r}]"
        entries_list = _as_list(entries_raw, context=ctx)
        compiled: list[_ChatEntry] = []
        for i, entry_raw in enumerate(entries_list):
            entry_ctx = f"{ctx}[{i}]"
            entry = _as_mapping(entry_raw, context=entry_ctx)
            tier = _entry_tier(entry, context=entry_ctx)
            id = _opt_str(entry.get("id"), key="id", context=entry_ctx)
            name = _opt_str(entry.get("name"), key="name", context=entry_ctx)
            aliases = _str_list(
                entry.get("also_match"), key="also_match", context=entry_ctx
            )
            # An entry with no identifier can never match any ref — it compiles
            # but is silently ALLOW-for-everything, masking an operator typo
            # like ``{tier: blocked}`` missing its identifier. Fail loud.
            if id is None and name is None and not aliases:
                raise ExclusionConfigError(
                    f"{entry_ctx}: an exclusion entry with no id, name, or "
                    f"also_match can never match — add an identifier"
                )
            compiled.append(
                _ChatEntry(
                    id=id,
                    name_lower=name.casefold() if name is not None else None,
                    aliases_lower=frozenset(a.casefold() for a in aliases),
                    tier=tier,
                )
            )
        result[source_id] = tuple(compiled)
    return result


def _compile_drive(top: dict[str, object]) -> dict[str, tuple[_DriveEntry, ...]]:
    """Compile the ``drive:`` section into per-account-id lookup tables.

    Shape (§6.1)::

        drive:
          <account-id>:
            - {path: "/HR/", tier: blocked}   # prefix; inherits to subfolders

    Returns ``account_id → tuple[_DriveEntry, ...]``. An absent ``drive`` key
    or an empty mapping yields ``{}``. A **present-but-null** ``drive:`` is
    malformed and raises (same rationale as :func:`_compile_chat`). Each
    entry's ``path`` is normalized to a :class:`PurePosixPath` (which
    collapses redundant trailing separators — ``/HR/`` and ``/HR`` compare
    equal) for the segment-aware ancestry check in
    :meth:`ExclusionGate._check_drive`.
    """
    if "drive" not in top:
        return {}
    raw = top["drive"]
    if raw is None:
        raise ExclusionConfigError(
            "exclusions.yaml: 'drive' must be a mapping, got null "
            "(a present section must be a mapping; remove the key if unused)"
        )
    drive_map = _as_mapping(raw, context="exclusions.yaml: drive")
    result: dict[str, tuple[_DriveEntry, ...]] = {}
    for account_id, entries_raw in drive_map.items():
        ctx = f"exclusions.yaml: drive[{account_id!r}]"
        entries_list = _as_list(entries_raw, context=ctx)
        compiled: list[_DriveEntry] = []
        for i, entry_raw in enumerate(entries_list):
            entry_ctx = f"{ctx}[{i}]"
            entry = _as_mapping(entry_raw, context=entry_ctx)
            tier = _entry_tier(entry, context=entry_ctx)
            path_raw = entry.get("path")
            if not isinstance(path_raw, str) or not path_raw:
                raise ExclusionConfigError(
                    f"{entry_ctx}: 'path' must be a non-empty string, "
                    f"got {type(path_raw).__name__}"
                )
            compiled.append(_DriveEntry(path=PurePosixPath(path_raw), tier=tier))
        result[account_id] = tuple(compiled)
    return result


def _compile_meetings(top: dict[str, object]) -> tuple[_MeetingEntry, ...]:
    """Compile the flat ``meetings:`` list (§6.1).

    Shape::

        meetings:
          - {series_id: "abc123", title: "Comp review", tier: blocked}

    The list is flat (not keyed by source) — a meeting matches across all
    calendars. Returns a tuple of compiled entries. An absent ``meetings``
    key or an empty list yields ``()``. A **present-but-null** ``meetings:``
    is malformed and raises (same rationale as :func:`_compile_chat`).
    ``series_id`` is optional per entry only insofar as the operator may rely
    on the title-pattern fallback alone; the title is matched as a
    case-insensitive substring of the caller's ``MeetingRef.title``.
    """
    if "meetings" not in top:
        return ()
    raw = top["meetings"]
    if raw is None:
        raise ExclusionConfigError(
            "exclusions.yaml: 'meetings' must be a list, got null "
            "(a present section must be a list; remove the key if unused)"
        )
    meetings_list = _as_list(raw, context="exclusions.yaml: meetings")
    compiled: list[_MeetingEntry] = []
    for i, entry_raw in enumerate(meetings_list):
        entry_ctx = f"exclusions.yaml: meetings[{i}]"
        entry = _as_mapping(entry_raw, context=entry_ctx)
        tier = _entry_tier(entry, context=entry_ctx)
        series_id = _opt_str(
            entry.get("series_id"), key="series_id", context=entry_ctx
        )
        title = _opt_str(entry.get("title"), key="title", context=entry_ctx)
        # An entry with neither identifier can never match — the title-pattern
        # fallback needs a title, and the series/event-id path needs a
        # series_id. Such an entry is silently ALLOW-for-everything, masking
        # an operator typo. Fail loud.
        if series_id is None and title is None:
            raise ExclusionConfigError(
                f"{entry_ctx}: a meetings entry with no series_id or title "
                f"can never match — add an identifier"
            )
        compiled.append(
            _MeetingEntry(
                series_id=series_id,
                title_lower=title.casefold() if title is not None else None,
                tier=tier,
            )
        )
    return tuple(compiled)


def _compile_scan_roots(whitelist_raw: object) -> tuple[Path, ...]:
    """Compile ``local-whitelist.yaml`` into resolved scan-root paths.

    This is the ONLY reader of ``local-whitelist.yaml`` (§11.1 structural
    fact). Returns a tuple of :class:`pathlib.Path` resolved via
    :meth:`Path.resolve` (``strict=False`` is the default, so a not-yet-created
    project root still resolves lexically and compares equal once it exists).
    Declaration order is preserved.

    ``None`` (the file is absent) yields the empty tuple — and an empty
    whitelist means :meth:`ExclusionGate._check_local` returns BLOCKED for
    every path, which is the fail-closed mode for local scanning.
    """
    if whitelist_raw is None:
        return ()
    top = _as_mapping(whitelist_raw, context="local-whitelist.yaml")
    roots_list = _as_list(
        top.get("scan_roots"), context="local-whitelist.yaml: scan_roots"
    )
    compiled: list[Path] = []
    for i, r in enumerate(roots_list):
        if not isinstance(r, str) or not r:
            raise ExclusionConfigError(
                f"local-whitelist.yaml: scan_roots[{i}] must be a non-empty "
                f"string path, got {type(r).__name__}"
            )
        compiled.append(Path(r).resolve())
    return tuple(compiled)


# --- the gate -------------------------------------------------------------


class ExclusionGate:
    """Compiled exclusion policy — the single fetch-gate choke point (§5.2).

    Construct an instance with :meth:`load` (the only public constructor),
    which reads ``<config_root>/exclusions.yaml`` and
    ``<config_root>/local-whitelist.yaml`` and compiles the lookup tables a
    single time. ``check`` and ``local_scan_roots`` are O(entries) lookups
    over the compiled structures, not disk reads.

    The compiled structures are private and never exposed: callers go through
    ``check`` / ``local_scan_roots``. There is no public mutator, no root-
    argument overload, no way to inject an entry — fail-closed construction is
    a structural part of the guarantee (§11.1).
    """

    # Class-level type annotations (the instance is built once by ``load``).
    _chat: dict[str, tuple[_ChatEntry, ...]]
    _drive: dict[str, tuple[_DriveEntry, ...]]
    _meetings: tuple[_MeetingEntry, ...]
    _scan_roots: tuple[Path, ...]

    def __init__(
        self,
        *,
        chat: dict[str, tuple[_ChatEntry, ...]],
        drive: dict[str, tuple[_DriveEntry, ...]],
        meetings: tuple[_MeetingEntry, ...],
        scan_roots: tuple[Path, ...],
    ) -> None:
        self._chat = chat
        self._drive = drive
        self._meetings = meetings
        self._scan_roots = scan_roots

    @classmethod
    def load(cls, config_root: Path) -> ExclusionGate:
        """Compile a gate from the two config files under ``config_root``.

        Reads ``<config_root>/exclusions.yaml`` and
        ``<config_root>/local-whitelist.yaml``. Both files are optional at
        load time (the gate may load before ``mclaw doctor --init`` runs):

        * A missing ``exclusions.yaml`` → no exclusions; every non-local item
          returns :data:`Decision.ALLOW`.
        * A missing ``local-whitelist.yaml`` → no scan roots; every local item
          returns :data:`Decision.BLOCKED` (fail-closed for local).

        Loading always re-reads disk: calling ``load`` again after editing
        config IS the reload path (no separate ``reload()`` is needed or
        wanted — there is no cached state to invalidate).

        Raises :class:`ExclusionConfigError` if either file is malformed YAML
        or contains an entry with a ``tier`` outside the allowed set.
        """
        exclusions_raw = _load_yaml_or_none(config_root / "exclusions.yaml")
        whitelist_raw = _load_yaml_or_none(config_root / "local-whitelist.yaml")

        top = _as_mapping(exclusions_raw, context="exclusions.yaml")
        return cls(
            chat=_compile_chat(top),
            drive=_compile_drive(top),
            meetings=_compile_meetings(top),
            scan_roots=_compile_scan_roots(whitelist_raw),
        )

    def check(
        self, source_id: str, item_ref: ChatRef | DriveRef | MeetingRef | LocalRef
    ) -> Decision:
        """Return the gate's decision for ``item_ref`` from ``source_id``.

        Dispatch is by the ref's type (isinstance narrowing under mypy strict):

        * :class:`ChatRef`    — per-``source_id`` id/name/alias lookup.
        * :class:`DriveRef`   — per-``source_id`` path-prefix ancestry.
        * :class:`MeetingRef` — flat series-id/event-id/title-substring scan.
        * :class:`LocalRef`   — whitelist inversion against ``local_scan_roots``.

        Default for any non-matching item or unknown ``source_id`` is
        :data:`Decision.ALLOW` (§5.3 — "Default for anything not listed: fully
        processed"), except local, where the default is BLOCKED by whitelist
        inversion. The hard guarantee bites only on explicitly excluded items.
        """
        if isinstance(item_ref, ChatRef):
            return self._check_chat(source_id, item_ref)
        if isinstance(item_ref, DriveRef):
            return self._check_drive(source_id, item_ref)
        if isinstance(item_ref, MeetingRef):
            return self._check_meeting(item_ref)
        if isinstance(item_ref, LocalRef):
            return self._check_local(item_ref)
        # Unreachable: the type hint is a closed union. Raise defensively so
        # an unexpected subclass cannot silently slip through to ALLOW.
        raise TypeError(f"unsupported item_ref type: {type(item_ref).__name__}")

    def local_scan_roots(self) -> tuple[Path, ...]:
        """Return the whitelisted scan roots (the ONLY source for the scanner).

        This accessor is what makes §11.1's "no discovery code path outside
        them" structurally true — there is no constructor argument, ``check``
        overload, or other method that accepts a root. ``mclaw-fetch-local``
        (D1 / DEV-19) will read roots only from here. With no whitelist
        loaded, this returns the empty tuple and the scanner scans nothing.
        """
        return self._scan_roots

    # --- per-strategy checks ---------------------------------------------

    def _check_chat(self, source_id: str, ref: ChatRef) -> Decision:
        """First-match-wins id/name/alias lookup.

        ``id`` is matched exactly (case-sensitive — provider IDs are opaque
        tokens). ``name`` and ``also_match`` aliases are matched
        case-insensitively (case-folded). The first entry that matches
        supplies the tier — ordering is the operator's responsibility, and
        the gate is deterministic rather than silently picking the weaker.
        """
        for entry in self._chat.get(source_id, ()):
            if ref.id is not None and entry.id is not None and ref.id == entry.id:
                return entry.tier
            if ref.name is not None:
                name_cf = ref.name.casefold()
                if entry.name_lower is not None and name_cf == entry.name_lower:
                    return entry.tier
                if name_cf in entry.aliases_lower:
                    return entry.tier
        return Decision.ALLOW

    def _check_drive(self, source_id: str, ref: DriveRef) -> Decision:
        """Path-prefix ancestry lookup (segment-boundary aware).

        The item path and each entry path are :class:`PurePosixPath`, so the
        ancestry check (``item == entry`` or ``entry in item.parents``)
        compares whole path segments, not string prefixes — ``/HR`` matches
        ``/HR/payroll`` but NOT ``/HR-secret``. Drive paths are case-sensitive
        (provider paths are exact in the spec examples).
        """
        item_path = PurePosixPath(ref.path)
        for entry in self._drive.get(source_id, ()):
            if item_path == entry.path or entry.path in item_path.parents:
                return entry.tier
        return Decision.ALLOW

    def _check_meeting(self, ref: MeetingRef) -> Decision:
        """Flat-list lookup with title-pattern fallback (§5.2).

        A meeting matches an entry if ``ref.series_id == entry.series_id``
        (the canonical case), or ``ref.event_id == entry.series_id`` (an event
        belonging to a blocked series — providers surface a single series id
        on each occurrence), or the entry's ``title`` is a case-insensitive
        substring of ``ref.title`` (the fallback for cases where the caller
        has only a human-readable title). First match wins.
        """
        for entry in self._meetings:
            if (
                ref.series_id is not None
                and entry.series_id is not None
                and ref.series_id == entry.series_id
            ):
                return entry.tier
            if (
                ref.event_id is not None
                and entry.series_id is not None
                and ref.event_id == entry.series_id
            ):
                return entry.tier
            if (
                ref.title is not None
                and entry.title_lower is not None
                and entry.title_lower in ref.title.casefold()
            ):
                return entry.tier
        return Decision.ALLOW

    def _check_local(self, ref: LocalRef) -> Decision:
        """Whitelist inversion: BLOCKED unless under a whitelisted root.

        The item path and the compiled roots are both
        :class:`pathlib.Path`-resolved, so a ref naming a symlinked root and
        a whitelist entry naming its target compare equal — and segment
        boundaries are respected (``/parent/proj`` does NOT whitelist
        ``/parent/proj-evil``). With an empty whitelist (file absent or
        ``scan_roots: []``), every local item is blocked — fail-closed.
        """
        item_path = Path(ref.path).resolve()
        for root in self._scan_roots:
            if item_path == root or root in item_path.parents:
                return Decision.ALLOW
        return Decision.BLOCKED
