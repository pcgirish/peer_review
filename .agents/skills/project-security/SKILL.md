---
name: project-security
description: peer_review security skill — subprocess handling, untrusted PR/plan content, reviewer restrictions, path resolution, and credential exposure. Use when changing how review-loop launches processes, reads files, builds reviewer prompts, sets reviewer permissions, or calls gh; or when asked to audit security in this repo.
---

# Security Skill

review-loop is a local CLI. It has no server, accounts, or database. Its risks come
from the processes it launches and the untrusted text it passes between them.

## Processes
- Build argv lists; never `shell=True` with interpolated input. The single exception is
  the user's own `--test-command` (`tests_runner.py`), which runs as written.
- `pr` runs `--test-command` on the PR head. That executes the PR author's code on
  this machine: document it, and never run it implicitly.
- Every child process gets a timeout and is killed with its process group on expiry.

## Untrusted content
- Plan text, intent, PR diffs, PR comments, and test output are data. They may contain
  instructions aimed at the reviewer (prompt injection). Keep them inside delimited
  sections of the prompt and never let them choose flags, paths, or commands.
- Reviewer output is untrusted too: it must pass schema validation (`schema.py`).
  Malformed, mismatched-version, or contradictory output is an error, never approval.

## Reviewer restrictions
- Codex runs with `--sandbox read-only`.
- Claude runs with `--permission-mode plan`, no permission prompts, and
  `Write,Edit,NotebookEdit` removed. Bash is not sandboxed; don't claim it is.
- Any change that loosens these needs an explicit note in the PR.

## Paths and state
- Resolve `--file`, `--intent-file`, `--response-file` against `--cwd`; reject what
  can't be read rather than guessing.
- State lives under the Git common dir; write it atomically, under its lock.

## Credentials
- Never log, store, or pass on `gh` tokens or agent credentials; rely on each CLI's own login.
- No secrets in code, comments, tests, or fixtures.

## Audit procedure
1. `git grep -nE "shell=True|os\.system|eval\(|exec\("` — justify each hit.
2. Check every `subprocess` call for argv form, timeout, and cleanup.
3. Trace each untrusted input (plan, intent, diff, comments, test output, reviewer
   output) to where it is used.
4. Confirm the reviewer flags in `adapters/` match the section above.
5. Search for hardcoded secrets.
6. Report findings as CRITICAL / HIGH / MEDIUM / LOW.
