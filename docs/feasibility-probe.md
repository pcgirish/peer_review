# Feasibility Probe — Step 1 Findings

Probed 2026-09-18 on macOS with Claude Code 2.1.276 and Codex CLI 0.155.0. Each finding was verified by running the command, not read from docs.

## Session create / resume

| Capability | Claude | Codex |
|---|---|---|
| Non-interactive run | `claude -p "<prompt>"` | `codex exec "<prompt>"` |
| Choose session id before first call | `--session-id <uuid>` | Not supported; id is returned in the first `thread.started` event (`--json`) |
| Resume by id | `claude -p --resume <id>` | `codex exec [flags] resume <id> "<prompt>"` — global flags must precede `resume` |
| Resume from a different CWD | Works | Works with explicit id (`--skip-git-repo-check` outside a repo) |
| Resume across processes | Works | Works |
| Memory retained on resume | Yes | Yes |
| Session storage | `~/.claude/projects/<cwd-slug>/` | `~/.codex/sessions/<yyyy>/…` |

Consequence for the helper: for Claude the helper can persist the id before the first call. For Codex it must parse `thread.started` from the first `--json` event and persist immediately; the call is not retryable under the same id until that is saved.

## Structured output

| | Claude | Codex |
|---|---|---|
| Flag | `--output-format json --json-schema '<schema>'` | `--json --output-schema <file> -o <file>` |
| Where the object lands | `structured_output` field of the result JSON | `-o` file and `agent_message` text |
| Verified | `{"word":"PROBE_OK"}` | `{"verdict":"approved","blocking_findings":[]}` |

Codex `exec` blocks reading stdin if it is left open. The helper must pass `stdin=DEVNULL`.

## Tool restriction — what is actually enforced

| | Claude | Codex |
|---|---|---|
| Mechanism | `--disallowedTools Write,Edit,NotebookEdit` or `--tools ""` | `--sandbox read-only` |
| Enforcement level | Tool removed from the model; **Bash is not sandboxed**, so shell writes remain possible unless Bash is also disallowed | OS-level (seatbelt). `touch` in the repo, `open()` in `/tmp`, `mkdir __pycache__`, and `tempfile.mkdtemp()` all fail with `Operation not permitted` |
| Verified | "I don't have a Write tool"; file absent | File absent; PermissionError captured |

Observed limitation: under `--sandbox read-only`, writes to the repo, `/tmp`, and Python's default temp dirs all fail. Test runners that need a writable temp or cache directory (pytest by default) therefore cannot run. Options, to decide before Step 2:

1. **Chosen.** Reviewer stays read-only; the helper runs the project test command itself and passes the output into the reviewer prompt. `tests_run` entries carry `run_by: helper`. Simplest; matches "reviewer does not edit author files".
2. Codex reviewer uses `--sandbox workspace-write`; it can then edit the author's checkout. Prompt-only restraint.
3. Codex reviewer uses `--worktree` (managed git worktree) with `workspace-write`. Isolates writes; adds a per-session worktree to keep stable.

Network access was not probed. Regardless of sandbox mode, `gh` uses the user's stored GitHub credentials, so none of the above makes those credentials read-only.

## Recursion guard

`REVIEW_LOOP_REVIEWER=1` set on the helper process is visible to the reviewer's shell in both CLIs (`printenv` returned `1`). Neither CLI refuses to spawn itself from inside a session, so the guard is entirely the helper's job.

## Foreground time limits

| Host | Limit |
|---|---|
| Claude Code `Bash` tool (author host) | 120 s default, 600 s max per call; longer calls are moved to background and must be polled |
| Codex `exec` shell tool (reviewer) | No hard limit observed; a 130 s `sleep` completed. Long commands are backgrounded internally and polled |
| Codex as author host | Not probed; assume similar backgrounding behaviour and verify in Step 6 |

A single review round that exceeds 600 s from a Claude author will be backgrounded. The helper should either fit inside that with a configurable timeout or expose a poll/status path. Deferred to Step 2; default timeout 540 s.

## Plan mode

Claude: `--permission-mode plan` exists. In plan mode the host blocks writes outside the plan file, which includes the helper's state directory under `.git/`. The helper cannot bypass this; the skill reports the review as pending. Codex has no plan-mode flag on `exec`.

## Nested-call cost

Each `claude -p` call re-sends ~14–30 k cached tokens; a resumed session costs ~$0.004 when the cache is warm and ~$0.06 when cold. Codex calls reported 17–59 k input tokens. Not a blocker; noted for round limits.

## Decisions needed before Step 2

- Test-running model for the Codex reviewer (options above). Recommendation: option 1.
- Confirm 540 s default helper timeout.
