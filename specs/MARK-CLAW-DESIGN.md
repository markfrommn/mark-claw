# MARK-CLAW — System Design (Phase B)

**Status:** Draft v1 — design phase output, 2026-07-05
**Inputs:** `MARK-CLAW-SPEC.md` (Draft v2, requirements — canonical for *what*), `MARK-CLAW-TOOLS.md` (Confirmed v1.1, tool selections — **ground truth** for *with what*; conflicts get raised, never silently substituted)
**Output consumers:** Phase C/D implementation sessions
**Scope:** design only — no implementation code. Every schema shown is normative for implementation; field lists may grow, never silently change meaning.

**Framework guard (tools §12):** this design introduces **no agent-framework runtime or plugins** (OpenClaw, ZeroClaw, IronClaw, PicoClaw, NemoClaw, Sai, Hermes Agent, Vellum — all evaluated and declined). The trust boundary is the bespoke stack: **launchd + provider wrappers + `claude -p`**. Four patterns from that evaluation are folded in as design inputs (locations noted inline and indexed in §11.2): (a) heartbeat batching → §2.3; (b) credential isolation → §2.2/§6.4; (c) allowlist-over-prompt → §11.1; (d) output-side exclusion check → §5.4.

---

## 0. Design principles

1. **Files over databases.** All state is JSON/JSONL/markdown under XDG paths. Anything can be inspected with `cat`, fixed with `$EDITOR`, rebuilt by re-scanning.
2. **No daemons** except the loopback dashboard (read-only, KeepAlive). Everything else is a stateless launchd invocation that reads config + state, does its work, writes state, exits.
3. **Structural enforcement, not prompts.** Every hard constraint in spec §11 is a fact of the tool surface — an endpoint that doesn't exist, a deny-list in a wrapper, a directory that a writer can't reach. §11.1 is the per-constraint map; it is an acceptance criterion for every implementation phase.
4. **Two-tier intelligence.** Cheap deterministic rules + Haiku API for the high-frequency mechanical work; `claude -p` (Max subscription) for judgment. Billing mode is a one-line config switch (§6.1 `settings.yaml → runtime.billing`).
5. **Fail closed on the hard guarantee, fail open on everything else.** Exclusion violations block the artifact (§5.4). A dead source never blocks a briefing (§2.5).

---

## 1. Overall architecture

### 1.1 Component inventory

```
┌─ Tier 0: launchd LaunchAgents (schedules) ─────────────────────────────┐
│  mclaw-sweep-15m   :00/:15/:30/:45          mclaw-daily    07:50 (briefing)  │
│  mclaw-sweep-hourly :05 past each hour       mclaw-weekly   Sat 09:00 (cfg)  │
│  mclaw-dashboard   KeepAlive (loopback server)                            │
└────────────────────────────────────────────────────────────────────────┘
┌─ Tier 1: mechanical layer (Python, uv-managed, in repo bin/) ──────────┐
│  Orchestrators: mclaw-sweep-15m, mclaw-sweep-hourly, mclaw-daily, mclaw-weekly     │
│  Provider wrappers (secrets live ONLY here, via `security`/`mclaw secret`):│
│    mclaw-fetch-gmail  mclaw-fetch-graph   mclaw-fetch-slack   mclaw-fetch-mm       │
│    mclaw-fetch-tg     mclaw-fetch-signal  mclaw-fetch-github  mclaw-fetch-figma    │
│    mclaw-fetch-drive  mclaw-fetch-onedrive mclaw-fetch-local  mclaw-fetch-meet     │
│    mclaw-fetch-zoom   mclaw-cal (both calendars)                             │
│  Action wrappers:  mclaw-mail-act (label/move/archive/trash-aged/draft)   │
│                    mclaw-send-self (briefing email; see §7.3)             │
│  Shared modules:   exclusion gate (§5.2), output guard (§5.4),         │
│                    notify (§8), rules engine (§4.2), state I/O         │
│  Urgent classifier: Haiku via Messages API (structured output)         │
└────────────────────────────────────────────────────────────────────────┘
┌─ Tier 2: agentic layer (`claude -p`, sees spool files, NEVER secrets) ─┐
│  triage-judge (hourly gray-zone)   briefing-assembler (daily)          │
│  eod-capture (interactive)         weekly-review                       │
│  vault-enricher (dossiers/projects/insights)   meeting-prep            │
│  bulk-ingest pipelines (Phase 1, one-shot)                             │
└────────────────────────────────────────────────────────────────────────┘
        Vault (~/Documents/Obsidian/Mark-Claw): raw/ + wiki/   (§3.3)
        Config (~/.config/mark-claw/mark/)                     (§6.1)
        State  (~/.local/state/mark-claw/mark/)                (§6.2)
```

### 1.2 How pipelines run

Every scheduled entry point is an **orchestrator script** invoked by launchd. An orchestrator:

1. Acquires a per-pipeline lockfile (`state/locks/<pipeline>.lock`, flock; a still-running previous invocation makes the new one exit 0 with a `skipped: overlap` run record).
2. Reads config, reads its last-successful-run timestamp from `state/runs/`.
3. Fans out to provider wrappers as **subprocesses** (parallel where independent).
4. Runs its processing stage (rules engine, Haiku classify, or a `claude -p` call).
5. Passes every outbound artifact through the **output guard** (§5.4).
6. Writes run records + advances consumer cursors, releases lock, exits.

`claude -p` invocations use `--output-format json`, a per-pipeline `--allowedTools` allowlist, and log `total_cost_usd` + `session_id` into the run record. Prompts live in the repo (`prompts/<pipeline>.md`); they receive *data file paths and content*, never credentials (§2.2).

### 1.3 Schedules (launchd, per tools §7.1)

| Job | Trigger | Work |
|---|---|---|
| `mclaw-sweep-15m` | `StartCalendarInterval` :00/:15/:30/:45 | Batched sweep: 4 chat pollers + email urgent scan → urgent classify → alerts; chat capture to spool (urgent-check and full capture in one pass — spec §4.2 volume allows it, so **no separate hourly chat job**) |
| `mclaw-sweep-hourly` | :05 each hour | Full email triage across 4 accounts; relabel detection; draft creation; vault filing of triage output |
| `mclaw-daily` | 07:50 weekdays / 08:30 weekends | Morning briefing (weekend = urgent-only edition); calendar + GitHub + trackers assembly; meeting-prep briefs (Phase 4); junk-aging job; Drive/OneDrive change sweep; Figma poll; maintenance checks (signal-cli version, Figma PAT age, quarantine reminder) — **junk aging, Figma, and Drive ride this heartbeat; no extra scheduler entries** |
| `mclaw-weekly` | Sat 09:00 (config) | Weekly review note; outreach digest; junk-rescue digest; approval batch; continuous exclusion spot-scan (§5.5) |
| `mclaw-eod` | **on demand** (user runs it) | EOD auto-draft + voice gap interview (§7.4) |
| `mclaw-capture` | **on demand only** | Local audio capture + STT. **No launchd entry may reference this command** — structural enforcement of "no autonomous recording" (§11.1) |
| `mclaw-dashboard` | KeepAlive | Loopback status server (§9) |

