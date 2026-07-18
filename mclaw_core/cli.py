"""``mclaw`` command-line entry point.

argparse CLI over a minimal runtime surface (stdlib + PyYAML for config
parsing); the dependency allowlist + framework deny-list test guards it. Most
subcommands are stubs at this stage of the build; ``secret`` is functional,
``doctor`` validates the config/state trees, and ``auth`` is a deliberate stub
tracked in DEV-31.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__, doctor, paths, secret

#: Subcommands that are intentionally not implemented in this unit. Each prints a
#: clear message and exits non-zero so callers cannot mistake a stub for a no-op.
_STUBS: dict[str, str] = {
    "exclusions": "manage exclusion lists",
    "fetch": "run a source fetch",
    "ingest": "ingest fetched items into the vault",
    "guard": "run the fail-closed output guard",
}

_AUTH_PROVIDERS = ("google", "graph", "telegram")


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

    for name, help_text in _STUBS.items():
        stub = sub.add_parser(name, help=f"{help_text} (stub)")
        stub.set_defaults(func=cmd_stub, command=name)

    return parser


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
