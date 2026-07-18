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
from mclaw_core.output_guard import Clean, OutputGuard, Surface, Trip

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


@dataclass(frozen=True)
class CanaryFixture:
    """The isolated profile and unique sentinel for one canary invocation."""

    root: Path
    sentinel: str


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


def _write_fixture_config(config_root: Path, vault: Path, *, sentinel: str) -> None:
    """Seed every blocked fixture identifier with the invocation's sentinel."""
    blocked_channel = f"CANARY-BLOCKED-CHANNEL-{sentinel}"
    blocked_contact = f"CANARY-EPHEMERAL-CONTACT-{sentinel}"
    blocked_folder = f"/canary/{sentinel}/blocked-folder"
    config_root.mkdir(parents=True)
    (config_root / "exclusions.yaml").write_text(
        yaml.safe_dump(
            {
                "chat": {
                    "canary-chat": [
                        {"id": blocked_channel, "tier": "blocked"},
                        {"id": blocked_contact, "tier": "ephemeral"},
                    ]
                },
                "drive": {
                    "canary-drive": [
                        {"path": blocked_folder, "tier": "blocked"}
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


def _assert_no_sentinel(sentinel: str, *persistence_roots: Path) -> None:
    """Verify every normal output/state persistence surface is sentinel-free.

    ``state/quarantine`` is the sole, sanctioned §5.4 exception: it preserves
    a rejected artifact for human review outside vault/sync. The caller checks
    that exception's secure modes/location and scans its changelog/review
    records separately, where matched content must never appear.
    """
    leaked: list[Path] = []
    for root in persistence_roots:
        leaked.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and sentinel in path.read_text(encoding="utf-8")
        )
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
    vault = root / "output" / "vault"
    spool = state_root / "spool"
    logs = state_root / "logs"
    runs = state_root / "runs"
    ephemeral = state_root / "spool" / "ephemeral"
    for directory in (vault, spool, logs, runs, ephemeral):
        directory.mkdir(parents=True, exist_ok=True)

    guard = OutputGuard(config_root=config_root, state_root=state_root)
    blocked_channel = f"CANARY-BLOCKED-CHANNEL-{sentinel}"
    blocked_contact = f"CANARY-EPHEMERAL-CONTACT-{sentinel}"
    blocked_folder = f"/canary/{sentinel}/blocked-folder"
    providers = (
        MockProvider(
            FixtureItem(
                "canary-chat",
                ChatRef(id=blocked_channel),
                f"chat content from {blocked_channel}",
                Decision.BLOCKED,
            )
        ),
        MockProvider(
            FixtureItem(
                "canary-drive",
                DriveRef(path=f"{blocked_folder}/document"),
                f"folder content from {blocked_folder}",
                Decision.BLOCKED,
            )
        ),
        MockProvider(
            FixtureItem(
                "canary-chat",
                ChatRef(id=blocked_contact),
                f"ephemeral content from {blocked_contact}",
                Decision.EPHEMERAL,
            )
        ),
        MockProvider(
            FixtureItem(
                "canary-chat",
                ChatRef(id="CANARY-ALLOWED"),
                # Deliberate writer-side drill: this allowed item carries a
                # blocked identifier and must be stopped by OutputGuard.
                f"allowed item repeats blocked identifier {blocked_channel}",
                Decision.ALLOW,
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
            assert transient.read_text(encoding="utf-8") == content
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
    _assert_no_sentinel(
        sentinel,
        vault,
        spool,
        logs,
        runs,
        state_root / "changelog",
        state_root / "review-queue",
    )

    quarantine_dir = state_root / "quarantine"
    assert quarantine_dir.is_dir()
    assert quarantine_dir.stat().st_mode & 0o777 == 0o700
    quarantined = list(quarantine_dir.iterdir())
    assert quarantined, "allowed artifact containing a blocked ID did not trip guard"
    for artifact in quarantined:
        assert artifact.parent == quarantine_dir
        assert artifact.stat().st_mode & 0o777 == 0o600
        assert sentinel in artifact.read_text(encoding="utf-8")
    for record_dir in (state_root / "changelog", state_root / "review-queue"):
        for record in record_dir.rglob("*"):
            if record.is_file():
                assert sentinel not in record.read_text(encoding="utf-8")


@pytest.fixture
def canary_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[CanaryFixture]:
    """A clean, repo-local fixture profile; never touch user config or state."""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    monkeypatch.setenv("MCLAW_PROFILE", CANARY_PROFILE)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(TMP_ROOT / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(TMP_ROOT / "state"))
    sentinel = f"MCX-CANARY-{uuid.uuid4()}"
    _write_fixture_config(
        paths.config_root(), TMP_ROOT / "output" / "vault", sentinel=sentinel
    )
    yield CanaryFixture(root=TMP_ROOT, sentinel=sentinel)
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_canary_blocks_channel_folder_and_contact_without_output_leak(
    canary_fixture: CanaryFixture,
) -> None:
    gate = ExclusionGate.load(paths.config_root())

    _run_current_phase_one_pipelines(
        root=canary_fixture.root, sentinel=canary_fixture.sentinel, gate=gate
    )


def test_canary_fails_when_gate_is_mutated_to_always_allow(
    canary_fixture: CanaryFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controlled mutation test: proves the canary catches an always-ALLOW gate."""
    gate = ExclusionGate.load(paths.config_root())
    monkeypatch.setattr(gate, "check", lambda _source, _ref: Decision.ALLOW)

    with pytest.raises(CanaryLeakError, match="blocked fixture content was fetched"):
        _run_current_phase_one_pipelines(
            root=canary_fixture.root,
            sentinel=canary_fixture.sentinel,
            gate=gate,
        )


def test_canary_fails_when_output_guard_is_bypassed(
    canary_fixture: CanaryFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controlled mutation test: a writer that skips the guard leaks visibly."""
    gate = ExclusionGate.load(paths.config_root())
    monkeypatch.setattr(OutputGuard, "scan", lambda *_args, **_kwargs: Clean())

    with pytest.raises(CanaryLeakError, match="canary sentinel reached output"):
        _run_current_phase_one_pipelines(
            root=canary_fixture.root,
            sentinel=canary_fixture.sentinel,
            gate=gate,
        )