All timed jobs use `StartCalendarInterval` (coalesced catch-up on wake — tools §7.1). Plists are **generated** from `config/schedules.yaml` by `mclaw install-schedules` (idempotent; sets PATH, WorkingDirectory, StandardOut/ErrPath into `state/logs/launchd/`).

**Sleep gap (decision 2026-07-05): `pmset` scheduled wake.** `mclaw install-schedules` also installs a repeating `pmset repeat wakeorpoweron MTWRF 07:45:00` so the 07:50 briefing runs and writes the vault locally. The 15-min alert cadence **accepts** lid-closed gaps — the phone's native mail/chat apps cover true emergencies while the Mac sleeps. No cloud Routine backstop (declined; revisit trigger stands in tools §11).

### 1.4 The 15-minute heartbeat (batching — borrowed pattern a)

One launchd job, one orchestrator, one shared context — not N isolated polls:

```
mclaw-sweep-15m
 ├─ parallel subprocess fan-out (each wrapper: resolve secret → fetch since
 │  cursor → EXCLUSION GATE → normalize → append spool → advance cursor):
 │    mclaw-fetch-slack   mclaw-fetch-mm   mclaw-fetch-tg   mclaw-fetch-signal
 │    mclaw-fetch-gmail --urgent-scan ×3   mclaw-fetch-graph --urgent-scan
 ├─ collect: everything new this sweep (all sources, one list)
 ├─ single batched Haiku call: urgent-classify the batch (structured output:
 │    [{item_id, urgent: bool, kind, one_line, confidence}])
 ├─ alert dispatch via notify layer (dedup + rate cap, §8.3)
 └─ run record + per-source status
```

The email urgent scan is headers+snippet only (`newer_than` query, in:inbox), not full triage — full classification waits for the hourly sweep. Urgent-pattern rules from `rules/common.yaml` (`urgent: true` rules) run *before* Haiku; rule hits alert without model involvement.

### 1.5 Failure, retry, graceful degradation

- **Per-source isolation.** A wrapper that fails (network, auth, rate limit) exits nonzero; the orchestrator records the failure and continues with the other sources. The orchestrator itself only fails on internal errors, never because a source did.
- **Retry = next cadence.** Wrappers do 3 in-run HTTP retries with backoff (respecting `Retry-After`/FloodWait), then give up. Cursors advance **only on success**, so the next scheduled run re-covers the gap for free (idempotent fetch-since-cursor). Chat backfill after long gaps is page-capped (config `limits.max_backfill_pages`); hitting the cap is logged and surfaced in the briefing.
- **Escalation.** `consecutive_failures` per source is tracked in `state/runs/`. Crossing threshold (default 4 = ~1h for 15-min sources) sends **one** system-severity Telegram notice and marks the source red on the dashboard; recovery sends one all-clear. No re-alerting while stuck (dedup key = source+first-failure-time).
- **Graceful degradation.** Consumers (briefing, EOD, weekly) assemble from whatever spool/cursor data exists and render explicit gap lines: *"Slack: unavailable since 06:15 (auth error) — highlights may be incomplete."* Staleness is computed from cursor timestamps vs expected cadence; a source is "degraded" past 2× cadence, "down" past threshold.
- **Catch-up.** After wake, launchd fires the coalesced missed run; the wrapper's since-cursor fetch naturally covers the sleep window. Run records store `covered_from`/`covered_to` so the dashboard can show gap coverage.

---

## 2. Runtime layers & trust boundary

### 2.1 Trust boundary

The trust boundary is the Tier-1 mechanical layer. Everything that touches credentials, everything that enforces a hard constraint, and everything that writes to providers lives in small, reviewable Python wrappers in the repo. Tier 2 (`claude -p`) is treated as *untrusted-but-helpful*: it gets pre-fetched, pre-gated data files and a narrow `--allowedTools` surface; it cannot reach a provider except through an action wrapper that enforces its own invariants regardless of what the model asks for.

### 2.2 Credential isolation (borrowed pattern b — Vellum CES shape)

- Config stores **only** `keychain://` references (tools §7.3). Wrappers resolve them at spawn via `security find-generic-password` (through the `mclaw secret get` helper once B1 lands); tokens exist as env vars inside the wrapper process only.
- On-disk token material that must exist (OAuth token caches, Telethon StringSession, signal-cli data dir) lives in `state/secrets/` (0700 dir, 0600 files), read exclusively by wrappers.
- **Nothing in Tier 2 ever sees a token**: `claude -p` prompts and `--allowedTools` contain data file paths and wrapper command names only. Wrappers never echo secrets; log formatter has a redaction pass keyed on resolved secret values as a belt-and-suspenders (secrets are compared by value against every log line before write).
- The dashboard renders `state/` but is denied `state/secrets/` structurally: its file allowlist enumerates the directories it may read (§9.2).

### 2.3 Model/billing split (tools §7.2)

| Work | Model | Billing | Why |
|---|---|---|---|
| 15-min urgent classify | Haiku 4.5, cached system prompt, structured output | API key (low-limit) | 96 invocations/day; no subscription-policy exposure |
| Hourly gray-zone triage, briefings, EOD, weekly review, enrichment | `claude -p` (Sonnet-class default, per-pipeline override in config) | Max subscription | Judgment-heavy, lower frequency |

`settings.yaml → runtime.billing: subscription | api` flips every Tier-2 entry point to `ANTHROPIC_API_KEY` billing (the tools-spec policy hedge). Per-run cost is logged; the weekly review includes a cost line.

---

## 3. Data flow

### 3.1 End-to-end

