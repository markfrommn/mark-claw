"""``mclaw`` command-line entry point.

argparse CLI over a minimal runtime surface (stdlib + PyYAML for config
parsing); the dependency allowlist + framework deny-list test guards it. Most
subcommands are stubs at this stage of the build; ``secret`` is functional,
``doctor`` validates the config/state trees, and ``auth`` is a deliberate stub
tracked in DEV-31.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from . import __version__, doctor, output_guard, paths, secret

#: Subcommands that are intentionally not implemented in this unit. Each prints a
#: clear message and exits non-zero so callers cannot mistake a stub for a no-op.
#: (``guard`` has a real ``scan-vault`` subcommand — wired up in
#: :func:`_build_guard_parser` / :func:`cmd_guard`; only its other potential
#: subactions remain stubs.)
_STUBS: dict[str, str] = {
    "exclusions": "manage exclusion lists",
    "fetch": "run a source fetch",
    "ingest": "ingest fetched items into the vault",
}

_AUTH_PROVIDERS = ("google", "graph", "telegram")

# Absolute so ``mclaw test --canary`` works from an operator's preferred cwd.
_CANARY_TEST_DIR = Path(__file__).resolve().parents[1] / "tests" / "canary"


def _print_stub(command: str, *, tracked: str | None = None) -> int:
    """Report a not-yet-implemented command and return a non-zero exit code."""
    where = f" — tracked in {tracked}" if tracked else ""
    print(f"mclaw {command}: not implemented{where}", file=sys.stderr)
    return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Validate the config/state trees; with ``--init`` create them first.

    Delegates to :func:`mclaw_core.doctor.run_doctor` and prints the rendered
    report. The report's exit code is returned directly:

    * bare ``doctor`` — 0 iff every hard check is ok (vault-absent is WARN,
      not a hard fail; an unset vault path IS a hard FAIL here);
    * ``doctor --init`` — 0 when the only FAILs are deferred (unset vault);
      nonzero on any non-deferred hard FAIL (malformed config, missing
      ``security``, secure-dir perm/symlink failure). The full checklist
      still prints as information.
    """
    profile = paths.resolve_profile()
    report = doctor.run_doctor(profile, init=bool(getattr(args, "init", False)))
    print(report.render())
    return report.exit_code


def cmd_auth(args: argparse.Namespace) -> int:
    """Stub: the real OAuth / device-code / Telethon flows land in DEV-31."""
    return _print_stub(f"auth {args.provider}", tracked="DEV-31")


def cmd_secret(args: argparse.Namespace) -> int:
    """Dispatch the functional ``secret`` subcommands."""
    profile = paths.resolve_profile()
    try:
        if args.secret_cmd == "set":
            secret.set_secret(args.item, args.field, profile=profile)
            print(
                f"stored {args.item}-{args.field} on "
                f"{secret.service_name(profile)}",
                file=sys.stderr,
            )
            return 0
        if args.secret_cmd == "get":
            # Only the value goes to stdout; nothing else, so it is safe to pipe.
            print(secret.get_secret(args.item, args.field, profile=profile))
            return 0
        if args.secret_cmd == "list":
            for account in secret.list_accounts(profile=profile):
                print(account)
            return 0
        if args.secret_cmd == "export":
            target = secret.export_backup(profile=profile, recipient=args.recipient)
            print(f"wrote encrypted backup to {target}", file=sys.stderr)
            return 0
    except secret.SecretError as exc:
        print(f"mclaw secret {args.secret_cmd}: {exc}", file=sys.stderr)
        return 1
    return _print_stub(f"secret {args.secret_cmd}")  # pragma: no cover


def cmd_stub(args: argparse.Namespace) -> int:
    """Handler for the plain stub subcommands."""
    return _print_stub(args.command)


def cmd_guard(args: argparse.Namespace) -> int:
    """Dispatch the ``guard`` subcommands.

    Currently only ``scan-vault`` is implemented (B4 / DEV-15): the continuous
    spot-check of design §5.5.3, scanning every vault note through the output
    guard and reporting findings. The scan is informational — it never
    quarantines or blocks; the fail-closed action happens at write time inside
    :meth:`mclaw_core.output_guard.OutputGuard.on_trip`. An exit code of 0 with
    a non-zero finding count lets the weekly review surface findings without
    failing CI (the canary suite in B5/DEV-16 is the gating test).
    """
    if getattr(args, "guard_cmd", None) == "scan-vault":
        return _cmd_guard_scan_vault(paths.resolve_profile())
    return _print_stub("guard")


def cmd_test(args: argparse.Namespace) -> int:
    """Run explicit test suites exposed as stable operator/CI commands."""
    if getattr(args, "canary", False):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", str(_CANARY_TEST_DIR)], check=False
        )
        return completed.returncode
    return _print_stub("test")


