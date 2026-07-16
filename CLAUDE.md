# mark-claw

Personal AI assistant system: email/chat triage, daily briefings, and an Obsidian knowledge vault. This repo is the **tooling layer only** — it holds no personal data and no secrets.

## Authority chain

`specs/MARK-CLAW-SPEC.md` → `specs/MARK-CLAW-TOOLS.md` → `specs/MARK-CLAW-DESIGN.md` → **`CLAUDE.md`** (hard rules + commands) → `specs/plans/*-PLAN*.md` (what/when) → `specs/WORKFLOW.md` (how to execute).

On conflict, the higher document wins and the lower one is updated in the same PR. Escalate with `AskUserQuestion` before deviating.

## Hard rules

These are **structural**, not advisory: each is enforced by a fact of the tool surface — an endpoint that does not exist, a deny-list in a wrapper, a directory a writer cannot reach. Design §11.1 maps every constraint to its enforcing fact. A change that weakens one is a spec change, not an implementation choice.

| Rule | Structural enforcement |
| --- | --- |
| **Never hard-delete** | Wrappers implement no `messages.delete` / Graph `DELETE`. Deletion happens only by moving 30-day-aged junk to provider Trash/Spam; the provider's retention deletes it. |
| **Never send autonomously** | Read wrappers implement no send endpoint. Email drafts only (`drafts.create`). The sole send path is `mc-send-self`, whose token is `gmail.send`-scope-only and whose recipients must ⊆ `own_addresses`. |
| **Read-only on chat** | Pollers implement no post/react/mark-read call. API-method allowlists per platform; the Telethon wrapper exposes no send surface. |
| **No autonomous recording** | `mc-capture` is on-demand only. No launchd entry may reference it; `mc install-schedules` refuses a schedule naming it. |
| **Exclusion enforcement is absolute** | Fetch gate at enumeration inside the shared fetch path + fail-closed output guard on every writer. A blocked source appears in no output, log, or vault artifact. |
| **Secrets never reach agent context** | `op://` refs resolve inside wrappers only. `claude -p` receives file paths and content, never credentials. |
| **Local scan is whitelist-only** | Scan roots read exclusively from `local-whitelist.yaml`. No code path accepts a root argument. |
| **No personal data in this repo** | Profile comes from `MC_PROFILE` (default `mark`); all personal values live in config. `specs/` is a **known, accepted exception** — it predates the split and contains real domains. |
| **No agent frameworks** | Evaluated and declined (design §preamble, tools §12). A dependency deny-list test guards `pyproject.toml`. |

**Fail closed on the hard guarantees; fail open on everything else.** An exclusion violation blocks the artifact. A dead source never blocks a briefing.

## Architecture

Three tiers, per design §1:

- **Tier 0 — launchd** LaunchAgents provide schedules. Plists are generated from `config/schedules.yaml` by `mc install-schedules`.
- **Tier 1 — Python wrappers** hold credentials, talk to providers, and write JSONL to a spool.
- **Tier 2 — agentic layer** (`claude -p`) reads spool files and never sees secrets. Prompts live in `prompts/`.

Two principles drive most design questions:

- **Files over databases.** All state is JSON/JSONL/markdown under XDG paths — inspectable with `cat`, fixable with `$EDITOR`, rebuildable by re-scanning.
- **No daemons** except the read-only loopback dashboard. Everything else is a stateless launchd invocation: read config + state, work, write state, exit.

> **This stack uses no Prefect, no Postgres, and no database of any kind.** `specs/WORKFLOW.md` §3.5 and §4.5 are rendered from a shared `python-uv` profile and describe a Prefect server, a Prefect worker pane, an external Postgres, and `prefect_test_harness`. **None of that applies here** — it is inherited template boilerplate. Ignore it.

### The three-way split

| Layer | Home | Contents |
| --- | --- | --- |
| **Tooling** | this repo | Skills, pipelines, prompts, schedulers, docs, specs. No personal data, no secrets. |
| **Config** | `~/.config/mark-claw/<profile>/` | Accounts, `op://` credential refs, rules, exclusion lists, local whitelist, schedules, vault path. Human-readable, hand-editable. |
| **State** | `~/.local/state/mark-claw/<profile>/` | Cursors, spool, caches, changelogs, learned examples. Rebuildable without losing config. |

Tooling upgrades must never require touching config or state. Wiping state must cost only a re-scan.

## Files