```
                       ┌────────────── Tier 1 wrappers ─────────-─────┐
 Gmail ×3 ────────────▶│ secrets → fetch → EXCLUSION GATE → normalize │
 Graph (mail/cal/OD) ─▶│  (blocked: dropped at enumeration;           │──▶ state/spool/*.jsonl
 Slack/MM/TG/Signal ──▶│   ephemeral: → spool/ephemeral/, TTL=sweep)  │         │
 Drive/GitHub/Figma ──▶└──────────────────────────────────────────────┘         │
 Meet/Zoom transcripts                                                          │
                                                                                ▼
   15-min: spool(new) ─▶ urgent rules + Haiku ─▶ notify(Telegram) ─▶ alerts log
   hourly: spool(mail) ─▶ rules engine ─▶ claude -p gray-zone ─▶ mclaw-mail-act
             (label/archive/draft) + triage records + vault filing
   daily:  spool + cursors + trackers + calendars ─▶ briefing assembler
             ─▶ OUTPUT GUARD ─▶ wiki/briefings/ + mclaw-send-self (email)
   on-demand: day's data ─▶ EOD draft ─▶ interactive gap interview
             ─▶ OUTPUT GUARD ─▶ wiki/days/ + trackers
   weekly: triage stats + queues ─▶ review note ─▶ approvals ─▶ rule updates
   continuous: spool/vault ─▶ vault-enricher ─▶ wiki/people|projects|insights
```

Every path from spool to a persistent or outbound artifact passes the output guard. `spool/ephemeral/` is readable **only** by the alert stage (§5.3).

### 3.2 Spool (the Tier-1 → Tier-2 handoff)

`state/spool/<source-id>/YYYY-MM-DD.jsonl` — append-only per fetch, one normalized item per line:

```json
{"id":"slack:C04XYZ:1751724182.4419","source":"slack-work","kind":"chat.message",
 "ts":"2026-07-05T14:03:02Z","conv":{"id":"C04XYZ","name":"#eng-infra","type":"channel"},
 "from":{"id":"U02ABC","name":"Jane Doe"},"text":"…","thread":"1751720000.1200",
 "mentions_me":true,"sweep":"2026-07-05T14:15Z","tier":"full"}
```

```json
{"id":"gmail:powderhorns:197f2c1a","source":"powderhorns","kind":"email.message",
 "ts":"2026-07-05T13:58:41Z","from":"billing@stripe.com","to":["mark@powderhorns.biz"],
 "subject":"Payment failed…","snippet":"…","thread":"197f2c00",
 "provider_labels":["INBOX","UNREAD"],"body":"…full text…","sweep":"…","tier":"full"}
```

Common envelope fields (`id`, `source`, `kind`, `ts`, `sweep`, `tier`) + per-kind payload. `tier` is `full` or `ephemeral` (ephemeral items are written under `spool/ephemeral/` instead, never mixed). Consumers track their own high-water marks in `state/cursors/consumers.json` (`{"briefing":{"last_sweep":"…"},"eod":{…}}`) so triage never waits for briefings and vice versa. Spool is pruned at 30 days (durable content has moved to the vault by then).

### 3.3 Vault layout & naming conventions

Vault root: `~/Documents/Obsidian/Mark-Claw` (config `settings.yaml → vault.path`). Two layers plus a meta dir; `wiki/_meta/VAULT-GUIDE.md` is the structure-enforcing doc (written in Phase 1, referenced by every vault-writing prompt).

```
raw/                                  # full-fidelity, machine-named, append-mostly
  email/<account-id>/YYYY/MM/YYYY-MM-DD--<subject-slug>--<msgid8>.md
  chat/<platform>/<conv-slug>/YYYY-MM.md      # monthly append file per conversation
  transcripts/YYYY/YYYY-MM-DD--<meeting-slug>.md
  docs/<source-id>/<mirrored/path/name>.md    # ingested Drive/OneDrive/local docs
  ingest/…                                    # Phase-1 bulk-ingest artifacts (reports, life-story)
wiki/                                 # human-named, heavily wikilinked
  people/<Firstname Lastname>.md              # contact dossiers (§ spec 8.3)
  companies/<Company>.md
  projects/<Project Name>.md
  meetings/YYYY-MM-DD--<slug>.md              # summary notes (decisions, action items)
  meetings/YYYY-MM-DD--<slug>--prep.md        # meeting-prep briefs (Phase 4)
  briefings/YYYY-MM-DD-morning.md             # (-weekend for weekend edition)
  days/YYYY-MM-DD.md                          # EOD activity records
  insights/<topic-slug>.md                    # newsletter/bulk-useful extractions
  trackers/tasks.md
  trackers/waiting-on.md
  reviews/YYYY-Wnn.md                         # weekly review + approval note (§10)
  _meta/VAULT-GUIDE.md
```

Conventions:
- Dated names: `YYYY-MM-DD--kebab-slug` (double-dash separates date from slug, slug from ids).
- Every machine-written note carries YAML frontmatter: `source:`, `created:`, `pipeline:`, `refs:` (provider permalinks), plus kind-specific keys. Frontmatter is the contract that lets enrichment and dashboards find notes without a database.
- `raw/` is selective, not exhaustive: high/middle-bucket email bodies worth keeping, all transcripts, all ingested docs, chat monthly logs. Junk/Outreach stay in the provider (Outreach feeds dossiers via wiki summaries; provider retains the originals indefinitely per spec).
- Wiki notes link raw notes (`[[raw/…]]`), people, and projects; the vault-enricher maintains backlinks. Trackers are markdown tables where each row carries a stable `id` in an HTML comment so machine edits and hand edits coexist (`| … | <!--wo:2026-034--> |`).

---

## 4. Classification pipeline (email)

### 4.1 Decision flow per message (hourly sweep)

```
new message ─▶ per-account override rules ─▶ learned rules ─▶ common rules
      │ (first match wins, account layer first)
      ├─ matched → bucket + flags (urgent, needs-draft)
      └─ unmatched → context features (dossier lookup: known contact?
           prior thread? references real context?) → claude -p triage-judge
           (batched: all unmatched messages in one call, with feature digest)
           → bucket + confidence + rationale
 ─▶ mclaw-mail-act applies: label (mclaw/<bucket>), archive if bucket ≠ high,
    draft-create if high & needs-reply (§ spec 3.7)
 ─▶ triage record appended (state/triage/<account>/YYYY-MM.jsonl)
 ─▶ changelog entry
```

**Rules vs LLM split:** rules decide everything they *can* decide (senders, list-ids, domains, header patterns — the stable bulk of mail volume); the LLM decides only what rules can't: the human-judgment buckets (is this a real person with real context? did this SDR do their homework?). Cold-call detection is a **feature lookup, not an LLM guess**: the wrapper resolves sender against `wiki/people/` frontmatter + the Phase-1 email-history contact index (`state/contacts/index.json`) and hands the judge a boolean `known_contact` + interaction summary.

Bucket → provider mapping (tools §2 label adapter): Gmail labels `mclaw/high|middle|bulk|outreach|junk`; Outlook categories `MC High|…` + move to Archive well-known folder; star ↔ flag. Rules are written provider-neutral; the adapter lives in `mclaw-mail-act`.

