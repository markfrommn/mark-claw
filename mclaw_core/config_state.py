"""Idempotent init for the config and state trees (design §6.1, §6.2).

``init_config_tree`` writes skeleton config files (only when absent — config is
human-authored and init is **non-destructive**). ``init_state_tree`` creates the
state directory layout and enforces ``0700`` on the fail-closed guarantee dirs
(``secrets/``, ``quarantine/``, ``spool/ephemeral/``) per design §2.2/§5.3/§5.4.

Both are idempotent: a re-run fixes/creates missing pieces and reasserts state
directory permissions without touching operator-authored config content. The
skeletons are empty-but-valid so a freshly init'd profile parses cleanly; real
content is operator-authored in later units.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import paths

# --- state directory layout (design §6.2) ----------------------------------

#: Every state directory, relative to the profile state root. ``spool/ephemeral``
#: is nested under ``spool/`` and listed after it so ``parents=True`` creation
#: stays readable.
STATE_DIRS: tuple[str, ...] = (
    "cursors",
    "spool",
    "spool/ephemeral",
    "runs",
    "contacts",
    "changelog",
    "quarantine",
    "secrets",
    "locks",
    "logs",
)

#: Directories that must be ``0700`` — the fail-closed hard-guarantee dirs
#: (token caches, quarantined artifacts, ephemeral-tier content). All other
#: state dirs default to ``0755``.
SECURE_DIRS: frozenset[str] = frozenset({"secrets", "quarantine", "spool/ephemeral"})

#: Required mode for the fail-closed dirs (design §2.2, §5.3, §5.4).
SECURE_DIR_MODE = 0o700

#: Default mode for non-secure state dirs.
DEFAULT_DIR_MODE = 0o755

# --- config skeletons (design §6.1) ----------------------------------------

_SETTINGS_YAML = """\
# mark-claw settings — profile config (design §6.1).
# `mclaw doctor --init` writes this skeleton ONCE when the file is absent and
# NEVER overwrites it. Edit by hand; real values are operator-supplied.

# Obsidian vault root. Leave empty until you set the real absolute path
# (C1 / DEV-18 creates the vault). `mclaw doctor` reports:
#   - FAIL  when the path is unset or non-absolute,
#   - WARN  when absolute but the directory does not yet exist (C1 not done),
#   - ok    once the directory exists.
vault:
  path: ""
  # path: /Users/you/Documents/Obsidian/YourVault

# IANA timezone name (e.g. "America/Chicago"); empty = system local.
timezone: ""

profile:
  display_name: ""

runtime:
  billing: subscription   # subscription | api (design §2.3)
  models:
    triage-judge: ""      # optional model override for the gray-zone judge

limits:
  max_backfill_pages: 5
  alert_rate_cap: 5       # max urgent alerts per sweep
  relabel_window_days: 14 # relabel-detection lookback
"""

_ACCOUNTS_YAML = """\
# mark-claw account definitions (design §6.1).
# `mclaw doctor --init` writes this skeleton ONCE when the file is absent and
# NEVER overwrites it.
#
# Entry shape:
#   email:
#     - id: work                     # short stable id
#       address: you@example.com
#       provider: gmail              # gmail | graph
#       auth:
#         client_ref: "keychain://mark-claw-<profile>/<item>"
#         token_cache: "$STATE/secrets/<provider>/<account>/"
#       roles: [mail]                # mail | calendar | onedrive
#   own_addresses: [you@example.com] # recipients allowed for mclaw-send-self

email: []
own_addresses: []
"""

_SOURCES_YAML = """\
# mark-claw chat + activity sources (design §6.1).
# `mclaw doctor --init` writes this skeleton ONCE when the file is absent and
# NEVER overwrites it.
#
# Entry shapes (commented — fill in per source):
#   chat:
#     slack-work:
#       token_ref: "keychain://mark-claw-<profile>/slack-xoxp"
#       channels: []                 # optional; empty = auto-discover
#     mattermost-work:
#       url: "https://mm.your-domain.example"
#       token_ref: "keychain://mark-claw-<profile>/mm-pat"
#     telegram:
#       api_id_ref: "keychain://mark-claw-<profile>/tg-api_id"
#       api_hash_ref: "keychain://mark-claw-<profile>/tg-api_hash"
#       session: "$STATE/secrets/telegram/session.string"
#     signal:
#       data_dir: "$STATE/secrets/signal-cli/"
#   activity:
#     github:    {token_ref: "keychain://mark-claw-<profile>/github-pat"}
#     figma:     {token_ref: "keychain://mark-claw-<profile>/figma-pat"}
#     zoom:      {account_id: "...", client_ref: "keychain://..."}
#     meet:      {transcripts_folder: "drive:/<folder-id>"}
#     local:     {}   # scan_roots come from local-whitelist.yaml, NOT here.

chat: {}
activity: {}
"""

_EXCLUSIONS_YAML = """\
# mark-claw blocked / ephemeral exclusions (design §5, §6.1).
# `mclaw doctor --init` writes this skeleton ONCE when the file is absent and
# NEVER overwrites it. The exclusion gate (B3 / DEV-14) compiles this file.
#
# Entry shapes:
#   chat:
#     <source-id>:
#       - {id: "C0EXAMPLE", name: "#private", tier: blocked, also_match: ["alias"]}
#       - {id: "D0EXAMPLE", name: "DM: Someone", tier: ephemeral}
#   drive:
#     <account-id>:
#       - {path: "/HR/", tier: blocked}   # prefix; inherits to subfolders
#   meetings:
#     - {series_id: "abc123", title: "Private 1:1", tier: blocked}

