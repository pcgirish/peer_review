---
name: project-review
description: peer_review code-review skill — review order, red-flag list, PR/branch review procedure, and an APPROVED/BLOCKED verdict format. Use before merging a branch or PR in this repo.
---

# Review Skill

Use before merging a branch or PR, or whenever asked to review code.

## Review Order (check in this sequence)
1. Security — the `project-security` skill: processes, untrusted content, reviewer restrictions
2. Correctness — does it do what the plan or issue says?
3. Error handling — every failure ends in `error`, never a silent `approved`
4. Tests — the `project-testing` skill's coverage
5. Style — linter-clean, readable, consistent with the codebase

## Red Flags (block the PR if any are present)
- Secret or credential in code, a comment, or a fixture
- `shell=True` or string-built commands with untrusted input
- Reviewer restrictions loosened without saying so in the PR
- A path where malformed reviewer output or a failed call counts as approval
- A subprocess with no timeout or cleanup
- Tests that call real `claude`, `codex`, or GitHub
- A change to the JSON output or exit codes without updating `docs/usage.md`

## Review Procedure
1. Run `gh pr diff` (or `git diff main...HEAD` if there is no PR yet)
2. Check for secrets or hardcoded values
3. Run `uv run pytest -q` — report any failures
4. Run `uv run ruff check src tests && uv run ruff format --check src tests` — report any errors
5. Summarise: what changed, risk level, what lacks tests

## Output Format
- APPROVED / BLOCKED / NEEDS CHANGES verdict
- List of blocking issues (if any)
- List of suggestions (non-blocking)
- Confirmation that the test and lint commands pass