### 4.2 Rules format

`config/rules/common.yaml` (hand-authored — machine never edits), `config/rules/learned.yaml` (machine-managed via the learning loop — still human-readable/editable), `config/rules/accounts/<account-id>.yaml` (hand-authored overrides):

```yaml
version: 1
rules:
  - id: monitoring-down-alerts
    match: {from: "*@uptimerobot.com", subject~: "(?i)\\b(down|failure)\\b"}
    bucket: high
    urgent: true                # also fires in the 15-min scan
  - id: stratechery
    match: {list_id: "*.stratechery.com"}
    bucket: bulk
  - id: calendar-invites
    match: {header: {Content-Type~: "text/calendar"}}
    bucket: middle
```

Match keys: `from`, `to`, `cc`, `list_id`, `subject~` (regex), `header`, `body~` (regex, discouraged), all AND-ed within a rule; `any_of:` for OR groups. Fields glob by default, `~` suffix = regex. Evaluation order: **account overrides → learned → common**, first match wins; an account file may also `disable: [rule-ids]` to suppress a common/learned rule for that account. `learned.yaml` uses the same schema plus provenance:

```yaml
rules:
  - id: learned-junk-sender-0142
    match: {from: "*@growthblastr.io"}
    bucket: junk
    learned: {from_event: "relabel:gmail:powderhorns:197f…", at: "2026-07-12T09:05Z",
              evidence: "user moved message from mclaw/junk to inbox? no — junked by user"}
```

### 4.3 Relabel detection → learning

Each hourly sweep, for messages triaged in the last 14 days (window in config), `mclaw-fetch-gmail`/`mclaw-fetch-graph` re-read current labels/folder and diff against the triage record:

- **User moved/relabeled** (record says `mclaw/junk`, message now in inbox or under another label) → correction event: fix the triage record, update the message's `mclaw/` label to match user intent, append a **learning example** (`state/examples/relabels.jsonl`: `{msg, account, was, now, features, at}`).
- **Learning routing** (spec §9 buckets): sender-level consequences (this exact sender/domain → new bucket) are **auto-apply** — written to `rules/learned.yaml` + changelog. Pattern-level consequences ("many rescues share shape X → propose rule") are **needs-sign-off** — queued for the weekly review. Ambiguity → **more-context** question in the same review note.
- Junk rescues are additionally tallied for the weekly junk-rescue digest (spec §8.4) — the misclassification-visibility metric (§ spec 13.2).

### 4.4 Changelog format

`state/changelog/YYYY-MM.jsonl`, append-only, one record per autonomous action or learned change — the audit surface required by spec §11:

```json
{"ts":"2026-07-05T15:05:12Z","actor":"triage-hourly","action":"mail.label",
 "target":"gmail:powderhorns:197f2c1a","detail":{"bucket":"junk","by":"rule:learned-junk-sender-0142"}}
{"ts":"2026-07-05T15:05:13Z","actor":"triage-hourly","action":"mail.archive","target":"…"}
{"ts":"2026-07-12T09:05:20Z","actor":"learning","action":"rule.add","approval":"auto-apply",
 "target":"rules/learned.yaml#learned-junk-sender-0142","detail":{"evidence_event":"relabel:…"}}
{"ts":"2026-08-05T08:02:00Z","actor":"junk-aging","action":"mail.trash-aged",
 "target":"gmail:powderhorns:1963aa02","detail":{"junk_labeled":"2026-07-05","age_days":31}}
```

`action` values are a closed enum (`mail.label|mail.move|mail.archive|mail.draft|mail.trash-aged|rule.add|rule.edit|exclusion.propose|note.write|alert.sent|guard.trip|…`). The weekly review and dashboard both render from this file; `mclaw log` greps it.

### 4.5 One-time cleanup (Phase 2 bootstrap)

Same pipeline, pointed at full inbox depth instead of a cursor: paginate everything currently in each inbox, classify (rules + batched judge calls), label/archive, and emit `raw/ingest/cleanup-report-<account>.md` (counts per bucket, notable finds, proposed rules → sign-off queue). Already-archived mail untouched (query scope = `in:inbox` only). Rate: batched, checkpointed (`state/cursors/cleanup-<account>.json`) so it can resume.

---

## 5. Exclusion enforcement (the hard guarantee)

### 5.1 Model

Two independent layers; both must fail for a leak to occur, and the second failing closed means a leak becomes a blocked artifact + alert instead:

1. **Fetch gate** (primary choke point) — every source read passes each item's *identifiers* through one shared module before content is fetched or emitted.
2. **Output guard** (defense-in-depth, borrowed pattern d) — every emitted artifact is scanned for blocked-source identifiers before write/send.

### 5.2 Fetch gate — single choke point

One module in the tooling layer (`mclaw_core/exclusion.py`), imported by **every** wrapper; no wrapper implements its own filtering. API shape (normative):

```
gate = ExclusionGate.load(config)        # compiles exclusions.yaml + local-whitelist.yaml
gate.check(source_id, item_ref) -> ALLOW | EPHEMERAL | BLOCKED
```

