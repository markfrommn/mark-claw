"""``mclaw doctor`` — validate the profile config + state trees and report.

Two modes (CLI flag ``--init``):

* ``doctor``        — validate-and-report. Prints a checklist, one line per
  check, each ``ok`` / ``FAIL`` / ``WARN`` / ``MISSING``. Exit 0 iff every hard
  check is ok.
* ``doctor --init`` — idempotently create both trees
  (:mod:`mclaw_core.config_state`), then run the same validation and print the
  full checklist (useful info — "scaffolding created; here's what's left to
  configure"). Exits 0 when the only FAILs are on **deferred** checks (an
  unset vault path, owned by C1/DEV-18); any non-deferred hard FAIL (malformed
  config, a missing ``security`` binary, a secure-dir perm/symlink failure)
  surfaces nonzero. ``--init`` is a bootstrap command; its exit code reflects
  scaffolding success plus non-deferred validation, not whether the operator
  has finished C1-owned configuration.

Checks (design §6.1, §6.2; CLAUDE.md hard rules):

* config root and state root exist.
* each required config file exists, parses as YAML, and has its required
  top-level keys (light schema — later units own real content validation).
* state dir permissions: ``secrets/``, ``quarantine/``, ``spool/ephemeral/``
  are ``0700`` (the fail-closed guarantee dirs).
* Keychain reachability via :mod:`mclaw_core.secret` — never resolves or prints
  a secret value (the check probes the ``security`` surface only).
* vault path in ``settings.yaml``: unset/non-absolute → FAIL; absolute + dir
  absent → WARN (C1/DEV-18 creates the vault, blocked-by B2); existing → ok.

When either root is missing, the doctor short-circuits with a MISSING report
and exit 0 — the pre-init state is expected, and the hint points at ``--init``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config_state, paths, secret

#: Required top-level keys per config file (light schema). A file missing any
#: of its listed keys reports FAIL. Real content validation is deferred to the
#: units that own each file's semantics.
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "settings.yaml": ("vault", "timezone", "profile", "runtime", "limits"),
    "accounts.yaml": ("email", "own_addresses"),
    "sources.yaml": ("chat", "activity"),
    "exclusions.yaml": ("chat", "drive", "meetings"),
    "local-whitelist.yaml": ("scan_roots",),
}

# Check statuses. FAIL is a hard failure (contributes to nonzero exit); WARN is
# soft (reported but not fatal); MISSING is the pre-init root state (exit 0).
STATUS_OK = "ok"
STATUS_FAIL = "FAIL"
STATUS_WARN = "WARN"
STATUS_MISSING = "MISSING"

_HARD_FAIL = {STATUS_FAIL}


@dataclass
class Check:
    """One checklist line."""

    name: str
    status: str
    detail: str = ""
    #: True only for FAILs whose unset/absent state is owned by a later unit
    #: (the vault-path-unset check, owned by C1/DEV-18). A deferred FAIL is
    #: dropped from :attr:`DoctorReport.exit_code` under ``--init`` (bootstrap),
    #: but still counted by bare ``doctor`` (the validator).
    deferred: bool = False

    def render(self) -> str:
        base = f"  {self.name:<26} [{self.status}]"
        return f"{base}  {self.detail}" if self.detail else base


@dataclass
class DoctorReport:
    """The full doctor report — rendered to text, with an exit code."""

    profile: str
    checks: list[Check] = field(default_factory=list)
    #: True when a root was MISSING and the doctor skipped the deep checks.
    short_circuited: bool = False
    #: True when the operator invoked ``--init``. Under ``init``, deferred FAIL
    #: checks (an unset vault path, owned by C1/DEV-18) are dropped from the
    #: exit-code computation; non-deferred hard FAILs (malformed config, a
    #: missing ``security`` binary, a secure-dir perm/symlink failure) still
    #: surface nonzero. Bare ``doctor`` (init=False) counts every FAIL,
    #: including deferred ones.
    init: bool = False
    hint: str = ""

    @property
    def exit_code(self) -> int:
        if self.short_circuited:
            # MISSING roots are the expected pre-init state, not a hard fail.
            return 0
        fails = [c for c in self.checks if c.status in _HARD_FAIL]
        if self.init:
            # ``--init`` bootstrap: drop deferred FAILs (unset vault, owned by
            # C1/DEV-18) from the exit computation. Any non-deferred hard FAIL
            # (malformed config, missing `security`, secure-dir failure) still
            # surfaces nonzero — bootstrap success does not mask them.
            fails = [c for c in fails if not c.deferred]
        return 1 if fails else 0

    def render(self) -> str:
        lines = [f"mclaw doctor — profile: {self.profile}"]
        lines.extend(c.render() for c in self.checks)
        if self.hint:
            lines.append("")
            lines.append(self.hint)
        return "\n".join(lines)


def run_doctor(profile: str, *, init: bool = False) -> DoctorReport:
    """Run the doctor, optionally initializing both trees first.

    Returns a :class:`DoctorReport` whose :attr:`DoctorReport.exit_code` the
    CLI returns directly. When ``init`` is true, both trees are created first
    and :attr:`DoctorReport.init` is set — deferred FAILs (an unset vault path)
    are then dropped from the exit computation, but non-deferred hard FAILs
    still surface nonzero. A :class:`config_state.StateInitError` (a symlinked
    secure dir refusing init) is swallowed so the validation pass can report
    it via ``_check_perm`` rather than crashing the CLI; that FAIL is
    non-deferred and still exits nonzero under ``--init``.
    """
    if init:
        config_state.init_config_tree(profile=profile)
        try:
            config_state.init_state_tree(profile=profile)
        except config_state.StateInitError:
            # A symlinked secure dir refuses init (fail-closed). Swallow it and
            # fall through to the validation pass, which re-flags the symlink
            # via ``_check_perm`` (non-deferred FAIL → nonzero under --init).
            # ``init`` stays set so deferred downstream gaps (unset vault) are
            # still exempted; the symlink FAIL is not masked. The doctor
            # reports rather than crashes.
            pass

    report = DoctorReport(profile=profile, init=init)

    cfg = paths.config_root(profile)
    st = paths.state_root(profile)
    cfg_ok = cfg.is_dir()
    st_ok = st.is_dir()

    if not cfg_ok or not st_ok:
        # Pre-init: report which roots are missing and point at --init. Do not
        # attempt the deep checks (no files/dirs to check). This is the expected
        # state before the operator has run init — exit 0.
        report.checks.append(
            Check("config root", STATUS_OK if cfg_ok else STATUS_MISSING, str(cfg))
        )
        report.checks.append(
            Check("state root", STATUS_OK if st_ok else STATUS_MISSING, str(st))
        )
        report.short_circuited = True
        missing = [
            label
            for label, ok in (("config", cfg_ok), ("state", st_ok))
            if not ok
        ]
        report.hint = (
            f"{' and '.join(missing)} root(s) missing — run "
            "`mclaw doctor --init` to create the tree."
        )
        return report

    # Both roots exist: run the full checklist.
    report.checks.append(Check("config root", STATUS_OK, str(cfg)))
    report.checks.append(Check("state root", STATUS_OK, str(st)))

    settings_data = _check_config_file(report, cfg / "settings.yaml")
    for name in (
        "accounts.yaml",
        "sources.yaml",
        "exclusions.yaml",
        "local-whitelist.yaml",
    ):
        _check_config_file(report, cfg / name)

    hyg = cfg / "hygiene-patterns.txt"
    if hyg.is_file():
        report.checks.append(Check("hygiene-patterns.txt", STATUS_OK, str(hyg)))
    else:
        # Missing hygiene patterns degrade gracefully (the hygiene test skips),
        # so this is a soft WARN — fail-open on everything but the hard
        # guarantees. ``--init`` creates the skeleton.
        report.checks.append(
            Check(
                "hygiene-patterns.txt",
                STATUS_WARN,
                f"missing at {hyg} (run `mclaw doctor --init`)",
            )
        )

    # Fail-closed guarantee dirs: must be exactly 0700.
    for rel in config_state.STATE_DIRS:
        if rel in config_state.SECURE_DIRS:
            _check_perm(report, st / rel, rel, config_state.SECURE_DIR_MODE)

    _check_keychain(report, profile)
    _check_vault(report, settings_data)

    return report


def _check_config_file(report: DoctorReport, path: Path) -> dict[str, object] | None:
    """Append a check for one YAML config file; return parsed data or None."""
    name = path.name
    if not path.is_file():
        report.checks.append(Check(name, STATUS_FAIL, f"missing at {path}"))
        return None
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        report.checks.append(Check(name, STATUS_FAIL, f"YAML parse error: {exc}"))
        return None
    except (OSError, UnicodeDecodeError) as exc:
        # An existing-but-unreadable file (e.g. ``PermissionError``) or a
        # non-UTF-8 file must surface as a hard FAIL checklist line, not abort
        # the doctor with a traceback. Keep the detail short (class name only)
        # so no exception-chain noise leaks into the report.
        report.checks.append(
            Check(name, STATUS_FAIL, f"unreadable: {type(exc).__name__}")
        )
        return None
    if not isinstance(data, dict):
        report.checks.append(Check(name, STATUS_FAIL, "top-level is not a mapping"))
        return None
    required = REQUIRED_KEYS.get(name, ())
    missing = [k for k in required if k not in data]
    if missing:
        report.checks.append(Check(name, STATUS_FAIL, f"missing keys: {missing}"))
        return data
    report.checks.append(Check(name, STATUS_OK, str(path)))
    return data


def _check_perm(
    report: DoctorReport, path: Path, rel: str, want_mode: int
) -> None:
    label = f"perms {rel}/ ({oct(want_mode)[2:]})"
    # Pre-check for "missing" so that case gets a clear detail line. TOCTOU
    # here is harmless: the doctor reports and never mutates, and the
    # ``O_NOFOLLOW`` open below is what guarantees the mode read never follows
    # a symlink.
    if not path.exists() and not path.is_symlink():
        report.checks.append(Check(label, STATUS_FAIL, "missing"))
        return
    # Anchor to the dir via ``O_NOFOLLOW | O_DIRECTORY`` (refuses a symlink at
    # the final component atomically) and read the mode via ``fstat`` on that
    # fd — the comparison never resolves a link. The doctor reports; it does
    # not chmod, so a single ``fstat`` is enough. ``ELOOP`` (symlink) or
    # ``ENOTDIR`` (regular file at this path) → not trusted as a fail-closed
    # secure directory.
    try:
        fd = os.open(str(path), os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError:
        report.checks.append(
            Check(label, STATUS_FAIL, "is a symlink — not trusted")
        )
        return
    try:
        cur = os.fstat(fd).st_mode & 0o777
    finally:
        os.close(fd)
    if cur == want_mode:
        report.checks.append(Check(label, STATUS_OK))
    else:
        report.checks.append(Check(label, STATUS_FAIL, f"got {oct(cur)[2:]}"))


def _check_keychain(report: DoctorReport, profile: str) -> None:
    """Probe the keychain surface via :mod:`mclaw_core.secret` — no values.

    ``list_accounts`` runs ``security dump-keychain`` and returns the account
    slugs for this profile's service; it never resolves or returns a secret
    value, so neither does this check. A missing ``security`` binary is a hard
    FAIL — the keychain integration cannot work without it. The binary's
    presence is probed directly via ``shutil.which`` rather than relying on a
    ``FileNotFoundError`` from ``list_accounts``: ``secret`` catches that
    internally and re-raises it as :class:`secret.SecretError`, so the
    missing-binary case would otherwise fall into the soft WARN branch and the
    doctor would exit 0 with keychain integration dead. A
    :class:`secret.SecretError` (locked/unreachable keychain) is a soft WARN.
    """
    service = secret.service_name(profile)
    label = f"keychain {service}"
    if shutil.which(secret.SECURITY) is None:
        report.checks.append(
            Check(
                label,
                STATUS_FAIL,
                "`security` not on PATH — keychain integration cannot run",
            )
        )
        return
    try:
        secret.list_accounts(profile=profile)
    except secret.SecretError:
        # Doctor intentionally does not forward secret-module exception text —
        # a fixed string means a future value-bearing SecretError can never leak
        # through this path (defense-in-depth on "secrets never reach stdout").
        report.checks.append(
            Check(
                label,
                STATUS_WARN,
                "unreachable (locked or access denied — run "
                "'mclaw secret list' to diagnose)",
            )
        )
        return
    report.checks.append(Check(label, STATUS_OK, "reachable"))


def _check_vault(
    report: DoctorReport, settings: dict[str, object] | None
) -> None:
    label = "vault path"
    if settings is None:
        report.checks.append(
            Check(label, STATUS_FAIL, "settings.yaml not parsed")
        )
        return
    vault = settings.get("vault")
    if not isinstance(vault, dict):
        report.checks.append(Check(label, STATUS_FAIL, "`vault` is not a mapping"))
        return
    raw = vault.get("path", "")
    if not isinstance(raw, str) or not raw.strip():
        # The unset vault path is the only deferred check: its absent state is
        # owned by C1/DEV-18, so under ``--init`` it is dropped from the exit
        # computation. Bare ``doctor`` still counts it as a hard FAIL.
        report.checks.append(Check(label, STATUS_FAIL, "unset", deferred=True))
        return
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        report.checks.append(
            Check(label, STATUS_FAIL, f"non-absolute: {raw!r}")
        )
        return
    if expanded.is_dir():
        report.checks.append(Check(label, STATUS_OK, str(expanded)))
    else:
        report.checks.append(
            Check(
                label,
                STATUS_WARN,
                f"absolute but dir absent: {expanded} (C1/DEV-18 creates it)",
            )
        )
