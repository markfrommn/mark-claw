# MARK-CLAW — Tool Selection & Feasibility (Phase A)

**Status:** Confirmed v1.1 — tool choices approved by Mark 2026-07-05; amended same day with §7.4 (local status dashboard) and §12 (agent-framework evaluation — verdict: stay bespoke)
**Input:** `MARK-CLAW-SPEC.md` (Draft v2 requirements)
**Output consumers:** Phase B design (`MARK-CLAW-DESIGN.md`) treats this file as ground truth
**Method:** Web research (5 parallel research passes, 2025–2026 primary sources), user interview (license tiers, admin rights, risk tolerance), and local verification on this machine. Every entry carries a **VERIFIED** / **ASSUMED** flag; citations at the end of each section.

**Verification legend:**
- **VERIFIED** — confirmed against a current (2025–2026) primary web source, a user answer about their own account, or a direct test on this machine.
- **ASSUMED** — established practice or inference not re-verified this pass; safe to build on but check during implementation.

---

## 0. Account inventory (from interview — supersedes spec's 3-account table)

| # | Account | Provider | Role | Admin status |
|---|---|---|---|---|
| 1 | `mark@convoydefense.ai` | Google Workspace **Business Standard** | Work mail + work calendar + work Drive + Meet | Mark is an admin; full super-admin via `consoleops@convoydefense.ai` for provisioning |
| 2 | `mark@powderhorns.biz` | Google Workspace | Personal mail #1 | Mark is super admin |
| 3 | `mark@jumpweb.net` | Microsoft 365 (own small Business tenant) | Personal mail #2 + personal calendar + OneDrive | Mark is tenant global admin |
| 4 | `markfrommn@gmail.com` | Consumer Google | Personal mail #3 (added in Phase A interview) | n/a (consumer) |

**Consequence:** *no employer/third-party admin approval is required anywhere in this system.* Every OAuth app, PAT, and API enablement is self-service. This removes the single biggest feasibility risk category.

Day-to-day API access uses `mark@` identities; `consoleops@convoydefense.ai` is available for admin-console provisioning (trusting the OAuth client, checking service settings) but should never hold pipeline credentials.

---

## 1. Email — Gmail ×3 (accounts 1, 2, 4)

| Field | Value |
| **Recommended Primary** | **`gog` CLI** (already installed at `/opt/homebrew/bin/gog`, verified locally) — generic Google Workspace REST wrapper covering `gmail`, `calendar`, `docs`, `drive`, `sheets`, `sides`, `forms`, `contacts`, `tasks` and some admin feature. Per-account credentials available via --user arugment (currently: `mark@powderhorns.biz`, `markfrommn@gmail.com`, `markfrommn@gmail.com`). Documented at https://gogcli.sh/.  |
| **First Fallback** | **`gws` CLI** (already installed at `/opt/homebrew/bin/gws`, verified locally) — generic Google Workspace REST wrapper covering `gmail`, `calendar`, `drive`, `meet`, `people`, `reports`, with per-account credential dirs under `~/.config/gws/` (currently: `mark@powderhorns.biz`, `convoyops@convoydefense.ai`). Add `mark@convoydefense.ai` and `markfrommn@gmail.com`. A `gws-multi-account` skill exists for account switching. |
| **Second Fallback** | Direct Gmail REST API scripts (google-auth `InstalledAppFlow`, per-account token files); Google's **official Gmail MCP server** (Developer Preview 2026 — tool set is exactly read/label/draft with *no send, no delete*: structurally matches our constraints); `taylorwilsdon/google_workspace_mcp` (active, 2.8k★); claude.ai Gmail connector (one account only). |
| **Mechanism** | Gmail REST API v1: `messages.list/get`, `labels.*`, `messages.modify` (label/archive = remove `INBOX` label), `drafts.create`. |
| **Auth** | OAuth 2.0 installed-app flow, one Desktop-app client, per-account token files. Scope: **`gmail.modify`** (covers read/search/label/archive/draft). For the two Workspace domains: mark the client trusted / use an org-internal GCP project (admin self-service). For `markfrommn@gmail.com`: External unverified app is fine for personal use — **must be published to Production** (Testing status = 7-day refresh-token expiry). |
| **Limits** | 1.2M quota units/min/project; 6,000 units/min/user; 80M units/day/project free tier. Assistant volume is orders of magnitude below all of these. Full mailbox history available; `history.list` incremental sync IDs only reach back ~1 week, so periodic query-based sweeps rather than long-lived historyIds (ASSUMED). |
| **Cost** | $0. (Google announced Gmail/Calendar API metering for post–May 2026 projects "later in 2026" — free daily threshold far exceeds our volume.) |
| **Risks** | ⚠️ **`gmail.modify` technically permits send and trash.** Scope-level "cannot send / cannot hard-delete" does not exist short of read-only. → *No-send / no-hard-delete is a code-level invariant of the tooling layer* (deny-listing `messages.send`, `drafts.send`, `messages.delete` at the wrapper). The official Gmail MCP is the fallback whose tool surface structurally cannot send/delete. |
| **Flag** | **VERIFIED** (scopes/restricted status, personal-use exemption, 7-day Testing expiry rule, quotas, `gws` presence locally). ASSUMED: `gws` OAuth client details — confirm its client type and scope set during Phase B/C setup. |

Citations: restricted-scope list <https://support.google.com/cloud/answer/13464325>; personal-use/unverified exemption <https://support.google.com/cloud/answer/13464323>; Testing-status 7-day expiry <https://support.google.com/cloud/answer/15549945>; quotas <https://developers.google.com/workspace/gmail/api/reference/quota>; official Gmail MCP <https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server>.

---

## 2. Email + Calendar + OneDrive — Microsoft 365 (`mark@jumpweb.net`)

