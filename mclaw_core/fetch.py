"""Shared, exclusion-safe provider fetch base (design §§2.2, 3.2, 5.2).

Provider wrappers enumerate only identifiers and metadata into
:class:`EnumeratedItem`.  This module performs the exclusion check before it
can call the wrapper's content method, which makes the primary exclusion
boundary a shared structural fact rather than provider convention.

Secrets are deliberately not an argument to :func:`fetch_items`.  A provider
wrapper resolves a configured ``keychain://`` ref with :func:`get_secret` in
its own process immediately before it creates its provider client; neither the
base, spool, cursor, nor run record receives the resulting value.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from .exclusion import (
    ChatRef,
    Decision,
    DriveRef,
    ExclusionGate,
    LocalRef,
    MeetingRef,
)
from .paths import state_root as profile_state_root

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type ItemRef = ChatRef | DriveRef | LocalRef | MeetingRef
type Sleep = Callable[[float], None]
type Clock = Callable[[], str]

_REQUIRED_ENVELOPE_FIELDS = frozenset({"id", "source", "kind", "ts"})
_MAX_ATTEMPTS = 3


class FetchError(Exception):
    """A provider item could not be safely fetched or persisted."""


@dataclass(frozen=True)
class EnumeratedItem:
    """Identifier-only provider result, checked before its content is fetched.

    ``cursor`` is opaque provider state.  It is checkpointed only after this
    item is safely skipped (blocked) or written to the appropriate spool.
    """

    ref: ItemRef
    cursor: JsonValue
    envelope: Mapping[str, JsonValue]


class FetchProvider(Protocol):
    """Small read-only interface implemented by each provider wrapper."""

    source_id: str

    def enumerate_items(self, cursor: JsonValue | None) -> Iterable[EnumeratedItem]:
        """Return identifier-only items after ``cursor``."""

    def fetch_content(self, item: EnumeratedItem) -> Mapping[str, JsonValue]:
        """Fetch content for an already-allowed item.

        The wrapper owns its short-lived provider client and resolves any
        ``keychain://`` credentials with :func:`get_secret`, never exposing a
        credential to this shared base or an agent-facing surface.
        """


@dataclass(frozen=True)
class FetchResult:
    """Identifier-free summary safe to put in a run record."""

    fetched: int
    blocked_skipped: int
    ephemeral: int


def get_secret(ref: str, *, profile: str) -> str:
    """Resolve one ``keychain://`` ref for use *inside a provider wrapper*.

    This helper invokes the existing ``mclaw secret get`` security wrapper.
    It intentionally has no logging and callers must keep the return value
    local to their provider-client construction.  It is not used by
    :func:`fetch_items`, keeping credentials outside the shared data flow.
    """
    prefix = f"keychain://mark-claw-{profile}/"
    if not ref.startswith(prefix):
        raise FetchError("credential reference must be a profile keychain reference")
    account = ref.removeprefix(prefix)
    if not account or "/" in account:
        raise FetchError("credential reference has an invalid keychain account")
    item, separator, field = account.rpartition("-")
    if not separator or not item or not field:
        raise FetchError("credential reference must contain an item and field")
    completed = subprocess.run(
        ["mclaw", "secret", "get", item, field],
        check=False,
        capture_output=True,
        text=True,
        env={"MCLAW_PROFILE": profile, "PATH": os.environ.get("PATH", os.defpath)},
    )
    if completed.returncode != 0:
        raise FetchError("provider credential resolution failed")
    return completed.stdout.rstrip("\n")


def fetch_items(
    provider: FetchProvider,
    *,
    gate: ExclusionGate,
    state_root: Path | None = None,
    pipeline: str | None = None,
    sleep: Sleep = time.sleep,
    now: Clock | None = None,
) -> FetchResult:
    """Enumerate, gate, fetch, spool, and checkpoint one source.

    A blocked item never reaches ``provider.fetch_content``. EPHEMERAL is also
    rejected before content fetch in Phase 1: no real 15-minute sweep
    orchestrator exists yet, so exposing a caller-controlled lifecycle hook
    would be self-authorizing rather than structural. Phase 2's designated
    orchestrator must add the only persistence/consumption capability. Each
    cursor is written only after its item has been successfully skipped or
    spooled; failures therefore leave the prior checkpoint intact for a safe
    retry.
    """
    root = state_root if state_root is not None else profile_state_root()
    run_name = pipeline or f"fetch-{provider.source_id}"
    _validate_filename_component(provider.source_id, label="source id")
    _validate_filename_component(run_name, label="pipeline")
    started = _timestamp(now)
    fetched = 0
    blocked_skipped = 0
    ephemeral = 0
    try:
        cursor = _read_cursor(root, provider.source_id)
        for item in provider.enumerate_items(cursor):
            _validate_item(item, provider.source_id)
            decision = gate.check(provider.source_id, item.ref)
            if decision is Decision.BLOCKED:
                blocked_skipped += 1
                _write_cursor(root, provider.source_id, item.cursor, started)
                continue
            if decision is Decision.EPHEMERAL:
                raise FetchError("ephemeral items are unsupported until Phase 2")
            content = _fetch_with_retry(provider, item, sleep=sleep)
            _append_spool(
                root, provider.source_id, item.envelope, content, "full", started
            )
            _write_cursor(root, provider.source_id, item.cursor, started)
            fetched += 1
    except Exception as exc:
        _write_run_record(
            root,
            run_name,
            started=started,
            result="error",
            fetched=fetched,
            blocked_skipped=blocked_skipped,
            ephemeral=ephemeral,
            error=type(exc).__name__,
        )
        if isinstance(exc, FetchError):
            raise
        raise FetchError("provider fetch failed") from exc
    else:
        _write_run_record(
            root,
            run_name,
            started=started,
            result="ok",
            fetched=fetched,
            blocked_skipped=blocked_skipped,
            ephemeral=ephemeral,
            error=None,
        )
    return FetchResult(
        fetched=fetched,
        blocked_skipped=blocked_skipped,
        ephemeral=ephemeral,
    )


def _timestamp(now: Clock | None) -> str:
    if now is not None:
        return now()
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_cursor(root: Path, source_id: str) -> JsonValue | None:
    path = root / "cursors" / f"{source_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FetchError("cursor state is unreadable") from exc
    if (
        not isinstance(data, dict)
        or data.get("source") != source_id
        or "data" not in data
        or not _is_json_value(data["data"])
    ):
        raise FetchError("cursor state is malformed")
    return cast(JsonValue, data["data"])


def _write_cursor(root: Path, source_id: str, cursor: JsonValue, updated: str) -> None:
    _atomic_json_write(
        root / "cursors" / f"{source_id}.json",
        {"source": source_id, "updated": updated, "data": cursor},
    )


def _append_spool(
    root: Path,
    source_id: str,
    envelope: Mapping[str, JsonValue],
    content: Mapping[str, JsonValue],
    tier: str,
    sweep: str,
) -> None:
    destination = root / "spool"
    if tier == "ephemeral":
        destination /= "ephemeral"
    destination /= source_id
    destination.mkdir(parents=True, exist_ok=True)
    if tier == "ephemeral":
        destination.chmod(0o700)
    record = dict(content)
    record.update(envelope)
    record["sweep"] = sweep
    record["tier"] = tier
    try:
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    except (TypeError, ValueError) as exc:
        raise FetchError("provider item is not JSON serializable") from exc
    day = sweep[:10]
    with (destination / f"{day}.jsonl").open("a", encoding="utf-8") as spool:
        spool.write(encoded)


def _validate_item(item: EnumeratedItem, source_id: str) -> None:
    missing = _REQUIRED_ENVELOPE_FIELDS.difference(item.envelope)
    common_values_are_strings = all(
        isinstance(item.envelope.get(field), str) and item.envelope[field]
        for field in _REQUIRED_ENVELOPE_FIELDS
    )
    if (
        missing
        or not common_values_are_strings
        or item.envelope.get("source") != source_id
    ):
        raise FetchError("provider item has an invalid spool envelope")


def _fetch_with_retry(
    provider: FetchProvider,
    item: EnumeratedItem,
    *,
    sleep: Sleep,
) -> Mapping[str, JsonValue]:
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return provider.fetch_content(item)
        except Exception as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            sleep(_retry_delay(exc, attempt))
    raise AssertionError("unreachable")


def _retry_delay(exc: Exception, attempt: int) -> float:
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after >= 0:
        return float(retry_after)
    seconds = getattr(exc, "seconds", None)
    if isinstance(seconds, (int, float)) and seconds >= 0:
        return float(seconds)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        value = headers.get("Retry-After")
        try:
            return max(0.0, float(cast(str | int | float, value)))
        except (TypeError, ValueError):
            pass
    return float(2**attempt)


def _write_run_record(
    root: Path,
    pipeline: str,
    *,
    started: str,
    result: str,
    fetched: int,
    blocked_skipped: int,
    ephemeral: int,
    error: str | None,
) -> None:
    record: dict[str, JsonValue] = {
        "pipeline": pipeline,
        "last_start": started,
        "last_result": result,
        "items": {"fetched": fetched, "ephemeral": ephemeral},
        "blocked_skipped": blocked_skipped,
    }
    if result == "ok":
        record["last_success"] = started
    else:
        last_success = _read_last_success(root, pipeline)
        if last_success is not None:
            record["last_success"] = last_success
    if error is not None:
        record["error"] = error
    _atomic_json_write(root / "runs" / f"{pipeline}.json", record)
    history = root / "runs" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")


def _atomic_json_write(path: Path, value: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        try:
            encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
            offset = 0
            while offset < len(encoded):
                offset += os.write(fd, encoded[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_last_success(root: Path, pipeline: str) -> str | None:
    """Return a valid previous success timestamp, treating bad state as absent."""
    path = root / "runs" / f"{pipeline}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("pipeline") != pipeline:
        return None
    last_success = data.get("last_success")
    return last_success if isinstance(last_success, str) and last_success else None


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _validate_filename_component(value: str, *, label: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise FetchError(f"{label} is not a safe state-file component")