| File | What | When to read |
| --- | --- | --- |
| `specs/MARK-CLAW-SPEC.md` | Requirements: sources, triage buckets, exclusions, phasing | Any question of what the system must do |
| `specs/MARK-CLAW-TOOLS.md` | Ground truth for provider access paths and their limits | Choosing or changing how a source is reached |
| `specs/MARK-CLAW-DESIGN.md` | Architecture, data flow, config/state schemas, §11.1 constraint map | Implementing anything; §11.1 before touching a hard rule |
| `specs/WORKFLOW.md` | Per-PR playbook (generated — edit the template, not the file) | Driving a PR by hand; recovering a derailed agent |
| `specs/PROMPTS.md` | Session prompts used to produce the specs | Re-running or extending a planning phase |

## Subdirectories

| Directory | What | When to read |
| --- | --- | --- |
| `specs/` | Requirements, tools, design, workflow | Start here — the authority chain lives here |
| `specs/plans/` | Per-phase implementation plans with Linear issue links | Executing a phase; `PHASE-1-PLAN.md` is current |
| `.claude/` | Agents, skills, conventions, settings | Understanding delegation and review conventions |

The runtime tree (`bin/`, `mc_core/`, `prompts/`, `plists/`, `tests/`) is specified in design §6.3 and created by DEV-12.

## Build

```bash
uv sync
```

## Test

```bash
uv run pytest
uv run mc test --canary   # exclusion canary suite; CI-blocking
```

## Development

Local Definition of Done — all green before opening a PR:

```bash
uv lock --check && uv run ruff check . && uv run mypy && uv run pytest && uv run mc test --canary
```

Plus `/stack-check origin/main...HEAD` clean and the issue's acceptance criteria demonstrably met.

The canary suite blocks any commit touching `mc_core/`, `bin/`, or a vault writer. It is the test that proves the exclusion guarantee bites — never weaken it to make a build pass.

**None of this toolchain exists yet.** `pyproject.toml`, the `mc` CLI, and the canary suite arrive with DEV-12 (skeleton) and DEV-16 (canary). Until then these commands fail, and that is expected.

## Workflow

**One Linear issue = one branch = one PR.** The standard path is `/phase <issue-id>`, which resolves the issue, brings up the branch/worktree, delegates the build to `common-developer` (TDD-first), runs the gates, reviews, and opens the PR. The human always merges; Claude never does.

Issues live in Linear team **DEV**, project [Agentic and AI Tooling](https://linear.app/psols/project/agentic-and-ai-tooling-ea2f10db893e). Use the **`linear-server-psols`** MCP — `linear-server-convoy` is a different workspace and is never correct for this repo. An issue's acceptance criteria are the PR's Definition of Done.

Read `specs/WORKFLOW.md` for the full loop, the worktree/tmux setup, and the §8 escape hatch (WIP-commit + `HANDOFF.md`) for when an agent derails.

### Skills

| Skill | When |
| --- | --- |
| `/phase <issue-id>` | Standard path for one unit of work |
| `/stack-check` | Pre-PR conformance audit of the working diff |

### Agents

| Agent | When |
| --- | --- |
| `common-developer` | Spec → code + tests (the default build delegate) |
| `architect` | Design calls not settled by the specs |
| `common-quality-reviewer` | Pre-PR risk/conformance review |
| `technical-writer` | Docs, README, CLAUDE.md updates |
| `debugger` | Hard bugs — evidence-gathering, not guessing |
| `common-worktree` | Branch/worktree bring-up |

Conventions in `.claude/conventions/` are auto-applied by these agents.

## Generated files

`specs/WORKFLOW.md`, `AGENTS.md`, `GEMINI.md`, `.claude/agents/*`, `.claude/skills/*`, `.claude/commands/*`, `.claude/conventions/*`, `.claude/settings.json`, `.cursor/`, and `.vscode/` are rendered by `cwft` from templates. **Never hand-edit them.** Change `.cwft-settings.yaml` and run `cwft ai refresh`.

`.cwft-ai-manifest.json` is the authoritative list — `cwft ai status` reports drift. Most rendered files also open with a `cwft-ai` sentinel comment, but do not rely on grepping for it: this file discusses the marker in prose and will match. `CLAUDE.md` itself is hand-authored and absent from the manifest.

Where a rendered file's generic profile contradicts this repo's design — the Prefect/Postgres notes above being the live example — **CLAUDE.md wins**, per the authority chain.
