# MARK-CLAW — Phase Prompts

Copy/paste one prompt per fresh session. Each phase reads the prior phase's spec file(s) — no conversation history is needed. Recommended models noted per phase.

Progression: requirements (`MARK-CLAW-SPEC.md`, done) → tools (`MARK-CLAW-TOOLS.md`) → design (`MARK-CLAW-DESIGN.md`) → per-delivery-phase plans (`specs/plans/`) → execution.

---

## Phase A — Tool Selection & Feasibility Spec

**Model: Fable 5** (delegate web-research fan-out to subagents)

```
Read @specs/MARK-CLAW-SPEC.md — the requirements spec for my personal AI assistant
system. Your job in this session is tool selection and feasibility research, producing
@specs/MARK-CLAW-TOOLS.md. Do not do system design in this phase — only decide and
verify HOW each information source, output channel, and runtime capability can be
accessed, and with what tradeoffs.

Cover every source and capability the spec names:
- Email + calendars: work Google Workspace, personal Google Workspace, personal O365
  (mail read/label/archive/draft, calendar read)
- Chat: Slack (work), Mattermost, Telegram, Signal — read access, history depth,
  ~15-min polling viability. Signal and Telegram need special care (user-account vs
  bot access, linked-device approaches, ToS and reliability tradeoffs).
- Call transcripts: Google Meet, Zoom, Microsoft Teams transcript retrieval (note
  license-tier dependencies), plus local audio capture + speech-to-text options.
- Docs: Google Drive (work + personal), OneDrive. GitHub. Figma. Local filesystem.
- Notifications: SMS and Telegram outbound, behind a pluggable channel abstraction.
- Runtime: scheduling options for the 15-min/hourly/daily cadences, and where the
  pipelines themselves run (Claude Code sessions, cron, MCP servers, etc.).
- Voice input for the EOD capture workflow.

For each item produce a matrix entry: recommended option, fallback option, access
mechanism (API/MCP/export/etc.), auth model, rate/history limits, cost, risks, and a
VERIFIED vs ASSUMED flag. Verify cheaply where possible — e.g. confirm OAuth scopes
are grantable on my work account, confirm my Zoom/Teams license tier — by asking me
to run commands or check settings, rather than assuming. Interview me (AskUserQuestion)
whenever an account detail, license, admin restriction, or risk tolerance affects a
choice. Flag anything that requires my employer's admin approval.

Use web research liberally; today's ecosystem (MCP servers, APIs) moves fast, so
prefer current sources over training knowledge and cite what you relied on.

Constraints from the requirements spec that bind tool choices: never hard-delete,
read-only on chat platforms, no autonomous recording, exclusion enforcement must be
possible per source, 3-way tooling/config/state split with per-profile config.

Before writing the spec, present the decision matrix and your recommendations for my
confirmation. Then write @specs/MARK-CLAW-TOOLS.md including: the per-source matrix,
open risks, items needing admin/license action from me, and a "revisit triggers"
section (what would invalidate each choice).
```

---

## Phase B — Design Spec

**Model: Fable 5**

```
Read @specs/MARK-CLAW-SPEC.md (requirements) and @specs/MARK-CLAW-TOOLS.md (tool
selections — treat as ground truth; if a design need contradicts a tool choice, stop
and raise it rather than silently substituting). Your job in this session is system
design, producing @specs/MARK-CLAW-DESIGN.md. No implementation code in this phase.

Design at minimum:
1. Overall architecture — components, how pipelines run, how the 15-min/hourly/daily
   schedules trigger them, failure/retry behavior, and how graceful degradation works
   when a source is down.
2. Data flow — from each source through classification/summarization into the vault,
   briefings, alerts, and trackers. Include the vault's raw/ + wiki/ layout and note
   naming conventions.
3. Classification pipeline — how the five email buckets are decided (rules + LLM
   judgment split), how per-account overrides layer on common rules, how relabel
   detection feeds learning, and the changelog format.
4. Exclusion enforcement — this is a hard guarantee in the requirements. Design it as
   a choke point (single enforcement layer all source reads pass through), with the
   Blocked vs Ephemeral tier semantics, and describe how we test that nothing leaks.
5. Config & state schemas — concrete file layouts for ~/.config/mark-claw/<profile>/
   and ~/.local/state/mark-claw/<profile>/: rules format, exclusion lists, source
   definitions, credentials handling, sync cursors, review queues.
6. Briefing & EOD workflows — content assembly, delivery (email + vault note),
   the EOD auto-draft + voice gap-interview flow.
7. Notification layer — pluggable channel abstraction, Telegram bot first (one-way
   only — a two-way chat interface was explicitly declined; leave the channel
   abstraction able to grow one later, but do not design it now).
8. Self-improvement loop — the three approval buckets, review-note format, and how
   approved changes apply.
9. Local status dashboard (tools spec §7.4) — a read-only renderer over the state
   layer (pipeline health, last-run times, triage counts per bucket, alert history,
   pending approvals, cursor freshness). Static HTML or tiny loopback-only server;
   design the state-file formats so the dashboard needs no pipeline cooperation.

Framework guard: tools spec §12 records an evaluated-and-declined decision on agent
frameworks (OpenClaw, ZeroClaw, IronClaw, PicoClaw, NemoClaw, Sai, Hermes Agent,
Vellum). Do not introduce any agent-framework runtime or its plugins — the bespoke
launchd + wrappers + `claude -p` architecture is the trust boundary. DO fold in the
four borrowed patterns from §12.3 as design inputs: (a) heartbeat batching — one
batched sweep per cadence instead of N isolated polls where sources share a schedule;
(b) credential isolation — secrets resolve only inside provider wrappers, never in
agent prompts/context; (c) allowlist-over-prompt — every hard constraint must be a
structural fact of the tool surface, and the design should state where, per
constraint; (d) output-side exclusion check — a second scan for blocked-source
identifiers on everything emitted (briefings, notes, logs), in addition to the
fetch-side choke point.

Keep the "run it, don't over-engineer it" ethos: prefer boring, inspectable designs
(files over databases, prompts+rules over trained models) unless the requirements
force otherwise. Interview me (AskUserQuestion) on genuine design tradeoffs — not on
things the two specs already answer. Suggest simplifications where the requirements
allow them.

Before writing, present the architecture outline for my confirmation.
Also ask me at this time and as needed during development for any
decisions - tradeoffs, risk considerations, judgement calls or uncertainty.

Then write @specs/MARK-CLAW-DESIGN.md, structured so each delivery phase (spec §12) maps to a
clearly identifiable slice of the design.
```