| Field | Value |
|---|---|
| **Recommended** | **Microsoft Graph REST** with our own app registration in the jumpweb.net tenant. Mail: `/me/messages`, `/me/mailFolders`, `move`, `categories` PATCH, draft create (`POST /me/messages` — never call `/send`). Calendar: **`/me/calendarView`** (returns recurring events pre-expanded for a time window). OneDrive: `/me/drive` + **`/root/delta`** for change tracking. |
| **Fallback** | **Softeria `ms-365-mcp-server`** (MIT, v0.128.x June 2026, 200+ Graph tools, device-code auth, active) — requires a tool allowlist to enforce no-send/no-delete; IMAP XOAUTH2 (`outlook.office.com/IMAP.AccessAsUser.All`) as a lossy escape hatch (loses categories/flags/search fidelity). |
| **Mechanism** | Graph v1.0 REST; device-code flow for CLI-friendly auth; `offline_access` for refresh tokens. |
| **Auth** | Own Entra app registration (free; public client, "Allow public client flows" on). Delegated scopes: `Mail.ReadWrite`, `MailboxSettings.ReadWrite` (category master list), `Calendars.Read`, `Files.Read`, `offline_access`. Mark self-grants admin consent as tenant admin. Refresh tokens: 90-day rolling window, renewed on every use — daily pipelines never expire; an idle 90 days kills the grant. |
| **Label mapping** | Outlook has no Gmail labels. Mapping: Gmail label → Outlook **category** (multi-tag, mailbox-wide master list); Gmail archive → **move to Archive well-known folder**; Gmail star → `flag`. Classification rules must be expressed in provider-neutral terms with this adapter mapping. |
| **Limits** | 10,000 requests / 10 min / mailbox·app; 4 concurrent per mailbox. Vastly sufficient. |
| **Cost** | $0 — Graph Outlook/Files APIs unmetered. |
| **Risks** | Known 2026 issue: some outlook.com custom folders occasionally invisible via Graph (monitor; fall back to well-known names/search). The June-2026 MSA `common`-authority refresh-token bug does **not** apply (this is a real tenant, not a consumer MSA). |
| **Flag** | **VERIFIED** (scopes incl. personal-account columns, calendarView recurrence expansion, delta on OneDrive, throttling numbers, Softeria maintenance status, IMAP XOAUTH2 viability). |

Citations: move/permissions <https://learn.microsoft.com/en-us/graph/api/message-move>; calendarView <https://learn.microsoft.com/en-us/graph/api/user-list-calendarview>; driveItem delta <https://learn.microsoft.com/en-us/graph/api/driveitem-delta>; throttling <https://learn.microsoft.com/en-us/graph/throttling-limits>; Softeria <https://github.com/softeria/ms-365-mcp-server>; IMAP OAuth <https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth>.

---

## 3. Chat platforms (read-only, ~15-min polling)

Cross-platform invariant, verified for all four: **none of the recommended read paths marks messages read** — read-marking is always a separate explicit call (Slack `conversations.mark`, Mattermost `POST .../view`, Telegram `messages.readHistory`, signal-cli `--send-read-receipts`). The one unavoidable signal is Signal's *delivery* receipt (indistinguishable from the phone's own).

### 3.1 Slack (work, paid plan, app installs open)

| Field | Value |
|---|---|
| **Recommended** | Self-built **internal app with a user token (xoxp)**, scopes `channels:history`, `groups:history`, `im:history`, `mpim:history`, `users:read`, `channels:read` (+ `search:read` optional). Poll `conversations.list` + `conversations.history`/`replies` every 15 min with per-conversation cursors. |
| **Fallback** | `search.messages` (user token, Tier 2, sees everything the user sees, ~10k-result ceiling, now flagged "legacy"); a bot token invited to selected channels via Socket Mode (loses DM coverage). *Rejected:* `korotovsky/slack-mcp-server` (browser xoxc/xoxd tokens — ToS gray zone); workspace exports (admin-only, public channels only); official Slack MCP (admin-configured, Slack-AI-gated). |
| **Key finding** | **The 2025 rate-limit crackdown does not apply.** The 1 req/min · 15 msgs/request limits hit only apps "commercially distributed outside of the Marketplace" created after 2025-05-29; **internal customer-built apps are explicitly exempt** and keep ~50 req/min at up to 1,000 msgs/request. Additionally, enforcement against existing non-Marketplace installs was quietly dropped (live docs: existing installations "are not subject to the new posted limits"). |
| **History** | Full workspace history (~5 months) — backfill at Tier 3 is minutes-to-hours of work. Paid plan = no 90-day visibility cap. |
| **Auth** | App created in Mark's workspace, user-token install (plan allows member installs — interview-confirmed). Token in macOS Keychain. |
| **Cost** | $0. |
| **Risks** | If Slack ever reclassifies internal apps into the crackdown tier, 15-min polling caps at ~15 conversations/cycle — see Revisit Triggers. Workspace owners could later enable app-approval requirements. |
| **Flag** | **VERIFIED** (exemption FAQ, rate-limit page re-fetched live this session to adjudicate a stale claim; member-install default). ASSUMED: reads never marking channels read (standard API behavior). |

Citations: <https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/>; <https://docs.slack.dev/apis/web-api/rate-limits/>; <https://slack.com/help/articles/222386767>.

### 3.2 Mattermost (self-hosted, Mark is system admin)

| Field | Value |
|---|---|
| **Recommended** | REST API v4 + **Personal Access Token**. Poll `GET /api/v4/channels/{id}/posts` with `since` (Unix ms) per channel; page backward for backfill. |
| **Fallback** | WebSocket event stream; official Mattermost Agents-plugin MCP server (Jan 2026, needs server ≥11.9). |
| **Auth** | Enable `EnableUserAccessTokens` (system console, default off) and grant Mark's account a PAT. PATs don't expire. |
| **Limits** | Server rate limiter off by default; if on, defaults (10/s, burst 100) are ample. Full history. |
| **Cost** | $0. |
| **Risks** | PATs carry the user's **full** permissions — no read-only scoping exists; read-only is tooling discipline (same invariant pattern as Gmail no-send). ⚠️ Free self-hosted **"Entry" tier (v11+) hides messages beyond the most recent 10,000** — **action item: confirm server edition**; if Entry-capped, backfill immediately and archive continuously. |
| **Flag** | **VERIFIED** (PAT mechanics, `since` param from OpenAPI source, rate-limit defaults, Entry-tier cap). ASSUMED: per_page max 200; API subject to same 10k visibility cap as UI. |

