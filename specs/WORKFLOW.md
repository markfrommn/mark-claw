<!-- BEGIN cwft-ai specs set-v1 1d9ad20bd38daa0f83192ed5f450c478d06c04b2 — rendered by cwft; edit the template, not this file -->
# Workflow — mark-claw

This is the **operational playbook** for building this repo with Claude Code: the per-PR loop, the
terminal/worktree setup, how the human and Claude hand work back and forth,
and how to recover when an agent goes off the rails. It is rendered for this repo specifically —
everything below is already specialized to this stack, so there is nothing to cross-reference and no
other workflow file to read.

**Authority chain (this repo):** (none) → `CLAUDE.md` (hard rules +
commands) → the implementation plan doc(s) `(none)` (one or more paths,
space-separated; what/when) → this playbook (how to execute). On any conflict, the higher document
wins and the lower one is updated.

---

## 0. The loop in one breath

> **One Linear issue = one branch = one PR.** Pick the next unblocked issue → **run
> `/phase <issue-id>`**, which brings up the branch/worktree, delegates the build to the
> `common-developer` agent (TDD), runs the gates, reviews, opens the PR → the human merges → the issue auto-closes →
> the worktree is cleaned up. Repeat.

`/phase` is the standard path and automates §2–§7 below. The rest of this document is **what `/phase`
does** — read it to understand the loop, to drive a step by hand when you take over, and for the
escape hatch (§8) when something derails.

---

## 1. Roles

| Actor | Responsibility |
| --- | --- |
| **Human driver (you)** | Picks the next issue, launches/steers Claude (usually via `/phase`), owns design judgment calls, resolves "off the rails" situations, gives final PR approval, **merges**. |
| **`/phase` orchestrator** (interactive Claude session) | The default executor: one session per PR. Resolves the issue, brings up the branch/worktree, delegates the build, runs gates, opens the PR, works the bot sequence — stopping at the human merge gate. |
| **Subagents** (`common-developer`, `architect`, `common-quality-reviewer`, `technical-writer`, `debugger`, `common-worktree`) | Delegated specialists the orchestrator calls: `common-developer` writes the code+tests TDD-first, `architect` for design calls, `common-quality-reviewer` for the pre-PR review, `technical-writer` for docs, `debugger` for hard bugs, `common-worktree` for branch/worktree bring-up. |

The human is always the merge gate. Claude never merges.

---

## 2. Source of truth: the Linear project

Issues live in this repo's Linear project(s), all under team **ISSUE**:


All issues live in the **ISSUE** team: <https://linear.app/markfrommn/team/ISSUE/all>. Each issue maps to a
plan sub-step and carries its acceptance criteria in its description.

Rules:

- **Each issue's acceptance criteria are the per-PR Definition of Done** — read them first
  (`get_issue ISSUE-NNN`); they are the spec for the PR. `/phase` fetches the issue and sets it
  **In Progress** for you.
- **Use Linear's git branch name.** Every issue has a generated `gitBranchName` — copy it from
  Linear's **"Copy git branch name"** action. Use it verbatim so Linear's GitHub integration
  auto-links the PR and moves the issue state.
- **State transitions** are driven by the integration: branch/PR opened → *In Progress*; PR
  merged → *Done*. Set *In Progress* manually when you start if the branch link doesn't.
