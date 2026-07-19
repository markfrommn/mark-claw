"""Tests for the shared, exclusion-safe fetch base (DEV-19)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from mclaw_core.exclusion import ChatRef, ExclusionGate
from mclaw_core.fetch import (
    EnumeratedItem,
    FetchError,
    JsonValue,
    TransientProviderError,
    _atomic_json_write,
    fetch_items,
    get_secret,
)
from mclaw_core.secret import SecretError


def _gate(tmp_path: Path, *, tier: str | None = None) -> ExclusionGate:
    (tmp_path / "exclusions.yaml").write_text(
        "chat:\n  source:\n"
        + (f"    - {{id: blocked, tier: {tier}}}\n" if tier else "    []\n"),
        encoding="utf-8",
    )
    (tmp_path / "local-whitelist.yaml").write_text("scan_roots: []\n", encoding="utf-8")
    return ExclusionGate.load(tmp_path)


class Provider:
    source_id = "source"

    def __init__(self, items: Iterable[EnumeratedItem], *, fail: bool = False) -> None:
        self.items = list(items)
        self.fail = fail
        self.content_calls: list[str] = []

    def enumerate_items(self, cursor: JsonValue | None) -> Iterable[EnumeratedItem]:
        return self.items

    def fetch_content(self, item: EnumeratedItem) -> dict[str, JsonValue]:
        self.content_calls.append(str(item.envelope["id"]))
        if self.fail:
            raise RuntimeError("provider unavailable")
        return {"text": "safe content"}


class FlakyProvider(Provider):
    def __init__(self, items: Iterable[EnumeratedItem]) -> None:
        super().__init__(items)
        self.attempts = 0

    def fetch_content(self, item: EnumeratedItem) -> dict[str, JsonValue]:
        self.attempts += 1
        if self.attempts < 3:
            error = TransientProviderError("slow down")
            error.retry_after = 7  # type: ignore[attr-defined]
            raise error
        return super().fetch_content(item)


def _item(item_id: str, ref: str, cursor: JsonValue) -> EnumeratedItem:
    return EnumeratedItem(
        ref=ChatRef(id=ref),
        cursor=cursor,
        envelope={
            "id": item_id,
            "source": "source",
            "kind": "chat.message",
            "ts": "2026-07-18T12:00:00Z",
        },
    )


def test_gate_runs_before_content_fetch_for_blocked_item(tmp_path: Path) -> None:
    provider = Provider([_item("chat:blocked", "blocked", "2")])

    result = fetch_items(
        provider, gate=_gate(tmp_path, tier="blocked"), state_root=tmp_path / "state"
    )

    assert provider.content_calls == []
    assert result.blocked_skipped == 1
    assert result.fetched == 0


def test_blocked_item_does_not_checkpoint_its_opaque_cursor(tmp_path: Path) -> None:
    state = tmp_path / "state"
    cursor_path = state / "cursors" / "source.json"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text('{"source":"source","data":"old"}', encoding="utf-8")

    fetch_items(
        Provider([_item("chat:blocked", "blocked", "excluded-opaque-cursor")]),
        gate=_gate(tmp_path, tier="blocked"),
        state_root=state,
    )

    assert json.loads(cursor_path.read_text(encoding="utf-8"))["data"] == "old"


def test_cursor_advances_only_after_successful_processing(tmp_path: Path) -> None:
    state = tmp_path / "state"
    cursor_path = state / "cursors" / "source.json"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text('{"source":"source","data":"old"}', encoding="utf-8")
    provider = Provider([_item("chat:one", "allowed", "new")], fail=True)

    with pytest.raises(FetchError):
        fetch_items(
            provider, gate=_gate(tmp_path), state_root=state, sleep=lambda _: None
        )

    assert json.loads(cursor_path.read_text(encoding="utf-8"))["data"] == "old"


def test_malformed_cursor_writes_error_run_record(tmp_path: Path) -> None:
    state = tmp_path / "state"
    cursor_path = state / "cursors" / "source.json"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text("not json", encoding="utf-8")

    with pytest.raises(FetchError, match="cursor state is unreadable"):
        fetch_items(Provider([]), gate=_gate(tmp_path), state_root=state)

    run = json.loads((state / "runs" / "fetch-source.json").read_text(encoding="utf-8"))
    history = (state / "runs" / "history.jsonl").read_text(encoding="utf-8")
    assert run["last_result"] == "error"
    assert run["error"] == "FetchError"
    assert '"last_result":"error"' in history


def test_error_run_preserves_prior_last_success(tmp_path: Path) -> None:
    state = tmp_path / "state"
    runs = state / "runs"
    runs.mkdir(parents=True)
    (runs / "backfill.json").write_text(
        '{"pipeline":"backfill","last_success":"2026-07-17T01:00:00Z"}',
        encoding="utf-8",
    )
    provider = Provider([_item("chat:one", "allowed", "new")], fail=True)

    with pytest.raises(FetchError):
        fetch_items(
            provider,
            gate=_gate(tmp_path),
            state_root=state,
            pipeline="backfill",
            sleep=lambda _: None,
        )

    run = json.loads((runs / "backfill.json").read_text(encoding="utf-8"))
    assert run["last_success"] == "2026-07-17T01:00:00Z"


def test_atomic_json_write_uses_unique_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    stale_fixed_temp = tmp_path / "state.json.tmp"
    stale_fixed_temp.write_text("another writer", encoding="utf-8")

    _atomic_json_write(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert stale_fixed_temp.read_text(encoding="utf-8") == "another writer"


def test_spool_record_uses_common_envelope_schema(tmp_path: Path) -> None:
    state = tmp_path / "state"
    provider = Provider([_item("chat:one", "allowed", {"after": "one"})])

    result = fetch_items(
        provider,
        gate=_gate(tmp_path),
        state_root=state,
        now=lambda: "2026-07-18T12:01:02Z",
    )

    spool = next((state / "spool" / "source").glob("*.jsonl"))
    record = json.loads(spool.read_text(encoding="utf-8"))
    assert {"id", "source", "kind", "ts", "sweep", "tier"} <= record.keys()
    assert record["tier"] == "full"
    assert record["text"] == "safe content"
    assert result.fetched == 1


def test_run_record_counts_blocked_items_without_identifiers(tmp_path: Path) -> None:
    state = tmp_path / "state"
    provider = Provider([_item("chat:blocked", "blocked", "2")])

    fetch_items(
        provider,
        gate=_gate(tmp_path, tier="blocked"),
        state_root=state,
        pipeline="backfill",
    )

    run_record = (state / "runs" / "backfill.json").read_text(encoding="utf-8")
    assert json.loads(run_record)["blocked_skipped"] == 1
    assert "chat:blocked" not in run_record


def test_retries_three_times_and_honors_retry_after(tmp_path: Path) -> None:
    provider = FlakyProvider([_item("chat:one", "allowed", "new")])
    delays: list[float] = []

    fetch_items(
        provider,
        gate=_gate(tmp_path),
        state_root=tmp_path / "state",
        sleep=delays.append,
    )

    assert provider.attempts == 3
    assert delays == [7.0, 7.0]


def test_non_transient_provider_error_is_not_retried(tmp_path: Path) -> None:
    provider = Provider([_item("chat:one", "allowed", "new")], fail=True)

    with pytest.raises(FetchError):
        fetch_items(
            provider,
            gate=_gate(tmp_path),
            state_root=tmp_path / "state",
            sleep=lambda _: pytest.fail("non-transient error must not sleep/retry"),
        )

    assert provider.content_calls == ["chat:one"]


def test_falsey_retry_predicate_is_honored(tmp_path: Path) -> None:
    provider = FlakyProvider([_item("chat:one", "allowed", "new")])

    class FalseyRetryable:
        def __bool__(self) -> bool:
            return False

        def __call__(self, error: Exception) -> bool:
            return False

    with pytest.raises(FetchError):
        fetch_items(
            provider,
            gate=_gate(tmp_path),
            state_root=tmp_path / "state",
            retryable=FalseyRetryable(),
            sleep=lambda _: pytest.fail("falsey predicate rejected retry"),
        )

    assert provider.attempts == 1


def test_ephemeral_item_is_rejected_before_content_fetch(tmp_path: Path) -> None:
    provider = Provider([_item("chat:blocked", "blocked", "new")])

    with pytest.raises(FetchError, match="unsupported until Phase 2"):
        fetch_items(
            provider,
            gate=_gate(tmp_path, tier="ephemeral"),
            state_root=tmp_path / "state",
            pipeline="backfill",
        )

    assert provider.content_calls == []
    assert not (tmp_path / "state" / "spool" / "ephemeral").exists()


def test_ephemeral_item_rejects_caller_selected_sweep_name(tmp_path: Path) -> None:
    provider = Provider([_item("chat:blocked", "blocked", "new")])

    with pytest.raises(FetchError, match="unsupported until Phase 2"):
        fetch_items(
            provider,
            gate=_gate(tmp_path, tier="ephemeral"),
            state_root=tmp_path / "state",
            pipeline="sweep-15m",
        )

    assert provider.content_calls == []


def test_secret_resolution_receives_requested_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[tuple[str, str, str]] = []

    def fake_keychain_get_secret(item: str, field: str, *, profile: str) -> str:
        received.append((item, field, profile))
        return "secret"

    monkeypatch.setattr(
        "mclaw_core.fetch.keychain_get_secret", fake_keychain_get_secret
    )

    assert get_secret("keychain://mark-claw-alt/item-field", profile="alt") == "secret"
    assert received == [("item", "field", "alt")]


def test_secret_resolution_error_becomes_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> str:
        raise SecretError("locked")

    monkeypatch.setattr("mclaw_core.fetch.keychain_get_secret", fail)

    with pytest.raises(FetchError, match="resolution failed"):
        get_secret("keychain://mark-claw-alt/item-field", profile="alt")
