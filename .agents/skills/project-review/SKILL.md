---
name: project-review
description: peer_agent code-review skill — review order, red-flag list, PR/branch review procedure, and an APPROVED/BLOCKED verdict format. Use before merging a branch or PR in this repo.
---

# Review Skill

Use before merging a branch or PR, or whenever asked to review code.

## Review Order (check in this sequence)
1. Security — auth, input validation, secrets, injection
2. Correctness — does it do what the spec says?
3. Error handling — what happens when things go wrong?
4. Tests — are the right cases covered?
5. Style — linter-clean, readable, consistent with codebase

## Red Flags (block the PR if any are present)
- Secret or credential in code or comment
- Missing auth check on non-PUBLIC endpoint
- Raw SQL / query string with user input
- Password hash returned in response
- Migration file hand-edited
- Hard DELETE instead of soft delete

## Review Procedure
1. Run `gh pr diff` (or `git diff main...HEAD` if no PR yet) to see all changes
2. Verify no secrets or hardcoded values anywhere
3. Run `# fill in test command` — report any failures
4. Run `# fill in lint command` — report any lint errors
5. Summarise: what changed, risk level, what is missing tests

## Output Format
- APPROVED / BLOCKED / NEEDS CHANGES verdict
- List of blocking issues (if any)
- List of suggestions (non-blocking)
- Confirmation that `# fill in test command` and `# fill in lint command` pass