- Wrappers call `gate.check` at **enumeration time** — on conversation/folder/meeting IDs *before* requesting content. `BLOCKED` → the item is skipped entirely: no content API call, no log line naming it (the run record counts `blocked_skipped: n` without identifiers), nothing downstream. Where the provider API allows server-side filtering (Drive query, Slack conversation list), blocked IDs are additionally excluded from the request itself.
- **Structural placement:** the gate call sits inside the shared `fetch_items()` base that all wrappers extend — a wrapper that "forgets" the gate would have to bypass the shared fetch path, which code review + the canary test (§5.5) catches.
- **Local drive is whitelist-inverted:** `mclaw-fetch-local` takes its scan roots *only* from `local-whitelist.yaml`; there is no code path that scans an unlisted root (allowlist-over-prompt: the capability doesn't exist).
- Drive/OneDrive folder exclusions are path-prefix matches (inherit to subfolders); meetings match by event/series ID with title-pattern fallback; chat matches by conversation/contact ID.

### 5.3 Tier semantics

| Tier | Fetch gate behavior | Persistence |
|---|---|---|
| **Blocked** | Never fetched. Skipped at enumeration; no content call, no identifier in logs. | Nothing, anywhere, ever. |
| **Ephemeral** | Content fetched **only within the 15-min sweep**, written to `state/spool/ephemeral/` (0700). | Consumed by the urgent-classify + alert stage of the *same sweep*, then the directory is emptied at sweep end (and defensively at sweep start). Alert text for ephemeral hits carries the pointer ("check Signal — urgent message from J.") not the content. Briefing/EOD/enricher readers have no code path into `spool/ephemeral/` — their readers enumerate `spool/<source>/` only. |

Default for anything not listed: fully processed (spec §7.1).

### 5.4 Output guard — fail closed (decision 2026-07-05)

`mclaw_core/output_guard.py` compiles a deny-pattern set from `exclusions.yaml` — every blocked/ephemeral entry's IDs, names, aliases, and per-entry `also_match:` strings (word-boundary, case-insensitive; ephemeral entries contribute their identifiers for *persistence* surfaces only, since alerts legitimately name them as pointers). Every writer calls it on the full artifact before emitting:

- **Scanned surfaces:** briefing markdown + email body, every vault note write, EOD output, weekly review, alert texts (against blocked entries), log lines (via the logging formatter), draft bodies.
- **On hit — fail closed:** the artifact is **not** written/sent. It is moved to `state/quarantine/` (0700, outside the vault and outside any sync), a `guard.trip` changelog record is written, a system-severity Telegram alert fires ("exclusion guard tripped on morning-briefing — artifact quarantined"), and a needs-sign-off review item is queued with the match context. A tripped briefing is *lost until reviewed* — accepted cost; false-positive patterns get refined via the review loop (`also_match`/alias tuning is config, user-owned).
- The guard itself never logs the matched content — only the pattern ID and artifact name.

### 5.5 Testing the guarantee

1. **Unit tests** on the gate: blocked/ephemeral/allow for every source type; prefix inheritance; ID vs name matching; whitelist inversion for local; config hot-reload.
2. **Canary integration test** (repo `tests/canary/`): a fixture profile whose exclusions list a channel/folder/contact seeded with unique sentinel tokens (`MCX-CANARY-<uuid>`); run every pipeline against fixture data; assert **zero** sentinel occurrences across the entire output tree (vault, spool minus ephemeral-during-sweep, logs, run records, rendered briefing, alert payload capture) and assert ephemeral content exists *only* transiently. This test is CI-blocking for any change to wrappers, gate, guard, or writers.
3. **Continuous spot-check** (the spec §13.5 success criterion made continuous): the weekly job runs the output-guard pattern set over the *entire* vault + last week's logs and reports zero-or-findings in the weekly review; any finding also fires the fail-closed alert path.
4. **Negative-path drill** (Phase 2 acceptance): temporarily add a live low-stakes channel to `exclusions.yaml`, post a sentinel in it, run a real sweep, verify skip + absence, remove.

---

## 6. Config & state schemas

### 6.1 `~/.config/mark-claw/mark/` (human-readable, hand-editable, no raw secrets)

```
settings.yaml          # vault path, timezone, profile display name,
                       #   runtime: {billing: subscription|api, models: {triage-judge: …}}
                       #   limits: {max_backfill_pages, alert_rate_cap, relabel_window_days}
accounts.yaml          # email/calendar/drive account definitions (below)
sources.yaml           # chat + activity sources (slack, mm, tg, signal, github, figma, zoom, meet, local)
rules/common.yaml      # hand-authored classification rules (§4.2)
rules/learned.yaml     # machine-managed learned rules (auto-apply bucket writes here)
rules/accounts/<id>.yaml   # per-account overrides + disables
exclusions.yaml        # blocked/ephemeral entries (§5), incl. output-guard aliases
local-whitelist.yaml   # scan_roots: [...] — the ONLY source of local scan paths
channels.yaml          # notification channels + routing (§8.2)
schedules.yaml         # cadences, briefing time, weekend time, weekly slot, pmset wake spec
briefing.yaml          # section toggles/order, weekend-edition scope, recipients (see §7.3)
```

`accounts.yaml` entry (email):

```yaml
email:
  - id: powderhorns
    address: mark@powderhorns.biz
    provider: gmail                    # gmail | graph
    auth:
      client_ref: "keychain://mark-claw-mark/gog-oauth-client-credential"
      token_cache: "$STATE/secrets/google/powderhorns/"   # 0600, rebuildable by re-auth
    roles: [mail]
  - id: jumpweb
    address: mark@jumpweb.net
    provider: graph
    auth: {client_ref: "keychain://mark-claw-mark/entra-app-client_id", token_cache: "$STATE/secrets/msal/jumpweb/"}
    roles: [mail, calendar, onedrive]
own_addresses: [mark@convoydefense.ai, mark@powderhorns.biz, mark@jumpweb.net, markfrommn@gmail.com]
```

`exclusions.yaml` entry shapes:

```yaml
chat:
  slack-work:
    - {id: "C0HRCHAN", name: "#people-private", tier: blocked, also_match: ["people-private"]}
    - {id: "D024JD",   name: "DM: Jane Doe",    tier: ephemeral}
drive:
  gdrive-work:
    - {path: "/HR/", tier: blocked}          # prefix; inherits to subfolders
meetings:
  - {series_id: "abc123…", title: "Comp review", tier: blocked}
```

`channels.yaml` and `schedules.yaml`: see §8.2 / §1.3.

### 6.2 `~/.local/state/mark-claw/mark/` (rebuildable; wiping costs a re-scan, never config)

```
cursors/<source-id>.json      # sync cursors (below)
cursors/consumers.json        # per-pipeline spool high-water marks
spool/<source-id>/YYYY-MM-DD.jsonl     # normalized items (§3.2); 30-day prune
spool/ephemeral/…             # 0700; emptied every sweep (§5.3)
runs/<pipeline>.json          # latest run status (below) — dashboard substrate
runs/history.jsonl            # one line per run, 30-day prune — dashboard timeline
triage/<account>/YYYY-MM.jsonl   # classification decision records (§4.1)
examples/relabels.jsonl       # learning examples (§4.3)
contacts/index.json           # Phase-1 email-mining contact index (cold-call feature)
changelog/YYYY-MM.jsonl       # audit log (§4.4)
alerts/YYYY-MM.jsonl          # alert history (§8.3)
review-queue/pending.jsonl    # canonical approval queue (§10.2)
quarantine/                   # 0700 — output-guard fail-closed artifacts (§5.4)
secrets/                      # 0700 — token caches, Telethon session, signal-cli data dir
locks/                        # per-pipeline flock files
logs/<pipeline>/YYYY-MM-DD.log  + logs/launchd/
```

Cursor file (`cursors/slack-work.json`):

```json
{"source":"slack-work","updated":"2026-07-05T14:15:09Z",
 "data":{"per_conversation":{"C04XYZ":"1751724182.4419","D024AA":"1751710002.0001"}},
 "note":"opaque per-provider shape under data: Graph deltaLink, Drive pageToken,
         Gmail last internalDate query floor, TG per-dialog max msg id, signal ts,
         github since-iso, figma per-file version ids"}
```

Run record (`runs/sweep-15m.json`):

```json
{"pipeline":"sweep-15m","last_start":"2026-07-05T14:15:00Z","last_success":"2026-07-05T14:15:41Z",
 "last_result":"ok","duration_s":41,"covered_from":"2026-07-05T14:00Z","covered_to":"…",
 "items":{"slack-work":12,"telegram":3,"signal":0,"mattermost":0,"email-urgent":2},
 "sources":{"signal":{"ok":false,"error":"receive timeout","consecutive_failures":2,
            "last_ok":"2026-07-05T13:45Z"}},
 "cost":{"haiku_tokens":8123},"alerts_sent":1,"blocked_skipped":4}
```

These two files (plus `alerts/`, `review-queue/`, `changelog/`, cursor mtimes) are deliberately sufficient for the dashboard — **the dashboard needs nothing a pipeline doesn't already write for its own operation** (§9).

### 6.3 Tooling layer (repo)

```
bin/            # orchestrators, wrappers, action tools, mclaw CLI (mclaw status, mclaw eod, mclaw log, …)
mclaw_core/     # shared modules: exclusion gate, output guard, notify, rules engine,
                #   spool/state I/O, provider adapters, secrets resolution
prompts/        # per-pipeline claude -p prompts (briefing.md, triage-judge.md, eod.md, …)
plists/         # generated-plist templates for mclaw install-schedules
tests/          # unit + canary suite (§5.5)
specs/          # this document and its inputs
```

No personal data, no secrets, nothing Mark-specific hard-coded — profile comes from `MCLAW_PROFILE` env (default `mark`), all personal values from config (spec §10).

### 6.4 Credentials handling summary

| Secret | Home | Notes |
|---|---|---|
| OAuth client IDs/secrets, Slack xoxp, MM PAT, TG api_id/hash + bot token, Zoom S2S, Figma PAT, Anthropic API key | macOS Keychain (login keychain, `mark-claw-mark` service), referenced as `keychain://` in config | Resolved inside wrappers only (§2.2) |
| Google/MSAL token caches, Telethon StringSession, signal-cli data dir | `state/secrets/` 0700/0600 | Rebuildable by re-auth/relink |
| `mclaw-send-self` credential | Distinct keychain account `gmail-send-only` on the same `mark-claw-mark` service; token cache scoped to `gmail.send` **only** | See §7.3 — the send credential can't read, the read credentials can't send |
| Full credential set (backup) | `state/secrets/backup.age` (age-encrypted, refreshed on provision/rotation) | Passphrase held by Mark, never on disk — see tools §7.3 |

---

## 7. Briefing & EOD workflows

### 7.1 Morning briefing (weekdays 08:00; weekend urgent-only edition)

`mclaw-daily` at 07:50 (pmset has woken the Mac at 07:45):

1. **Gather** (Tier 1, parallel): calendars via `mclaw-cal` (Google work + O365 personal; `calendarView` pre-expands recurrences), GitHub (`assigned issues`, `review-requested PRs`), spool since the last EOD/briefing consumer cursor, triage records since same, alert history overnight, trackers (`tasks.md`, `waiting-on.md`), pending drafts list, today's meeting-prep notes (Phase 4).
2. **Assemble** (Tier 2, one `claude -p` run with `prompts/briefing.md`): produces the § spec-6.1 sections — day plan (+ suggested day organization), overnight comms highlights grouped by bucket (+ unanswered chat DMs/mentions, bulk-useful "worth reading?" bullets), alerts recap with status, waiting-on nudges with staleness, meeting-prep pointers. Degraded sources render explicit gap lines (§1.5).
3. **Emit:** output guard → `wiki/briefings/YYYY-MM-DD-morning.md` → `mclaw-send-self` email. Weekend run (08:30) uses the same pipeline with `--edition weekend`: urgent-and-before-Monday items only.

### 7.2 Content contract

The briefing prompt receives *structured gathered data* (JSON files), not raw provider access — it cannot fetch, only compose. Links: Gmail/Outlook permalinks for messages, `obsidian://` URIs for vault notes, GitHub URLs for issues/PRs, draft deep-links for pending replies.

### 7.3 Delivery — `mclaw-send-self` (decision 2026-07-05)

A dedicated send-only wrapper, the **only** send-capable code in the system:

- Sends from **`mark@powderhorns.biz`** via Gmail `messages.send`, using a **separate OAuth token whose only scope is `gmail.send`** (the triage tokens carry `gmail.modify` but the triage wrappers implement no send; the send token can't read mail). 
- **No recipient parameter exists.** Recipients come from `briefing.yaml → recipients`, validated at send time to be a subset of `accounts.yaml → own_addresses`; anything else aborts. Structurally the system cannot email a third party (allowlist-over-prompt).
- Used by: morning briefing, weekly review delivery. Nothing else may import it (enforced by the canary suite grepping call sites).

### 7.4 EOD capture (on demand — hybrid auto-draft + voice gap interview)

`mclaw eod` (user runs it when wrapping up):

1. **Auto-draft** (Tier 1 gather + one `claude -p`): day slice of spool (email/chat activity), calendar events attended, transcripts filed today, `mclaw-fetch-local` git scan (`--since=midnight` across whitelisted repos), GitHub commits/PRs, Figma versions (best-effort — undercount noted, tools §5.4), Drive/OneDrive changes. Draft = narrative day record + **explicit gap list** ("2:00–3:30 has no visible activity; the Acme thread got a reply drafted but not sent — decision?").
2. **Gap interview** (interactive): `mclaw eod` drops into an interactive `claude` session primed with `prompts/eod.md` + the draft. The session presents the draft, then interviews on **gaps only** — action items, decisions, loose ends invisible in the data. Mark answers by voice via **VoiceInk** (global hotkey, pastes into the prompt; >5-min dumps: record-then-transcribe per tools §4.5). The session has vault-write and tracker-edit in `--allowedTools`, nothing provider-side.
3. **File:** output guard → `wiki/days/YYYY-MM-DD.md` (frontmatter: sources used, gaps unresolved); extracted action items appended to `trackers/tasks.md` / `trackers/waiting-on.md` (row-id convention §3.3); **comms recap** section (interesting email+chat since the morning briefing) included in the note. Skipping the interview (Ctrl-C / `--draft-only`) files the draft marked `interview: skipped`.

---

## 8. Notification layer

### 8.1 Abstraction

`mclaw_core/notify.py`: one interface, per-channel adapters, config-driven routing. **One-way by design** — the interface has exactly one operation:

```
Channel.send(alert: Alert) -> DeliveryResult
Alert = {severity: urgent|system, title, body, ref?: url, dedup_key, ts}
```

No receive path, no webhook listener, no polling for replies — a two-way chat interface was **explicitly declined** (tools §12.4). The abstraction leaves room to grow one later only in the sense that channels are pluggable config objects; no inbound design exists in this document, deliberately.

### 8.2 Channels

`channels.yaml`:

```yaml
channels:
  - id: tg-mark
    kind: telegram-bot
    token_ref: "keychain://mark-claw-mark/telegram-bot-token"
    chat_id: 123456789          # obtained once via getUpdates after /start (tools §6)
routing:
  urgent: [tg-mark]             # problem-class email/chat alerts
  system: [tg-mark]             # source-down, guard trips, approval reminders
```

Ships with the **Telegram bot adapter only** (`sendMessage`, Retry-After-aware backoff on 429/5xx). Pushover / iMessage-to-self are documented future adapters (tools §6) — config additions, no core change. SMS dropped (tools §6).

### 8.3 Alert semantics

- **Dedup:** `dedup_key = hash(source, thread-or-service, kind)`; a key re-alerts only after a suppression window (default 4h) unless severity escalates.
- **Rate cap:** max N urgent alerts per sweep (default 5); overflow collapses into one "N more urgent items — see briefing/inbox" message.
- **History:** every send appended to `state/alerts/YYYY-MM.jsonl` (`{ts, severity, source, title, dedup_key, channel, ok, ref}`) — feeds the briefing alerts-recap and the dashboard.
- Ephemeral-tier sources alert by pointer only (§5.3).

---

## 9. Local status dashboard (decision 2026-07-05: loopback server)

### 9.1 Shape

A single Python-stdlib HTTP server (`mclaw-dashboard`), bound to `127.0.0.1:<port from settings.yaml>`, launchd KeepAlive. **Renders state files on each request** — always current, no regeneration cadence, no cache. ~200–300 lines, zero frameworks, zero JS build; one HTML page (+ `?json=1` for raw data). No STATUS.md vault mirror (declined this round; trivial to add later as another renderer over the same files).

### 9.2 Read-only by construction

- Serves **GET only** (any other method → 405). No mutation endpoints exist.
- Reads through an explicit directory allowlist: `runs/`, `cursors/`, `alerts/`, `review-queue/`, `changelog/`, `triage/`, `quarantine/` (names/counts only, never content). `state/secrets/`, `spool/ephemeral/` are not in the allowlist — the server has no code path to them.
- **No pipeline cooperates with it**: everything it renders (§6.2 run records, cursor files, alert/changelog/queue JSONL) is written by pipelines for their own operation. Killing the dashboard changes nothing; deleting a state file degrades one panel to "no data."

### 9.3 Panels

| Panel | Source | Content |
|---|---|---|
| Pipeline health | `runs/*.json`, `runs/history.jsonl` | Last start/success/result/duration per pipeline; red past 2× cadence; sparkline of recent runs |
| Cursor freshness | `cursors/*.json` | Per-source last-advance time vs expected cadence; consecutive-failure counts; degraded/down flags |
| Triage counts | `triage/*/YYYY-MM.jsonl` (aggregated on request; volumes make this cheap) | Per-bucket counts today/7d per account; LLM-vs-rule ratio; junk-rescue count |
| Alert history | `alerts/*.jsonl` | Recent alerts, severity, delivery result |
| Pending approvals | `review-queue/pending.jsonl` | Open sign-off/question items, age |
| Guard & quarantine | `changelog` (`guard.trip`), `quarantine/` listing | Trip count, quarantined artifact names + ages |
| Cost | run records | `total_cost_usd` / token tallies, 7-day trend |

---

## 10. Self-improvement loop

### 10.1 Buckets (spec §9) — routing

| Bucket | Trigger examples | Mechanics |
|---|---|---|
| **Auto-apply** | Sender-level rule from a relabel (§4.3); label correction; wiki backlink fix; spool/state prune | Applied immediately by the detecting pipeline; `changelog` record with `approval: auto-apply`; visible in weekly review's "what I changed" section |
| **Needs sign-off** | New classification rule/pattern or category; purge-policy change; new/edited pipeline or prompt; **proposed exclusions** (assistant may propose, never apply — spec §7.3); anything quality-degrading | Appended to `review-queue/pending.jsonl`; rendered into the weekly review note; applied only by `mclaw apply-approvals` after human markup |
| **More context** | Ambiguities (e.g., "is sender X a real contact or persistent SDR?") | Same queue, `kind: question`; answers free-text in the review note |

Inputs: relabel events, in-session feedback (EOD/interactive sessions can append queue items), the weekly self-audit (classification-accuracy stats from `triage/` + `examples/`), and session-history analysis.

### 10.2 Queue record + review-note format

`review-queue/pending.jsonl`:

```json
{"id":"P-2026-034","kind":"proposal","bucket":"sign-off","created":"2026-07-12T09:06Z",
 "title":"New rule: vendor-SDR pattern","proposal":{"rule_yaml":"…"},
 "evidence":["relabel:…","relabel:…"],"risk":"may over-junk borderline vendors"}
```

The weekly review note (`wiki/reviews/YYYY-Wnn.md`) is the **canonical approval UI** — a vault note, so it's reviewable and editable from the phone via Obsidian Sync:

```markdown
## Pending approvals
### P-2026-034 — New rule: vendor-SDR pattern
Evidence: 3 relabels this week (links) · Risk: may over-junk borderline vendors
```yaml
match: {from: "*@*.outreach-platform.io"}
bucket: junk
```
- [ ] approve   - [ ] reject   - [ ] approve & don't ask again for this category

### Q-2026-011 — Question
Is "Dana Kim <dana@…>" a real contact? Two borderline threads this month.
> _answer here_
```

### 10.3 Apply

`mclaw apply-approvals` (run by `mclaw-weekly`'s next cycle and available on demand): parses checkbox/answer markup, applies approved changes (rule → `rules/learned.yaml`; prompt/pipeline edits → staged as a repo diff for Mark to commit — the tooling layer stays human-committed; exclusion proposals → **rendered as a ready-to-paste `exclusions.yaml` snippet, never auto-applied**), writes `changelog` records (`approval: signed-off`, with review-note ref), removes items from the queue, and updates the note with an "applied/rejected" results section. "Don't ask again" registers a category grant in `settings.yaml → learning.auto_grant: [category-ids]`, moving that category to auto-apply going forward.

---

## 11. Constraint enforcement map & borrowed patterns

### 11.1 Allowlist-over-prompt (borrowed pattern c): every hard constraint → a structural fact

| Constraint (spec §11) | Structural fact enforcing it | Where |
|---|---|---|
| Never hard-delete | Wrappers implement no `messages.delete` / Graph DELETE; only `mclaw-mail-act trash-aged`, which internally verifies `mclaw/junk` label + ≥30d age before calling `messages.trash` / move-to-Deleted-Items | `mclaw-mail-act` |
| Nothing sent autonomously (email) | Read wrappers implement no send endpoint; drafts via `drafts.create` only; the sole send path is `mclaw-send-self`, whose token is `gmail.send`-scope-only and whose recipients must ⊆ `own_addresses` (no recipient parameter exists) | §7.3 |
| Read-only on chat | Pollers implement no post/react/mark-read calls (verified per-platform in tools §3: no read path marks read); rejected MCP servers with send tools are not installed | chat wrappers |
| No autonomous recording | `mclaw-capture` has no launchd entry and is referenced by no orchestrator; `mclaw install-schedules` refuses a schedule naming it | §1.3 |
| Exclusion hard guarantee | Fetch gate at enumeration inside shared fetch path (§5.2) + fail-closed output guard on every writer (§5.4) + canary/continuous tests (§5.5) | §5 |
| Notifications one-way | `Channel` interface has `send()` only; no inbound listener exists anywhere | §8.1 |
| Secrets never in agent context | `keychain://` refs resolve inside wrappers; Tier-2 `--allowedTools` = data paths + wrapper commands; dashboard directory allowlist excludes `secrets/` | §2.2 |
| Local scan = whitelist only | Scan roots read exclusively from `local-whitelist.yaml`; no discovery code path outside them | §5.2 |
| No personal data in tooling | Profile via env; config/state under XDG per-profile dirs; canary suite includes a repo grep for personal identifiers | §6.3 |
| Auditability | Closed-enum changelog written by every acting pipeline; `mclaw log` + dashboard render it | §4.4 |

### 11.2 Borrowed-pattern index (tools §12.3 → design)

(a) heartbeat batching → §1.4 · (b) credential isolation → §2.2, §6.4 · (c) allowlist-over-prompt → §11.1 (acceptance criterion each phase) · (d) output-side exclusion check → §5.4–5.5 · (dashboard, built bespoke) → §9.

---

## 12. Phase mapping (spec §12 → design slices)

| Spec phase | Design slices delivered | Acceptance highlights |
|---|---|---|
| **Phase 1 — Foundations** | Config/state/tooling skeletons (§6) with `mark` profile; vault layout + `VAULT-GUIDE.md` (§3.3) + Obsidian Sync; **exclusion gate + output guard + canary suite (§5) built before any ingestion**; `op` secret wiring (§2.2, §6.4); bulk-ingest pipelines (email history mining → `contacts/index.json` + dossier seeds; Slack/MM full + TG/Signal 90-day chat scan — Signal forward-only from link, so **link signal-cli day 1**; life-story session; local sweep via whitelist; Drive/OneDrive key docs) | Canary test green; exclusions live before first fetch; state wipe → rebuild works |
| **Phase 2 — Comms triage** | Rules engine + bucket taxonomy + provider label adapter (§4.1–4.2); one-time cleanup + reports (§4.5); `mclaw-sweep-15m` batched heartbeat (§1.4) + `mclaw-sweep-hourly`; notify layer + Telegram adapter (§8); junk-aging job; relabel detection + learning events (§4.3); changelog (§4.4); reply drafts; **initial dashboard** (§9) over the now-live state files | Negative-path exclusion drill (§5.5.4); no-send/no-delete tests; inbox trending to near-zero |
| **Phase 3 — Daily rituals + calls** | `mclaw-daily` briefing pipeline + weekend edition (§7.1–7.2); `mclaw-send-self` (§7.3); pmset wake install (§1.3); EOD auto-draft + VoiceInk gap interview (§7.4); transcript pipelines: Meet (Doc-via-Drive durable path), Zoom S2S VTT, `mclaw-capture` local capture + whisper.cpp STT (opt-out policy for work calendar via exclusions) | Briefing lands 08:00 with lid closed overnight; EOD files day note + tracker updates |
| **Phase 4 — Extras + learning maturity** | Waiting-on tracker automation, meeting-prep briefs, contact-dossier enrichment, weekly review (§ spec 8); full bucketed loop: review-queue + vault approval note + `mclaw apply-approvals` (§10); self-audit stats; dashboard cost/accuracy panels | ≤1–2 junk rescues/week; weekly approvals flowing; spec §13 checks pass |

---

## 13. Open items & deferred decisions

1. **Weekly review slot** defaulted to Sat 09:00 (`schedules.yaml`) — adjust in config anytime.
2. **`gws` OAuth client scope fitness** (tools §1 ASSUMED flag) — confirm during Phase C setup; fallback is our own Desktop-app client (design unchanged: wrappers abstract which client).
3. **Mattermost edition check** (Entry-tier 10k cap) — Phase 1 action item before backfill sizing.
4. **STATUS.md vault mirror** — declined this round; the renderer design makes it a ~1-hour add if mobile status visibility is missed.
5. **Cloud Routine briefing backstop** — declined (pmset chosen); revisit trigger stands (tools §11) if the Mac is routinely elsewhere/asleep at 08:00.
6. **Two-way Telegram** — out of scope by explicit decision; §8.1 deliberately designs no inbound path.

---

## Appendix A — Phase B decision log (2026-07-05)

| Question | Decision |
|---|---|
| Sleeping-Mac gap | **pmset scheduled wake** (~07:45 weekdays) for the briefing; accept 15-min alert gaps while lid closed (phone native apps cover emergencies); no cloud backstop |
| Output guard on hit | **Fail closed** — block artifact, quarantine (0700, unsynced), Telegram system alert, needs-sign-off review item |
| Dashboard form | **Loopback-only stdlib server**, render-on-request over state files; GET-only; directory allowlist; no STATUS.md mirror for now |
| Briefing email sender | **`mark@powderhorns.biz`** via dedicated `mclaw-send-self` wrapper: `gmail.send`-only token, recipients hardcoded to own addresses from config |
| Mechanical-tier language | Python throughout Tier 1 (Telethon forces Python for Telegram; one language for the tier) |
| Simplifications accepted with outline | Junk-aging/Figma/Drive ride the daily heartbeat; single 15-min chat pass does urgent + full capture; vault review note is the approval UI (no bespoke UI) |
