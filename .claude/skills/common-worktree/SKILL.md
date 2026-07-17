---
name: common-worktree
description: Ensure the current session is in a git worktree on the correct branch before phase work begins. Invoked at the start of the /phase skill. Resolves the Linear branch for the phase's issue(s), creates/reuses the branch and worktree (via cwft when available, raw git otherwise), and STOPS cleanly if not in a git repo or the user declines. Use whenever a runbook needs "get me onto the right branch in the right worktree first."
argument-hint: <ISSUE-123 [ISSUE-124 ...]>  (the phase's Linear issue id(s), lowest-numbered first)
---
<!-- BEGIN cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 — rendered by cwft; edit the template, not this file -->

# common-worktree — branch + worktree preflight

Bring the current session to a known-good state — **inside a git worktree, on the branch for this
phase's Linear issue(s)** — before any phase work runs. This is the shared front-half that the
`/phase` skill calls first; it owns branch/worktree selection so the phase runbooks don't each
re-implement it.

**Argument:** the phase's Linear issue id(s), e.g. `ISSUE-243` or `ISSUE-241 ISSUE-244`. Lowest-numbered
first. No argument → ask which issue(s) this phase covers before proceeding.

**Return contract:** on success you end in a worktree checkout on the resolved branch, working tree
clean, and you report the worktree path + branch so the caller continues there. On any **stop**
below, halt the whole runbook — do not fall through to phase work.

## Delegation: prefer `cwft`, fall back to raw git

`cwft` (the Common workflow tool) already ports the worktree preflights (§12.2), the three-way branch
resolution, and setup. **When `cwft` is
on `PATH`, let it do the worktree creation** so the preflight logic lives in one place; use the raw
git commands below only as the fallback. Detect once:

```bash
if command -v cwft >/dev/null 2>&1; then HAVE_CWFT=1; else HAVE_CWFT=0; fi
```

Everywhere this runbook says "create the worktree," that means:

- **`cwft` present:** `cwft session new "<branch>" --no-tmux` — creates/reuses the worktree, runs
  setup, records nothing, and prints the follow-up `cd` target (design §12.3). Parse/echo that path
  and `cd` into it.
- **`cwft` absent:** the `git worktree add …` fallback in [§ Worktree stage](#3-worktree-stage).

> **Branch-only mode.** Some runbooks want a plain branch instead of a worktree (e.g. a
> strictly-serial phase plan, or a repo that opts out of the worktree stage). Those callers pass
> `--branch-only`: do steps 0–2, skip step 3, and continue on the plain branch. Callers that want a
> worktree omit it and complete step 3.

---

## 0. Guard: are we even in a git repo?

```bash
git rev-parse --is-inside-work-tree 2>/dev/null   # must print exactly: true
```

If this is not `true` (non-zero exit or any other output): **STOP.** Report "not inside a git
repository — cannot set up a worktree here" and halt. Do not `git init`, do not search parent dirs.

Capture the essentials up front:

```bash
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"                       # e.g. main | <gitBranchName>
# main checkout vs. linked worktree: the two git dirs match only in the main checkout
if [ "$(git rev-parse --git-dir)" = "$(git rev-parse --git-common-dir)" ]; then
  IN_WORKTREE=0        # main checkout
else
  IN_WORKTREE=1        # already inside a linked worktree
fi
# The MAIN repo root, correct from either a main checkout or a linked worktree:
MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
```

**Already in a worktree** (`IN_WORKTREE=1`)? You're done with location setup. If `CUR_BRANCH` is a
real feature branch, skip to [§4](#4-continue). (Being in a worktree but on `main` is unusual —
treat it as "on main" and continue at step 1, creating the branch/worktree from here.)

---

## 1. On `main`: refresh, then decide the branch

Only when `CUR_BRANCH == main` (or the odd worktree-on-main case). First bring `main` current, using
the same safety preflights as `new-worktree.sh` — **do not blindly `git pull`** onto a dirty or
diverged `main`:

```bash
git -C "$MAIN_ROOT" diff --cached --quiet || { echo "staged changes on main — commit/stash/reset first"; }   # hard STOP
git -C "$MAIN_ROOT" diff --quiet          || echo "unstaged changes on main"                                  # → confirm before continuing
git -C "$MAIN_ROOT" fetch origin
[ "$(git -C "$MAIN_ROOT" rev-list --count origin/main..main)" -eq 0 ] || { echo "local main ahead of origin — push/reset first"; }  # STOP
git -C "$MAIN_ROOT" merge-base --is-ancestor main origin/main || { echo "main diverged from origin/main"; }  # STOP
git -C "$MAIN_ROOT" pull --ff-only
```

- **Staged changes**, **main ahead of origin**, or **diverged history** → **STOP** with the message
  shown; these are not safe to auto-resolve.
- **Unstaged changes only** → use AskUserQuestion to confirm continuing anyway; if declined, **STOP.**

Now decide which branch this phase uses. List existing local feature branches:

```bash
git -C "$MAIN_ROOT" for-each-ref --format='%(refname:short)' refs/heads/ | grep -vx main
```

- **Existing local branches found** → use **AskUserQuestion** (single-select) offering:
  - each existing local branch (reuse it),
  - **"Create a new branch"** (resolve the name in [§ Branch-name resolution](#branch-name-resolution)),
  - **"Stop"**.

  If **Stop** → halt the runbook. If an existing branch is chosen → that's `BRANCH`. If **Create** →
  resolve `BRANCH` from Linear.
- **No existing local branches** → skip the prompt; resolve `BRANCH` from Linear directly.

**Do not `git checkout`/`switch` the main checkout onto `BRANCH`.** Leave `main` on `main` — the
worktree stage checks the branch out in its own directory (git allows a branch checked out in only
one place, so switching here would just fight the `worktree add`). `BRANCH` is now just a name to
hand to step 3.

> If you started this skill **already on a feature branch in the main checkout** (`CUR_BRANCH != main`,
> `IN_WORKTREE=0`): skip step 1's branching entirely — `BRANCH="$CUR_BRANCH"`. **In branch-only
> mode, you're already done** — `BRANCH` is already checked out right here, so skip step 3 too (its
> own branch-only skip at the top applies the same way it would coming from step 1) and go straight
> to [§4](#4-continue). **Otherwise** (worktree mode), go to step 3; its local-branch path
> (`worktree add --force`) checks it out in a worktree. Optionally move the main checkout back to
> `main` afterward (`git -C "$MAIN_ROOT" switch main`) to free it.

### Branch-name resolution

Fetch each issue's Linear branch name (linear-server-psols MCP `get_issue`, field `gitBranchName`):

- **Single issue:** `BRANCH` = that issue's `gitBranchName` (copy it from Linear's "Copy git branch
  name").
- **Multiple issues:** base = the **lowest-numbered** issue's `gitBranchName`; append the *other*
  issue ids, lowercased, to the end. `ISSUE-241 + ISSUE-244 + ISSUE-245` →
  `<gitBranchName>-…-ISSUE-244-ISSUE-245` (the lowest issue's branch name, then each higher id in
  ascending order).

---

## 2. Compute the worktree location + id token

The **id token** names the worktree directory. Derive it from the issue id(s), lowercased and
joined with `-` (this matches cwft design §12.1 and `new-worktree.sh`; it is **lowercase** —
`ISSUE-123`, not `ISSUE-123`):

```
one issue   ISSUE-243            → ISSUE-243
many issues ISSUE-241 ISSUE-244    → ISSUE-241-ISSUE-244
```

If you only have a branch name (the already-on-a-branch case), derive the token the same way cwft
does: first `eng-NNN` match, lowercased; else the branch slug with `/`→`-`.

Worktree parent, then the target dir:

```bash
WT_PARENT="${COMMON_WORKTREE_PARENT:-$(dirname "$MAIN_ROOT")/$(basename "$MAIN_ROOT")-wt}"  # config workflow.worktree_parent wins when cwft resolves it
WT_DIR="$WT_PARENT/$ID_TOKEN"
```

> **Id-token collisions (design §12.1).** Two different branches can derive the same token
> (`ISSUE-241-fix` and `ISSUE-241-refactor` both → `ISSUE-241`). If `WT_DIR` already exists, the branch
> **checked out there** is authoritative — never assume it matches `BRANCH`. See step 3's reuse path.

---

## 3. Worktree stage

Skip this whole step in **branch-only mode** (see above) — continue in the plain branch
checkout instead, after making sure `BRANCH` is actually checked out (`git -C "$MAIN_ROOT" switch
--create "$BRANCH" origin/main` for a new branch, or `git -C "$MAIN_ROOT" switch "$BRANCH"` to reuse).

Otherwise, find worktrees that already serve this branch / target dir:

```bash
git -C "$MAIN_ROOT" worktree list --porcelain   # parse: 'worktree <path>' + 'branch refs/heads/<name>'
```

- **A worktree already exists for `BRANCH` (or `WT_DIR` exists)** → use **AskUserQuestion** offering:
  - each matching existing worktree (reuse — `cd` into it),
  - **"Create a new worktree"**,
  - **"Stop"**.

  **Stop** → halt. **Reuse** → `cd` into the chosen path. Its checked-out branch is authoritative:
  set the working branch from what's actually there, not from `BRANCH` (§12.1 collision rule).
- **No existing worktree** (or "Create a new worktree" chosen) → create it.

**Create — `cwft` present:**

```bash
cwft session new "$BRANCH" --no-tmux    # creates/reuses the worktree + setup; prints the cd target
```

**Create — raw git fallback** (mirrors `new-worktree.sh`'s three-way resolution; `main` preflights
from step 1 already ran):

```bash
mkdir -p "$WT_PARENT"
if   git -C "$MAIN_ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
  git -C "$MAIN_ROOT" worktree add        "$WT_DIR" "$BRANCH"                 # remote branch exists
elif git -C "$MAIN_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -C "$MAIN_ROOT" worktree add --force "$WT_DIR" "$BRANCH"                # local branch exists (maybe checked out elsewhere)
else
  git -C "$MAIN_ROOT" worktree add -b "$BRANCH" "$WT_DIR" "origin/main"       # brand-new branch off origin/main
fi

# Preserve correct feature tracking, but clear inherited base/main tracking
# so push.autoSetupRemote can publish the feature ref on the first bare push.
BRANCH_UPSTREAM="$(git -C "$MAIN_ROOT" for-each-ref --format='%(upstream:short)' "refs/heads/$BRANCH")"
if [ -n "$BRANCH_UPSTREAM" ] && [ "$BRANCH_UPSTREAM" != "origin/$BRANCH" ]; then
  git -C "$MAIN_ROOT" branch --unset-upstream "$BRANCH"
fi
cd "$WT_DIR"
```

(The `-b … origin/main` base is the default; a caller may pass a different `--base`, matching cwft's
`--base` / `new-worktree.sh`'s `[base-ref]`.)

---

## 4. Continue

You are now in a worktree checkout (or, in branch-only mode, on the branch) with a clean tree.
Report the **worktree path** and **branch**, then hand control back to the calling phase runbook —
which builds the context packet, delegates to the developer agent, runs gates, etc. Nothing below
this skill's scope (tmux panes, sandbox, PRs) belongs here; `cwft session new` (without `--no-tmux`)
owns the full session bring-up when that's wanted.

---

## Quick reference — the decision tree

```
in a git repo? ─ no ─▶ STOP
   │ yes
in a linked worktree? ─ yes, on a feature branch ─▶ §4 continue
   │ no (main checkout)
on main? ─ yes ─▶ pull --ff-only (safe preflights; STOP on staged/ahead/diverged)
   │              existing local branches? ─ yes ─▶ AskUserQuestion {branches | new | STOP}
   │                                          no  ─▶ resolve new BRANCH from Linear
   │ no (already on a feature branch) ─▶ BRANCH = current branch
resolve id token (lowercase eng-NNN[-eng-MMM…]) + WT_DIR
worktree for BRANCH/WT_DIR exists? ─ yes ─▶ AskUserQuestion {worktrees | new | STOP}
   │ no / "new" chosen
create worktree (cwft session new --no-tmux, else git worktree add) ─▶ cd ─▶ §4 continue
```

Use **AskUserQuestion** for any judgment call not covered above (ambiguous issue mapping, an
unexpected dirty worktree, a base-ref choice). When in doubt, prefer stopping with a clear message
over guessing.
<!-- END cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 -->
