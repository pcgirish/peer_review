# peer_agent

**Stack:** Python 3.12+, managed with `uv` (no runtime dependencies)

**Structure:** `/src/review_loop · /tests · /docs`

## Commands (always use the run prefix — never bare executables)
```
uv run review-loop --help          # run
uv run pytest -q                   # test
uv run ruff check src tests && uv run ruff format --check src tests   # lint
```
**Deps:** `uv sync` installs the package (editable) and dev tools; `uv add --dev <pkg>` adds a dev dependency.

## Critical Rules
- TDD RED-GREEN-Refactor — no implementation before a failing test
- Never write secrets/keys/passwords in code or comments
- Never read/write .env directly; use .env.example for shape
- Input validation at system boundaries — never trust raw request data

## Git Workflow
**Flow:** branch → implement → verify → review → push → open PR → merge PR. Never commit or push directly to main.

**Branches:**
- `feature/<name>` — new feature or refactor
- `fix/<short-name>` — bug fix (related fixes may share one branch; unrelated fixes get separate branches)
- `refactor/<name>` — non-functional code change
- Small single-spot fix (<10 lines): ask the user "review, or merge directly?" before doing a full review pass

## Agent Roles
### Implementer
Writes code, runs the test and lint commands above before considering the task done.

### Reviewer
Reviewed as a staff engineer would: challenges assumptions, spots edge cases, checks
security. If your agent supports spawning a fresh sub-session/subagent with no
knowledge of the implementation, prefer that for the review pass. Returns one of
APPROVED / NEEDS CHANGES / BLOCKED, with explicit reasons. See the `project-review`
skill below.

### Security Auditor
Runs the `project-security` skill before any auth-related change merges. Focuses on:
auth checks, input validation, secrets exposure, injection attacks.

## Skills
This project ships skills under `.agents/skills/` (the open, cross-tool
SKILL.md standard). `.claude/skills/` is a symlink to that same directory, so
Claude Code sees the identical skills with nothing duplicated. Invoke one
explicitly, or let the agent pick it up automatically when the task matches
its description:

| Skill | Use when |
|---|---|
| `project-security` | Writing auth/security-sensitive code, or asked to audit security |
| `project-review` | Reviewing a branch/PR before merge |
| `project-testing` | Writing or reviewing tests |
| `feature-scaffold` | Adding a new feature end-to-end |

- **Claude Code:** skills in `.claude/skills/` activate automatically when relevant,
  or ask for one by name ("use the project-security skill").
- **Codex CLI:** skills in `.agents/skills/` activate automatically, or force one
  with `$project-security` etc.
- **Other SKILL.md-aware agents:** point them at `.agents/skills/<name>/SKILL.md`.

## Agent-Specific Notes
- **Claude Code** reads this file automatically (via its AGENTS.md support), and
  additionally reads `.claude/settings.json` for tool permissions and the
  pre/post-tool-use hooks that back the git-workflow rules above — that file is
  Claude Code-specific and other agents can ignore it.
- **Codex CLI** reads this file automatically. There's no per-repo permissions
  file to write for it; instead run it with an explicit sandbox/approval mode,
  e.g. `codex --sandbox workspace-write --ask-for-approval on-request`.
- **Everyone else** (Cursor, Copilot, Gemini CLI, Windsurf, Zed, Aider, ...):
  this file and `.agents/skills/` follow the open conventions those tools read
  natively — no extra setup needed.

## Architecture
See `docs/PROGRAM.md` for architecture decisions (auth flow, DB conventions, API conventions).