def _cmd_guard_scan_vault(profile: str) -> int:
    """Walk the profile's vault and scan every note through the guard.

    Per the §B4 acceptance criterion, an empty/unset/relative vault prints
    ``"0 findings"`` and exits 0. A configured absolute vault with notes is
    scanned against the persistence deny-pattern set (vault notes are the
    canonical persistence surface — §5.4) and the count is reported. The
    command never exits non-zero on findings (it is the §5.5.3 continuous
    spot-check, not a gate); the non-zero paths are:

    * a guard construction failure (broken ``exclusions.yaml``) — reported to
      stderr;
    * an incomplete scan (one or more notes could not be read — ``OSError``,
      broken symlink, non-UTF-8) — printed as a warning, because reporting
      ``"0 findings"`` while notes were silently skipped is a false clean.
      The scan-vault contract already distinguishes 0-findings=exit-0 from
      failure=exit-1; an incomplete scan is a failure.
    """
    vault = _resolve_vault_path(profile)
    if vault is None:
        # Unset / relative / unparseable settings — the §B4 AC exit-0 path.
        # Reserved for "no vault configured"; a configured-but-wrong-shape
        # vault is a failure (see the ``not is_dir()`` branch below).
        print("0 findings")
        return 0
    if not vault.is_dir():
        # A configured absolute vault that is not a directory (a regular file,
        # a broken symlink, or a path that does not exist) is an operator
        # misconfiguration, not the unset case. Reporting "0 findings" here
        # would be a false clean — the scan never ran. Surface to stderr and
        # exit nonzero, the same contract as the incomplete-scan path below.
        print(
            f"mclaw guard scan-vault: configured vault is not a directory: {vault}",
            file=sys.stderr,
        )
        return 1

    try:
        guard = output_guard.OutputGuard.for_profile(profile)
    except output_guard.OutputGuardError as exc:
        print(f"mclaw guard scan-vault: cannot compile guard: {exc}", file=sys.stderr)
        return 1

    findings: list[output_guard.Trip] = []
    unreadable: list[str] = []
    # The directory traversal itself can raise ``OSError`` (an unreadable
    # subdir, a broken symlink on a dir entry, a permissions flip mid-scan).
    # Left unhandled that surfaces as an uncaught traceback; the scan-vault
    # contract already distinguishes 0-findings=exit-0 from failure=exit-1,
    # and "cannot enumerate the vault" is a failure of the same class as the
    # per-note unreadable-skip below — record it, print the incomplete-scan
    # warning, and exit nonzero rather than crashing.
    try:
        note_iter = sorted(vault.rglob("*.md"))
    except OSError as exc:
        print(
            f"mclaw guard scan-vault: cannot enumerate vault: {exc}",
            file=sys.stderr,
        )
        return 1
    for note_path in note_iter:
        try:
            text = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Surface the skip rather than hide it. A broken-symlink /
            # unreadable / non-UTF-8 note could be the very note carrying a
            # blocked identifier; reporting "0 findings" while skipping it
            # would be a false clean. Accumulate the relative path and warn
            # after the scan; the exit code is nonzero so the spot-check
            # cannot masquerade as success.
            unreadable.append(
                f"{note_path.relative_to(vault)} ({type(exc).__name__})"
            )
            continue
        rel = str(note_path.relative_to(vault))
        result = guard.scan(
            text, surface=output_guard.Surface.PERSISTENCE, artifact_name=rel
        )
        if isinstance(result, output_guard.Trip):
            findings.append(result)

    if unreadable:
        # Report the finding count first (the operator's primary question),
        # then the warning + skipped list. Exit nonzero: the scan did not
        # cover the whole vault, so "0 findings" alone would be misleading.
        print(f"{len(findings)} finding(s)")
        for trip in findings:
            print(f"  {trip.pattern_id}  {trip.artifact_name}")
        print(
            f"warning: {len(unreadable)} note(s) unreadable — scan incomplete:",
            file=sys.stderr,
        )
        for entry in unreadable:
            print(f"  {entry}", file=sys.stderr)
        return 1

    if not findings:
        print("0 findings")
        return 0
    print(f"{len(findings)} finding(s)")
    for trip in findings:
        print(f"  {trip.pattern_id}  {trip.artifact_name}")
    return 0


