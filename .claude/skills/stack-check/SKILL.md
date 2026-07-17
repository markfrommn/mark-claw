---
name: stack-check
description: Pre-PR stack-conformance gate (read-only). Deliberately audit the working diff for stack violations — disallowed/duplicate libs, air-gap breaks, hand-edited generated files, and the stack's architectural-boundary rules. Run before opening a PR. Not a bug hunt — for correctness/quality review use /code-review.
argument-hint: "[base-ref, e.g. origin/main...HEAD] (default: working tree vs HEAD)"
allowed-tools: Bash, Read, Grep, Glob
---
<!-- BEGIN cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 — rendered by cwft; edit the template, not this file -->

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

1. **Approved stack only — no duplicate-purpose libs.** This repo standardizes on the stack
   in `specs/TOOLSPEC.md` ("Selected Stack (Summary)"); flag any added dependency that duplicates an
   approved tool's purpose:

   - Dep/project manager other than **uv** (workspace, single `uv.lock`) — no Poetry / pip-tools.
   - Build backend other than **hatchling**.
   - Validation/models other than **Pydantic v2**; config other than **pydantic-settings**.
   - DB access other than **SQLAlchemy Core + psycopg 3** — explicitly **no ORM entity layer**: the app
     schema is owned by the app-schema owner repo's ORM (Architectural Principle 5), so a
     declarative/ORM layer or any schema-owning tool over it is ❌ "violates Principle 5", not a
     stack add.
   - HTTP client other than **httpx** (no requests/aiohttp as a second client).
   - Lint+format other than **Ruff** (no Black/isort/flake8 — Ruff replaces all three, the Biome analog
     here); type checker other than **mypy** (+ the Pydantic plugin).
   - Testing that conflicts with this repo's declared test stack (pytest. Run `uv run pytest` from the repo root.
); native
     packaging other than **nFPM**; SBOM other than **CycloneDX** (`cyclonedx-py`).
   - Any brand-new **direct** dependency not covered by TOOLSPEC → ⚠️ "needs TOOLSPEC update" (with
     `file:line` in the relevant `pyproject.toml`) unless clearly a deliberate approved-stack change.
     Scope this to **direct** additions (`[project.dependencies]`/`[project.optional-dependencies]`/
     `[dependency-groups]` in `pyproject.toml`) — `uv.lock`'s diff includes transitive packages pulled in by an approved
     direct dependency's own requirements, which aren't individually TOOLSPEC entries; use `uv.lock`
     for lock-consistency and provenance (see #2), not per-package approval-listing.

2. **Air-gapped first** (TOOLSPEC Architectural Principle 2). Install and runtime must require **no
   PyPI, no CDN, no external network** — connected-only enhancements must degrade gracefully, never
   hard-depend. The `uv.lock` file is the single source of dependency truth.
   - `uv lock --check` must show **no drift** (the CI lockfile-consistency gate) — ⚠️ if the diff
     changed `pyproject.toml` deps without a matching lock update.
   - Flag a hardcoded remote URL that is *required at runtime* — ❌. Source URLs that are **configuration**
     (so an air-gapped install can point httpx at a local snapshot bundle, DP-5) are fine; build-time
     network use (codegen, audit, wheel collection from the lockfile) does **not** violate the runtime
     air-gap.
   ```bash
   # A required-at-runtime remote URL in job/platform source is the regression; build scripts excluded.
   # Only drop the unambiguous local-target false positives — do NOT filter by generic words like
   # "config"/"settings"/"default=" or a bare "#", since a real required URL can sit right next to
   # any of those (an inline comment, a badly-named variable, a settings field whose *value* is the
   # violation) and a keyword filter would silently hide it.
   grep -rnE 'https?://' packages/*/src --include='*.py' \
     | grep -viE 'localhost|127\.0\.0\.1|example\.(com|org|net)' || true
   ```
   Triage every remaining hit by hand: a config *field name* containing "settings"/"default" is fine;
   what matters is whether the URL *value* is reachable only when connected and required at runtime.

3. **Ruff / mypy boundaries clean.** Config is one Ruff + one mypy config at the **workspace root**
   `pyproject.toml`. Note whether the diff passes `ruff format --check`, `ruff check`, and `mypy`.
   - Flag a new `# type: ignore` or `# noqa` without an inline justification/rule code — ⚠️ with
     `file:line`.
   - Flag any **Black / isort / flake8** config creeping into `pyproject.toml` or a `setup.cfg`/`.flake8`
     — Ruff owns lint+format here (same role Biome plays in the sibling TS repos) — ❌.

4. **The hand-authored-migration schema is plain ordered SQL — a feature, not a generated
   artifact** (DESIGN-SPEC C2). Migrations are plain, ordered SQL files applied idempotently by
   the ops CLI (`migrate`) and tracked in the schema-version table. Authoring a **new** `NNN_*.sql`
   by hand is the **intended** DBA-auditable design — do **not** flag it as a generated-file
   violation. Editing an **already-landed** migration in place IS a finding: it diverges upgraded
   installs (which already applied the old version) from fresh installs (which apply the edited
   version) — the fix is a new numbered migration, not an in-place edit. What *is* also a finding:
   introducing **Alembic** or any migration-autogeneration/ORM tool over the hand-authored-migration
   schema (explicitly rejected as unnecessary complexity) — ❌ "C2 says plain ordered SQL".

5. **App schema read only through vendored `specs/contracts/`** (DESIGN-SPEC C1). Jobs treat the vendored
   snapshot as the **only** schema truth they may assume:
   - `specs/contracts/DATA-MODEL.md` and `specs/contracts/app-schema.sql`, each carrying a provenance
     header (source repo, commit SHA, sync date).
   - Flag live introspection of, or hand-guessed columns against, the app-schema owner repo's actual
     database, or any direct dependence on the app-schema owner repo's internals instead of the
     snapshot — ❌. If a needed column/table isn't in the snapshot, the fix is "sync first", not
     defensive guessing.
   - Unlike the migrations in #4, the vendored contract files **are regenerated** — by
     `scripts/sync-contracts.sh` — so a **manual edit to `specs/contracts/*` is ❌**; it must come from a
     re-sync that re-stamps the provenance header.

6. **Generated files not hand-edited.** Be precise about which "generated" files follow which rule:
   - Regenerate-only (manual edit ❌): **`uv.lock`** (from `uv lock`/`uv sync`), **`specs/contracts/*`**
     (from `scripts/sync-contracts.sh`, see #5), **SBOM artifacts** (from `cyclonedx-py`).
   - Hand-authored-on-purpose (**authoring a new file is correct**): the hand-authored-migration
     schema's **`NNN_*.sql` migrations** (see #4) — but editing an **already-landed** migration in
     place is NOT fine (add a new numbered migration instead).
   Flag a manual edit to any regenerate-only artifact with `file:line`.

7. **Formatter is Ruff, never Biome.** This stack has **no** TypeScript/JS surface. Flag any Biome or
   Prettier-for-Python config, or a JS-ecosystem formatter pointed at `.py` files — ❌ "wrong ecosystem;
   Ruff formats Python here".

8. **Quality gates.** The repo's local-gates DoD (the rendered `specs/WORKFLOW.md`) is:
   `uv lock --check && uv run ruff format --check && uv run ruff check . && uv run mypy && uv run pytest -m 'not integration' && uv build`. Note whether the diff passes each command; run the quick ones if practical.
   The build step matters even for content/config-only-looking changes — a packaging-metadata or
   Hatchling build break will not surface from lint/type/test alone. DB/integration tests are
   marked/service-backed and excluded from the default fast pass by the `-m 'not integration'`
   marker. Report the first failure with `file:line`.


## Output

A concise checklist verdict (the items above), then a short prioritized list of must-fix (❌) and
should-fix (⚠️) items with locations. End with an overall **PASS / NEEDS-WORK**.
<!-- END cwft-ai claude set-v2 f331378ef71fa00379570cba93d6c1906c024eb5 -->
