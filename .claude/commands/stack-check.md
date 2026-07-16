---
description: Pre-PR stack-conformance gate (read-only). Deliberately audit the working diff for stack violations — disallowed/duplicate libs, air-gap breaks, hand-edited generated files, and the stack's architectural-boundary rules. Run before opening a PR. Not a bug hunt — for correctness/quality review use /code-review.
argument-hint: "[base-ref, e.g. origin/main...HEAD] (default: working tree vs HEAD)"
allowed-tools: Bash, Read, Grep, Glob
---
<!-- BEGIN cwft-ai claude set-v1 1d9ad20bd38daa0f83192ed5f450c478d06c04b2 — rendered by cwft; edit the template, not this file -->

Audit the current changes against this stack's approved-stack table and the `CLAUDE.md` hard rules +
pre-PR checklist. Read-only — **report findings, do not fix** unless the user asks.

## Scope

Audit **this branch's changes**. Two modes:

- **A base ref is given (`$ARGUMENTS`)** — pass it as a **three-dot range**, e.g.
  `origin/main...HEAD`, so only this branch's changes since the merge base are audited, never
  unrelated commits that landed on `origin/main` after the branch was cut:

  ```bash
  git diff --stat $ARGUMENTS
  git diff $ARGUMENTS
  ```

- **No argument** — audit everything not yet committed **against `HEAD`** (staged *and* unstaged);
  plain `git diff` compares against the index and would silently skip staged changes, so diff
  against `HEAD` explicitly:

  ```bash
  git diff --stat HEAD
  git diff HEAD
  ```

Per the pre-PR DoD the tree is committed and clean at gate time, so the three-dot range is the full
change set; the no-argument mode is for an ad-hoc audit of in-progress work.

`git diff` omits untracked (newly added) files, so also list them and include any that are in scope
in the audit — when a base ref is passed, `git diff` above already covers committed new files; this
catches uncommitted new files in the working tree:

```bash
git ls-files --others --exclude-standard   # untracked (newly added) files — audit these too
```

Also check the dependency manifests for added/duplicate dependencies.

## Checks (report each as ✅ pass / ⚠️ warn / ❌ fail with file:line)

1. **No approved-stack list to check against.** The generic profile ships no dependency allowlist — skip the dependency-approval check rather than reporting a false ❌/✅ against a list that doesn't exist. Note any new dependency in the PR description instead.

2. **Generated files not hand-edited.** N/A unless this repo has established its own codegen loop — skip this check with a note if it hasn't.

3. **Quality gates.** `true` is a no-op placeholder — running it proves nothing and MUST NOT be reported as a passing check. Discover and run whatever this repo's own README/CI actually documents as its build/lint/test commands. If nothing is documented yet, say so explicitly ("no gate command found — validation not performed") rather than treating the no-op as green.

4. **No secrets committed.** Scan the diff for obvious committed credentials (API keys, tokens, `.env` values) — ❌ with `file:line` on a hit.


## Output

A concise checklist verdict (the items above), then a short prioritized list of must-fix (❌) and
should-fix (⚠️) items with locations. End with an overall **PASS / NEEDS-WORK**.
<!-- END cwft-ai claude set-v1 1d9ad20bd38daa0f83192ed5f450c478d06c04b2 -->