---

## Phase C — Implementation Plan (run once per delivery phase)

**Model: Fable 5 or Opus 4.8**

Replace `<N>` with the delivery phase number (1–4) from `MARK-CLAW-SPEC.md` §12. Run Phase 1 first; run later ones only after the previous phase is built and shaken out.

```
Read @specs/MARK-CLAW-SPEC.md (requirements), @specs/MARK-CLAW-TOOLS.md (tools), and
@specs/MARK-CLAW-DESIGN.md (design). Your job is to produce a concrete implementation
plan for delivery Phase <N> only (as defined in the requirements spec §12 and the
design spec's phase mapping), written to @specs/plans/PHASE-<N>-PLAN.md.

Also read any completed plans in @specs/plans/ and their status notes so you build on
what exists rather than re-planning it.

The plan must:
- Break the phase into small, independently verifiable steps, each with concrete
  acceptance checks (real commands to run, real behavior to observe — not "works").
- Sequence auth/credential setup and any admin-approval items first, since they block
  everything and may involve waiting on external parties.
- State for each step what lands in the tooling repo vs config vs state (the 3-way
  split is a hard requirement — nothing personal in the repo).
- Include verification steps for the hard guarantees touched by this phase: exclusion
  enforcement, no-delete, read-only chat, no autonomous sending/recording — verified
  structurally (the send/delete call is absent or deny-listed in the wrapper), not by
  prompt inspection (tools spec §12.3).
- Honor the no-agent-framework decision (tools spec §12): no OpenClaw/ZeroClaw/etc.
  runtimes or plugins may appear as dependencies. Place the status dashboard's
  minimal slice wherever the design's phase mapping puts it.
- Identify what to build minimally now vs stub for a later phase, honoring the
  "don't over-engineer" ethos.
- End with a shakeout checklist: how I exercise the phase's output for a few days and
  what feedback we collect before starting the next phase.

Interview me (AskUserQuestion) only where the specs leave an implementation choice
genuinely open. Do also ask me at this time and as needed during development for any
decisions that are not clear - tradeoffs, risk considerations, judgement calls or
uncertainty. Before writing the file, present the step outline for confirmation.

When done create linear issues for each phase in the liner project at 
https://linear.app/convoyai/project/ai-personal-assistant-d00469515f38/overview
and update the implementation plan with the Linear Issue IDs for each phase.

```

---

## Phase D — Execution (run once per delivery phase, after its plan)

**Model: Opus 4.8 or Sonnet 5** (escalate to Fable 5 for classification pipeline and exclusion-enforcement work)

```
Read @specs/plans/PHASE-<N>-PLAN.md and execute it step by step. Context lives in
@specs/MARK-CLAW-SPEC.md, @specs/MARK-CLAW-TOOLS.md, and @specs/MARK-CLAW-DESIGN.md —
consult them when a step is ambiguous; if plan and design conflict, stop and ask.

Rules of engagement:
- Complete each step's acceptance checks before moving on; show me the evidence.
- Stop and ask before anything hard to reverse, anything touching live mailbox
  contents beyond labels/archiving, and any credential/auth step I must do myself
  (tell me exactly what to click/run).
- Hard guarantees are non-negotiable: never hard-delete, read-only on chat, no
  autonomous sending or recording, blocked sources never appear in any output. If a
  step would violate one, stop. Enforce them structurally — never-implemented or
  deny-listed calls in the wrappers — not via prompts (tools spec §12.3).
- No agent-framework dependencies (tools spec §12): do not install or wrap
  OpenClaw/ZeroClaw/IronClaw/PicoClaw/NemoClaw/Hermes/Vellum or their plugins.
  Pipelines stay small scripts + `claude -p`.
- Secrets never enter agent prompts or `claude -p` context — they resolve only
  inside the provider wrappers (tools spec §7.3/§12.3).
- Keep the 3-way split clean: no personal data or secrets in the repo; config in
  ~/.config/mark-claw/mark/; state in ~/.local/state/mark-claw/mark/.
- Update the plan file as you go: mark steps done, record deviations and why.
- When the phase is complete, write a short status note at the bottom of the plan
  file (what shipped, what's stubbed, known issues) — the next planning session
  reads this.
```

---

## Notes

- Each prompt assumes a fresh session in this repo with the referenced spec files present.
- If a phase's session dies midway, restart with the same prompt — each prompt instructs the model to read existing outputs and continue rather than restart.
- After Phase D of delivery Phase 1, loop: Phase C prompt for Phase 2, then Phase D, and so on through Phase 4.
