# MARK-CLAW — Phase 1 Implementation Plan: Foundations (BASE + UPLOAD)

**Status:** Ready for execution — planned 2026-07-05
**Inputs:** `MARK-CLAW-SPEC.md` §12 Phase 1 · `MARK-CLAW-TOOLS.md` (ground truth for access paths) · `MARK-CLAW-DESIGN.md` §12 phase mapping (Phase 1 slice)
**Executor contract:** run steps in order within a stage; Stage A and Stage B1 may run in parallel (A's token flows use the `mc auth` helpers from B1). Mark each step done in this file; record deviations inline. Append a status note at the bottom when the phase completes — the Phase 2 planning session reads it.

## Decisions made in this planning session (interview 2026-07-05)

| Question | Decision |
|---|---|
| Google API access path | **Direct `google-api-python-client` with our own Desktop-app OAuth client** — not gog/gws CLI. Strongest structural deny-list (the HTTP layer rejects send/delete URIs); no dependency on a third-party CLI's client. gog/gws remain manual-inspection tools. Deviation from tools §1 "recommended primary" is deliberate and recorded here; tools §11 revisit-trigger logic already anticipated it. |
| Email mining depth | **Metadata full history (all 4 accounts) + full bodies last 24 months.** Complete contact graph for cold-call detection; bounded token/runtime cost. |
| Drive/OneDrive key docs | **Propose-then-approve**: metadata-only sweep → candidate report → Mark approves → content fetch of approved items only. |
| Linear tracking | Phase-level parent issues (1–4) + one sub-issue per Phase 1 step, under project `ai-personal-assistant`. IDs recorded per step below. |
| Phase-1 Google scopes | **`gmail.readonly` + `drive.readonly` only.** Bulk ingest never holds a write-capable Google token. `gmail.modify` re-consent happens in Phase 2 when `mc-mail-act` exists. |

## Phase 1 scope (from design §12)

Config/state/tooling skeletons with `mark` profile · vault + `VAULT-GUIDE.md` + Obsidian Sync · **exclusion gate + output guard + canary suite before any ingestion** · `op` secret wiring · bulk ingest (email mining, chat backfill, life-story, local sweep, Drive/OneDrive key docs).

**Explicitly deferred (do not build):** status dashboard (Phase 2 per design §12 — Phase 1 only writes the state files it will render), notify layer / Telegram bot (Phase 2; guard trips surface via `mc doctor` + log until then), any mail-write capability (labels/archive/drafts = Phase 2), launchd scheduling (nothing in Phase 1 recurs; `mc install-schedules` = Phase 2), rules engine (Phase 2), triage of any kind.

**Hard guarantees touched this phase:** exclusion enforcement (fully built + tested here), no-delete / no-send (readonly scopes + structural deny-list), read-only chat (structural), no autonomous recording (nothing schedulable exists this phase), no personal data in repo (hygiene test). Verification is structural per tools §12.3 — see §V below.

---

## Stage A — Credential & account setup (blocks everything; start immediately)

All self-service (tools §0 — no third-party approvals). Console work proceeds at once; token flows depend on the `mc auth` helpers from B1.

### A1 — macOS Keychain conventions + backup
**Lands:** macOS login keychain (secrets) · repo (`docs/SECRETS.md` naming conventions — generic, no personal values) · config (`keychain://` refs appear in `accounts.yaml`/`sources.yaml` as later steps add them) · state (`secrets/backup.age` seeded empty).
**Do:** document item naming (service `mark-claw-mark`, account = flattened `<item>-<field>` slug, one keychain item per secret value); confirm `security` CLI reachable (built-in, no install). Stage A steps use raw `security` commands directly — `mc secret set/get/list` (B1) wraps this convention once the CLI skeleton exists. Establish the `mc secret export` → age-encrypted backup convention now (passphrase held by Mark, never written to disk); B1 implements the command, this step just reserves the state path and the passphrase-custody decision.
**Accept:**
```
security add-generic-password -a smoke-test -s mark-claw-mark -w x -A     # create, always-allow
security find-generic-password -a smoke-test -s mark-claw-mark -w        # → x
security delete-generic-password -a smoke-test -s mark-claw-mark          # clean up
```
- [ ] Done · Linear: [DEV-5](https://linear.app/psols/issue/DEV-5)

### A2 — Google OAuth client + tokens for 3 Gmail accounts + work Drive (read-only scopes)
**Lands:** GCP console (own project) · Keychain (`mark-claw-mark`/`google-oauth-client-credential`) · state (`secrets/google/<account-id>/token.json`, 0600) · config (`accounts.yaml` entries with `client_ref` + `token_cache`). Nothing in repo.
**Do:** new GCP project (under `mark@powderhorns.biz`); consent screen External, **publish to Production** (kills the 7-day Testing refresh-token expiry — tools §1); Desktop-app OAuth client; enable Gmail + Drive APIs. Scopes requested: `gmail.readonly`, `drive.readonly` (restricted scopes; personal-use/unverified exemption applies — proceed through the "unverified app" screen; for the two Workspace domains optionally mark the client trusted via admin console / `consoleops@`). Run `uv run mc auth google <id>` for `convoydefense`, `powderhorns`, `gmail-personal`.
**Accept (per account):**
```
uv run mc auth google powderhorns            # browser flow → token file written
stat -f "%Lp" ~/.local/state/mark-claw/mark/secrets/google/powderhorns/token.json   # 600
uv run mc auth google powderhorns --self-test
#   → prints profile emailAddress + total message count via users.getProfile / labels.list
```
Also: consent screen status shows **In production**; token file contains no scope beyond readonly (`grep -c modify token.json` → 0).
- [ ] Done · Linear: [DEV-6](https://linear.app/psols/issue/DEV-6)

### A3 — Entra app registration (jumpweb.net) + Graph token
**Lands:** Entra portal · Keychain (`mark-claw-mark`/`entra-app-client_id`, `mark-claw-mark`/`entra-app-tenant_id`) · state (`secrets/msal/jumpweb/`) · config (`accounts.yaml` graph entry).
**Do:** app registration in jumpweb tenant; public client, "Allow public client flows" on; delegated scopes `Mail.Read`, `Files.Read`, `Calendars.Read`, `offline_access` (calendar added now to avoid Phase-3 re-consent; Phase 1 code never calls calendar). Self-grant admin consent. Device-code flow via `uv run mc auth graph jumpweb`.
**Accept:**
```
uv run mc auth graph jumpweb --self-test
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
**Do:** my.telegram.org → api_id/api_hash; one interactive `uv run mc auth telegram` login (code via app) → persistent StringSession. Real device info in session metadata; never re-login repeatedly (tools §3.3).
**Accept:**
```
uv run mc auth telegram --self-test    # prints dialog count via get_dialogs, no login prompt on 2nd run
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

### B1 — Tooling repo skeleton + `mc` CLI + repo-hygiene test
**Lands:** repo only (`pyproject.toml` (uv), `mc_core/`, `bin/`, `prompts/`, `tests/`, `docs/`).
**Do:** uv-managed Python project; `mc` CLI entry point with subcommands stubbed (`doctor`, `auth`, `exclusions`, `fetch`, `ingest`, `guard`, `secret` — `set`/`get`/`list`/`export`, wraps `security add-generic-password`/`find-generic-password`/`dump-keychain` + age encryption for `export`); `MC_PROFILE` env (default `mark`) resolves config/state roots — **no personal value hardcoded anywhere**. Hygiene test `tests/hygiene/`: greps the entire repo for personal-identifier patterns; the pattern list itself is personal, so the test reads patterns from `$CONFIG/hygiene-patterns.txt` (config layer) and **skips with a loud warning** if absent (so the repo stays cloneable).
**Dependency guard:** `pyproject.toml` contains no agent-framework packages (tools §12); test asserts the dependency list against a deny-list (`openclaw*, zeroclaw*, ironclaw*, picoclaw*, nemoclaw*, hermes*, vellum*`).
**Accept:**
```
uv run mc doctor            # runs, reports missing config/state (expected at this point)
uv run pytest tests/hygiene # green (patterns file present) — finds 0 personal identifiers in repo
git ls-files | xargs grep -l "powderhorns\|jumpweb\|convoydefense\|markfrommn" | grep -v specs/  # empty
```
*(specs/ is exempt — it predates the split and is Mark's own repo; flag if he wants specs scrubbed too.)*
- [ ] Done · Linear: [DEV-12](https://linear.app/psols/issue/DEV-12)

### B2 — Config + state trees, `mark` profile
**Lands:** config (`~/.config/mark-claw/mark/`: `settings.yaml`, `accounts.yaml`, `sources.yaml`, skeleton `exclusions.yaml`, `local-whitelist.yaml`, `hygiene-patterns.txt`) · state (`~/.local/state/mark-claw/mark/`: `cursors/ spool/ runs/ contacts/ changelog/ quarantine/ secrets/ locks/ logs/` — `secrets/`, `quarantine/`, `spool/ephemeral/` 0700) · repo (`mc doctor` validation logic + example config files under `docs/examples/` with placeholder values only).
**Do:** `mc doctor --init` creates both trees idempotently; validates YAML schemas, dir perms, `op` sign-in, vault path.
**Accept:**
```
uv run mc doctor    # all green checklist: dirs, perms (700 on secrets/), yaml parse, op ok
stat -f "%Lp" ~/.local/state/mark-claw/mark/secrets   # 700
rm -rf ~/.local/state/mark-claw/mark && uv run mc doctor --init && uv run mc doctor  # rebuilds green
```
- [ ] Done · Linear: [DEV-13](https://linear.app/psols/issue/DEV-13)

### B3 — Exclusion gate (`mc_core/exclusion.py`)
**Lands:** repo (module + unit tests). Config supplies the data (B6).
**Do:** per design §5.2 (normative API): `ExclusionGate.load(config)`; `gate.check(source_id, item_ref) → ALLOW | EPHEMERAL | BLOCKED`; compiled from `exclusions.yaml` + `local-whitelist.yaml`; path-prefix inheritance (Drive/OneDrive), conversation/contact IDs (chat), event/series IDs (meetings), **whitelist inversion for local** (scan roots come only from the whitelist file — no other code path exists). Gate call sits inside the shared `fetch_items()` base (D1) so a wrapper cannot skip it without bypassing the shared path.
**Accept:**
```
uv run pytest tests/unit/test_exclusion.py
# covers: blocked/ephemeral/allow per source type; prefix inheritance to subfolders;
# ID-and-name matching; local whitelist inversion (unlisted root → no scan); config reload
```
- [ ] Done · Linear: [DEV-14](https://linear.app/psols/issue/DEV-14)

### B4 — Output guard (`mc_core/output_guard.py`) — fail closed
**Lands:** repo (module + tests). State receives `quarantine/` artifacts + `changelog/` records on trips.
**Do:** per design §5.4: compile deny-pattern set from every blocked/ephemeral entry (IDs, names, aliases, `also_match`, word-boundary, case-insensitive); `guard.scan(artifact) → clean | Trip`; on trip the writer does **not** write — artifact moved to `state/quarantine/` (0700, outside vault/sync), `guard.trip` changelog record appended. Telegram alert on trip is Phase 2 (notify layer doesn't exist yet); until then `mc doctor` shows quarantine count ≠ 0 in red. Guard never logs matched content — pattern ID + artifact name only. Every Phase-1 vault writer calls it.
**Accept:**
```
uv run pytest tests/unit/test_output_guard.py
# covers: hit → no write + quarantined + changelog guard.trip; clean → written;
# ephemeral identifiers matched on persistence surfaces; no content in logs
uv run mc guard scan-vault   # command exists; on empty vault → "0 findings"
```
- [ ] Done · Linear: [DEV-15](https://linear.app/psols/issue/DEV-15)

### B5 — Canary suite (CI-blocking)
**Lands:** repo (`tests/canary/` fixture profile + integration test).
**Do:** per design §5.5.2: fixture profile (`MC_PROFILE=canary`, config/state under `tests/canary/tmp/`) whose exclusions block a fixture channel/folder/contact seeded with sentinel tokens `MCX-CANARY-<uuid>`; run every Phase-1 pipeline (fetch base + ingest writers, mocked providers) over fixture data; assert **zero** sentinel occurrences across the whole output tree (spool, vault fixture dir, logs, run records) and ephemeral content exists only transiently. Wire as pre-commit/CI gate: `uv run mc test --canary` must pass before any commit touching `mc_core/`, `bin/`, or writers.
**Accept:**
```
uv run mc test --canary        # green
grep -r "MCX-CANARY" tests/canary/tmp/output/ | wc -l    # 0
# mutate the gate to always-ALLOW in a scratch branch → canary goes red (proves the test bites)
```
- [ ] Done · Linear: [DEV-16](https://linear.app/psols/issue/DEV-16)

### B6 — Author the real exclusions + local whitelist (gate live before first fetch)
**Lands:** config only (`exclusions.yaml`, `local-whitelist.yaml`) — Mark authors, assistant assists with ID lookup (channel IDs via A4/A5 tokens, folder paths, contacts).
**Do:** Mark walks Slack channels / MM channels / TG+Signal contacts / Drive+OneDrive folders / meeting series and lists Blocked + Ephemeral entries per spec §7.2; whitelist the local scan roots. This step **gates Stage E** — no ingest runs until it's done and linted.
**Accept:**
```
uv run mc exclusions lint      # schema valid; prints counts per source + tier
uv run python -c "…gate.check('slack-work', '<a-known-blocked-id>')"   # → BLOCKED
uv run mc exclusions lint --show-local   # prints exactly the whitelisted roots, nothing else
```
- [ ] Done · Linear: [DEV-17](https://linear.app/psols/issue/DEV-17)

---

## Stage C — Vault

### C1 — Vault tree + VAULT-GUIDE + Obsidian Sync
**Lands:** vault (`~/Documents/Obsidian/Mark-Claw`: `raw/{email,chat,transcripts,docs,ingest}`, `wiki/{people,companies,projects,meetings,briefings,days,insights,trackers,reviews,_meta}`) · config (`settings.yaml → vault.path`) · repo (`mc_core/vault.py` writer helpers enforcing naming conventions + frontmatter contract; `wiki/_meta/VAULT-GUIDE.md` content is generated from a repo template with no personal data).
**Do:** create tree per design §3.3; write `VAULT-GUIDE.md` (naming: `YYYY-MM-DD--kebab-slug`, frontmatter keys `source/created/pipeline/refs`, raw-vs-wiki contract, tracker row-id convention); Mark enables Obsidian Sync (manual, his Obsidian account); trackers `tasks.md` / `waiting-on.md` created empty with header rows.
**Accept:**
```
uv run mc doctor               # vault path resolves, tree complete
git -C ~/Documents/Obsidian/Mark-Claw status 2>/dev/null; echo ok   # vault is NOT a git repo in the tooling repo
grep -r "Obsidian/Mark-Claw" <repo>/mc_core <repo>/bin | wc -l      # 0 — path comes from config
```
Manual: create a note on the Mac → appears on phone via Obsidian Sync within ~1 min; `VAULT-GUIDE.md` readable on phone.
- [ ] Done · Linear: [DEV-18](https://linear.app/psols/issue/DEV-18)

---

## Stage D — Fetch wrappers (backfill mode, read-only by construction)

### D1 — Shared fetch base
**Lands:** repo (`mc_core/fetch.py`: `fetch_items()` base — secret resolve via `mc secret get` (wraps `security find-generic-password`) inside the wrapper only, exclusion-gate call at enumeration, cursor read/write, spool JSONL writer per design §3.2 envelope, 3× retry with backoff honoring `Retry-After`/FloodWait, run-record write) · state (spool/cursors/runs written at runtime).
**Accept:**
```
uv run pytest tests/unit/test_fetch_base.py
# covers: gate consulted before content fetch (mock provider asserts no content call for BLOCKED);
# cursor advances only on success; spool envelope schema; blocked_skipped counted without identifiers
```
- [ ] Done · Linear: [DEV-19](https://linear.app/psols/issue/DEV-19)

### D2 — Mail wrappers: Gmail ×3 + Graph (read-only)
**Lands:** repo (`bin/mc-fetch-gmail`, `bin/mc-fetch-graph` + shared google/msal client builders) · state (token caches already from A2/A3; spool + cursors at runtime).
**Do:** Gmail: `messages.list/get`, `labels.list`, profile — **nothing else**. The shared Google client builder wraps the HTTP layer with a **URI deny-list** that raises on any request whose path matches `/send|/trash|/delete|/drafts` and any non-GET/POST-list method — belt over the readonly-scope suspenders. Graph client allows **GET only** in Phase 1 (method allowlist in the client builder). Backfill mode: full-depth metadata query paging + bounded body fetch (E1 policy), checkpoint cursor per account.
**Accept (structural — tools §12.3):**
```
uv run pytest tests/structural/test_mail_readonly.py
# 1) grep/AST scan of repo: zero call sites for send/trash/delete/drafts on any mail client
# 2) deny-list unit test: hand-craft a forbidden request through the client builder → raises
# 3) Graph client: POST/PATCH/DELETE attempt → raises
uv run mc fetch gmail --account powderhorns --backfill --limit 50 --dry-run   # lists, fetches nothing
uv run mc fetch gmail --account powderhorns --backfill --limit 50             # 50 items in spool, cursor written
```
- [ ] Done · Linear: [DEV-20](https://linear.app/psols/issue/DEV-20)

### D3 — Chat wrappers: Slack, Mattermost, Telegram, Signal (read-only)
**Lands:** repo (`bin/mc-fetch-slack|mm|tg|signal`) · state (spool/cursors at runtime; TG session + signal data dir already from A6/A7).
**Do:** Slack: `conversations.list/history/replies`, `users.list` via an **API-method allowlist** in the client (any other method name → raises). MM: `GET /channels/{id}/posts?since=` only; client is GET-only. Telegram: thin Telethon wrapper exposing only `get_dialogs`/`iter_messages` (module does not import send/read-ack functions; wrapper class has no send surface). Signal: subprocess arg-builder for `signal-cli receive` — arg list is constructed from a fixed template; test asserts `send`, `--send-read-receipts` can never appear. Per-conversation cursors; page-capped backfill (`limits.max_backfill_pages`).
**Accept (structural):**
```
uv run pytest tests/structural/test_chat_readonly.py
# per platform: forbidden-method call → raises; grep: no chat.postMessage / posts POST /
# send_message / signal-cli send call sites anywhere in repo
uv run mc fetch slack --backfill --conversations 2 --dry-run   # enumerates, honors gate (blocked conv absent)
```
- [ ] Done · Linear: [DEV-21](https://linear.app/psols/issue/DEV-21)

### D4 — Drive + OneDrive + local-sweep wrappers
**Lands:** repo (`bin/mc-fetch-drive`, `bin/mc-fetch-onedrive`, `bin/mc-fetch-local`) · state (cursors: Drive pageToken, Graph deltaLink — captured now so Phase 2+ incremental sync starts from here).
**Do:** Drive: `files.list` metadata + `files.export` (read-only scope from A2); folder exclusions as path-prefix at enumeration + excluded from the query itself where the API allows. OneDrive: `/me/drive/root/delta` GET-only. Local: scan roots read **exclusively** from `local-whitelist.yaml`; git-repo discovery + `mdfind`/`find -newermt` fallback per tools §5.5; no code path accepts a root argument.
**Accept:**
```
uv run pytest tests/structural/test_local_whitelist.py   # root injection attempt → no scan outside whitelist
uv run mc fetch drive --account convoydefense --metadata-only --limit 20   # metadata in spool; excluded folder absent
uv run mc fetch local --list-roots    # prints exactly local-whitelist.yaml contents
```
- [ ] Done · Linear: [DEV-22](https://linear.app/psols/issue/DEV-22)

---

## Stage E — Bulk ingest (each gated by B6; each checkpointed + resumable; every writer behind the output guard)

### E1 — Email history mining
**Lands:** state (`contacts/index.json`, checkpoints) · vault (`wiki/people/` dossier seeds, `wiki/projects/` seeds, `raw/ingest/mining-report-<account>.md`, writing-style profile at `wiki/_meta/writing-style.md`) · repo (pipeline + `prompts/ingest-email.md`).
**Do:** per decision: metadata full-depth ×4 accounts → contact index (address, name, first/last interaction, counts, direction ratio — the cold-call feature); bodies last 24 months batched through `claude -p` (Max subscription; per-run `total_cost_usd` logged) → dossier seeds for significant contacts, project note seeds, commitments, writing-style profile from sent mail. Checkpoint per account per stage; resumable (kill → rerun continues).
**Accept:**
```
uv run mc ingest email --account powderhorns          # runs to completion or checkpoint
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
uv run mc ingest chat --source slack-work    # (and mattermost, telegram, signal)
# counts per conversation logged and reconciled vs API totals (±paging tolerance)
ls ~/Documents/Obsidian/Mark-Claw/raw/chat/slack/ | head
# REAL exclusion check: a B6-blocked channel name/ID → zero matches:
grep -ri "<blocked-conv-name>" ~/Documents/Obsidian/Mark-Claw/ ~/.local/state/mark-claw/mark/spool/ | wc -l   # 0
uv run mc guard scan-vault    # 0 findings
```
- [ ] Done · Linear: [DEV-24](https://linear.app/psols/issue/DEV-24)

### E3 — Drive/OneDrive key docs (propose → approve → ingest)
**Lands:** vault (`raw/ingest/docs-proposal.md`, then `raw/docs/<source-id>/…` for approved items + wiki links) · state (cursors) · repo (pipeline).
**Do:** metadata-only sweep of both Drives (names/paths/dates/sizes/owners — no content), respecting folder exclusions → proposal report grouped by folder with checkboxes; Mark ticks items; `--apply` fetches content for approved items only.
**Accept:**
```
uv run mc ingest docs --propose      # proposal report lands in vault; excluded folders absent from it
# Mark edits checkboxes …
uv run mc ingest docs --apply        # only ticked items fetched; report appended with results
```
- [ ] Done · Linear: [DEV-25](https://linear.app/psols/issue/DEV-25)

### E4 — Local machine sweep
**Lands:** vault (`raw/ingest/local-sweep-report.md`, ingested reference docs under `raw/docs/local/…`) · repo (pipeline reusing D4).
**Do:** whitelisted roots only; git-repo inventory (name, remote, recent activity) as a reference note; candidate non-repo documents proposed in the sweep report (same propose-then-approve shape as E3, cheap reuse).
**Accept:**
```
uv run mc ingest local --propose && uv run mc ingest local --apply
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
1. **State wipe/rebuild drill** (design §12 acceptance): `mv ~/.local/state/mark-claw/mark{,.bak}` → `mc doctor --init` → re-auth Google one account + re-run one bounded ingest slice → confirm skeleton, perms, cursors rebuild with **zero config edits**; restore backup.
2. **Full-vault guard scan:** `uv run mc guard scan-vault` over the entire vault + all Phase-1 logs → 0 findings (this becomes the Phase-2 weekly continuous check).
3. Full test suite: `uv run pytest && uv run mc test --canary` green.
4. Append the **status note** at the bottom of this file: what shipped, deviations, stub list, known issues, Mattermost edition, Signal history gap extent — the Phase 2 planning session reads it.
- [ ] Done · Linear: [DEV-28](https://linear.app/psols/issue/DEV-28)

---

## §V — Hard-guarantee verification map (structural, per tools §12.3)

| Guarantee | Structural fact in Phase 1 | Verified by |
|---|---|---|
| Never hard-delete / never send (mail) | Google tokens are `gmail.readonly`-scoped; client builders deny-list send/trash/delete/draft URIs; Graph client is GET-only; **zero send/delete call sites exist** | `tests/structural/test_mail_readonly.py` (AST/grep + deny-list raise test); `grep modify token.json → 0` |
| Read-only on chat | API-method allowlists in each chat client; Telethon wrapper exposes no send surface; signal-cli arg template cannot produce `send`/receipt flags | `tests/structural/test_chat_readonly.py` |
| Exclusion hard guarantee | Gate at enumeration inside shared `fetch_items()`; output guard fail-closed on every writer; exclusions authored (B6) **before** first fetch (E gated on B6) | Unit tests (B3/B4), canary (B5), live blocked-channel grep (E2), full-vault scan (F1) |
| No autonomous recording | No capture code exists in Phase 1 at all; no scheduler exists in Phase 1 at all | `grep -r audiotee\|ffmpeg mc_core bin → 0`; no plists installed |
| No autonomous sending of anything | No notify layer, no send wrapper, no scheduler in Phase 1 — nothing runs unless Mark runs it | absence is the guarantee; re-verified structurally in Phase 2 when those layers land |
| Secrets never in agent context | `keychain://` resolution + token reads only inside `mc_core/fetch.py`/auth helpers via `mc secret get`; `claude -p` calls receive file paths; log redaction pass | `tests/structural/test_secret_isolation.py` (prompt-assembly funcs take no secret args; grep for `security find-generic-password` outside `mc secret`/wrappers → 0) |
| No personal data in repo | Profile via `MC_PROFILE`; hygiene grep with config-supplied patterns; example configs are placeholders | `tests/hygiene/` + B1 grep check |
| No agent frameworks | Dependency deny-list test on `pyproject.toml` | B1 test |

## Minimal-now vs stubbed-for-later

**Minimal now:** fetch wrappers do backfill only (no polling loop, no urgent scan); run records written but nothing reads them yet (dashboard = Phase 2); changelog written by guard/ingest only (closed enum started, most actions arrive Phase 2); `mc doctor` is the only health surface; guard trips alert via doctor/log only (Telegram = Phase 2).
**Not built at all (resist temptation):** rules engine, label taxonomy, mail-write of any kind, notify layer, launchd plists, dashboard, review queue, trackers automation (files exist, nothing writes them), meeting/transcript anything.

## Shakeout checklist (run 3–5 days before planning Phase 2)

Daily (~10 min):
- [ ] Browse the vault in Obsidian (Mac + phone): does the structure feel navigable? Note friction in `raw/ingest/shakeout-notes.md`.
- [ ] Ask the vault 1–2 real questions ("what did we decide about X", "who is Y") via a plain `claude` session pointed at the vault — record hit/miss.

Once during the window:
- [ ] Spot-check 10 contact dossiers against your memory of those people — factual errors? tone/emphasis wrong? List corrections.
- [ ] Spot-check the writing-style profile against 3 recent sent emails you're proud of.
- [ ] Exclusion spot-audit: pick 2 blocked sources, search the vault + spool + logs for their names/IDs (`mc guard scan-vault` + manual grep) — must be zero.
- [ ] Confirm Signal poller (`mc fetch signal`) run manually a few times keeps accumulating; note gap size vs phone history.
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
