# Secrets: macOS Keychain conventions

How this repo stores and retrieves secrets. This is the how-to companion to
`specs/MARK-CLAW-TOOLS.md` §7.3 (design rationale); if the two ever disagree,
§7.3 wins and this file gets fixed in the same PR.

All secrets live in the macOS **login keychain**, accessed via the built-in
`security` CLI — no install, no third-party secrets manager. This repo holds
no personal data, so every example below uses generic placeholders
(`<profile>`, `<item>`, `<field>`) rather than real account names.

## Naming convention

- **Service** is fixed per profile: `mark-claw-<profile>`. The profile
  defaults to `mark` (`MC_PROFILE`, per `CLAUDE.md`'s config/state layout), so
  the default service is `mark-claw-mark`.
- **Account** is a flattened `<item>-<field>` slug — one keychain item per
  secret *value*. If a credential has multiple parts (e.g. a client ID and a
  client secret, or an API ID and an API hash), each part gets its own item
  under the same service, with the field name as the account suffix.
- One keychain item per secret value. Don't pack multiple secrets into one
  item's password field (e.g. as JSON) — it defeats per-secret rotation and
  makes `security` lookups ambiguous.

Config files (`accounts.yaml`, `sources.yaml`, etc., under
`~/.config/mark-claw/<profile>/`) never hold raw secret values. They hold a
`keychain://` reference of the form `keychain://<service>/<account>`, which a
wrapper resolves at runtime via `security find-generic-password`.

## Creating an item

Items are created with `-A` (always-allow), so a headless launchd invocation
can read the item without ever hitting a GUI access-control prompt. **Don't
pass the secret value as a `-w` argument** — even via a shell variable, it's
still visible in the process list (`ps`) for the life of the command, not
just shell history. Instead put `-w` **last with no value following it**;
`security` then prompts twice (entry + confirmation) without echoing, per
its own usage text ("Use of the -p or -w options is insecure. Specify -w as
the last option to be prompted."):

```sh
security add-generic-password -a <item>-<field> -s mark-claw-<profile> -A -w
# password data for new item: <type the secret, not echoed>
# retype password for new item: <type it again>
```

- `-a` — account (the flattened `<item>-<field>` slug)
- `-s` — service (`mark-claw-<profile>`)
- `-w` — the secret value
- `-A` — always-allow access for any application (required for unattended
  launchd runs; without it, the first `security find-generic-password` call
  after each reboot/relogin blocks on a GUI prompt)

Items are created in the default login keychain, which is already unlocked
for the login session — no separate unlock step is needed for launchd agents
running as the logged-in user. Items created via the classic `security` CLI
are **local to this keychain, not synced via iCloud Keychain** — the
`security` command doesn't set the `kSecAttrSynchronizable` attribute iCloud
sync requires. Cross-device recovery is what the age-encrypted backup below
is for, not iCloud sync.

## Reading and deleting an item

```sh
security find-generic-password -a <item>-<field> -s mark-claw-<profile> -w
security delete-generic-password -a <item>-<field> -s mark-claw-<profile>
```

`find-generic-password -w` prints only the secret value to stdout, suitable
for capturing into a variable in a wrapper script.

## Stage A: raw `security` commands, then `mc secret`

Stage A's initial credential provisioning (Google OAuth, Entra, Slack,
Mattermost, Telegram, etc.) runs these `security` commands directly — there
is no CLI yet to wrap them. Once the `mc` CLI skeleton lands (DEV-12 / B1),
`mc secret set` / `mc secret get` / `mc secret list` will wrap this same
service/account convention (enforcing the `-A` flag and the naming scheme
consistently) and later steps will switch to it. Do not invent `mc secret`
syntax before that CLI exists — for now, use raw `security` commands as shown
above.

## Backup and recovery

The macOS Keychain has no cross-device recovery kit of its own. The plan is
for `mc secret export` (arriving with `mc secret set/get/list` in B1 /
DEV-12) to write an age-encrypted blob of the full credential set to:

```text
~/.local/state/mark-claw/<profile>/secrets/backup.age
```

refreshed after each provisioning or rotation. The age passphrase/recipient
key is held by Mark and is **never written to disk** anywhere in this
system — not in config, not in state, not in this repo.

This step (A1) only reserves that state path: it seeds an empty
`backup.age` file and creates its parent `secrets/` directory at
permission `0700`. It does not implement the export itself; that arrives
with B1.

## Verify your setup

Smoke-test the round trip without leaving a permanent item behind:

```sh
security add-generic-password -a smoke-test -s mark-claw-mark -w x -A     # create, always-allow
security find-generic-password -a smoke-test -s mark-claw-mark -w        # → x
security delete-generic-password -a smoke-test -s mark-claw-mark          # clean up
```

Expected: the second command prints `x`; after the third command, a repeat
`find-generic-password` for the same account/service fails
(`SecKeychainSearchCopyNext: The specified item could not be found in the
keychain.`), confirming cleanup.
