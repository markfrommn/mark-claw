"""End-to-end exclusion canary for the Phase-1 foundation layers.

There are no real fetch-base implementations or ingest writers yet (those
land after DEV-16).  This deliberately small harness exercises their required
contract with mocked providers: enumerate -> gate -> fetch -> emit.  It is
kept here, rather than production code, so future pipelines can be added to
the same fixture without inventing a premature pipeline abstraction.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from mclaw_core import paths
from mclaw_core.exclusion import ChatRef, Decision, DriveRef, ExclusionGate
from mclaw_core.output_guard import OutputGuard, Surface, Trip

CANARY_ROOT = Path(__file__).parent
TMP_ROOT = CANARY_ROOT / "tmp"
CANARY_PROFILE = "canary"


class CanaryLeakError(AssertionError):
    """A blocked item crossed the fetch boundary or reached an output surface."""


@dataclass(frozen=True)
class FixtureItem:
    """A provider item with a ref checked before its content may be fetched."""

    source_id: str
    ref: ChatRef | DriveRef
    content: str
    tier: Decision


class MockProvider:
    """Provider double that makes a blocked content fetch an immediate failure."""

    def __init__(self, item: FixtureItem) -> None:
        self.item = item
        self.fetch_calls = 0

    def fetch_content(self) -> str:
        self.fetch_calls += 1
        if self.item.tier is Decision.BLOCKED:
            raise CanaryLeakError("blocked fixture content was fetched")
        return self.item.content


def _write_fixture_config(config_root: Path, vault: Path) -> None:
    config_root.mkdir(parents=True)
    (config_root / "exclusions.yaml").write_text(
        yaml.safe_dump(
            {
                "chat": {
                    "canary-chat": [
                        {"id": "CANARY-BLOCKED-CHANNEL", "tier": "blocked"},
                        {"id": "CANARY-EPHEMERAL-CONTACT", "tier": "ephemeral"},
                    ]
                },
                "drive": {
                    "canary-drive": [
                        {"path": "/canary/blocked-folder", "tier": "blocked"}
                    ]
                },
                "meetings": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (config_root / "local-whitelist.yaml").write_text(
        "scan_roots: []\n", encoding="utf-8"
    )
    (config_root / "settings.yaml").write_text(
        yaml.safe_dump({"vault": {"path": str(vault)}}, sort_keys=False),
        encoding="utf-8",
    )


def _assert_no_sentinel(fixture_root: Path, sentinel: str) -> None:
    """Verify every emitted fixture surface is sentinel-free.

    The fixture root includes output spool/vault/logs/run records and the
    XDG state tree (ephemeral spool, quarantine, changelog, and state logs).
    Config is also harmless to scan and makes this a stronger whole-tree
    assertion without exposing any user profile path.
    """
    leaked = [
        path.relative_to(fixture_root)
        for path in fixture_root.rglob("*")
        if path.is_file() and sentinel in path.read_text(encoding="utf-8")
    ]
    if leaked:
        raise CanaryLeakError(f"canary sentinel reached output: {leaked}")


def _run_current_phase_one_pipelines(
    *, root: Path, sentinel: str, gate: ExclusionGate
) -> None:
    """Run the currently available pipeline contract over mocked providers.

    DEV-19 and later provide the real fetch base and writers.  Until then the
    fixture proves the two critical boundaries: the gate is invoked before a
    provider content read, and persistence writes are checked by the guard.
    """
    config_root = paths.config_root(CANARY_PROFILE)
    state_root = paths.state_root(CANARY_PROFILE)
    output_root = root / "output"
    vault = output_root / "vault"
    spool = output_root / "spool"
    logs = output_root / "logs"
    runs = output_root / "runs"
    ephemeral = state_root / "spool" / "ephemeral"
    for directory in (vault, spool, logs, runs, ephemeral):
        directory.mkdir(parents=True, exist_ok=True)

    guard = OutputGuard(config_root=config_root, state_root=state_root)
    providers = (
        MockProvider(
            FixtureItem(
                "canary-chat",
                ChatRef(id="CANARY-BLOCKED-CHANNEL"),
                f"chat content {sentinel}",
                Decision.BLOCKED,
            )
        ),
        MockProvider(
            FixtureItem(
                "canary-drive",
                DriveRef(path="/canary/blocked-folder/document"),
                f"folder content {sentinel}",
                Decision.BLOCKED,
            )
        ),
        MockProvider(
            FixtureItem(
                "canary-chat",
                ChatRef(id="CANARY-EPHEMERAL-CONTACT"),
                f"ephemeral content {sentinel}",
                Decision.EPHEMERAL,
            )
        ),
    )

    for provider in providers:
        decision = gate.check(provider.item.source_id, provider.item.ref)
        if decision is Decision.BLOCKED:
            continue
        content = provider.fetch_content()
        if decision is Decision.EPHEMERAL:
            transient = ephemeral / "during-sweep.jsonl"
            transient.write_text(content, encoding="utf-8")
            # The only permitted ephemeral persistence is within the sweep.
            transient.unlink()
            continue
        for destination in (spool / "items.jsonl", vault / "note.md"):
            result = guard.scan(
                content,
                surface=Surface.PERSISTENCE,
                artifact_name=destination.name,
            )
            if isinstance(result, Trip):
                guard.on_trip(result, content=content, actor="canary")
                continue
            destination.write_text(content, encoding="utf-8")
        # Current run/log records intentionally contain counts only, never refs.
        (logs / "pipeline.log").write_text("canary pipeline completed\n")
        (runs / "run.json").write_text('{"blocked_skipped": 2}\n')

    assert not any(ephemeral.iterdir()), "ephemeral content survived its sweep"
    _assert_no_sentinel(root, sentinel)


@pytest.fixture
def canary_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A clean, repo-local fixture profile; never touch user config or state."""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    monkeypatch.setenv("MCLAW_PROFILE", CANARY_PROFILE)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(TMP_ROOT / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(TMP_ROOT / "state"))
    _write_fixture_config(paths.config_root(), TMP_ROOT / "output" / "vault")
    yield TMP_ROOT
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_canary_blocks_channel_folder_and_contact_without_output_leak(
    canary_root: Path,
) -> None:
    sentinel = f"MCX-CANARY-{uuid.uuid4()}"
    gate = ExclusionGate.load(paths.config_root())

    _run_current_phase_one_pipelines(root=canary_root, sentinel=sentinel, gate=gate)


def test_canary_fails_when_gate_is_mutated_to_always_allow(
    canary_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controlled mutation test: proves the canary catches an always-ALLOW gate."""
    sentinel = f"MCX-CANARY-{uuid.uuid4()}"
    gate = ExclusionGate.load(paths.config_root())
    monkeypatch.setattr(gate, "check", lambda _source, _ref: Decision.ALLOW)

    with pytest.raises(CanaryLeakError, match="blocked fixture content was fetched"):
        _run_current_phase_one_pipelines(root=canary_root, sentinel=sentinel, gate=gate)
