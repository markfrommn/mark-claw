---
name: phase
description: Drive one unit of work (one PR) of the mark-claw implementation plan end-to-end — branch/worktree, delegate to the common-developer agent, run gates, review, open the PR, work the bot sequence, and stop at the human merge gate. Use when asked to "run phase N" / "run <milestone>" / "run DEV-NNN".
argument-hint: <phase / milestone / issue — e.g. P3, M3, ISSUE-279>
---
<!-- BEGIN cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 — rendered by cwft; edit the template, not this file -->

# phase — per-PR execution runbook

You are the **orchestrator** for one unit of work (one PR) of the mark-claw
build. You sequence the work and verify it; you do **not** write implementation code — the
`common-developer` agent does. Read `CLAUDE.md` first — it is authoritative for this repo's agent
instructions and hard rules (process/execution constraints). For scope/behavior, the authority
chain is: specs/MARK-CLAW-SPEC.md -> specs/MARK-CLAW-TOOLS.md -> specs/MARK-CLAW-DESIGN.md → the relevant plan doc(s) `specs/plans/*-PLAN*.md`
(this unit's section; `plan_docs` is one or more paths, space-separated) → the rendered
`specs/WORKFLOW.md` (process). On conflict within that chain, the
higher doc wins; escalate with `AskUserQuestion` before deviating.

**Argument = one PR's worth of work.** The build runs **one Linear issue ⇄ one branch ⇄ one PR**,
and a plan section usually maps to a single issue. Accept a phase/milestone label or a Linear issue
id; resolve it against the plan doc(s) `specs/plans/*-PLAN*.md` (`plan_docs` is one or more
paths, space-separated — search **all** of them) to the exact **issue(s) this one PR closes** and the
detailed per-PR plan doc if one exists. If a phase/unit identifier (e.g. "Unit 1", "P3") matches a
section in **more than one** listed doc, that is an ambiguous case → use `AskUserQuestion` naming
each doc and its matching section, rather than silently picking the first. A typo or no match at all
(within a single doc) keeps the one-line fallback below. No argument, or an otherwise ambiguous one
→ ask which phase/issue with `AskUserQuestion`.

## 0. Preconditions (abort with a clear message if any fails)

- **You are at the repo's base directory** — if you were launched from a subdirectory, `cd` to the
  root first (`cd "$(git rev-parse --show-toplevel)"`); `cwft`, the gates, and path-relative steps
  below all assume the repo root.
- You are in the correct checkout (main or the issue's worktree) with a clean working tree.
- The prerequisite phases/issues are **merged** — respect the dependency graph in the plan doc(s)
  `specs/plans/*-PLAN*.md`. Check `git log origin/main` / `gh pr list --state merged` and the
  Linear blocking relations. Independent units in the same wave may run in **parallel worktrees** —
  this session drives exactly one.
- Read the unit's section in the plan doc(s) `specs/plans/*-PLAN*.md` **and** the design section(s) it cites:
  note the Linear issue, scope, files, dependencies, and **Acceptance criteria** (the AC are the
  Definition of Done).
- Fetch the Linear issue (linear-server-psols MCP): confirm state and `gitBranchName`. Set it
  **In Progress**. Do **not** manually close issues — the GitHub↔Linear integration moves them to
  *Done* on merge.
- **Confirm the canonical session name.** Run `cwft session name` to surface it. A session launched
  via the `cwft-claude`/`cwft-yolo` alias is already named for you (auto-injected `-n`); if this
  session was **not** launched that way — Claude cannot introspect its own name — ask the human to
  `/rename <name>` (or relaunch via `cwft-claude`) so `claude --resume`, pickers, and the prompt bar
  stay legible.

## 1. Branch / worktree

Bring up the working branch in a git worktree so this repo stays usable on `main`. Use `common-worktree` (without `--branch-only`):

- Worktree convention: a sibling directory `<repo>-wt/eng-NNN` alongside the repo. The worktree is a new branch off `origin/main`.
- The worktree shares the main repo's `.git`; the `.venv` is independent per worktree (`uv sync` in the worktree).

- Keep the worktree on its branch; do not merge to `main` locally — the PR is the merge path.


## 2. Build the context packet and delegate

Spawn the `common-developer` agent (Sonnet for typical job/platform work, Opus for spec/architectural phases) with a context packet containing: the phase scope, the acceptance criteria, and the authority chain it must read first (`specs/MARK-CLAW-SPEC.md -> specs/MARK-CLAW-TOOLS.md -> specs/MARK-CLAW-DESIGN.md` → the relevant plan).

Restate the standing constraints for this stack:

- Plain-Python code; air-gapped first (bundled wheels, pinned `uv.lock`, no runtime PyPI/CDN).

- No schema-owning ORM — app DB via SQLAlchemy Core/psycopg 3 SQL; the hand-authored-migration schema's migrations are hand-authored SQL (C2); vendored `specs/contracts/` as the only app-schema source (C1).
- Pydantic v2 / mypy / Ruff / pytest. Run `uv run pytest` from the repo root.
 TDD-first; gates green before returning (`uv lock --check && uv run ruff format --check && uv run ruff check . && uv run mypy && uv run pytest -m 'not integration' && uv build` from the repo root).

"Done" = a report listing what was implemented (by package/flow), the AC checklist (met/unmet), exact gate output, any deviation from the design (with rationale), and any open questions.


If the agent hits a wall (same failure ~3×, spec ambiguity, scope drift), it stops and reports; you
either re-scope, escalate to a **`debugger`** agent on **opus**, or use `AskUserQuestion` / the
suspend-and-`HANDOFF.md` protocol per `specs/WORKFLOW.md`.

## 3. Verify independently

Never trust the report alone:

- Re-run the gates yourself (same working directory as stated in step 2's delegate context):
  `uv lock --check && uv run ruff format --check && uv run ruff check . && uv run mypy && uv run pytest -m 'not integration' && uv build`.
- If the unit touched a generated surface, run the stack's codegen loop and confirm **no drift** — a
  dirty tree after regeneration is drift and means not done.
- Diff review sanity: `git diff origin/main --stat` — scope matches the unit; no hand-edited
  generated files; no stray files; no new disallowed/duplicate dependency.
- Run **`/stack-check origin/main...HEAD`** (approved stack only, no CDN/air-gap break, no
  hand-edited generated files, the stack's architectural boundaries, dependency hygiene) — the
  explicit range, not the no-argument default, since the working tree is typically clean by this
  point and would otherwise audit an empty diff.
- If the unit has runtime surface, **verify against the running app/server**: exercise the new flow
  directly (`uv build`) or drive it with the `verify` skill. For air-gap-sensitive
  units, confirm **zero external network requests**.
- Check the AC checklist item by item; anything unmet goes back to the common-developer agent.

## 4. Pre-PR reconciliation

Before starting review, reconcile the delivery record with what was actually built:

- Update the relevant spec/design and plan documents for decisions, scope changes, and outcomes
  discovered during this phase. Keep the plan aligned with the implementation; do not defer routine
  corrections to a separate documentation PR.
- Update the Linear issue(s) with the delivered outcome, any decision that belongs in the issue,
  and remaining follow-up work. Preserve the issue's acceptance criteria and do not manually close
  it.
- Run a short pre-PR interview with the human: identify any outstanding documentation adjustment,
  plan/spec correction, or Linear loose end that should ship in this PR. Apply agreed routine
  updates now, then re-run the affected verification and the diff review.

If the resulting change is trivial (for example documentation-only or a clearly mechanical one-line
change), offer `/no-review-change` as the PR path. Otherwise continue with the normal review path.

## 5. Pre-PR review

Spawn the **`common-quality-reviewer`** agent on the full diff (or run `/code-review` at high effort
if available in your harness). Triage findings: real problems → back to the common-developer agent
(or a small fix agent — haiku for mechanical fixes); non-issues → note the justification for the PR
description. Re-run gates after fixes.

## 6. Open the PR

```bash
git push -u origin <gitBranchName>
gh pr create --base main --assignee @me \
  --title "<type>(<scope>): <unit title>" \
  --body $'<summary of what/why, AC checklist>\n\nCloses DEV-NNN\n\n@coderabbitai ignore'
# append  --project "<name>"  when the github_projects bullet below resolves to one
```

- **Assignee and project.** Always pass `--assignee @me` (the house convention). Attach a GitHub
  Project board with `--project`, driven by the `github_projects` list in `.cwft-settings.yaml` (an
  array of GitHub Project board names — distinct from `linear_projects`, which is Linear's own
  Projects concept; no `gh projects list` query — config-driven only).
  No `github_projects` configured (key unset or empty) → **omit `--project` entirely** (today's behavior, unchanged).

- **Close every issue this PR covers** — one `Closes DEV-NNN` line each. The Linear branch name also
  auto-links so merge moves the issue to *Done*.
- **Re-fetch the body (`gh pr view --json body`) and confirm the `@coderabbitai ignore` line is
  present** — if missing, `gh pr edit` to add it before CodeRabbit starts (otherwise both bots run
  concurrently and duplicate findings).

## 7. Bot sequence (`specs/WORKFLOW.md` §7)

1. Wait for **Macroscope**; poll ~once per minute (`gh pr view --comments`, `gh pr checks`) — never
   busy-poll.
2. Work every Macroscope finding: fix (delegate to a triage agent — haiku/sonnet by judgment
   required) or justify. Reply `fixed by git commit <hash>` (or the justification) and resolve the
   comment.
3. When Macroscope is clean, add the **`skip-review` label**.
4. Comment **`@coderabbitai fullreview`**; use its "Estimated code review effort" as the wait
   estimate; work its findings the same way.
5. Keep CI green throughout; small commits per finding batch.

## 8. Hand off to the human

When both bots are satisfied and CI is green: summarize the PR state, confirm the Linear issue still
links the PR, remove the **`skip-review` label**, and **stop — the human reviews and merges. Never
merge, never enable auto-merge.**

After the human merges: confirm the Linear issue moved to *Done* (set manually only if the
automation missed), note anything learned that should amend the plan doc(s)
`specs/plans/*-PLAN*.md` / the design docs (propose the edit — keep plan and Linear in sync),
remove the worktree if you made one
(`cwft session` cleanup, or `git worktree remove`), and report which unit(s) this unblocks. The next
unit runs in a **fresh session**.
<!-- END cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 -->