Citations: <https://developers.mattermost.com/integrate/reference/personal-access-token/>; <https://docs.mattermost.com/administration-guide/configure/rate-limiting-configuration-settings.html>; <https://docs.mattermost.com/product-overview/editions-and-offerings.html>.

### 3.3 Telegram (personal, user-account access — risk accepted in interview)

| Field | Value |
|---|---|
| **Recommended** | **Telethon** (v1.44+, actively maintained) with a **persistent StringSession** on the established personal account; api_id/api_hash from my.telegram.org. `iter_messages` for the 90-day backfill; 15-min `get_dialogs` + per-dialog incremental fetch. |
| **Fallback** | TDLib (heavier, first-party); GramJS (staler — last publish Feb 2025). *Rejected:* Bot API — bots cannot see existing DM history, cannot enumerate dialogs, and only receive messages after being added; useless for monitoring Mark's own chats. Existing Telegram MCP servers (chigwell/, chaindead/) are maintained but ship send/delete tools — fork/restrict or write a thin read-only poller instead. |
| **History** | Full (90-day requirement trivially met). |
| **Auth** | One interactive login → StringSession stored in state (api_id/api_hash in Keychain); never re-login repeatedly; real device info in session metadata. |
| **Limits** | FloodWait errors carry explicit wait times; Telethon auto-sleeps <60 s. 15-min polling is far below thresholds. |
| **Cost** | $0. |
| **ToS/ban risk** | Telegram explicitly permits third-party clients via api_id; bans cluster around fresh accounts, VoIP numbers, and mass actions. Low-volume read-only polling on an established account is the lowest-risk MTProto profile. Mitigations: single long-lived session, no mass joins/scrapes, check @SpamBot if concerned. **Risk accepted by Mark (interview).** |
| **Flag** | **VERIFIED** (ToS pages, Telethon FAQ/maintenance, read/ack separation). ASSUMED: interpretation that a background fetcher isn't a "ghost-mode client" under ToS; unlimited history depth. |

Citations: <https://core.telegram.org/api/terms>; <https://core.telegram.org/api/obtaining_api_id>; <https://docs.telethon.dev/en/stable/quick-references/faq.html>.

### 3.4 Signal (personal, linked device — risk accepted in interview)