chat: {}
drive: {}
meetings: []
"""

_LOCAL_WHITELIST_YAML = """\
# mark-claw local scan whitelist (design §5.2, §6.1).
# This is the ONLY source of local scan paths — no code path accepts a root arg.
# `mclaw doctor --init` writes this skeleton ONCE when the file is absent and
# NEVER overwrites it.
#
# Entry shape:
#   scan_roots:
#     - /Users/you/Projects/your-repo

scan_roots: []
"""

_HYGIENE_PATTERNS_TXT = """\
# mark-claw hygiene patterns — one Python regex per line.
# Lines starting with '#' are comments; blank lines are ignored. Each regex is
# matched (re.search) against every tracked repo file's relative path AND text
# content, outside the exempt set (see tests/hygiene/test_hygiene.py).
#
# Populate with your personal-identifier patterns (email, domains, real channel
# or user IDs, etc.). Until at least one ACTIVE pattern is present, the hygiene
# test skips. The guarantee activates the moment you add one.
#
# Placeholder examples (commented — these match nothing real):
# ^you@example\\.com$
# ^.*your-domain\\.example
# ^.*C0EXAMPLE
"""

#: Mapping of config filename → skeleton content. Iteration order is stable so
#: the init/doctor output reads top-down. All skeletons are empty-but-valid.
CONFIG_SKELETONS: dict[str, str] = {
    "settings.yaml": _SETTINGS_YAML,
    "accounts.yaml": _ACCOUNTS_YAML,
    "sources.yaml": _SOURCES_YAML,
    "exclusions.yaml": _EXCLUSIONS_YAML,
    "local-whitelist.yaml": _LOCAL_WHITELIST_YAML,
    "hygiene-patterns.txt": _HYGIENE_PATTERNS_TXT,
}

#: Config files that must parse as YAML (``hygiene-patterns.txt`` is free text).
YAML_CONFIG_FILES: tuple[str, ...] = tuple(
    name for name in CONFIG_SKELETONS if name.endswith(".yaml")
)


@dataclass(frozen=True)
class InitResult:
    """Outcome of one init pass.

    ``created``  — paths newly written (config files) or newly created and
    mode-corrected (state dirs).
    ``reused``   — paths that already existed in the required state (no change).
    ``fixed_perms`` — existing state dirs whose mode was just corrected.
    """

    created: list[Path]
    reused: list[Path]
    fixed_perms: list[Path]


class StateInitError(Exception):
    """Raised when state init refuses to trust an unsafe path.

    A symlink placed at one of the fail-closed secure dirs (``secrets``,
    ``quarantine``, ``spool/ephemeral``) would be followed by ``is_dir`` /
    ``mkdir(exist_ok=True)`` / ``stat`` / ``chmod``, so init would treat the
    link's target as a valid secure dir, apply ``chmod(0700)`` to an arbitrary
    directory outside the state tree, and let future secret/quarantine/ephemeral
    writes follow the link. Init fails closed instead.
    """


def init_config_tree(*, profile: str) -> InitResult:
    """Create skeleton config files under the profile config root.

    Non-destructive: a file that already exists is **never** overwritten —
    config is human-authored. Creation is atomic via exclusive-open (mode
    ``"x"``): the prior check-then-write (``exists()`` → ``write_text``, which
    opens in truncating ``"w"`` mode) was a TOCTOU race that could clobber a
    file appearing between the check and the write. With ``"x"`` the open
    itself fails atomically if the path already exists, and the existing bytes
    are left untouched. Parent directories are created as needed.
    """
    root = paths.config_root(profile)
    created: list[Path] = []
    reused: list[Path] = []
    for name, content in CONFIG_SKELETONS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x") as f:
                f.write(content)
        except FileExistsError:
            reused.append(path)
            continue
        created.append(path)
    return InitResult(created=created, reused=reused, fixed_perms=[])


def init_state_tree(*, profile: str) -> InitResult:
    """Create state directories and enforce the required mode on each.

    Idempotent: missing dirs are created; existing dirs are re-``chmod``'d to
    the required mode (0700 for secure dirs, 0755 otherwise). ``mkdir``'s own
    ``mode`` argument is reduced by the process umask, so the explicit ``chmod``
    after creation is what actually guarantees the bits — a widened secure dir
    is corrected back to 0700 on the next init.

    Fail-closed guarantee: a symlink at one of :data:`SECURE_DIRS` is refused
    with :class:`StateInitError` rather than followed. ``is_dir`` /
    ``mkdir(exist_ok=True)`` / ``stat`` / ``chmod`` all resolve a symlink, so a
    link at ``secrets``/``quarantine``/``spool/ephemeral`` would otherwise let
    init ``chmod(0700)`` the link's target (an arbitrary directory outside the
    state tree) and let future writes follow it. Non-secure dirs are unaffected
    (out of scope).
    """
    root = paths.state_root(profile)
    created: list[Path] = []
    reused: list[Path] = []
    fixed_perms: list[Path] = []
    for rel in STATE_DIRS:
        path = root / rel
        target_mode = SECURE_DIR_MODE if rel in SECURE_DIRS else DEFAULT_DIR_MODE
        if rel in SECURE_DIRS and path.is_symlink():
            raise StateInitError(
                f"refusing to init {path}: a symlink at a fail-closed secure "
                f"dir ({rel!r}) is not trusted (stat/chmod would follow the "
                f"link and mutate its target)"
            )
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        cur_mode = path.stat().st_mode & 0o777
        if cur_mode != target_mode:
            path.chmod(target_mode)
            if existed:
                fixed_perms.append(path)
            else:
                created.append(path)
        elif existed:
            reused.append(path)
        else:
            created.append(path)
    return InitResult(created=created, reused=reused, fixed_perms=fixed_perms)
