# MARK-CLAW — Personal AI Assistant System Specification

**Status:** Draft v2 — requirements phase (no tooling decisions yet)
**Owner:** Mark Gooderum (mark@powderhorns.biz)
**Date:** 2026-07-05
**Reference pattern:** B.U.I.L.D. self-improving-system framework (`raw/oneshot/yt-self-improving-system.txt`)

---

## 1. Overview & Goals

Integrate AI into day-to-day workflow beyond coding. Three pillars:

1. **Communications under control** — one-time cleanup of all inboxes, then continuous triage that keeps them prioritized and near-zero; chat and call transcripts monitored and captured alongside email.
2. **Daily assistant rituals** — a morning briefing that plans the day and an end-of-day capture that records it.
3. **Personal PIM/wiki** — an Obsidian-compatible knowledge vault that accumulates context (people, projects, decisions, insights) and gets smarter over time via a bucketed self-improvement loop.

The system follows the B.U.I.L.D. pattern: a knowledge **base** (raw + wiki layers), a **bulk upload** of historical data, continuous **inflow** pipelines, an improvement **loop** with human-in-the-loop approvals, and pragmatic **drive** (run it, don't over-engineer it).

These requirements are a starting point, not canonical — the system is expected to evolve through its own learning loop.

---

## 2. Information Sources

### Email accounts (3)
| Account | Provider | Role |
|---|---|---|
| Work | Google Workspace | Work mail + work calendar |
| Personal #1 | Google Workspace | Personal mail |
| Personal #2 | Office 365 | Personal mail + personal calendar |

### Chat sources (4)
| Source | Notes | Initial history scan | Ongoing |
|---|---|---|---|
| Slack (work) | Workspace only ~5 months old (since mid-Feb 2026) | Full history | ~15-min polling |
| Mattermost | Low volume — one other person plus self-notes | Full history | ~15-min polling |
| Telegram | Personal | Last 90 days | ~15-min polling |
| Signal | Personal | Last 90 days | ~15-min polling |

### Call transcript sources
- **Automated:** Google Meet, Zoom, Microsoft Teams meeting transcription.
- **Semi-automated:** local audio capture + speech-to-text transcription (always manually initiated) for calls/meetings the automated path can't cover.

### Calendars feeding the day plan
- Work Google Calendar
- Personal O365 Calendar
- (Personal Google Calendar excluded from the day plan for now)

### Task sources feeding the day plan
- GitHub assigned issues / PRs awaiting review
- Task notes in the vault (e.g., `tasks.md`, waiting-on tracker)

### Activity & document sources
- **GitHub** — commits, PRs, reviews (feeds EOD dump and morning plan)
- **Local development** — repo/file activity on this machine (feeds EOD dump)
- **Figma** — design activity (feeds EOD dump)
- **Google Drive** (work) — documents (vault ingestion, EOD/morning signals)
- **OneDrive + Google Drive** (personal) — documents (vault ingestion)

All sources are subject to the sensitivity/exclusion controls in §7.

---

## 3. Email Triage System

### 3.1 Classification buckets

| Bucket | What lands here | Treatment |
|---|---|---|
| **High priority** | (a) Emails from real people that are not cold calls — previous contacts, or messages referencing genuine work/personal context. (b) Automated emails that look like a problem (UptimeRobot down alerts, failed payments, security warnings). | Stays in inbox. Problem-class mail also triggers immediate push notification. High-priority mail clearly needing a response gets an auto-created reply draft. |
| **Middle priority** | Invites; 1:1 emails that don't pass the high-priority bar. | Labeled, surfaced in briefings, archived out of the inbox once noted. |
| **Bulk-useful** | Real newsletters and other genuinely useful bulk mail. | Filed + summarized: a 1–2 line "worth reading?" bullet in the next briefing, plus key claims/insights extracted into the vault as reference notes. |
| **Outreach** | Gray-area human outreach: recruiters, vendor SDRs who did their homework, agencies, event/conference invites from real people. | Filed under an Outreach label, never in the inbox. Summarized in the weekly review digest. **Kept indefinitely** — feeds contact dossiers and may matter later. |
| **Junk** | True junk: templated mass outreach, spam that got through, dead subscriptions. | Filed under a Junk label, archived. After **30 days**, moved to the provider's native Trash/Spam so the provider's own auto-purge performs deletion. |

Cold-call detection is context-driven: the vault's contact dossiers and email history mining (see §8, §12) provide the "have we interacted before / does this reference real context" signal.

### 3.2 End state & autonomy

- **End state:** prioritized, near-zero inboxes. Only items needing action remain in the inbox; everything else is labeled and archived. The assistant maintains this continuously.
- **Autonomy:** the assistant labels, moves, and archives freely. **It never hard-deletes anything.** The only path to deletion is moving 30-day-aged junk to provider Trash/Spam, where the provider's retention policy does the deleting.
- **Rules model:** one common rule set shared across all three accounts, with per-account overrides where needed (expected to be limited). Rules live in human-readable form so they can be inspected and hand-edited (see §10 — rules are part of the configuration layer).

### 3.3 One-time cleanup (bootstrap task)

- Scope: **everything currently in each inbox, full depth** — regardless of age.
- Already-archived / already-filed mail is left alone.
- Every message is classified into a bucket per §3.1 and labeled/archived accordingly.
- Output: a cleanup report per account (counts per bucket, notable finds, proposed rules learned from the pass).

### 3.4 Ongoing triage cadence

- **Every 15 minutes:** lightweight urgent scan — looks only for problem-class mail (and anything matching urgent patterns). Hits trigger immediate push notification.
- **Hourly:** full classification sweep over new mail across all accounts.
- Briefings (§6) consume the triage results; triage does not wait for briefings.

### 3.5 Urgent alerting

- Problem-class emails (outages, security warnings, failed payments, and similar) trigger an **immediate push notification**.
- **Initial channels: SMS and Telegram.** The channel layer must be pluggable/configurable so other channels (push apps, Slack, etc.) can be added later without reworking the alert logic.
- Alerts include a one-line summary and enough context to act (what, which service/account, link/reference to the message).
- The urgent scan also covers chat sources — see §4.

### 3.6 Misclassification feedback loop

- **Primary mechanism:** the user moves or relabels a message in the mail client (e.g., drags it out of Junk, changes its label). On its next sweep the assistant detects the change, corrects the message's classification, and updates its rules (subject to the learning-loop buckets in §9).
- Each correction becomes a learning example. Rescues from Junk are also listed in the weekly review digest so misclassification rates are visible.

### 3.7 Reply drafting

- For high-priority emails that clearly need a response, the assistant creates a **draft** in the relevant mail account, written in the user's voice (informed by writing-style data from bulk ingest).
- **Drafts are never sent automatically.** Briefings link to pending drafts.

---

## 4. Chat Monitoring

Chat is monitored, not filed — the assistant is **read-only** on chat platforms (it never posts, reacts, or marks messages read). Unlike email there is no bucket/label taxonomy; chat feeds the same downstream consumers as email.

### 4.1 Roles

1. **Briefing highlights** — important DMs, @-mentions, and threads awaiting the user's reply surface in morning/EOD briefings alongside email highlights.
2. **Urgent alert source** — the 15-minute urgent scan includes chat: problem-class messages (outage channels, incident threads) and direct pings from key people trigger the same push notifications as problem-class email (§3.5).
3. **EOD activity + vault enrichment** — chat activity feeds the daily activity record; conversations enrich contact dossiers and project notes.
4. **Waiting-on tracking** — chat threads where the user owes or is owed a reply feed the waiting-on tracker (§8.1), same as email.

### 4.2 Ingestion

- **Initial history scan** (part of bulk ingest, §12 Phase 1), per-source depth as specified in §2: Slack and Mattermost full history; Telegram and Signal last 90 days.
- **Ongoing:** near-real-time polling every ~15 minutes across all four sources. Volume is low enough that the urgent check and full processing happen in the same pass.
- Chat content lands in the vault per the normal raw/wiki pattern, subject to exclusions (§7).

---

## 5. Call Transcripts

### 5.1 Capture policy

- **Work meetings: opt-out.** Meetings on the work calendar (Google Meet, Zoom, Teams) are transcribed by default; specific meetings or recurring series can be excluded (§7).
- **Everything else: opt-in.** Calls/meetings outside the work calendar are captured only when explicitly requested.
- **Semi-automated path:** local audio capture + speech-to-text for calls the platforms can't transcribe (phone calls, in-person meetings). Always manually initiated — never records on its own.

### 5.2 Persistence & downstream use

- **Full transcript** stored in the vault's `raw/` layer.
- **Summary note** in `wiki/` — key points, decisions, and action items — linked to attendee contact dossiers and related project notes.
- Extracted action items flow into task notes and the waiting-on tracker.
- Transcripts feed the EOD activity record and future meeting-prep briefs for the same attendees.

---

## 6. Daily Assistant Workflows

### 6.1 Morning briefing

- **Schedule:** ready by **8:00am, weekdays**. Weekends get a lighter morning edition limited to urgent items needing attention before Monday.
- **Delivery:** (a) formatted email to the inbox, and (b) a dated markdown note in the vault so briefings accumulate as a searchable journal.
- **Content:**
  1. **Day plan** — today's events from work Google Calendar + personal O365 Calendar; open tasks from GitHub (assigned issues, PRs awaiting review) and vault task notes; suggested organization of the day.
  2. **Overnight comms highlights** — interesting/high-priority email since the last EOD capture, grouped by bucket; unanswered chat DMs/mentions; bulk-useful "worth reading?" bullets.
  3. **Alerts recap** — any problem-class events overnight (email or chat) and their status.
  4. **Waiting-on nudges** — stale threads where the user is owed a reply (§8.1).
  5. **Meeting prep pointers** — links to prep briefs for today's meetings (§8.2).

### 6.2 End-of-day capture

- **Trigger:** on demand — the user kicks it off when wrapping up (end-of-day time varies).
- **Model: hybrid auto-draft + voice interview.**
  1. Assistant auto-assembles a draft of the day from data sources: email and chat activity, calendar events attended, call transcripts, git commits/PRs, Figma activity, document changes.
  2. Assistant presents the draft and highlights gaps.
  3. User does a voice dump (speech-to-text; specific tool TBD in the tooling phase) and the assistant conducts a short interview focused on **gaps only**: action items, work items, decisions, loose ends not visible in the data.
  4. Result is filed to the vault as the day's activity record; extracted action items flow into task notes / waiting-on tracker.
- **Comms recap:** the EOD output also includes interesting communications (email + chat) since the morning briefing (the "repeat" digest).

---

## 7. Sensitivity & Exclusions

Cross-cutting controls that let specific sources be excluded due to sensitivity or confidentiality.

### 7.1 Two exclusion tiers

| Tier | Meaning |
|---|---|
| **Blocked** | The system never reads the source at all — no triage, no alerting, no summaries, no vault persistence. |
| **Ephemeral** | The source may be read for real-time purposes only (urgent alerting, "activity happened" signals). Its content is **never** summarized into briefings or persisted to the vault. |

Each excluded source is assigned a tier. Default for anything not listed: fully processed.

### 7.2 What can be excluded

- **Chat:** specific channels and specific contacts/conversations (any platform).
- **Documents:** specific Google Drive / OneDrive folders (exclusion inherits to subfolders).
- **Meetings:** specific meetings or recurring series (overrides the work opt-out default in §5.1).
- **Local drive: whitelist model** — inverted logic. Local filesystem scanning only touches explicitly whitelisted paths; everything else is implicitly blocked. This manages both bulk and non-standard paths.

### 7.3 Management

- Exclusion lists and the local whitelist live in the **configuration layer** (§10) as human-readable, hand-editable files.
- The assistant may **propose** exclusions (e.g., "this channel looks like HR-sensitive traffic") via the needs-sign-off bucket (§9), but never adds or removes exclusions on its own.
- Exclusion enforcement is a **hard guarantee**, not best-effort: a blocked source must not appear in any output, log, or vault artifact.

### 7.4 General privacy posture

- Beyond explicit exclusions, **no hard boundary between work and personal**: work content may be processed, summarized, and persisted in the single personal vault. The user owns the machine and accepts the mingling.
- (Revisit if employer policy or a sensitive category ever demands a broader filter.)

---

## 8. Additional Use Cases (in scope)

### 8.1 Waiting-on / follow-up tracker
Track threads (email and chat) where the user is owed a reply or was promised something. Briefings surface items going stale ("You asked Bob for the contract 6 days ago — no response"). Items originate from email/chat analysis, call-transcript action items, and EOD interviews.

### 8.2 Meeting prep briefs
Before each calendar meeting: a mini-brief with attendees, recent email/chat/thread history with them, prior meeting transcripts, related vault notes, and open items involving them. Linked from the morning briefing.

### 8.3 Contact dossiers
The vault auto-builds and maintains a note per significant person/company from email, chat, and meeting history: who they are, interaction history, commitments in both directions. Dossiers also power cold-call detection (known contact = not a cold call) and meeting prep.

### 8.4 Weekly review
A weekly rollup containing:
- Themes and time allocation for the week
- Stale action items and waiting-on items
- **Outreach digest** — summary of the week's Outreach bucket
- **Junk-rescue digest** — what was junked, what was rescued (misclassification visibility)
- **Learning-loop approvals** — batch review of pending rule/system change proposals (§9)

---

## 9. Self-Improvement Loop

Bucketed approvals, per the reference framework's middle-of-spectrum approach:

| Bucket | Examples | Handling |
|---|---|---|
| **Auto-apply** | New junk sender learned from a user relabel; corrected label; fixed wiki linkage; data-bloat cleanup | Applied immediately; recorded in a changelog the user can audit anytime |
| **Needs sign-off** | New classification rule or category; changes to purge policy; new or edited skills/pipelines; proposed exclusions (§7.3); anything that could degrade output quality | Written to a review note with approve / reject / approve-and-don't-ask-again options; the weekly review is the default approval venue |
| **More context required** | Ambiguities the system can't decide alone | Queued as questions in the same review note |

Inputs to the loop: user corrections in the mail client, in-session feedback, session-history analysis, and periodic self-audit of classification accuracy.

---

## 10. Configuration & Parameterization

A clean **3-way split** between tooling, configuration, and state:

| Layer | Home | Contents |
|---|---|---|
| **Tooling** | `mark-claw` repo | Skills, pipelines, prompts, schedulers, docs, specs. Contains **no personal data and no secrets** — cloneable and shareable as-is. |
| **Configuration** | `~/.config/mark-claw/<profile>/` | Accounts and source definitions, credential references (not raw secrets where avoidable), classification rules and per-account overrides, exclusion lists and local-drive whitelist (§7), notification channels, schedules, vault path, briefing preferences. Human-readable, hand-editable. |
| **State** | `~/.local/state/mark-claw/<profile>/` | Sync cursors ("last message seen" per source), caches, action/purge changelogs, learned classification examples, pending review queues. Rebuildable without losing configuration. |

### Profiles

- Layout is **per-profile** (`<profile>` directory); default profile is `mark`.
- **Intent: shareable tooling, single user for now.** Another person could adopt the tooling with their own config + state (their own accounts, rules, vault) without forking the repo. No second user or profile is anticipated yet, but nothing in the tooling may hard-code Mark-specific values — everything personal belongs in configuration.
- Upgrade/rebuild guarantees: tooling is upgradeable without touching config or state; state can be wiped and rebuilt (at the cost of re-scanning) without losing configuration.

---

## 11. Non-Functional Requirements & Constraints

- **No hard deletion, ever** — deletion only via provider Trash/Spam retention (§3.1, §3.2).
- **Nothing is sent autonomously** — email drafts only; the assistant is read-only on chat platforms; notifications are the only outbound messages the system originates, and they go to the user.
- **No autonomous recording** — the semi-automated audio-capture path is always manually initiated.
- **Exclusion enforcement is a hard guarantee** — blocked sources never appear in any output or artifact (§7.3).
- **Pluggable notification channels** — SMS + Telegram first; architecture must allow adding channels via configuration.
- **Human-readable rules & config** — classification rules, exclusions, and source definitions inspectable and hand-editable.
- **No personal data in the tooling layer** — all personal/secret material lives in config or state (§10).
- **Auditability** — changelog for autonomous actions and learned changes; cleanup and purge actions logged.
- **Graceful degradation** — if a source (chat platform, Figma, Drive, GitHub) is unavailable, briefings still run with what's available and note the gap.
- **Tooling-agnostic at this stage** — this spec deliberately avoids choosing MCP servers, APIs, schedulers, or STT tools; that's the next phase.

---

## 12. Phasing

**Phase 1 — Foundations: config split + vault + bulk ingest (BASE + UPLOAD)**
1. Stand up the 3-way tooling/config/state layout with the `mark` profile (§10).
2. Create the vault at `~/Documents/Obsidian/Mark-Claw` with `raw/` + `wiki/` structure and structure-enforcing documentation; set up Obsidian Sync.
3. Establish initial exclusion lists and local-drive whitelist (§7) **before** ingestion begins.
4. Bulk ingest:
   - **Email history mining** — contacts, projects, commitments, writing style → seeds contact dossiers, project notes, and cold-call detection signal.
   - **Chat history scan** — Slack + Mattermost full history; Telegram + Signal last 90 days (§4.2).
   - **Life story / goals session** — recorded (voice or typed) session; assistant interviews to fill gaps; result becomes core vault context.
   - **Local machine sweep** — whitelisted paths only; documents worth ingesting as reference material.
   - **Drive/OneDrive key docs** — pull important existing documents into `raw/`, respecting folder exclusions.

**Phase 2 — Comms triage (INFLOW part 1)**
5. Classification rules (common + per-account) and bucket taxonomy set up in all three mail accounts.
6. One-time full-inbox cleanup per account, with cleanup reports.
7. Ongoing sweeps: 15-min urgent scan (email + chat) + hourly full email triage + ~15-min chat polling; SMS/Telegram alerting; 30-day junk aging job; relabel-detection feedback loop; high-priority reply drafts.

**Phase 3 — Daily rituals + call capture (INFLOW part 2)**
8. Morning briefing (8am weekdays; urgent-only weekend edition) — email + vault delivery.
9. EOD on-demand capture — auto-draft + voice interview pipeline.
10. Call transcript pipelines: Meet/Zoom/Teams automated capture (work opt-out policy) + semi-automated audio-capture path (§5).

**Phase 4 — Extras + learning maturity (LOOP)**
11. Waiting-on tracker, meeting prep briefs, contact dossier automation, weekly review.
12. Full bucketed self-improvement loop with changelog + review-note approvals.

---

## 13. Success Criteria (~30 days after Phase 3)

1. **Inboxes stay near-zero** — all three inboxes hold only action-needed items, maintained by triage without manual weeding.
2. **Trustworthy classification** — ≤1–2 junk rescues per week; the user no longer checks Junk/Outreach folders "just in case."
3. **Briefings replace inbox scanning** — the user opens the inbox (and chat apps) to act, not to scan; morning/EOD briefings are sufficiently complete across email and chat.
4. **The vault answers real questions** — "what did we decide about X," "who was that person from Y" get answered from the vault instead of email/chat search.
5. **Nothing sensitive leaks** — spot checks confirm blocked sources never appear in briefings, notes, or logs.

---

## 14. Out of Scope (this phase)

- Tooling/implementation choices: MCP servers, mail/chat APIs, schedulers, STT engines, notification providers — all deferred to the next (design/tooling) phase.
- Autonomous email sending, chat posting, or any outbound communication besides notifications to the user.
- Hard deletion of any mail by the assistant.
- Autonomous audio recording.
- Personal Google Calendar in the day plan (excluded for now; revisit later).
- Employer-policy privacy filters beyond the explicit exclusion mechanism (revisit if circumstances change).
- Multi-user operation (the architecture allows it; only the `mark` profile is being built).

---

## Appendix A — Decision log

### Interview round 1 (2026-07-05)

| Topic | Decision |
|---|---|
| Autonomy | Label/move freely; never delete |
| Junk purge | Move 30-day-aged junk to provider Trash/Spam; provider auto-purge deletes |
| Outreach aging | Kept indefinitely |
| Feedback | User relabels in mail client; assistant detects and learns |
| Urgent alerts | Immediate push; SMS + Telegram first, pluggable |
| Accounts | Unified view; common rules + limited per-account overrides |
| End state | Prioritized near-zero inbox |
| Triage cadence | 15-min urgent scan + hourly full sweep |
| Briefing delivery | Email + vault markdown |
| Morning schedule | 8:00am weekdays; weekend urgent-only edition |
| EOD | On demand; hybrid auto-draft + voice gap interview |
| Newsletters | Briefing bullets + insight notes in vault |
| Vault | Obsidian vault at `~/Documents/Obsidian/Mark-Claw`, Obsidian Sync for mobile; repo holds automation only |
| Privacy | No hard work/personal boundary |
| Learning loop | Bucketed approvals (auto-apply / sign-off / more-context) |
| Reply drafts | Auto-draft for high-priority; never send |
| Phasing | Vault + bulk ingest first, then triage, then rituals, then extras |
| Bulk ingest | Email mining + life-story session + machine sweep + Drive/OneDrive docs |
| Extras | Waiting-on tracker, meeting prep, contact dossiers, weekly review — all in scope |

### Interview round 2 — chat, calls, exclusions, parameterization (2026-07-05)

| Topic | Decision |
|---|---|
| Chat roles | Full set: briefing highlights, urgent alert source, EOD activity + dossiers, waiting-on tracking |
| Chat history depth | Slack full history (~5 months), Mattermost full history (low volume), Telegram + Signal 90 days |
| Chat ongoing cadence | ~15-minute polling; assistant read-only on chat platforms |
| Call capture policy | Work meetings opt-out (captured by default with exclusions); everything else opt-in; audio capture always manual |
| Transcript persistence | Full transcript in raw/ + summary note (decisions, action items) in wiki/, linked to dossiers/projects |
| Exclusion semantics | Two tiers: Blocked (never read) and Ephemeral (alerting only, never persisted); per-source assignment |
| Exclusion targets | Chat channels/contacts; Drive/OneDrive folders; meetings/series; local drive is whitelist-only |
| Exclusion ownership | Config-layer files, user-owned; assistant may propose via sign-off bucket, never self-applies |
| Parameterization | Shareable tooling, single user for now; per-profile layout, default profile `mark` |
| Layer homes | Tooling: repo. Config: `~/.config/mark-claw/<profile>/`. State: `~/.local/state/mark-claw/<profile>/` (XDG-style) |