- **New bootstrapping tasks** (something the workflow itself needs that isn't yet an issue) →
  create a Linear issue in the appropriate project first, then work it like any other. Don't do
  un-tracked work; the issue is the progress record and the close gate.

The Linear MCP is available to Claude — it can read issue descriptions, set status, and comment.

---

## 3. Environment (macOS, terminal-first)

Assumed tools (install via Homebrew if missing): `git` (worktrees), `gh` (authed),
`tmux`, plus this stack's toolchain — **none** via
**none** (runtime **none**). Nice-to-haves:
`fzf`, `caffeinate` (keep the Mac awake during long agent runs), `terminal-notifier` or `osascript`
(desktop ping when an agent finishes).

### 3.1 Worktrees: one per in-flight PR

Each issue gets its **own git worktree** so independent issues run in parallel without colliding on
the working tree, laid out under this repo's worktree parent dir — by convention
`../mark-claw-wt/<id>` (derived from the issue id, e.g. `ISSUE-218`). Override that
location with the `workflow.worktree_parent` config key in `.cwft-settings.yaml`. Bring-up is handled
for you by `/phase` (via the **`common-worktree`** agent); the underlying turnkey command is `cwft`,
which creates the worktree, branches from `origin/main`, runs the stack's setup
(none install), and opens its own tmux session in one shot:

```bash
# /phase does this for you; by hand it's:
cwft session new <gitBranchName>                      # Linear → "Copy git branch name"
cwft session ISSUE-218                                 # reconnect later: by issue id or session name
cwft session                                         # no arg → picker across live sessions
```

The equivalent raw-git bring-up, if you ever bypass the tooling
(`<WT>` = this repo's worktree parent dir):

```bash
BRANCH=<gitBranchName>
git fetch origin && git worktree add -b "$BRANCH" "<WT>/eng-NNN" origin/main
cd "<WT>/eng-NNN"     # then run the stack's setup (see the profile notes below)
```

When done (after merge): `git worktree remove "<WT>/eng-NNN"` and `git branch -d "$BRANCH"`. **Some
repos use a plain branch instead of a worktree** (they pass `--branch-only`) — the profile notes at
the end of this section say which bring-up applies here.

### 3.2 tmux: one session per issue

One tmux **session per issue/worktree**, split into panes for the things you watch at once — pane 0
the agent/driver, panes 1..N this repo's `workflow.panes` (its committed `.cwft-settings.yaml` key,
typically the dev server and the test watcher). `cwft session new` builds the layout as part of the
turnkey setup; pass `--no-dev` / `--no-test` (or `--no-pane <name>`) to leave individual panes a
plain shell. Drive Claude in pane 0; glance at the other panes for live feedback. Parallel issues =
parallel sessions, each its own worktree + Claude driver — **don't run two Claude sessions in one
worktree.**

- **Dev-server port:** n/a
- See the profile notes below for this stack's exact pane commands and any parallel-worktree port
  caveat.

### 3.3 Cursor: the human's editor

Claude drives in the terminal; **Cursor is where the human reads, reviews, and takes over** (§8
hand-offs, bot-finding fixes, design tweaks). Each worktree is a normal folder — open it **as its own
Cursor window** (`cursor "<WT>/eng-NNN"`):

- **Open the worktree, not the main checkout.** A worktree has its own installed dependencies and
  its own checked-out branch, so opening the worktree folder is what makes Cursor's language server
  resolve the right modules and its Git panel show the right branch. Editing a worktree's files from
  a window rooted at the main checkout will mislead both.
- **One Cursor window per worktree**, mirroring one tmux session / one Claude session per worktree.
- **Shared config is committed and auto-inherited by every worktree** (tracked files):
  - `.vscode/settings.json` — this stack's formatter (**none**) runs on save;
    Prettier only for Markdown; heavy/generated dirs excluded from search/watch. (Cursor reads
    `.vscode/`.)
  - `.vscode/extensions.json` — the baseline recommended extensions (a stack-agnostic set; per-stack
    editor specifics, if any, are in the profile notes). Accept the prompt when there is one, so
    inline lint/format match CI.
  - `.cursor/rules/common-workflow.mdc` — an always-on Project Rule that teaches **Cursor's own AI**
    the workflow guardrails and hard rules.

### 3.4 Cursor caveats (don't let it break the workflow)

1. **One driver per worktree at a time.** Never run Cursor's Agent/Composer on a worktree while a
   Claude Code session is editing it — two writers on the same files corrupt each other's edits.
2. **Disk-vs-buffer.** If Claude changed files while you had them open, Cursor shows *"file changed
   on disk."* **Reload/revert to disk** — don't save your stale buffer over Claude's work.
3. **Let the stack's formatter own formatting.** The committed settings make **none**
   the formatter for code; Prettier is Markdown-only. Don't switch the default or add a second
   format-on-save formatter — it creates PR churn the bots flag.
4. **Never hand-edit generated files** (this stack's list is in the profile notes), including via
   Cursor tab-complete or Agent. Regenerate via the stack's codegen loop. `/stack-check` is the
   backstop.
5. **Respect the branch/PR model.** If you commit from Cursor's Git panel, commit/push to the
   issue's Linear branch only — don't create side branches, merge to `main`, or let Cursor's Agent
   open its own PR. The PR is created and shepherded per §4 / §7.
6. **Shared `.git`.** All worktrees point at one `.git`. Per-window Git ops are fine, but don't run
   `git worktree`/branch surgery from inside a worktree window without meaning to.

### 3.5 Stack profile notes (this repo)

- **No established toolchain yet.** This repo runs the generic profile (`ai.stack: generic`) — there is no language-specific setup command, dev-server pane, formatter, or codegen loop wired in.
- **Setup command.** None — run whatever this repo's own README/CI documents, if anything.
- **Dev loop.** Pane 0 (the agent) is the whole session until this repo adopts a real stack profile with its own dev-server/test-watch panes.
- **Generated files.** None known. If this repo establishes a codegen loop later, list it here (or move to a real stack profile that already documents one).
- **Project skills.** `/stack-check` (generic variant — the dependency-approval and generated-files checks are no-ops until this repo has real stack content).
- **Escalation harness.** `Plan` agent / plan mode.
- **Changesets.** None assumed.
- **Editor extensions.** None — the `.vscode/` baseline shipped here is stack-agnostic (no language extensions or formatter binding).


---

## 4. The per-issue execution loop

**The standard path is `/phase <issue-id>`**, which runs the steps below end-to-end. This section is
what it does, step by step — read it to understand the loop and to drive a step by hand when you take
over.

### 4.1 Pick the next unblocked issue

Respect the dependency graph in the plan doc(s) `(none)`. `/phase` resolves the phase/issue
argument to the exact issue(s) this one PR closes, sets it **In Progress**, and reads its
acceptance criteria — that's the DoD.

### 4.2 Bring up the branch/worktree (§3)

`/phase` delegates to the **`common-worktree`** agent (worktree, or branch-only per the profile
notes), launches the build in a fresh context, and drives from pane 0.

### 4.3 Delegate the build (Hybrid model)

- **Default — delegate to `common-developer`.** `/phase` builds a context packet (issue AC, plan
  section, cited design sections, this stack's standing constraints) and spawns the `common-developer`
  agent to write the code TDD-first. Most issues (S/M) are one focused delegation.
- **Escalate** for **large or internally-parallel** issues: decompose and delegate to
  `common-developer` in waves, calling `architect` for design calls and `common-quality-reviewer`
  before the PR.
- **Delegate opportunistically** even mid-loop: `architect` for a design call, `common-developer`
  for a well-specified chunk, `common-quality-reviewer` before you open the PR, `technical-writer`
  for doc issues, `debugger` when stuck (see §8).

### 4.4 Use the project skills

These encode the spec's conventions — prefer them over ad-hoc work. This stack's skills are placed
under `.claude/skills/` and `.claude/commands/` (see the profile notes for the stack-specific ones);
every repo has **`/stack-check`** (audits the working diff against the stack's rules: approved stack
only, no air-gap/CDN breaks, no hand-edited generated files, pattern/boundary conformance,
dependency hygiene — run before every PR) plus its codegen loop where it has one. **Never hand-edit
generated files** — regenerate via the stack's loop.

### 4.5 TDD-first

Build test-first where practical: **write the test from the acceptance criteria, watch it fail,
implement until green**, using this stack's test stack:

> No stack-specific test harness is assumed. Use whatever test framework
fits this repo's language(s); keep tests colocated with the code they
cover and wire them into this repo's own gate command.


Mock the data/tenancy or network boundaries for unit tests; reserve a live DB / real backend for e2e.

### 4.6 Commit cadence (enables clean recovery)

Commit at **logical checkpoints**, small and often, on the issue branch (never on `main`). Small
commits make the escape hatch (§8) and bot review cheap. Follow repo commit conventions; commits
authored in a Claude session carry the
`Co-Authored-By: Claude <noreply@anthropic.com>` trailer.

### 4.7 Local Definition of Done (must be green before the PR)

Run this stack's **local gates** (working directory per this repo's profile notes, §3.5 — it
varies by stack):

```bash
true
```

Plus: **`/stack-check origin/main...HEAD` clean**; **no codegen drift** if the schema/API surface was touched (the
stack's codegen loop produces no new output on a clean tree); a **changeset** for version-affecting
changes if this stack requires them (see profile notes); and the issue's own acceptance criteria
demonstrably met. For endpoint/UI changes, **verify against the running app/server**:
(none) — not just via tests.

### 4.8 Open the PR

```bash
git push -u origin "$BRANCH"
gh pr create --base main --fill   # title references the issue; body links it
```

- **Link the issue** so merge auto-closes it: the Linear branch name auto-links, and/or put
  `Closes ISSUE-NNN` (Linear magic word) in the PR body.
- **Keep PRs reasonably scoped** — one sub-step; the PR is the progress/close gate.
- Open as a normal PR (or draft while iterating).

---

## 5. Per-issue checklist (copy-paste)

```text
[ ] Run /phase <issue-id>  (does the steps below; the rest is what it automates)
[ ] Linear issue read; acceptance criteria = my DoD; set In Progress
[ ] Branch/worktree via common-worktree (or branch-only per the profile notes); stack setup
[ ] tmux session per profile notes (pane 0 claude, panes 1..N this repo's workflow.panes) — skip
    entirely for a branch-only profile (no worktree, no panes)
[ ] Delegate to common-developer — or escalate for large/parallel issues
[ ] TDD: test from acceptance → fail → implement → green
[ ] Codegen/sync per this stack (never hand-edit generated files) — see profile notes
[ ] Small commits at checkpoints (issue branch only)
[ ] Gates (working directory per profile notes): true
[ ] /stack-check origin/main...HEAD clean; no codegen drift (if schema/API touched); changeset if this stack needs one
[ ] Verify in the running app/server ((none)) for endpoint/UI changes
[ ] Push; gh pr create; body: "Closes ISSUE-NNN"
[ ] Human approves + merges; issue → Done; remove worktree
```

---

## 6. Phasing and sequencing

Take phasing and sequencing from the plan doc(s) `(none)`, which is generally supplied by your
prompt (and by the `/phase` argument). Run independent issues in parallel worktrees; respect the
plan's dependency edges, and don't fan out past a barrier/join point prematurely.

---



---

## 8. When the agent goes off the rails (suspend / resume)

The protocol is **WIP-commit + handoff note**, so a human can take over in the same worktree and hand
back without losing work.

**Trip-wires — stop and invoke the hatch when:**

- the same error/test fails ~3 times despite different fixes,
- `/stack-check` keeps failing or Claude proposes editing a **generated file** to fix a type error,
- the diff is drifting outside the issue's scope, or a design judgment call appears that isn't in the
  issue or the specs (use `AskUserQuestion` first — only escalate to a human takeover if that doesn't
  resolve it),
- Claude is looping (re-running the same commands, re-reading the same files with no progress).

**Suspend (Claude or human triggers):**

1. **WIP-commit** everything on the branch: `git add -A && git commit -m "wip(ISSUE-NNN): <where I am>"`.
2. **Write a handoff note** to `HANDOFF.md` at the worktree root:

   ```markdown
   # HANDOFF — ISSUE-NNN
   - **Goal / acceptance:** <the issue's DoD>
   - **Done so far:** <what works, what's committed>
   - **Stuck on:** <the specific failure, with the exact error / file:line>
   - **Tried:** <approaches already ruled out>
   - **Suspected cause / next idea:** <hypothesis>
   - **Resume from:** <the file/step to pick up at>
   ```

3. **Stop.** Tell the human it's parked.

**Human takeover:** **first confirm the Claude session is stopped** (the one-driver rule), then open
the worktree in Cursor, fix it, commit to the issue branch, and update `HANDOFF.md` with what
changed. Don't use Cursor's Agent on the worktree while Claude is still live.

**Resume:** relaunch Claude in that worktree with *"Read `HANDOFF.md` and resume ISSUE-NNN from
there."* Before opening the PR, **delete `HANDOFF.md`** (it's a scratch artifact; keep it out of the
final diff — it's fine on intermediate WIP commits).

For genuine debugging dead-ends, delegate to the **`debugger`** subagent (systematic
evidence-gathering) rather than continuing to guess.

---

## 9. Harness reference

**Skills** (invoke as `/name`): `/phase` (the per-PR runbook above), `/stack-check`, plus this
stack's skills (see the profile notes). **Subagents** (via the Agent tool): `common-worktree`
(branch/worktree bring-up), `common-developer` (spec→code+tests), `architect` (design/conventions),
`common-quality-reviewer` (pre-PR risk/conformance review), `technical-writer` (docs — own the doc
issues and any `CLAUDE.md`/README updates), `debugger` (hard bugs). **Conventions** live in
`.claude/conventions/` (code-quality, structural, documentation) and are auto-applied by the agents.

**Context hygiene (Anthropic guidance):** keep one issue per session; `/clear` between unrelated
tasks so the context stays focused; point Claude at `CLAUDE.md` + the specific issue rather than
dumping the whole plan; be specific and course-correct early rather than letting it run long.

---

## 10. Best-practices cheatsheet

- **Explore → Plan → Code → Commit.** Read the issue + code, plan before editing; `/phase` frames
  this for you.
- **TDD where practical** — test from acceptance criteria, fail, implement, pass.
- **Small, frequent commits and PRs** — one sub-step per PR; it's the review unit and the close gate.
- **Let Claude verify itself** — it has the gates (`true`, `/stack-check`) and the
  running app/server ((none)). Make it prove the DoD.
- **Respect the boundaries every time** — air-gap (no CDNs/SaaS at runtime), this stack's core
  pattern and boundaries, and generated files (regenerate, never hand-edit). `/stack-check` is the
  backstop.
- **Use `AskUserQuestion` for judgment calls**, the escape hatch for derailment, and a human merge
  for every PR.
- **Keep the docs honest** — when execution reveals the plan/specs are wrong, update them (and this
  playbook's source template) as part of the PR.

---

## 11. Quick command appendix

`<WT>` = this repo's worktree parent dir (`../mark-claw-wt/<id>` by default).

```bash
# per-PR runbook (the standard path)
/phase ISSUE-NNN

# new worktree + tmux session for an issue (branch name from Linear) — what /phase orchestrates
cwft session new "$BRANCH"
# …or by hand:
git fetch origin && git worktree add -b "$BRANCH" "<WT>/$ID" origin/main
cd "<WT>/$ID"     # then run the stack setup (see §3.5 profile notes)

# open that worktree in Cursor for human review/edit (one window per worktree)
cursor "<WT>/$ID"

# local gates (the per-PR DoD) — working directory varies by stack, see §3.5 profile notes
true
# + /stack-check origin/main...HEAD   (+ the stack's codegen loop shows no drift if schema/API changed)

# open the PR
git push -u origin "$BRANCH"
gh pr create --base main --fill

# cleanup after merge
git worktree remove "<WT>/$ID" && git branch -d "$BRANCH"

# macOS niceties
caffeinate -dimsu claude         # keep the Mac awake while a long agent run proceeds
```
<!-- END cwft-ai specs set-v1 1d9ad20bd38daa0f83192ed5f450c478d06c04b2 -->