| Field | Value |
|---|---|
| **Recommended** | **signal-cli** (`brew install signal-cli`, v0.14.5 June 2026, active) linked as a secondary device via QR. **15-min cron `receive`** (not the long-running daemon — simpler, and satisfies the 45-day linked-device inactivity unlink). |
| **Fallback** | `bbernhard/signal-cli-rest-api` container (active, v0.100); reading **Signal Desktop's local DB** (e.g. signaldb-cli) as a *history* source if Desktop is/was linked. signald is dead. |
| **History** | ⚠️ **Link-time forward only.** Signal's official history-transfer to new linked devices (Desktop 7.42+) cannot be received by signal-cli (open issue #1708). The spec's 90-day Signal backscan is **not achievable** with signal-cli alone; partial mitigation via the Signal Desktop DB fallback. **→ Link signal-cli as early as possible** so the assistant accumulates its own archive. |
| **Auth** | QR link from phone; local key store in signal-cli's data dir (whole dir treated as a secret). |
| **Receipts** | Delivery receipts sent automatically on receive (unavoidable, looks identical to the phone's); read receipts only with explicit opt-in flags we never use. |
| **Cost** | $0. |
| **Risks** | signal-cli must be kept current — Signal enforces protocol deadlines (e.g., March 2026 SPQR post-quantum ratchet unregistered outdated clients). Add `brew upgrade signal-cli` to the maintenance loop. ToS has no third-party-client ban; no 2025–26 ban reports found. Daemon mode has long-run bugs (CPU spin, DNS-at-boot) — another reason for cron receive. **Risk accepted by Mark (interview).** |
| **Flag** | **VERIFIED** (release cadence, issue #1708 no-history, receipt semantics from man page, 45-day unlink, SPQR enforcement issue). |

Citations: <https://github.com/AsamK/signal-cli/releases>; <https://github.com/AsamK/signal-cli/issues/1708>; <https://support.signal.org/hc/en-us/articles/360007320551>; <https://github.com/AsamK/signal-cli/issues/1993>.

---

## 4. Call transcripts

### 4.1 Google Meet (work — Business Standard confirmed)

- **License:** Meet transcription is included in **Business Standard and above** — confirmed available (interview: plan is Business Standard). Transcription is on by default, started per-meeting by a participant; on Business Standard the admin cannot even disable it. **VERIFIED.**
- **Recommended:** Meet REST API v2 (`conferenceRecords.list` → `transcripts.list/get` → `docsDestination`) then export the transcript Google Doc via Drive API (`files.export`). Accessible via `gws meet` + `gws drive`. Scope: `meetings.space.created` (or `.readonly`).
- **Fallback:** Skip the Meet API — watch the organizer's Drive **"Meet Recordings" folder** via the Drive Changes API with only `drive.readonly`.
- **Limits/risks:** API returns only conference records the authenticated user can access — organizer-centric; meetings Mark merely attended may not be retrievable (the transcript Doc lives in the organizer's Drive). Transcript *entries* endpoint purges ~30 days post-meeting; the Doc persists — the durable pipeline is Doc-via-Drive. **VERIFIED** (structure); ASSUMED (30-day entry purge detail).

Citations: <https://support.google.com/a/answer/12076932>; <https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts>; <https://support.google.com/meet/answer/12849897>.

### 4.2 Zoom (Mark's own paid account — S2S app confirmed possible)

- **Recommended:** **Server-to-Server OAuth app** on Mark's Zoom account (10-min setup, no marketplace review; confirmed he administers the account). Enable cloud recording + "Create audio transcript". Poll `GET /v2/users/me/recordings` (or `recording.transcript_completed` webhook) → download `file_type: "TRANSCRIPT"` VTT. Optional: AI Companion `GET /meetings/{id}/meeting_summary`.
- **Limits:** Pro license is sufficient (has it). Built-in transcript is **English-only**; processing lag up to ~2× meeting length; 10 GB cloud storage/licensed user — recordings silently stop when full (add a cleanup job / storage check).
- **Hard boundary:** **Meetings Mark attends but does not host have no API path — verified negative** (recordings/transcripts belong to the host's account; open Zoom devforum confirmation May–June 2026). Route: §4.4 local capture.
- **Flag:** **VERIFIED.**

Citations: <https://developers.zoom.us/docs/api/meetings/>; support.zoom.com KB0065911/KB0067670; <https://devforum.zoom.us/t/143615>.

### 4.3 Microsoft Teams — decision: local capture, no Graph investment

- Graph transcript API requires the **meeting tenant's** admin consent (`OnlineMeetingTranscript.Read.All`) plus tenant toggles; consumer/attendee access: not supported. Mark attends Teams meetings in other orgs' tenants → **not feasible**, and per interview the standard route for Teams meetings is **§4.4 local capture** (manual VTT download from Recap remains an ad-hoc option when the organizer shares it). Positive note: the transcript APIs are no longer metered (Aug 2025) and delegated access works for any invitee **if** a tenant ever grants it — revisit trigger, not a plan. **VERIFIED.**

Citations: <https://learn.microsoft.com/en-us/graph/api/calltranscript-get>; <https://learn.microsoft.com/en-us/graph/teams-licenses>.

### 4.4 Local audio capture + STT (the universal fallback; always manual per spec)

| Component | Recommended | Fallback | Notes |
|---|---|---|---|
| Capture | **Core Audio process taps** (macOS 14.4+; e.g. `audiotee` CLI, MIT) — system audio via tap + mic as second stream; keep two tracks (better diarization) | **Audio Hijack 4** ($69, driverless, JS/Shortcuts automation); BlackHole 2ch + ffmpeg (free, clunky) | Permission: "System Audio Recording Only" + mic — one-time, **no monthly re-approval nag** (that's screen capture; avoid ScreenCaptureKit). ⚠️ A denied tap yields **silence, not an error** — pre-flight a 1-s capture and check for non-silence. **VERIFIED** (API, permission class); ASSUMED (no-nag). |
| STT | **whisper.cpp** (`brew install whisper-cpp`, Metal) with `large-v3-turbo-q8_0` | **parakeet-mlx** (≈10× faster, English/EU languages only); `yap` (Apple SpeechAnalyzer, macOS 26, ~45× RT); API: AssemblyAI ~$0.15–0.17/hr or ElevenLabs Scribe $0.22/hr (both include diarization) | Whisper hallucinates on silence/music. Bench a 10-min sample on this machine before committing. **VERIFIED** (tools/prices); ASSUMED (RTF figures). |
| Diarization | **senko** (CoreML/ANE, very fast) or pyannote community-1 (CPU, HF token) | Hosted (pyannoteAI ~€0.09–0.15/hr); trailofbits/scribe bundles parakeet+pyannote | The weak link locally; two-track capture reduces the need. **VERIFIED** (existence); ASSUMED (quality/speed). |
| Meeting bots | Not planned. Noted for completeness: Recall.ai ~$0.65/meeting-hr; visible bot in roster; Teams will gate external bots behind "Unverified" labels (GA ~June 2026) | — | **VERIFIED.** |
| Legal | ~11 US states require all-party consent for recording; assume strictest law on interstate calls; announce when in doubt. Not legal advice. | — | **VERIFIED** (state lists). |

### 4.5 Voice input (EOD voice dump) — user pick confirmed

- **Primary: VoiceInk** ($25–49 one-time, or free GPL-3 build; local whisper.cpp/Parakeet; global hotkey; pastes into any app including the Claude Code prompt; requires Apple Silicon + macOS 14.4+). **VERIFIED** product facts; chosen in interview.
- **Fallback: Handy** (free, MIT, offline).
- Practice notes: for dumps >5 min, record-then-transcribe (MacWhisper-style file drop or whisper-cli) beats live dictation; dictate into the Claude prompt or `$EDITOR`, never a bare shell.

---

## 5. Docs, code, design activity

### 5.1 Google Drive (work + personal) — see §1 auth
- **Recommended:** `gws drive` / Drive REST. Sync loop: **Changes API** (`changes.getStartPageToken` → `changes.list`, cursor in state layer). Human-readable "what changed today": **Drive Activity API v2** (`drive.activity.readonly`). Docs export via `files.export` (10 MB limit; use `exportLinks` beyond). Folder exclusions (§7 of spec) enforced by path-prefix filtering in the tooling layer before any content fetch. **VERIFIED.**

### 5.2 OneDrive (personal) — see §2
- Graph `/me/drive/root/delta` with cursor in state; `Files.Read`. Local-sync-folder scan as fallback. **VERIFIED.**

### 5.3 GitHub
- **Recommended:** `gh` CLI (already authenticated as `markfrommn` — verified locally). `gh search issues --assignee=@me --state=open`, `gh search prs --review-requested=@me`, `gh api /notifications`, per-repo `GET /repos/{o}/{r}/commits?author&since` for EOD.
- **Gotchas (VERIFIED):** Notifications API accepts **classic PATs only** (`notifications`/`repo` scope) — the current keyring token has `repo` ✓; the user-events feed has 30 s–6 h latency and a 300-event/30-day cap — use per-repo commit queries for EOD accuracy, not the events feed.
- Limits: 5,000 req/hr core / 30 req/min search — ample. Cost $0.

### 5.4 Figma
- **Recommended:** REST API with a **scoped PAT** (`projects:read`, `file_versions:read`, `file_comments:read`, `file_metadata:read`), daily polling sweep: team projects → project files (`last_modified`) → for changed files, `/versions` + `/meta` + `/comments`; persist last-seen version IDs. Works on free/Starter, $0.
- **Honest limitations (VERIFIED):** no activity-feed or "recent files" endpoint; **Drafts are invisible to the API** (only files in projects); PATs expire at **90 days max** (rotation chore — put it in the maintenance loop); webhooks need Professional+ and a public endpoint (not the baseline); team IDs configured manually from URLs. → Figma's EOD signal will *undercount*; treat as best-effort.
- Rate limits: Nov 2025 tiered regime; hot endpoints ~25/min on Starter — fine for daily polling.

Citations: <https://developers.figma.com/docs/rest-api/rate-limits/>; <https://github.com/figma/rest-api-spec>.

### 5.5 Local filesystem (whitelist model per spec §7.2)
- **Primary: git repo scan** — `find <whitelisted dirs> -maxdepth 4 -type d -name .git` then `git -C <repo> log --since=midnight` + `status --porcelain` + `stash list`. **Benchmarked on this machine: 123 repos discovered in 26 ms; day's log across all in 2.2 s.** Prefer unfiltered `--since` over `--author` (author-email drift). No daemon.
- **Secondary: `mdfind`** (`kMDItemContentModificationDate >= $time.today`, `-onlyin` per whitelist entry; 0.2 s locally) for non-repo files; post-filter `node_modules|\.git/|dist/`. Auto-fallback to `find -newermt` with `-prune` (1.1 s) if Spotlight index is unhealthy (`mdutil -s`).
- **Rejected:** fswatch/FSEvents daemons (~150 MB RAM at this tree size, babysitting) — daily/EOD granularity doesn't need them.
- **TCC note:** keep the whitelist outside TCC-protected dirs where possible; if `~/Documents`/`~/Desktop` must be scanned from launchd, grant FDA to a stable wrapper app, not `/bin/bash`; mdfind **silently omits** TCC-filtered results. **VERIFIED locally.**

---

## 6. Notifications (pluggable channel layer)

| Channel | Status | Mechanism | Cost | Notes |
|---|---|---|---|---|
| **Telegram bot** | **Initial sole channel (user decision)** | BotFather bot + `sendMessage` to Mark's chat_id (obtain via `getUpdates` after Mark `/start`s the bot once — bots cannot initiate) | $0 | No breaking Bot API changes 2025–26; ~1 msg/s per chat limit irrelevant; handle occasional 429/5xx with Retry-After. **VERIFIED.** |
| Pushover | Documented future channel | HTTPS POST; "emergency priority" retries until acknowledged, bypasses quiet hours | $4.99 one-time | Functionally better than SMS for urgency. **VERIFIED.** |
| iMessage-to-self | Documented future channel | `osascript` → Messages.app to own Apple ID | $0 | One-time TCC Automation approval from GUI session; breaks if Mac asleep; AppleScript timeout regressions on Tahoe. **VERIFIED** (scriptability); ASSUMED (no Apple ID flagging at low volume). |
| Carrier SMS | **Dropped** | — | — | Twilio requires A2P 10DLC even for self-texts (~$19 setup + ~$3.5/mo + 1–2 wk vetting); email-to-SMS gateways dead/dying (AT&T June 2025, T-Mobile Dec 2024, Verizon by Mar 2027). **VERIFIED.** Revisit only if a true SMS requirement emerges. |

The channel abstraction (spec §3.5) gets one interface + per-channel adapters in config; Telegram ships first, others are config additions.

---

## 7. Runtime & scheduling

### 7.1 Scheduler: launchd LaunchAgents (not cron)
- `StartCalendarInterval` **coalesces missed firings into one catch-up run on wake** — right semantics for the daily briefing and weekly review. Express the 15-min scan as a calendar array (`:00/:15/:30/:45`), because `StartInterval` *skips* runs missed during sleep. Apple deprecates cron; cron also needs FDA and never catches up. **VERIFIED** (local man page + docs).
- Plist hygiene: set PATH/WorkingDirectory explicitly (launchd doesn't source the shell profile), `StandardOutPath`/`StandardErrorPath` logs, `launchctl bootstrap gui/$(id -u)`. Persist a last-successful-run timestamp per pipeline in the state layer so catch-up runs cover their gap.
- **Lid-closed gap:** a sleeping Mac runs nothing. Optional backstop for the 8 am briefing: a Claude **Routine** (cloud, 1-hr floor) that emails the briefing — note it *cannot write the local vault*; vault copy would backfill on wake. Decision deferred to Phase B.

### 7.2 Execution model: hybrid (user decision)
- **Mechanical 15-min scans:** small Python/TS scripts calling the Messages API directly (**Haiku 4.5**, cached system prompt, structured outputs) for fetch/filter/urgent-classify. Estimated **$5–20/mo**. No subscription-policy exposure; emit JSONL/markdown "inbox" files for the agentic layer.
- **Judgment-heavy passes** (hourly triage narrative, briefings, EOD interview, weekly review): **`claude -p` / Claude Agent SDK** under the **Max subscription** (current June-2026 policy: SDK/`-p` draw from subscription limits). Use `--output-format json` and log `total_cost_usd` + `session_id` per run; `--allowedTools` for unattended discipline.
- **Policy hedge (important):** Anthropic *paused* (not cancelled) a plan to split SDK usage into separate metered credits; weekly limits were introduced specifically to curb 24/7 background use. → Design every scheduled entry point to switch to `ANTHROPIC_API_KEY` billing with a one-line config change.

### 7.3 Secrets
- **macOS Keychain (`security` CLI, built-in — no install).** Config layer stores `keychain://service/account` references (service fixed to `mark-claw-<profile>`, account a flattened `<item>-<field>` slug — one keychain item per secret value); wrapper scripts resolve at runtime via `security find-generic-password -s <service> -a <account> -w`. Items live in the default **login keychain** (already unlocked for the whole login session, so unattended launchd agents can read them without an extra unlock step) and are created with `security add-generic-password -A` so no per-app access prompt ever blocks a headless run. Items created via the classic `security` CLI are local to this keychain only — the CLI does not set `kSecAttrSynchronizable`, so they do **not** follow to a second Mac via iCloud Keychain. Cross-device recovery is the age-encrypted backup below, not iCloud sync. Satisfies spec §10 "credential references, not raw secrets where avoidable". Token files that must exist on disk (OAuth token caches, Telethon session, signal-cli data dir) live in the state layer, chmod 600, and are treated as rebuildable-by-relink secrets.
- **`mclaw secret` CLI wrapper (arrives with B1).** A thin `mclaw_core` helper (`mclaw secret set/get/list`) around `security add-generic-password`/`find-generic-password` enforces the naming convention and the `-A` flag consistently; Stage A's initial credential setup runs raw `security` commands directly (no CLI wrapper exists yet), and later steps switch to `mclaw secret`.
- **Backup/recovery.** Keychain has no cross-device recovery kit. `mclaw secret export` writes an age-encrypted blob of the full credential set to `~/.local/state/mark-claw/<profile>/secrets/backup.age`, refreshed after each provisioning or rotation; the age passphrase/recipient key is held by Mark and never written to disk.
- **Pattern upgrade (from §12 framework research):** adopt Vellum's credential-isolation shape — secrets are resolved only inside the thin fetch/action wrappers (the code that talks to provider APIs), never passed into agent prompts or `claude -p` context. The agentic layer sees data files and wrapper commands, not tokens.

### 7.4 Local status dashboard (added 2026-07-05 — user decision)
- **Requirement:** a centralized view of system health — per-pipeline last-run time + success/failure, triage counts per bucket, alert history, pending review-queue/approval items, source-cursor freshness.
- **Recommended:** **bespoke and local** — rendered from the state layer (which is already files): either a generated static HTML page or a tiny localhost-only server; loopback-bound, no auth exposure, no external framework. A generated `STATUS.md` in the vault is a cheap complementary mobile view via Obsidian Sync (optional, Phase B decides).
- **Rejected:** adopting a Claw-family runtime for its dashboard (the attraction that prompted §12) — the dashboard alone doesn't justify importing an agent framework's security surface (§12.1).
- **Phase B input:** design the state-layer file formats so the dashboard is a pure read-only renderer over them; no pipeline may depend on the dashboard.

Citations: <https://code.claude.com/docs/en/headless>; <https://support.claude.com/en/articles/15036540>; launchd scheduling <https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html>.

---

## 8. Constraint compliance map (spec §11 → tooling)

| Constraint | Where enforced |
|---|---|
| Never hard-delete | Gmail: wrapper deny-lists `messages.delete` (`messages.trash` only, for the 30-day junk aging); Graph: never call DELETE, moves only. Code-level invariant + audit log. |
| Read-only on chat | No write-capable calls in any chat poller; Telegram/Signal/MCP tools with send/delete are forked/allowlisted out. Verified: no read path marks messages read. |
| No autonomous recording | Local capture is a manually-invoked CLI only; no scheduler entry may reference it. |
| Per-source exclusion enforcement | Exclusions applied in the fetch layer (before content enters any pipeline): Slack/Mattermost channel IDs, Telegram/Signal chat IDs, Drive/OneDrive folder prefixes, meeting/series IDs, local whitelist. Blocked sources are never fetched at all. |
| 3-way tooling/config/state split | Tooling: repo scripts/skills. Config: `~/.config/mark-claw/mark/` (accounts, `keychain://` refs, rules, exclusions, channels, schedules). State: `~/.local/state/mark-claw/mark/` (cursors: Slack conversation cursors, Mattermost `since`, Telegram message IDs, Drive page tokens, Graph delta links, GitHub since-timestamps; token caches; changelogs). |
| Pluggable notifications | Channel interface + adapters; Telegram first (§6). |
| Graceful degradation | Every fetcher is independent; briefing assembles from whatever "inbox" files exist and notes gaps. |

---

## 9. Action items for Mark (all self-service; no employer/third-party approval needed)

| # | Action | Blocks |
|---|---|---|
| 1 | Add `mark@convoydefense.ai` + `markfrommn@gmail.com` credentials to `gws` (use the `gws-multi-account` skill; needs `/reload-plugins`); publish/trust the OAuth client appropriately (Production status for the consumer account) | All Google pipelines |
| 2 | Entra app registration in jumpweb.net tenant; grant delegated scopes; run device-code flow once | O365 mail/calendar/OneDrive |
| 3 | Create Slack internal app with user scopes; install to workspace | Slack ingestion |
| 4 | Enable PATs on Mattermost server; issue PAT. **Also confirm server edition** (Entry-tier 10k-message visibility cap) | Mattermost ingestion |
| 5 | my.telegram.org → api_id/api_hash; BotFather → bot token; `/start` the bot from your account | Telegram ingestion + alert channel |
| 6 | `brew install signal-cli`; link as secondary device — **do this early; history starts at link time** | Signal ingestion |
| 7 | Zoom marketplace: create S2S OAuth app; enable cloud recording + "Create audio transcript" in settings | Zoom transcripts |
| 8 | Figma: create scoped PAT (90-day expiry — calendar the rotation); collect team IDs from URLs | Figma signal |
| 9 | Install VoiceInk; `brew install whisper-cpp`; pick/install a tap-capture CLI (audiotee) and pre-grant System Audio Recording + mic permissions | EOD voice + local capture |
| 10 | Confirm Max subscription active; create a low-limit `ANTHROPIC_API_KEY` for the mechanical scans; store all secrets in the macOS Keychain (`mark-claw-mark` service) | Runtime |

---

## 10. Open risks

1. **Anthropic background-usage policy** (highest impact): the paused SDK-billing split may return; weekly limits already target 24/7 patterns. Mitigation is architectural (one-line billing switch, hybrid design keeps the high-frequency tier off-subscription).
2. **Signal history is unrecoverable** before link time; signal-cli must be kept updated against protocol deadlines or the linked device stops receiving.
3. **Attended-not-hosted Zoom/Teams meetings** have no API path, permanently — local capture is the plan, and it carries consent etiquette/law considerations.
4. **Gmail no-send/no-delete is a code invariant**, not a platform guarantee — a tooling bug could theoretically send; mitigate with wrapper-level deny-lists + tests.
5. **Figma undercounts** (drafts invisible); EOD design signal is best-effort.
6. **Telegram/Mattermost read-only is discipline, not scoping** (full-permission tokens).
7. **Slack policy drift**: internal-app exemption is Slack's current published stance; a reclassification would force slower polling.
8. **launchd + sleep**: a closed lid silently skips the 15-min cadence; catch-up semantics cover daily/weekly jobs but real-time alerting has a gap when the Mac sleeps. (Phase B: consider `caffeinate`/power settings or the cloud-Routine backstop.)

---

## 11. Revisit triggers (what invalidates each choice)

| Choice | Revisit if… |
|---|---|
| `gws` CLI as Google access path | The tool proves unmaintained/broken for a needed endpoint, or its OAuth client can't carry `gmail.modify` for all three accounts → fall back to direct REST scripts with our own client. |
| Hybrid Max + API runtime | Anthropic reinstates the SDK-billing split or weekly limits bite → shift agentic tier to API billing (designed-in switch); re-evaluate monthly cost. |
| Slack internal xoxp app | Slack extends the non-Marketplace rate limits to internal apps, or the workspace enables app approval / restricts user tokens → fall back to `search.messages` or bot-in-selected-channels; worst case, reduce polling scope. |
| Telethon user session | Any account warning from Telegram (@SpamBot flag, FloodWait escalation) → immediately drop polling frequency; consider Bot-API-only for groups the bot can join, or exit. |
| signal-cli | signal-cli lags a Signal protocol deadline or issue #1708 (history transfer) gets fixed → in the first case pause the poller until updated; in the second, do a proper history backfill. |
| Telegram-only alerting | A missed urgent alert matters in practice → add Pushover ($5, emergency priority) as the second channel; it's a config addition. |
| Meet transcripts via Meet API | Attended-but-not-organized work meetings turn out to be the common case (organizer-centric API misses them) → switch primary to Drive "Meet Recordings" folder watching + ask organizers to share, or local capture. |
| Zoom S2S transcripts | Zoom reshuffles the AI Companion/recordings API surface (brand retired 2026, churn ongoing) → VTT-from-cloud-recording is the stable path; re-verify endpoints before Phase 3 build. |
| whisper.cpp STT | Local benchmark on a real meeting shows unacceptable accuracy/speed → parakeet-mlx (English) or AssemblyAI/ElevenLabs API (~$0.15–0.22/hr with diarization). |
| Figma polling | Team upgrades to Professional+ → add webhooks (FILE_VERSION_UPDATE) for better fidelity. |
| launchd-only scheduling | The Mac is routinely asleep at 8 am → add cloud Routine backstop for the briefing (email delivery, vault backfill on wake). |
| O365 Graph direct | Microsoft ships an official consumer/M365 MCP that matches our constraints → reconsider; meanwhile Softeria MCP is the maintained fallback. |
| Mattermost Entry-tier cap | Server is on Entry and history visibility matters → upgrade edition or accelerate backfill/archival. |
| Bespoke runtime (no agent framework — §12) | ZeroClaw or IronClaw reaches a stable 1.x with ~6 months of clean CVE record, **plus** first-class M365/Graph support, **plus** a sanctioned Claude-subscription provider path → re-run the §12 evaluation. Timebox: reassess the landscape ~Q1 2027 regardless. |
| No two-way Telegram interface | Mark wants phone-side interaction with the assistant after living with one-way alerts → design exists to add it (channel layer is pluggable); PicoClaw's Go gateway (§12.2) is a candidate vendored slice for the bot-protocol plumbing. |
| Vellum ignored as competitor | Vellum's personal-assistant product ships M365 + Signal support and stays MIT/self-hostable → re-compare against the bespoke build's remaining backlog. |

---

## 12. Agent-framework evaluation (addendum, 2026-07-05)

Evaluated whether an off-the-shelf personal-agent runtime should replace or augment the bespoke launchd + wrappers + `claude -p` architecture: **OpenClaw, ZeroClaw, IronClaw, PicoClaw, NemoClaw, Sai, Hermes Agent, Vellum**. Method: 3 parallel Sonnet web-research passes (2026 primary sources; GitHub stats pulled via API rather than scraped summaries — the Claw space is meme-fueled and full of same-named repos and SEO content) + user interview.

**Decision (Mark, 2026-07-05): stay bespoke.** No framework becomes the runtime or the trust boundary. Borrow specific design patterns (§12.3); revisit per the triggers in §11. Mark's stated bar: frameworks are acceptable only if the hard constraints are structurally enforceable and the attack surface is acceptable — none cleared it; his tolerance for a full re-base was on the table and was still declined on the merits.

### 12.1 Cross-cutting findings (why bespoke wins)

1. **Nobody covers the two load-bearing integrations.** Microsoft 365/Graph is undocumented in *every* candidate; `claude -p`/Max-subscription as a model provider is absent (IronClaw, PicoClaw, NemoClaw, Sai, Vellum), contested in the project's own issue tracker (ZeroClaw #2990 — declined for ToS reasons despite marketing claims), overage-credits-only (Hermes Agent), or policy-unstable (OpenClaw). Both stay custom work under any runtime — so a framework buys channel plumbing we largely already solved, not the hard parts.
2. **The hard guarantees remain our code regardless.** No framework structurally enforces never-send/never-delete for email or the blocked-source output guarantee; at best generic tool deny-lists wrap community-authored skills that bundle read+send+delete. A bespoke API client that simply never implements the send/delete endpoints is the stronger guarantee.
3. **Security hard bar.** Every candidate is ≤5 months old and/or carries a 2026 CVE cluster: OpenClaw (~42k exposed unauthenticated gateways found by scanners; RCEs CVE-2026-25253, CVE-2026-30741; poisoned ClawHub skills per Unit42/Cisco), Hermes Agent (9 CVEs in 4 days, Mar 2026), IronClaw (1,305 open issues, pre-1.0), ZeroClaw/PicoClaw (pre-1.0, weekly churn). A system holding tokens for 4 email accounts + 4 chat platforms doesn't adopt that surface for convenience.
4. **Architecture conflicts.** OpenClaw's single `~/.openclaw/` blob vs our 3-way split; scheduling inside a must-stay-up gateway daemon vs stateless launchd invocations; IronClaw's PostgreSQL+pgvector dependency vs files-over-databases; Vellum's own memory stack vs the Obsidian vault.

### 12.2 Per-tool verdicts

| Tool | What it is (verified mid-2026) | Verdict | Decisive factors |
|---|---|---|---|
| **OpenClaw** (MIT, OpenClaw Foundation, ~250–300k★) | Node gateway + channels + ClawHub skills; the ecosystem's center of gravity | **No** | Worst 2026 security record of the set; email = alpha community skills; config-model conflict; contested subscription path. Its cron/heartbeat design is genuinely good — pattern borrowed (§12.3). |
| **ZeroClaw** (`zeroclaw-labs`, Rust, Apache-2.0, 32k★, v0.8.2) | Single-binary rewrite; OS sandboxes (Landlock/Seatbelt), tool receipts, SQLite+FTS5 memory, cron+heartbeat | **No (best-shaped of the Claws — primary revisit candidate)** | 5 months old; ≥3 unrelated repos share the name (supply-chain hazard); no Signal, no M365; Claude-subscription support contradicted by its own issue #2990. |
| **IronClaw** (NEAR AI, Rust, Apache-2.0, 12.5k★, v0.29.1) | "Agent OS": WASM capability-sandboxed tools, credential injection at host boundary, Signal channel, Gmail extension, best-documented routines engine | **No (right enforcement *shape* — pattern borrowed)** | Requires PostgreSQL+pgvector; TEE guarantee is hosted-cloud-only; 1,305 open issues; no Slack/M365; agent-writes-its-own-WASM-tools feature enlarges the surface we must reason about. |
| **PicoClaw** (Sipeed, Go, MIT, 29.6k★, v0.3.1) | Ultra-minimal single binary, 19+ chat channels, JSONL memory | **No** | Thin security posture, no Signal/M365. Noted: its `gateway` is a candidate vendored slice if a two-way Telegram interface is ever wanted (declined for now — §12.4). |
| **NemoClaw** (NVIDIA, Apache-2.0, 21.6k★) | Not a runtime — a governance **wrapper** (default-deny egress, audit logging) that runs OpenClaw/Hermes inside it | **No** | Solves an enterprise problem we don't have; would add a framework *plus* a wrapper. Its default-deny-egress idea is echoed in our fetch-layer exclusion choke point. |
| **Sai** (Simular AI, $20–500/mo) | GUI computer-use agent; cloud remote desktop by default | **No** | Wrong shape (drives UIs; our work is API-driven triage); primary mode puts logged-in mail/chat sessions on vendor cloud; API-key billing only. |
| **Hermes Agent** (Nous Research, MIT, ~210k★, v0.18.0) | Local-first agent framework; native Telegram/Slack/Signal/Email gateways; NL cron; self-improving skills | **No (closest architectural cousin — reference material)** | Pre-1.0 with a 9-CVE cluster (Mar 2026); Claude-subscription mode consumes only purchased Max *overage* credits — defeats the hybrid billing plan. Its skill-accumulation pattern maps to our §9 learning loop; worth reading, not running. |
| **Vellum** (vellum.ai — pivoted 2026 from LLMOps to consumer "Personal Intelligence"; assistant repo MIT) | Direct competitor product: email triage, briefings, cron/RRULE scheduling, self-host-on-Mac with isolated Credential Execution Service | **No (best privacy architecture seen — pattern borrowed)** | Full platform migration (own memory stack vs our vault/3-way split); no M365, Mattermost, or Signal; Claude-subscription reuse unconfirmed. Mark is open to SaaS in principle, but nothing here beats self-host. |

### 12.3 Patterns adopted into Phase B (design inputs, not dependencies)

1. **Heartbeat batching** (OpenClaw): where several 15-min polls hit the same cadence, run them as one batched sweep sharing a session/context rather than N isolated jobs — fewer invocations, shared triage context. Implement in our launchd + wrapper world.
2. **Credential isolation** (Vellum CES): secrets resolve only inside the thin provider wrappers; the agentic layer (prompts, `claude -p` context) never sees raw tokens (§7.3).
3. **Allowlist-over-prompt enforcement** (IronClaw): every hard constraint must be a structural fact of the tool surface (endpoint never implemented / call deny-listed in the wrapper), never a prompt instruction. Already Phase A doctrine — now explicit Phase B acceptance criterion.
4. **Output-side exclusion check** (OpenClaw output filters): exclusion enforcement stays fetch-side as the primary choke point, **plus** a second output-side scan (blocked-source identifiers must not appear in briefings/notes/logs) as defense-in-depth and as the §13-success-criterion spot-check made continuous.
5. **Local status dashboard** (the ZeroClaw attraction, built bespoke): §7.4.

### 12.4 Interview decisions (this round, 2026-07-05)

| Topic | Decision |
|---|---|
| Motivation probed | General effort reduction + attraction to a centralized dashboard (ZeroClaw's) |
| Security bar | **Hard bar** — framework only if constraints structurally enforceable + acceptable attack surface |
| Cloud SaaS processing content | Open to it in principle; nothing found made a compelling case over self-host |
| Rework tolerance | Full re-base was acceptable if a framework fit — none did |
| Overall verdict | **Accept: stay bespoke; borrow patterns; revisit triggers set (§11)** |
| Dashboard | Build lightweight local (state-layer renderer, §7.4) |
| Two-way Telegram chat interface | **No** — notifications stay one-way; pluggable channel design leaves the door open (§11 trigger) |

---

## Appendix — Phase A verification log

- Local machine: macOS 26.5.1 (Tahoe), arm64. Present & verified: `gh` (authed, classic token w/ `repo`), `gws` (multi-account, services incl. gmail/calendar/drive/meet), `op`, `ffmpeg`, `docker`, `uv`, `node`, `python3`. Missing: `signal-cli`, `gcloud`, `twilio`. `gam` binary present but fails with exec-format error from non-interactive shell (alias/arch issue — irrelevant, GAM not selected).
- Filesystem benchmarks run live (find/git/mdfind timings in §5.5).
- Interview decisions (2026-07-05): work GWS = convoydefense.ai Business Standard (Mark admin; consoleops@ for provisioning); powderhorns.biz GWS (Mark super admin); jumpweb.net = own M365 tenant (Mark global admin); markfrommn@gmail.com added as 4th mail account; Slack paid + open app installs; Mattermost self-hosted (Mark admin); Telegram+Signal user-account automation risk accepted; Zoom own paid account (S2S OK), mostly attends others' meetings; Teams → local capture; Telegram-only initial alerting; Max+API hybrid runtime; VoiceInk for voice input.
- Web research: 5 parallel research passes with primary-source fetches; key adjudication: Slack rate-limit page re-fetched live to resolve conflicting claims about 2026 enforcement (current page: existing non-Marketplace installs not subject to new limits; internal apps exempt).
