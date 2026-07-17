---
name: no-review-change
description: Open a trivial documentation or mechanical one-line change without the normal automated review cycle. Use only when the change is demonstrably low risk and does not alter behavior.
argument-hint: <brief reason the change qualifies as trivial>
---
<!-- BEGIN cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 — rendered by cwft; edit the template, not this file -->

# no-review-change — trivial-change PR path

Use this skill only for a change that is truly trivial: documentation-only, formatting-only, or an
obviously mechanical one-line correction with no behavior, API, configuration, generated-output,
or dependency impact. If there is any doubt, use the normal `/phase` review path instead.

## 1. Confirm the exception is appropriate

- Read `CLAUDE.md` and the relevant plan/spec section. Confirm the change has a narrow, easily
  explained purpose and its acceptance criteria are fully met.
- Run the applicable local gates and `/stack-check origin/main...HEAD`.
- Inspect `git diff origin/main...HEAD`; stop and use the normal review path if the diff includes
  implementation logic, tests, generated files, or unrelated changes.
- Ask the human to confirm that this PR should skip the normal review cycle, stating the reason in
  one sentence. Do not use this skill without that confirmation.

## 2. Open the PR with review suppression

Use the repository wrapper so the PR body and label follow the house convention:

```bash
cwft gh pr create --no-review \
  --title "docs(<scope>): <brief change>" \
  --body $'<summary of the trivial change and why it is safe>\n\nCloses DEV-NNN'
```

`--no-review` adds the `skip-review` label and the
`@coderabbitai ignore` line to the PR body. Re-fetch the PR after creation
and verify that both are present:

```bash
gh pr view --json body,labels
```

Do not run `common-quality-reviewer`, request the normal review-bot sequence, or remove the
`skip-review` label. It stays on through merge.

## 3. Hand off

Summarize the reason this qualified for the exception, the gates run, and the confirmed PR body and
label. Stop at the human merge gate; never merge or enable auto-merge.
<!-- END cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 -->