def _resolve_vault_path(profile: str) -> Path | None:
    """Return the configured absolute vault path, or ``None`` if unset.

    Reads ``settings.yaml → vault.path`` (the only source for the vault root,
    per design §6.1). Returns ``None`` when:

    * ``settings.yaml`` is absent / unparseable / not a mapping;
    * ``vault`` or ``vault.path`` is missing / non-string / empty;
    * ``vault.path`` is relative after expansion — a relative path would be
      resolved against the launchd-invoked process's cwd (which is **not** a
      stable anchor for the vault), so the scan would either miss the real
      vault entirely or scan an unrelated directory. Treated as unset so the
      caller reports ``0 findings`` rather than scanning cwd by accident.

    Never raises — a malformed settings file or an unknown ``~user`` simply
    means "no vault configured", which the caller reports as ``0 findings``.
    """
    settings_path = paths.config_root(profile) / "settings.yaml"
    if not settings_path.is_file():
        return None
    try:
        with settings_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    vault = data.get("vault")
    if not isinstance(vault, dict):
        return None
    raw = vault.get("path", "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        # ``Path.expanduser`` raises ``RuntimeError`` on an unknown ``~user``
        # and ``OSError`` on a pathological value. The contract here is
        # "never raises" (a malformed vault path is the operator's settings
        # problem, not a traceback at scan-vault time) — treat both as unset.
        expanded = Path(raw).expanduser()
    except (RuntimeError, OSError):
        return None
    if not expanded.is_absolute():
        # A relative path is the wrong shape for a vault root — do NOT scan
        # cwd by accident. The operator should set an absolute path.
        return None
    return expanded


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser and all subcommands."""
    parser = argparse.ArgumentParser(
        prog="mclaw",
        description="mark-claw tooling CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"mclaw {__version__}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    doctor = sub.add_parser(
        "doctor",
        help="validate config/state trees; --init creates them idempotently",
    )
    doctor.add_argument(
        "--init",
        action="store_true",
        help="create config + state trees idempotently, then validate",
    )
    doctor.set_defaults(func=cmd_doctor)

    auth = sub.add_parser(
        "auth",
        help="authenticate a provider (stub — see DEV-31)",
    )
    auth.add_argument("provider", choices=_AUTH_PROVIDERS)
    auth.set_defaults(func=cmd_auth)

    _build_secret_parser(sub)

    _build_guard_parser(sub)

    test = sub.add_parser("test", help="run Mark-Claw test suites")
    test.add_argument(
        "--canary",
        action="store_true",
        help="run the exclusion zero-leak integration canary",
    )
    test.set_defaults(func=cmd_test)

    for name, help_text in _STUBS.items():
        stub = sub.add_parser(name, help=f"{help_text} (stub)")
        stub.set_defaults(func=cmd_stub, command=name)

    return parser


def _build_guard_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the ``guard`` subcommand with its ``scan-vault`` sub-action.

    ``guard scan-vault`` is the §5.5.3 continuous spot-check over the vault
    (B4 / DEV-15). Other potential guard actions remain out of scope; the
    dispatcher :func:`cmd_guard` falls through to the stub message for any
    unimplemented sub-action.
    """
    guard = sub.add_parser(
        "guard",
        help="run the fail-closed output guard",
    )
    # Default dispatch for bare ``mclaw guard`` (no sub-action): goes to
    # :func:`cmd_guard`, which prints the stub message and exits non-zero.
    # A real sub-action (``scan-vault``) overrides via its own ``set_defaults``.
    guard.set_defaults(func=cmd_guard)
    guard_sub = guard.add_subparsers(
        dest="guard_cmd",
        metavar="<action>",
    )
    sv = guard_sub.add_parser(
        "scan-vault",
        help="scan every vault note through the output guard; report findings",
    )
    sv.set_defaults(func=cmd_guard)


def _build_secret_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Add the ``secret set|get|list|export`` subcommands."""
    secret_parser = sub.add_parser("secret", help="manage keychain secrets")
    secret_sub = secret_parser.add_subparsers(
        dest="secret_cmd",
        metavar="<action>",
        required=True,
    )

    s_set = secret_sub.add_parser(
        "set",
        help="create/update a secret (value read from stdin or prompt)",
    )
    s_set.add_argument("item")
    s_set.add_argument("field")
    s_set.set_defaults(func=cmd_secret)

    s_get = secret_sub.add_parser("get", help="print a secret value to stdout")
    s_get.add_argument("item")
    s_get.add_argument("field")
    s_get.set_defaults(func=cmd_secret)

    s_list = secret_sub.add_parser("list", help="list secret accounts for this profile")
    s_list.set_defaults(func=cmd_secret)

    s_export = secret_sub.add_parser(
        "export",
        help="write an age-encrypted backup of the credential set",
    )
    s_export.add_argument(
        "--recipient",
        help="age recipient (public key); omit for interactive passphrase mode",
    )
    s_export.set_defaults(func=cmd_secret)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 1
    result = args.func(args)
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
