"""``mclaw`` command-line entry point.

Stdlib ``argparse`` only — the tooling layer keeps its runtime dependency set
empty (the framework deny-list test guards this). Most subcommands are stubs at
this stage of the build; ``secret`` is functional and ``auth`` is a deliberate
stub tracked in DEV-31.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__, paths, secret

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
    """Report the resolved profile and whether config/state roots exist.

    Minimal for this unit: full validation and ``--init`` arrive in DEV-13. It
    must run and print a sensible report even when the roots are missing (they
    will be at this stage).
    """
    profile = paths.resolve_profile()
    config = paths.config_root(profile)
    state = paths.state_root(profile)

    print(f"mclaw doctor — profile: {profile}")
    all_ok = True
    for label, path in (("config", config), ("state", state)):
        exists = path.is_dir()
        all_ok = all_ok and exists
        status = "ok" if exists else "MISSING"
        print(f"  {label:<6} {path}  [{status}]")
    if not all_ok:
        print(
            "\nOne or more roots are missing. This is expected before "
            "`mclaw doctor --init` (DEV-13) has created them.",
        )
    return 0


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

    doctor = sub.add_parser("doctor", help="report profile and config/state paths")
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
