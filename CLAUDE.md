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
| **Never send autonomously** | Read wrappers implement no send endpoint. Email drafts only (`drafts.create`). The sole send path is `mclaw-send-self`, whose token is `gmail.send`-scope-only and whose recipients must ⊆ `own_addresses`. |
| **Read-only on chat** | Pollers implement no post/react/mark-read call. API-method allowlists per platform; the Telethon wrapper exposes no send surface. |
| **No autonomous recording** | `mclaw-capture` is on-demand only. No launchd entry may reference it; `mclaw install-schedules` refuses a schedule naming it. |
| **Exclusion enforcement is absolute** | Fetch gate at enumeration inside the shared fetch path + fail-closed output guard on every writer. A blocked source appears in no output, log, or vault artifact. |
| **Secrets never reach agent context** | `keychain://` refs resolve inside wrappers only (macOS Keychain via `security`). `claude -p` receives file paths and content, never credentials. |
| **Local scan is whitelist-only** | Scan roots read exclusively from `local-whitelist.yaml`. No code path accepts a root argument. |
| **No personal data in this repo** | Profile comes from `MCLAW_PROFILE` (default `mark`); all personal values live in config. `specs/` is a **known, accepted exception** — it predates the split and contains real domains. |
| **No agent frameworks** | Evaluated and declined (design §preamble, tools §12). A dependency deny-list test guards `pyproject.toml`. |

**Fail closed on the hard guarantees; fail open on everything else.** An exclusion violation blocks the artifact. A dead source never blocks a briefing.

## Architecture

Three tiers, per design §1:

- **Tier 0 — launchd** LaunchAgents provide schedules. Plists are generated from `config/schedules.yaml` by `mclaw install-schedules`.
- **Tier 1 — Python wrappers** hold credentials, talk to providers, and write JSONL to a spool.
- **Tier 2 — agentic layer** (`claude -p`) reads spool files and never sees secrets. Prompts live in `prompts/`.

Two principles drive most design questions:

- **Files over databases.** All state is JSON/JSONL/markdown under XDG paths — inspectable with `cat`, fixable with `$EDITOR`, rebuildable by re-scanning.
- **No daemons** except the read-only loopback dashboard. Everything else is a stateless launchd invocation: read config + state, work, write state, exit.

> **Rendered workflow warning.** The shared `python-uv` profile currently injects assumptions from a different product into `specs/WORKFLOW.md`, `/phase`, `/stack-check`, `common-developer`, `common-quality-reviewer`, and the Cursor rule. Mark-Claw has no Prefect, Postgres, application database, SQLAlchemy/psycopg layer, ORM, SQL migrations, vendored schema contracts, SBOM/native-packaging stack, or air-gapped runtime. Provider APIs and `claude -p` require network access. It also has no `specs/TOOLSPEC.md`; dependency choices come from `specs/MARK-CLAW-TOOLS.md`. Ignore inherited references to those foreign constraints, to Pydantic/Hatchling/httpx-only/nFPM/CycloneDX requirements, and to gates or codegen paths not listed in this file. See `specs/STACK-CLEANUP-NOTES.md` for the upstream-cleanup ledger.

### The three-way split

| Layer | Home | Contents |
| --- | --- | --- |
| **Tooling** | this repo | Skills, pipelines, prompts, schedulers, docs, specs. No personal data, no secrets. |
| **Config** | `~/.config/mark-claw/<profile>/` | Accounts, `keychain://` credential refs, rules, exclusion lists, local whitelist, schedules, vault path. Human-readable, hand-editable. |
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

The runtime tree (`bin/`, `mclaw_core/`, `prompts/`, `plists/`, `tests/`) is specified in design §6.3 and created by DEV-12.

## Build

```bash
uv sync
```

## Test

```bash
uv run pytest
uv run mclaw test --canary   # exclusion canary suite; CI-blocking
```

## Development

Local Definition of Done — all green before opening a PR:

```bash
uv lock --check && uv run ruff check . && uv run mypy && uv run pytest && uv run mclaw test --canary
```

Plus `/stack-check origin/main...HEAD` clean and the issue's acceptance criteria demonstrably met.

The canary suite blocks any commit touching `mclaw_core/`, `bin/`, or a vault writer. It is the test that proves the exclusion guarantee bites — never weaken it to make a build pass.

**None of this toolchain exists yet.** `pyproject.toml`, the `mclaw` CLI, and the canary suite arrive with DEV-12 (skeleton) and DEV-16 (canary). Until then these commands fail, and that is expected.

## Workflow

**One Linear issue = one branch = one PR.** The standard path is `/phase <issue-id>`, which resolves the issue, brings up the branch/worktree, delegates the build to `common-developer` (TDD-first), runs the gates, reviews, and opens the PR. The human always merges; Claude never does.

### Execution mapping

The Phase D prompt in `specs/PROMPTS.md` predates the common workflow. Do **not** execute an entire delivery-phase plan in one long Phase D session. Execute each child issue with a fresh `/phase DEV-NNN` session, in the dependency order recorded in the plan. Independent issues may run concurrently in separate worktrees. Complete the issue's acceptance checks and show evidence before starting a dependent issue.

The plan is the cross-issue delivery record. Each repo-changing PR marks its own step done and records deviations. The phase-closing issue appends the phase status note (shipped, stubbed, deviations, and known issues) for the next planning session.

**No-repo issue exception.** Some credential, account, config, state, or other operator-run issues intentionally land no repository change. For those issues, use the same `/phase` scoping, authority, safety, acceptance, and evidence discipline, but do not create an empty branch, worktree, commit, PR, or bot-review cycle. Set the Linear issue In Progress, stop for human-only authentication or console work with exact instructions, record acceptance evidence and deviations in Linear, then mark it Done when verified. Do not open a documentation-only PR solely to tick its plan checkbox; reconcile that checkbox in the next repo-changing PR or the phase-closing issue.

### Mark-Claw execution overrides

Every `/phase` context packet, implementation delegation, review, and recovery handoff must preserve these repo-specific rules from the former Phase D prompt:

- Stop and ask when the plan conflicts with a higher authority document, before a hard-to-reverse action, before touching live mailbox contents beyond the explicitly authorized label/archive operation, and for credential/authentication steps requiring the human. State exactly what the human must click or run.
- Enforce the hard guarantees structurally: unavailable or deny-listed provider methods, read-only tokens and wrappers, the shared fetch exclusion gate, and the fail-closed output guard. Prompt instructions alone are not enforcement.
- Add no agent framework or plugin from the rejected set in `specs/MARK-CLAW-TOOLS.md` §12. Pipelines remain small Python wrappers/scripts plus `claude -p`.
- Resolve secrets only inside provider wrappers. Never place a credential, token, or resolved secret in an agent prompt, context packet, log, or `claude -p` input.
- Keep tooling, config, and state separate. Personal data and secrets stay out of this repo; config and state remain under their XDG profile roots.
- Respect the plan's explicit deferrals. A convenient adjacent feature is still out of scope until its planned issue.

The local gates in this `CLAUDE.md` replace the inherited gate string in rendered artifacts. Do not require `ruff format --check`, `pytest -m 'not integration'`, `uv build`, a schema/codegen loop, server verification, or air-gap verification unless the issue's authoritative plan/spec acceptance criteria add them. `/stack-check` remains required, but until its upstream template is corrected, apply only its checks that derive from this file and `specs/MARK-CLAW-TOOLS.md`: dependency/lock consistency, Mark-Claw hard rules and architecture, generated-file discipline, issue scope, and the local gates above. Findings based only on the foreign stack assumptions are inapplicable, not blockers.

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

Where a rendered file's generic profile contradicts this repo's design, **CLAUDE.md wins**, per the authority chain. Do not fix the rendered file locally; record the contamination in `specs/STACK-CLEANUP-NOTES.md` and fix it in the upstream template.
