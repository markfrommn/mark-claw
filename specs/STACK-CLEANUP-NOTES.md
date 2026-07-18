# Common-workflow stack cleanup notes

This file tracks foreign stack and technology assumptions rendered into Mark-Claw by the shared
`python-uv` common-workflow profile. The generated files must not be hand-edited in this repo;
corrections belong upstream and will arrive through `cwft ai refresh`.

## Mark-Claw baseline

- Runtime: small Python wrappers and orchestrators plus `claude -p`; provider APIs are inherently
  networked.
- Storage: JSON, JSONL, YAML, and Markdown files under the tooling/config/state split. There is no
  application database.
- Dependency authority: `specs/MARK-CLAW-TOOLS.md`; there is no `specs/TOOLSPEC.md`.
- Gates: `uv lock --check && uv run ruff check . && uv run mypy && uv run pytest && uv run mclaw test --canary`, plus the issue acceptance criteria and the applicable Mark-Claw portion of
  `/stack-check origin/main...HEAD`.
- Generated surfaces: those listed in `CLAUDE.md` plus tool-generated artifacts such as `uv.lock`
  once the Python project exists. Mark-Claw has no schema-contract sync, migration, or SBOM loop.

## Upstream contamination inventory

| Rendered surface | Foreign assumptions to remove or parameterize | Mark-Claw behavior |
| --- | --- | --- |
| `.claude/skills/phase/SKILL.md` §§2–3 | Air-gapped runtime; SQLAlchemy Core/psycopg; hand-authored SQL migrations; vendored `specs/contracts/`; Pydantic v2 as a mandated stack; `ruff format --check`; excluded integration tests; `uv build`; schema codegen; server/`verify` skill; zero-network verification | Build the context packet from the Mark-Claw authority chain and hard rules. Use the gates in `CLAUDE.md` and the issue's acceptance checks. Provider network calls are expected. No `verify` skill is installed. |
| `.claude/agents/common-developer.md` | Same air-gap, database, migration, contract, Pydantic, codegen, and gate assumptions | TDD against the issue AC; implement file-backed wrappers/pipelines; run Mark-Claw gates and structural hard-guarantee tests. |
| `.claude/agents/common-quality-reviewer.md` | Air-gap as a correctness hotspot; SQLAlchemy/psycopg and vendored contracts as boundaries; migration/SBOM checks | Review provider safety, exclusion enforcement, secret isolation, tooling/config/state separation, no-framework rule, issue scope, and test adequacy. |
| `.claude/skills/stack-check/SKILL.md` | Missing `specs/TOOLSPEC.md`; Hatchling, Pydantic/pydantic-settings, SQLAlchemy/psycopg, httpx-only, nFPM, CycloneDX; air-gap; migrations; `specs/contracts/`; `scripts/sync-contracts.sh`; DB integration marker; `uv build` | Resolve allowed dependencies from `MARK-CLAW-TOOLS.md`. Check lock consistency, hard rules, generated-file discipline, issue scope, and the gates in `CLAUDE.md`. Do not reject required provider network access or selected provider SDKs. |
| `specs/WORKFLOW.md` §§3.5, 4.4–4.7, 5, 10–11 | SQL migrations, jobs-state/app schemas, vendored contracts, SBOM, air-gap, database-backed tests, foreign gate string, codegen, running server, `uv build` | Use the file-backed architecture, Mark-Claw gates, issue-specific runtime acceptance checks, and structural safety verification. |
| `.cursor/rules/common-workflow.mdc` | Treats air-gapped runtime/no SaaS dependency as a standing repo boundary | Mark-Claw intentionally calls SaaS/provider APIs and `claude -p`; the real boundary is that secrets remain inside wrappers and provider capabilities are structurally limited. |
| `.claude/settings.json` | Grants generated command permissions for `uv build` and `docker compose config`, even though neither belongs to the documented Mark-Claw workflow | Render permissions from the selected repo commands. Keep `uv sync`, lock, Ruff, mypy, pytest, and Linear tools; omit Docker Compose and unselected build commands. |

## Workflow semantics needing upstream parameters

- `/phase` is correctly one issue/branch/PR, while the legacy Phase D prompt describes one session
  for an entire delivery phase. Mark-Claw uses repeated `/phase DEV-NNN` runs.
- The profile needs a **no-repo issue** path for credentials, external account setup, config, and
  state-only acceptance work. Such issues should retain Linear/AC/evidence handling while skipping
  branch, worktree, PR, and review bots.
- `common-worktree` accepts multiple issue IDs even though this repo's workflow says one issue per
  branch and PR. The generic capability should not be offered when the repository selects strict
  one-issue mode.
- `common-worktree` says its ID token is lowercased, but its examples show uppercase issue tokens.
  The upstream template should make the convention and examples agree.
- `/phase` references a `verify` skill that is not rendered or installed for this repo. Runtime
  verification should be a profile value or omitted when unavailable.
- `/phase` changes Linear issue state and delivery notes, but generated settings explicitly allow
  only the Linear `get_issue` and `list_issues` tools. The profile should include the update tool
  required by its own workflow or make that operation an explicit approval boundary.
- The generated workflow's generic “running app/server” language is misleading for a CLI whose
  only eventual daemon is the Phase 2 loopback dashboard.

## Reviewed generated agents without product-stack contamination

- `.claude/agents/architect.md` and `.claude/agents/technical-writer.md` contain generic role and
  convention guidance but do not impose the foreign database/packaging stack.
- `.claude/agents/debugger.md` contains cross-language debugging examples. They are illustrative,
  not Mark-Claw technology requirements; no upstream stack correction is required from this review.
- `.claude/conventions/` contains cross-language examples. Treat them as shared examples rather
  than selected Mark-Claw technologies unless a rendered rule explicitly promotes one to a repo
  requirement.

## Upstream acceptance criteria

- Rendering the Mark-Claw `python-uv` profile produces zero references to Prefect, Postgres,
  databases, SQLAlchemy, psycopg, ORM migrations, `specs/contracts/`, app/jobs schemas, air-gapped
  runtime, nFPM, CycloneDX, Docker Compose, or a missing `specs/TOOLSPEC.md`.
- The rendered phase, developer, reviewer, stack-check, workflow, and Cursor rule use profile values
  for authority docs, gates, generated surfaces, runtime verification, network posture, and storage
  architecture.
- A profile can select strict one-issue/one-PR mode and a no-repo execution path.
- `cwft ai refresh` reproduces corrected artifacts without local edits or drift.
