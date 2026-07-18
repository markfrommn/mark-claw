# MARK-CLAW — Phase 1 Implementation Plan: Foundations (BASE + UPLOAD)

**Status:** Ready for execution — planned 2026-07-05
**Inputs:** `MARK-CLAW-SPEC.md` §12 Phase 1 · `MARK-CLAW-TOOLS.md` (ground truth for access paths) · `MARK-CLAW-DESIGN.md` §12 phase mapping (Phase 1 slice)
**Executor contract:** steps execute in **dependency order, not stage order** — see **[§ Dependencies & sequencing](#dependencies--sequencing)** for the DAG, the two cross-chain couplings, and the `blockedBy` map mirrored in Linear. Stage layout is a grouping, not a schedule: some Stage-A steps depend on the tooling chain (A2/A3/A6 need B1+B2) and part of the tooling chain depends on Stage A (B6 needs A2–A7). Independent steps within a wave may run in parallel worktrees; one `/phase` session drives exactly one. Mark each step done in this file; record deviations inline. Append a status note at the bottom when the phase completes — the Phase 2 planning session reads it.

## Decisions made in this planning session (interview 2026-07-05)

| Question | Decision |
|---|---|
| Google API access path | **Direct `google-api-python-client` with our own Desktop-app OAuth client** — not gog/gws CLI. Strongest structural deny-list (the HTTP layer rejects send/delete URIs); no dependency on a third-party CLI's client. gog/gws remain manual-inspection tools. Deviation from tools §1 "recommended primary" is deliberate and recorded here; tools §11 revisit-trigger logic already anticipated it. |
| Email mining depth | **Metadata full history (all 4 accounts) + full bodies last 24 months.** Complete contact graph for cold-call detection; bounded token/runtime cost. |
| Drive/OneDrive key docs | **Propose-then-approve**: metadata-only sweep → candidate report → Mark approves → content fetch of approved items only. |
| Linear tracking | Phase-level parent issues (1–4) + one sub-issue per Phase 1 step, under project `ai-personal-assistant`. IDs recorded per step below. |
| Phase-1 Google scopes | **`gmail.readonly` + `drive.readonly` only.** Bulk ingest never holds a write-capable Google token. `gmail.modify` re-consent happens in Phase 2 when `mclaw-mail-act` exists. |

## Phase 1 scope (from design §12)

Config/state/tooling skeletons with `mark` profile · vault + `VAULT-GUIDE.md` + Obsidian Sync · **exclusion gate + output guard + canary suite before any ingestion** · `op` secret wiring · bulk ingest (email mining, chat backfill, life-story, local sweep, Drive/OneDrive key docs).

**Explicitly deferred (do not build):** status dashboard (Phase 2 per design §12 — Phase 1 only writes the state files it will render), notify layer / Telegram bot (Phase 2; guard trips surface via `mclaw doctor` + log until then), any mail-write capability (labels/archive/drafts = Phase 2), launchd scheduling (nothing in Phase 1 recurs; `mclaw install-schedules` = Phase 2), rules engine (Phase 2), triage of any kind.

**Hard guarantees touched this phase:** exclusion enforcement (fully built + tested here), no-delete / no-send (readonly scopes + structural deny-list), read-only chat (structural), no autonomous recording (nothing schedulable exists this phase), no personal data in repo (hygiene test). Verification is structural per tools §12.3 — see §V below.

---

## Dependencies & sequencing

The 24 Phase-1 step-issues do **not** execute in stage order. They form a DAG with **two cross-chain couplings** that the Stage-A / Stage-B grouping hides:

1. **Tooling → credentials.** The token-minting halves of **A2 / A3 / A6** (Google, Graph, Telegram) require *working* `mclaw auth {google,graph,telegram}` helpers from **B1** plus account config from **B2**. Only the console/registration halves of those steps run independently. By contrast **A4, A5, A7** (Slack, Mattermost, Signal) have **no tooling dependency** — like A1 they use raw `security` / `curl` / `signal-cli`, so they can be done immediately, before any tooling exists (A7 is day-1 urgent: Signal history is forward-only from link time).
2. **Credentials → tooling.** **B6** (author the real exclusions) cannot be written until every source credential exists — you enumerate the channel / folder / contact IDs you are excluding *through* A2–A7 — and B6 in turn gates all of Stage E.

The critical path runs through the tooling chain — **B1 → B3/B4 → B5 → D1 → D2/D3/D4 → E\*** — so **B1/DEV-12 is the single highest-leverage unblock**. These edges are mirrored in Linear as `blockedBy` relations.

### Cross-chain coupling (the part stage order hides)

```mermaid
graph LR
  subgraph AC["Credential chain — human / console"]
    A2[A2·DEV-6 Google]
    A3[A3·DEV-7 Graph]
    A4[A4·DEV-8 Slack — free]
    A5[A5·DEV-9 Mattermost — free]
    A6[A6·DEV-10 Telegram]
    A7[A7·DEV-11 Signal — free, day-1]
  end
  subgraph TC["Tooling chain — repo PRs"]
    B1[B1·DEV-12 CLI + auth helpers]
    B2[B2·DEV-13 config/state]
    B3[B3·DEV-14 exclusion gate]
    B6[B6·DEV-17 exclusions]
  end
  B1 --> B2
  B1 --> B3
  B1 --> A2
  B1 --> A3
  B1 --> A6
  B2 --> A2
  B2 --> A3
  B2 --> A6
  A2 --> B6
  A3 --> B6
  A4 --> B6
  A5 --> B6
  A6 --> B6
  A7 --> B6
  B2 --> B6
  B3 --> B6
```

### Full dependency DAG

```mermaid
graph LR
  A1[A1·DEV-5 Keychain ✅]
  A4[A4·DEV-8 Slack]
  A5[A5·DEV-9 Mattermost]
  A7[A7·DEV-11 Signal ⏰]
  B1[B1·DEV-12 skeleton + CLI + auth]
  B1 --> B2[B2·DEV-13 config/state]
  B1 --> B3[B3·DEV-14 exclusion gate]
  B1 --> B4[B4·DEV-15 output guard]
  B3 --> B5[B5·DEV-16 canary]
  B4 --> B5
  B1 --> A2[A2·DEV-6 Google]
  B2 --> A2
  B1 --> A3[A3·DEV-7 Graph]
  B2 --> A3
  B1 --> A6[A6·DEV-10 Telegram]
  B2 --> A6
  B2 --> B6[B6·DEV-17 exclusions]
  B3 --> B6
  A2 --> B6
  A3 --> B6
  A4 --> B6
  A5 --> B6
  A6 --> B6
  A7 --> B6
  B1 --> C1[C1·DEV-18 vault]
  B2 --> C1
  B2 --> D1[D1·DEV-19 fetch base]
  B3 --> D1
  B5 --> D1
  D1 --> D2[D2·DEV-20 mail]
  A2 --> D2
  A3 --> D2
  D1 --> D3[D3·DEV-21 chat]
  A4 --> D3
  A5 --> D3
  A6 --> D3
  A7 --> D3
  D1 --> D4[D4·DEV-22 drive/onedrive/local]
  A2 --> D4
  A3 --> D4
  B6 --> D4
  D2 --> E1[E1·DEV-23 email mining]
  B4 --> E1
  C1 --> E1
  B6 --> E1
  D3 --> E2[E2·DEV-24 chat backfill]
  B4 --> E2
  C1 --> E2
  B6 --> E2
  D4 --> E3[E3·DEV-25 drive docs]
  B4 --> E3
  C1 --> E3
  B6 --> E3
  D4 --> E4[E4·DEV-26 local sweep]
  B4 --> E4
  C1 --> E4
  B6 --> E4
  C1 --> E5[E5·DEV-27 life-story]
  B4 --> E5
  E1 --> E5
  E1 --> F1[F1·DEV-28 phase close]
  E2 --> F1
  E3 --> F1
  E4 --> F1
  E5 --> F1
```

### `blockedBy` map (mirrored in Linear)

| Step / issue | blockedBy | Notes |
|---|---|---|
| A1 / DEV-5 ✅ | — | done |
| A2 / DEV-6 | B1, B2 | console half runs earlier; token flow waits on B1 |
| A3 / DEV-7 | B1, B2 | |
| A4 / DEV-8 | — | **independent — startable now** |
| A5 / DEV-9 | — | **independent — startable now** |
| A6 / DEV-10 | B1, B2 | |
| A7 / DEV-11 | — | **independent — startable now (day-1)** |
| B1 / DEV-12 | — | tooling root; critical path |
| B2 / DEV-13 | B1 | |
| B3 / DEV-14 | B1 | |
| B4 / DEV-15 | B1 | |
| B5 / DEV-16 | B3, B4 | |
| B6 / DEV-17 | B2, B3, A2, A3, A4, A5, A6, A7 | needs all source IDs + gate; gates Stage E |
| C1 / DEV-18 | B1, B2 | |
| D1 / DEV-19 | B2, B3, B5 | canary green before writer code lands |
| D2 / DEV-20 | D1, A2, A3 | |
| D3 / DEV-21 | D1, A4, A5, A6, A7 | |
| D4 / DEV-22 | D1, A2, A3, B6 | |
| E1 / DEV-23 | D2, B4, C1, B6 | |
| E2 / DEV-24 | D3, B4, C1, B6 | |
| E3 / DEV-25 | D4, B4, C1, B6 | |
| E4 / DEV-26 | D4, B4, C1, B6 | |
| E5 / DEV-27 | C1, B4, E1 | primed by E1 seeds |
| F1 / DEV-28 | E1, E2, E3, E4, E5 | |

### Recommended execution waves (topological order)

- **Wave 0 — startable now, in parallel:** B1/DEV-12 · A4/DEV-8 · A5/DEV-9 · A7/DEV-11  *(A1/DEV-5 done)*
- **Wave 1:** B2/DEV-13 · B3/DEV-14 · B4/DEV-15
- **Wave 2:** A2/DEV-6 · A3/DEV-7 · A6/DEV-10 · B5/DEV-16 · C1/DEV-18
- **Wave 3:** B6/DEV-17 · D1/DEV-19
- **Wave 4:** D2/DEV-20 · D3/DEV-21 · D4/DEV-22
- **Wave 5:** E1/DEV-23 · E2/DEV-24 · E3/DEV-25 · E4/DEV-26
- **Wave 6:** E5/DEV-27
- **Wave 7:** F1/DEV-28

---

## Stage A — Credential & account setup (blocks everything; start immediately)

All self-service (tools §0 — no third-party approvals). Console work proceeds at once; token flows depend on the `mclaw auth` helpers from B1.

### A1 — macOS Keychain conventions + backup
**Lands:** macOS login keychain (secrets) · repo (`docs/SECRETS.md` naming conventions — generic, no personal values) · config (`keychain://` refs appear in `accounts.yaml`/`sources.yaml` as later steps add them) · state (`secrets/backup.age` seeded empty).
**Do:** document item naming (service `mark-claw-mark`, account = flattened `<item>-<field>` slug, one keychain item per secret value); confirm `security` CLI reachable (built-in, no install). Stage A steps use raw `security` commands directly — `mclaw secret set/get/list` (B1) wraps this convention once the CLI skeleton exists. Establish the `mclaw secret export` → age-encrypted backup convention now (passphrase held by Mark, never written to disk); B1 implements the command, this step just reserves the state path and the passphrase-custody decision.
**Accept:**
```
security add-generic-password -a smoke-test -s mark-claw-mark -w x -A     # create, always-allow
security find-generic-password -a smoke-test -s mark-claw-mark -w        # → x
security delete-generic-password -a smoke-test -s mark-claw-mark          # clean up
```
- [x] Done · Linear: [DEV-5](https://linear.app/psols/issue/DEV-5)

### A2 — Google OAuth client + tokens for 3 Gmail accounts + work Drive (read-only scopes)
**Lands:** GCP console (own project) · Keychain (`mark-claw-mark`/`google-oauth-client-credential`) · state (`secrets/google/<account-id>/token.json`, 0600) · config (`accounts.yaml` entries with `client_ref` + `token_cache`). Nothing in repo.
**Do:** new GCP project (under `mark@powderhorns.biz`); consent screen External, **publish to Production** (kills the 7-day Testing refresh-token expiry — tools §1); Desktop-app OAuth client; enable Gmail + Drive APIs. Scopes requested: `gmail.readonly`, `drive.readonly` (restricted scopes; personal-use/unverified exemption applies — proceed through the "unverified app" screen; for the two Workspace domains optionally mark the client trusted via admin console / `consoleops@`). Run `uv run mclaw auth google <id>` for `convoydefense`, `powderhorns`, `gmail-personal`.
**Accept (per account):**
```
uv run mclaw auth google powderhorns            # browser flow → token file written
stat -f "%Lp" ~/.local/state/mark-claw/mark/secrets/google/powderhorns/token.json   # 600
uv run mclaw auth google powderhorns --self-test
#   → prints profile emailAddress + total message count via users.getProfile / labels.list
```
Also: consent screen status shows **In production**; token file contains no scope beyond readonly (`grep -c modify token.json` → 0).
- [ ] Done · Linear: [DEV-6](https://linear.app/psols/issue/DEV-6)

### A3 — Entra app registration (jumpweb.net) + Graph token
**Lands:** Entra portal · Keychain (`mark-claw-mark`/`entra-app-client_id`, `mark-claw-mark`/`entra-app-tenant_id`) · state (`secrets/msal/jumpweb/`) · config (`accounts.yaml` graph entry).
**Do:** app registration in jumpweb tenant; public client, "Allow public client flows" on; delegated scopes `Mail.Read`, `Files.Read`, `Calendars.Read`, `offline_access` (calendar added now to avoid Phase-3 re-consent; Phase 1 code never calls calendar). Self-grant admin consent. Device-code flow via `uv run mclaw auth graph jumpweb`.
**Accept:**
```
uv run mclaw auth graph jumpweb --self-test
#   → GET /me (UPN = mark@jumpweb.net); GET /me/messages?$top=1 → 200;
#     GET /me/drive/root → 200. Token cache present, 0600.
```
- [ ] Done · Linear: [DEV-7](https://linear.app/psols/issue/DEV-7)

### A4 — Slack internal app + user token
**Lands:** Slack app config · Keychain (`mark-claw-mark`/`slack-xoxp-credential`).
**Do:** create internal app in the work workspace; user-token scopes exactly `channels:history, groups:history, im:history, mpim:history, channels:read, users:read` — **no `chat:write`, no `*.write`**. Install to workspace.
**Accept:**
```
tok=$(security find-generic-password -a slack-xoxp-credential -s mark-claw-mark -w)
curl -s -H "Authorization: Bearer $tok" https://slack.com/api/auth.test | jq .ok   # true
# response header x-oauth-scopes lists only the 6 read scopes (no write scope present)
curl -s -H "Authorization: Bearer $tok" "https://slack.com/api/conversations.list?limit=1" | jq .ok  # true
```
- [ ] Done · Linear: [DEV-8](https://linear.app/psols/issue/DEV-8)

### A5 — Mattermost PAT + edition check
**Lands:** MM system console · Keychain (`mark-claw-mark`/`mattermost-pat`) · this plan (edition recorded).
**Do:** enable `EnableUserAccessTokens`; issue PAT for Mark's account. **Confirm server edition** — if free "Entry" tier (v11+), history beyond most-recent 10k messages is hidden → backfill becomes urgent (run E2 for Mattermost first).
**Accept:**
```
curl -s -H "Authorization: Bearer $MM_PAT" https://<server>/api/v4/users/me | jq .username
curl -s -H "Authorization: Bearer $MM_PAT" https://<server>/api/v4/license   # record edition ↓
```
Edition recorded here: ______ · 10k-cap applies: yes/no
- [ ] Done · Linear: [DEV-9](https://linear.app/psols/issue/DEV-9)

### A6 — Telegram api_id/hash + Telethon session
**Lands:** Keychain (`mark-claw-mark`/`telegram-api-api_id`, `mark-claw-mark`/`telegram-api-api_hash`) · state (`secrets/telegram/session`, 0600).
**Do:** my.telegram.org → api_id/api_hash; one interactive `uv run mclaw auth telegram` login (code via app) → persistent StringSession. Real device info in session metadata; never re-login repeatedly (tools §3.3).
**Accept:**
```
uv run mclaw auth telegram --self-test    # prints dialog count via get_dialogs, no login prompt on 2nd run
```
- [ ] Done · Linear: [DEV-10](https://linear.app/psols/issue/DEV-10)

### A7 — signal-cli install + link (DAY 1 — history accumulates only from link time)
**Lands:** brew (tool) · state (`secrets/signal/` data dir, 0700).
**Do:** `brew install signal-cli`; `signal-cli --config <state>/secrets/signal link -n mark-claw` → scan QR from phone. Check whether Signal Desktop is linked on this Mac — if yes, note its DB as the partial-backfill source for E2. Add `brew upgrade signal-cli` to the (Phase 2) maintenance list — noted for the Phase 2 plan.
**Accept:**
```
signal-cli --config <state>/secrets/signal receive --timeout 5   # exit 0
# send yourself a test message from the phone; re-run receive → message appears
stat -f "%Lp" <state>/secrets/signal    # 700
```
- [ ] Done · Linear: [DEV-11](https://linear.app/psols/issue/DEV-11)

---

## Stage B — Skeletons + hard-guarantee machinery (ALL before any ingestion)

### B1 — Tooling repo skeleton + `mclaw` CLI + repo-hygiene test
**Lands:** repo only (`pyproject.toml` (uv), `mclaw_core/`, `bin/`, `prompts/`, `tests/`, `docs/`).
**Do:** uv-managed Python project; `mclaw` CLI entry point. Most subcommands are stubbed this step (`doctor`, `exclusions`, `fetch`, `ingest`, `guard`), but two families ship **functional** because Stage A depends on them (see [§ Dependencies & sequencing](#dependencies--sequencing)): (1) `auth google|graph|telegram` — the real interactive OAuth / device-code / Telethon login flows that A2/A3/A6 invoke to mint token caches; (2) `secret` — `set`/`get`/`list`/`export`, wrapping `security add-generic-password`/`find-generic-password`/`dump-keychain` + age encryption for `export`. *(If `auth` is large enough to warrant its own PR, carve it into a follow-on issue and note the split here — but it is a B1-chain deliverable, not a Stage-A one, and A2/A3/A6 are `blockedBy` it.)* `MCLAW_PROFILE` env (default `mark`) resolves config/state roots — **no personal value hardcoded anywhere**. Hygiene test `tests/hygiene/`: greps the entire repo for personal-identifier patterns; the pattern list itself is personal, so the test reads patterns from `$CONFIG/hygiene-patterns.txt` (config layer) and **skips with a loud warning** if absent (so the repo stays cloneable).
**Dependency guard:** `pyproject.toml` contains no agent-framework packages (tools §12); test asserts the dependency list against a deny-list (`openclaw*, zeroclaw*, ironclaw*, picoclaw*, nemoclaw*, hermes*, vellum*`).
**Accept:**
```
uv run mclaw doctor            # runs, reports missing config/state (expected at this point)
uv run pytest tests/hygiene # green (patterns file present) — finds 0 personal identifiers in repo
git ls-files | xargs grep -l "powderhorns\|jumpweb\|convoydefense\|markfrommn" | grep -v specs/  # empty
```
*(specs/ is exempt — it predates the split and is Mark's own repo; flag if he wants specs scrubbed too.)*

**Deviation (DEV-12, 2026-07-18):** the `auth {google,graph,telegram}` helpers were **carved out to a follow-on issue, [DEV-31](https://linear.app/psols/issue/DEV-31)** (P1-B1b), per the sanctioned split above — `auth` ships as a stub here. Rationale: the three flows cannot be completed or integration-tested until the config schema (B2/DEV-13) and the provider console halves (A2/A3/A6) exist, and nothing on the tooling critical path needs them. DEV-31 is `blockedBy` DEV-12 + DEV-13 and `blocks` DEV-6/7/10 (re-pointed off DEV-12's direct blocker). Two smaller deviations: the hygiene guard exempts the cwft-managed scaffolding as well as `specs/` (see `specs/STACK-CLEANUP-NOTES.md` → "Personal identifiers rendered into generated surfaces"), and the `mc` → `mclaw` rename is reflected throughout.
- [x] Done · Linear: [DEV-12](https://linear.app/psols/issue/DEV-12)

### B2 — Config + state trees, `mark` profile
**Lands:** config (`~/.config/mark-claw/mark/`: `settings.yaml`, `accounts.yaml`, `sources.yaml`, skeleton `exclusions.yaml`, `local-whitelist.yaml`, `hygiene-patterns.txt`) · state (`~/.local/state/mark-claw/mark/`: `cursors/ spool/ runs/ contacts/ changelog/ quarantine/ secrets/ locks/ logs/` — `secrets/`, `quarantine/`, `spool/ephemeral/` 0700) · repo (`mclaw doctor` validation logic + example config files under `docs/examples/` with placeholder values only).
**Do:** `mclaw doctor --init` creates both trees idempotently; validates YAML schemas, dir perms, Keychain reachability (the credential backend — design §6.4; the earlier `op` wording here predates the 1Password→macOS-Keychain migration, DEV-5), vault path.
**Accept:**
```shell
uv run mclaw doctor    # all green checklist: dirs, perms (700 on secrets/), yaml parse, keychain ok
stat -f "%Lp" ~/.local/state/mark-claw/mark/secrets   # 700
rm -rf ~/.local/state/mark-claw/mark && uv run mclaw doctor --init && uv run mclaw doctor  # rebuilds green (`--init` exits 0 on scaffolding success; bare `doctor` is fully green once the vault path is set — C1/DEV-18 creates the vault, blocked-by this step)
```
- [x] Done · Linear: [DEV-13](https://linear.app/psols/issue/DEV-13)

**Deviation (DEV-13, 2026-07-18):** four recorded decisions, none weakening a hard rule.
1. **PyYAML is now the first runtime dependency.** Python ships no stdlib YAML and `doctor` must parse/validate the YAML config files; B3/DEV-14 (exclusion gate, a Wave-1 sibling) will also parse `exclusions.yaml`/`local-whitelist.yaml`, so the call is made once here to avoid a parallel-wave merge conflict. `pyyaml>=6.0` is added to `[project.dependencies]`; the former `test_runtime_dependencies_are_empty` is converted to `test_runtime_dependencies_are_allowlisted` (allowlist `{pyyaml}`) so the audited-minimal-runtime-surface property it guarded survives — a smuggled non-allowlisted dep still fails, and agent frameworks are still caught by the unchanged deny-glob test. The "stdlib-only" comment in `pyproject.toml` is updated to "stdlib + PyYAML." mark-claw is not air-gapped (provider APIs and `claude -p` require network), so the zero-dep property was not load-bearing.
2. **`op` → Keychain.** §B2's "validate `op` sign-in" / "op ok" wording predated the 1Password→macOS-Keychain migration (DEV-5); the doctor implements Keychain reachability via `mclaw_core.secret` (probes the `security` surface, never resolves or prints a value), per design §6.4. The plan text is corrected in this PR.
3. **`mclaw doctor --init` deferred-FAIL semantics.** `mclaw doctor --init` exits 0 when the only failures are deferred (unset vault); nonzero on any non-deferred hard failure. Bare `doctor` keeps nonzero-on-hard-FAIL. The vault-path-unset check is the only deferred check (its unset/absent state is owned by C1/DEV-18); all other FAILs (malformed config, missing `security` binary, secure-dir perm/symlink failure) surface nonzero under `--init` and are not masked by bootstrap success.
4. **`hygiene-patterns.txt` ships as a skeleton.** `--init` writes it with a format header and zero active patterns (patterns are personal and operator-authored). `tests/hygiene/test_hygiene.py` now skips loudly when the file exists but has no active patterns (an init'd-but-unpopulated profile) instead of hard-failing on `assert patterns`; the guarantee activates the moment the operator adds one.

### B3 — Exclusion gate (`mclaw_core/exclusion.py`)
**Lands:** repo (module + unit tests). Config supplies the data (B6).
**Do:** per design §5.2 (normative API): `ExclusionGate.load(config)`; `gate.check(source_id, item_ref) → ALLOW | EPHEMERAL | BLOCKED`; compiled from `exclusions.yaml` + `local-whitelist.yaml`; path-prefix inheritance (Drive/OneDrive), conversation/contact IDs (chat), event/series IDs (meetings), **whitelist inversion for local** (scan roots come only from the whitelist file — no other code path exists). Gate call sits inside the shared `fetch_items()` base (D1) so a wrapper cannot skip it without bypassing the shared path.
**Accept:**
```
uv run pytest tests/unit/test_exclusion.py
# covers: blocked/ephemeral/allow per source type; prefix inheritance to subfolders;
# ID-and-name matching; local whitelist inversion (unlisted root → no scan); config reload
```
- [ ] Done · Linear: [DEV-14](https://linear.app/psols/issue/DEV-14)

### B4 — Output guard (`mclaw_core/output_guard.py`) — fail closed
**Lands:** repo (module + tests). State receives `quarantine/` artifacts + `changelog/` records on trips.
**Do:** per design §5.4: compile deny-pattern set from every blocked/ephemeral entry (IDs, names, aliases, `also_match`, word-boundary, case-insensitive); `guard.scan(artifact) → clean | Trip`; on trip the writer does **not** write — artifact moved to `state/quarantine/` (0700, outside vault/sync), `guard.trip` changelog record appended. Telegram alert on trip is Phase 2 (notify layer doesn't exist yet); until then `mclaw doctor` shows quarantine count ≠ 0 in red. Guard never logs matched content — pattern ID + artifact name only. Every Phase-1 vault writer calls it.
**Accept:**
```
uv run pytest tests/unit/test_output_guard.py
# covers: hit → no write + quarantined + changelog guard.trip; clean → written;
# ephemeral identifiers matched on persistence surfaces; no content in logs
uv run mclaw guard scan-vault   # command exists; on empty vault → "0 findings"
```
- [ ] Done · Linear: [DEV-15](https://linear.app/psols/issue/DEV-15)

**Deviation (DEV-15, 2026-07-18):** four recorded decisions, none weakening a hard rule.
1. **Two matching strategies: token (word-boundary) and substring.** §5.4 says "word-boundary, case-insensitive." Token identifiers (IDs, names, aliases, `also_match`) compile as `(?<!\w)X(?!\w)` — a word-boundary variant that still anchors identifiers containing punctuation (e.g. `#hr-stuff`). Substring identifiers (drive paths AND meeting titles) compile as case-insensitive substrings: a drive path appears in artifact text as a contiguous span (`/HR/payroll`) where `\b` would reject the match, and a meeting title must trip inflected forms exactly as the fetch gate blocks them by containment (`Comp review` → `Comp reviews Q3`; a trailing `s` would defeat `(?!\w)`). Substring is broader → fail-closed (more quarantine, no leak); §5.4 is updated in this PR to spell out the two strategies.
2. **Quarantined artifact files are chmod 0600.** §5.4 specifies the quarantine dir at 0700; the artifact file itself is tightened to 0600 (belt-and-suspenders — content that tripped the guard is sensitive; the 0700 dir already excludes other users).
3. **`mclaw doctor` maps nonzero quarantine to FAIL/red (exit 1).** §5.4 says doctor "reds nonzero quarantine"; the existing doctor vocabulary's "red" is `STATUS_FAIL`, so a pending quarantine contributes to `doctor` exit 1 until cleared via the review loop.
4. **Telegram trip alert is a no-op stub.** §5.4 fires a system-severity Telegram alert on trip; the notify layer is Phase 2, so `OutputGuard._notify_guard_trip` lands as a clearly-marked stub to be wired when notify exists. The fail-closed quarantine + changelog + review-queue path is fully functional without it.

### B5 — Canary suite (CI-blocking)
**Lands:** repo (`tests/canary/` fixture profile + integration test).
**Do:** per design §5.5.2: fixture profile (`MCLAW_PROFILE=canary`, config/state under `tests/canary/tmp/`) whose exclusions block a fixture channel/folder/contact seeded with sentinel tokens `MCX-CANARY-<uuid>`; run every Phase-1 pipeline (fetch base + ingest writers, mocked providers) over fixture data; assert **zero** sentinel occurrences across the whole output tree (spool, vault fixture dir, logs, run records) and ephemeral content exists only transiently. Wire as pre-commit/CI gate: `uv run mclaw test --canary` must pass before any commit touching `mclaw_core/`, `bin/`, or writers.
**Accept:**
```
uv run mclaw test --canary        # green
grep -r "MCX-CANARY" tests/canary/tmp/output/ | wc -l    # 0
# mutate the gate to always-ALLOW in a scratch branch → canary goes red (proves the test bites)
```
- [x] Done · Linear: [DEV-16](https://linear.app/psols/issue/DEV-16)

**Deviation (DEV-16, 2026-07-18):** No real shared fetch base or ingest writers
exist until DEV-19+, so the canary exercises their required enumerate → gate →
fetch → emit contract through bounded mocked providers. GitHub Actions path
protection runs the canary for `mclaw_core/`, `bin/`, and future `writers/`
changes; this foundation repo has no pre-commit framework to wire locally.

### B6 — Author the real exclusions + local whitelist (gate live before first fetch)
**Lands:** config only (`exclusions.yaml`, `local-whitelist.yaml`) — Mark authors, assistant assists with ID lookup (channel IDs via A4/A5 tokens, folder paths, contacts).
**Do:** Mark walks Slack channels / MM channels / TG+Signal contacts / Drive+OneDrive folders / meeting series and lists Blocked + Ephemeral entries per spec §7.2; whitelist the local scan roots. This step **gates Stage E** — no ingest runs until it's done and linted.
**Accept:**
```
uv run mclaw exclusions lint      # schema valid; prints counts per source + tier
uv run python -c "…gate.check('slack-work', '<a-known-blocked-id>')"   # → BLOCKED
uv run mclaw exclusions lint --show-local   # prints exactly the whitelisted roots, nothing else
```
- [ ] Done · Linear: [DEV-17](https://linear.app/psols/issue/DEV-17)

---

## Stage C — Vault

### C1 — Vault tree + VAULT-GUIDE + Obsidian Sync
**Lands:** vault (`~/Documents/Obsidian/Mark-Claw`: `raw/{email,chat,transcripts,docs,ingest}`, `wiki/{people,companies,projects,meetings,briefings,days,insights,trackers,reviews,_meta}`) · config (`settings.yaml → vault.path`) · repo (`mclaw_core/vault.py` writer helpers enforcing naming conventions + frontmatter contract; `wiki/_meta/VAULT-GUIDE.md` content is generated from a repo template with no personal data).
**Do:** create tree per design §3.3; write `VAULT-GUIDE.md` (naming: `YYYY-MM-DD--kebab-slug`, frontmatter keys `source/created/pipeline/refs`, raw-vs-wiki contract, tracker row-id convention); Mark enables Obsidian Sync (manual, his Obsidian account); trackers `tasks.md` / `waiting-on.md` created empty with header rows.
**Accept:**
```
uv run mclaw doctor               # vault path resolves, tree complete
git -C ~/Documents/Obsidian/Mark-Claw status 2>/dev/null; echo ok   # vault is NOT a git repo in the tooling repo
grep -r "Obsidian/Mark-Claw" <repo>/mclaw_core <repo>/bin | wc -l      # 0 — path comes from config
```
Manual: create a note on the Mac → appears on phone via Obsidian Sync within ~1 min; `VAULT-GUIDE.md` readable on phone.
- [ ] Done · Linear: [DEV-18](https://linear.app/psols/issue/DEV-18)

---

## Stage D — Fetch wrappers (backfill mode, read-only by construction)

### D1 — Shared fetch base
**Lands:** repo (`mclaw_core/fetch.py`: `fetch_items()` base — secret resolve via `mclaw secret get` (wraps `security find-generic-password`) inside the wrapper only, exclusion-gate call at enumeration, cursor read/write, spool JSONL writer per design §3.2 envelope, 3× retry with backoff honoring `Retry-After`/FloodWait, run-record write) · state (spool/cursors/runs written at runtime).
**Accept:**
```
uv run pytest tests/unit/test_fetch_base.py
# covers: gate consulted before content fetch (mock provider asserts no content call for BLOCKED);
# cursor advances only on success; spool envelope schema; blocked_skipped counted without identifiers
```
- [ ] Done · Linear: [DEV-19](https://linear.app/psols/issue/DEV-19)

### D2 — Mail wrappers: Gmail ×3 + Graph (read-only)
**Lands:** repo (`bin/mclaw-fetch-gmail`, `bin/mclaw-fetch-graph` + shared google/msal client builders) · state (token caches already from A2/A3; spool + cursors at runtime).
**Do:** Gmail: `messages.list/get`, `labels.list`, profile — **nothing else**. The shared Google client builder wraps the HTTP layer with a **URI deny-list** that raises on any request whose path matches `/send|/trash|/delete|/drafts` and any non-GET/POST-list method — belt over the readonly-scope suspenders. Graph client allows **GET only** in Phase 1 (method allowlist in the client builder). Backfill mode: full-depth metadata query paging + bounded body fetch (E1 policy), checkpoint cursor per account.
**Accept (structural — tools §12.3):**
```
uv run pytest tests/structural/test_mail_readonly.py
# 1) grep/AST scan of repo: zero call sites for send/trash/delete/drafts on any mail client
# 2) deny-list unit test: hand-craft a forbidden request through the client builder → raises
# 3) Graph client: POST/PATCH/DELETE attempt → raises
uv run mclaw fetch gmail --account powderhorns --backfill --limit 50 --dry-run   # lists, fetches nothing
uv run mclaw fetch gmail --account powderhorns --backfill --limit 50             # 50 items in spool, cursor written
```
- [ ] Done · Linear: [DEV-20](https://linear.app/psols/issue/DEV-20)

### D3 — Chat wrappers: Slack, Mattermost, Telegram, Signal (read-only)
**Lands:** repo (`bin/mclaw-fetch-slack|mm|tg|signal`) · state (spool/cursors at runtime; TG session + signal data dir already from A6/A7).
**Do:** Slack: `conversations.list/history/replies`, `users.list` via an **API-method allowlist** in the client (any other method name → raises). MM: `GET /channels/{id}/posts?since=` only; client is GET-only. Telegram: thin Telethon wrapper exposing only `get_dialogs`/`iter_messages` (module does not import send/read-ack functions; wrapper class has no send surface). Signal: subprocess arg-builder for `signal-cli receive` — arg list is constructed from a fixed template; test asserts `send`, `--send-read-receipts` can never appear. Per-conversation cursors; page-capped backfill (`limits.max_backfill_pages`).
**Accept (structural):**
```
uv run pytest tests/structural/test_chat_readonly.py
# per platform: forbidden-method call → raises; grep: no chat.postMessage / posts POST /
# send_message / signal-cli send call sites anywhere in repo
uv run mclaw fetch slack --backfill --conversations 2 --dry-run   # enumerates, honors gate (blocked conv absent)
```
- [ ] Done · Linear: [DEV-21](https://linear.app/psols/issue/DEV-21)

### D4 — Drive + OneDrive + local-sweep wrappers
**Lands:** repo (`bin/mclaw-fetch-drive`, `bin/mclaw-fetch-onedrive`, `bin/mclaw-fetch-local`) · state (cursors: Drive pageToken, Graph deltaLink — captured now so Phase 2+ incremental sync starts from here).
**Do:** Drive: `files.list` metadata + `files.export` (read-only scope from A2); folder exclusions as path-prefix at enumeration + excluded from the query itself where the API allows. OneDrive: `/me/drive/root/delta` GET-only. Local: scan roots read **exclusively** from `local-whitelist.yaml`; git-repo discovery + `mdfind`/`find -newermt` fallback per tools §5.5; no code path accepts a root argument.
**Accept:**
```
uv run pytest tests/structural/test_local_whitelist.py   # root injection attempt → no scan outside whitelist
uv run mclaw fetch drive --account convoydefense --metadata-only --limit 20   # metadata in spool; excluded folder absent
uv run mclaw fetch local --list-roots    # prints exactly local-whitelist.yaml contents
```
- [ ] Done · Linear: [DEV-22](https://linear.app/psols/issue/DEV-22)

---

## Stage E — Bulk ingest (each gated by B6; each checkpointed + resumable; every writer behind the output guard)

### E1 — Email history mining
**Lands:** state (`contacts/index.json`, checkpoints) · vault (`wiki/people/` dossier seeds, `wiki/projects/` seeds, `raw/ingest/mining-report-<account>.md`, writing-style profile at `wiki/_meta/writing-style.md`) · repo (pipeline + `prompts/ingest-email.md`).
**Do:** per decision: metadata full-depth ×4 accounts → contact index (address, name, first/last interaction, counts, direction ratio — the cold-call feature); bodies last 24 months batched through `claude -p` (Max subscription; per-run `total_cost_usd` logged) → dossier seeds for significant contacts, project note seeds, commitments, writing-style profile from sent mail. Checkpoint per account per stage; resumable (kill → rerun continues).
**Accept:**
```
uv run mclaw ingest email --account powderhorns          # runs to completion or checkpoint
jq '.contacts | length' ~/.local/state/mark-claw/mark/contacts/index.json   # > 0, plausible count
ls ~/Documents/Obsidian/Mark-Claw/wiki/people/ | wc -l                       # dossier seeds present
# resume test: interrupt mid-run (Ctrl-C), rerun → continues from checkpoint (log line "resuming from …")
# spot-check: 5 dossiers factually sane; mining report lists counts per account
```
- [ ] Done · Linear: [DEV-23](https://linear.app/psols/issue/DEV-23)

### E2 — Chat history backfill
**Lands:** state (spool, per-conversation cursors — positioned so Phase 2 polling starts from "now") · vault (`raw/chat/<platform>/<conv-slug>/YYYY-MM.md` monthly files) · repo (pipeline).
**Do:** Slack full history (~5 mo, Tier-3 paging), Mattermost full (run **first** if A5 found the Entry 10k cap), Telegram 90 days, Signal: whatever exists since A7 link + Signal Desktop DB import if A7 found it usable (else record the known gap — tools §3.4). Excluded conversations skipped at enumeration.
**Accept:**
```
uv run mclaw ingest chat --source slack-work    # (and mattermost, telegram, signal)
# counts per conversation logged and reconciled vs API totals (±paging tolerance)
ls ~/Documents/Obsidian/Mark-Claw/raw/chat/slack/ | head
# REAL exclusion check: a B6-blocked channel name/ID → zero matches:
grep -ri "<blocked-conv-name>" ~/Documents/Obsidian/Mark-Claw/ ~/.local/state/mark-claw/mark/spool/ | wc -l   # 0
uv run mclaw guard scan-vault    # 0 findings
```
- [ ] Done · Linear: [DEV-24](https://linear.app/psols/issue/DEV-24)

### E3 — Drive/OneDrive key docs (propose → approve → ingest)
**Lands:** vault (`raw/ingest/docs-proposal.md`, then `raw/docs/<source-id>/…` for approved items + wiki links) · state (cursors) · repo (pipeline).
**Do:** metadata-only sweep of both Drives (names/paths/dates/sizes/owners — no content), respecting folder exclusions → proposal report grouped by folder with checkboxes; Mark ticks items; `--apply` fetches content for approved items only.
**Accept:**
```
uv run mclaw ingest docs --propose      # proposal report lands in vault; excluded folders absent from it
# Mark edits checkboxes …
uv run mclaw ingest docs --apply        # only ticked items fetched; report appended with results
```
- [ ] Done · Linear: [DEV-25](https://linear.app/psols/issue/DEV-25)

### E4 — Local machine sweep
**Lands:** vault (`raw/ingest/local-sweep-report.md`, ingested reference docs under `raw/docs/local/…`) · repo (pipeline reusing D4).
**Do:** whitelisted roots only; git-repo inventory (name, remote, recent activity) as a reference note; candidate non-repo documents proposed in the sweep report (same propose-then-approve shape as E3, cheap reuse).
**Accept:**
```
uv run mclaw ingest local --propose && uv run mclaw ingest local --apply
# report lists only whitelisted roots; repo inventory note in vault; TCC note: if ~/Documents
# is whitelisted and mdfind returns suspiciously little, check mdutil -s / FDA (tools §5.5)
```
- [ ] Done · Linear: [DEV-26](https://linear.app/psols/issue/DEV-26)

### E5 — Life-story / goals session
**Lands:** vault (`raw/ingest/life-story.md` full transcript · `wiki/_meta/core-context.md` distilled: who Mark is, goals, priorities, key people/projects — referenced by future pipeline prompts) · repo (`prompts/life-story.md`).
**Do:** interactive `claude` session primed with the interview prompt + the E1 contact/project seeds (so it asks informed gap questions); Mark answers typed or via VoiceInk (install is optional this phase — tools §4.5); session has vault-write only in `--allowedTools`.
**Accept:** transcript + distilled note exist with proper frontmatter; Mark reviews `core-context.md` and signs off on accuracy; output guard passed (it runs on every vault write).
- [ ] Done · Linear: [DEV-27](https://linear.app/psols/issue/DEV-27)

---

## Stage F — Phase close

### F1 — Rebuild drill + full-guard scan + status note
**Do:**
1. **State wipe/rebuild drill** (design §12 acceptance): `mv ~/.local/state/mark-claw/mark{,.bak}` → `mclaw doctor --init` → re-auth Google one account + re-run one bounded ingest slice → confirm skeleton, perms, cursors rebuild with **zero config edits**; restore backup.
2. **Full-vault guard scan:** `uv run mclaw guard scan-vault` over the entire vault + all Phase-1 logs → 0 findings (this becomes the Phase-2 weekly continuous check).
3. Full test suite: `uv run pytest && uv run mclaw test --canary` green.
4. Append the **status note** at the bottom of this file: what shipped, deviations, stub list, known issues, Mattermost edition, Signal history gap extent — the Phase 2 planning session reads it.
- [ ] Done · Linear: [DEV-28](https://linear.app/psols/issue/DEV-28)

---

## §V — Hard-guarantee verification map (structural, per tools §12.3)

| Guarantee | Structural fact in Phase 1 | Verified by |
|---|---|---|
| Never hard-delete / never send (mail) | Google tokens are `gmail.readonly`-scoped; client builders deny-list send/trash/delete/draft URIs; Graph client is GET-only; **zero send/delete call sites exist** | `tests/structural/test_mail_readonly.py` (AST/grep + deny-list raise test); `grep modify token.json → 0` |
| Read-only on chat | API-method allowlists in each chat client; Telethon wrapper exposes no send surface; signal-cli arg template cannot produce `send`/receipt flags | `tests/structural/test_chat_readonly.py` |
| Exclusion hard guarantee | Gate at enumeration inside shared `fetch_items()`; output guard fail-closed on every writer; exclusions authored (B6) **before** first fetch (E gated on B6) | Unit tests (B3/B4), canary (B5), live blocked-channel grep (E2), full-vault scan (F1) |
| No autonomous recording | No capture code exists in Phase 1 at all; no scheduler exists in Phase 1 at all | `grep -r audiotee\|ffmpeg mclaw_core bin → 0`; no plists installed |
| No autonomous sending of anything | No notify layer, no send wrapper, no scheduler in Phase 1 — nothing runs unless Mark runs it | absence is the guarantee; re-verified structurally in Phase 2 when those layers land |
| Secrets never in agent context | `keychain://` resolution + token reads only inside `mclaw_core/fetch.py`/auth helpers via `mclaw secret get`; `claude -p` calls receive file paths; log redaction pass | `tests/structural/test_secret_isolation.py` (prompt-assembly funcs take no secret args; grep for `security find-generic-password` outside `mclaw secret`/wrappers → 0) |
| No personal data in repo | Profile via `MCLAW_PROFILE`; hygiene grep with config-supplied patterns; example configs are placeholders | `tests/hygiene/` + B1 grep check |
| No agent frameworks | Dependency deny-list test on `pyproject.toml` | B1 test |

## Minimal-now vs stubbed-for-later

**Minimal now:** fetch wrappers do backfill only (no polling loop, no urgent scan); run records written but nothing reads them yet (dashboard = Phase 2); changelog written by guard/ingest only (closed enum started, most actions arrive Phase 2); `mclaw doctor` is the only health surface; guard trips alert via doctor/log only (Telegram = Phase 2).
**Not built at all (resist temptation):** rules engine, label taxonomy, mail-write of any kind, notify layer, launchd plists, dashboard, review queue, trackers automation (files exist, nothing writes them), meeting/transcript anything.

## Shakeout checklist (run 3–5 days before planning Phase 2)

Daily (~10 min):
- [ ] Browse the vault in Obsidian (Mac + phone): does the structure feel navigable? Note friction in `raw/ingest/shakeout-notes.md`.
- [ ] Ask the vault 1–2 real questions ("what did we decide about X", "who is Y") via a plain `claude` session pointed at the vault — record hit/miss.

Once during the window:
- [ ] Spot-check 10 contact dossiers against your memory of those people — factual errors? tone/emphasis wrong? List corrections.
- [ ] Spot-check the writing-style profile against 3 recent sent emails you're proud of.
- [ ] Exclusion spot-audit: pick 2 blocked sources, search the vault + spool + logs for their names/IDs (`mclaw guard scan-vault` + manual grep) — must be zero.
- [ ] Confirm Signal poller (`mclaw fetch signal`) run manually a few times keeps accumulating; note gap size vs phone history.
- [ ] Check state layer sizes (`du -sh ~/.local/state/mark-claw/mark/*`) — anything ballooning?
- [ ] Obsidian Sync conflict check: edit the same tracker on phone + Mac; confirm no data loss.

Feedback to collect for Phase 2 planning:
1. Dossier accuracy rate (of the 10 spot-checked) — drives how much E1 output Phase 2 triage can trust for cold-call detection.
2. Vault-question hit rate — drives wiki-enrichment priorities.
3. List of senders/newsletters you already know belong in specific buckets → seeds `rules/common.yaml`.
4. Any exclusion you forgot (things you saw in the vault and winced at) → B6 amendments **before** triage goes live.
5. Mattermost edition + Signal gap findings → Phase 2 cadence decisions.

## Linear tracking

Project: [agentic-and-ai-tooling](https://linear.app/psols/project/agentic-and-ai-tooling-ea2f10db893e/overview)
Phase parents: Phase 1 [DEV-1](https://linear.app/psols/issue/DEV-1) · Phase 2 [DEV-2](https://linear.app/psols/issue/DEV-2) · Phase 3 [DEV-3](https://linear.app/psols/issue/DEV-3) · Phase 4 [DEV-4](https://linear.app/psols/issue/DEV-4)
Step sub-issues recorded inline per step above.

---

## Status note (append at phase completion — Phase 2 planning reads this)

*(empty — to be written by the execution session)*
