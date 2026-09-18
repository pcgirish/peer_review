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

## Decisions taken before Step 2

- Test-running model for the Codex reviewer: option 1 (helper runs tests; `run_by: helper`).
- Default helper timeout: 540 s (`--timeout` overrides).

## Step 2 — one review call, live results

`uv run review-loop plan --file … --intent-file … --author <claude|codex>` reviewed a four-line sample plan in both directions on 2026-09-18:

| Direction | Round 1 | Round 2 (`--session-id`, `--response-file`) |
|---|---|---|
| Claude author → Codex reviewer | `changes_requested`, 2 findings, 44 s | `approved` in the same thread, 22 s |
| Codex author → Claude reviewer | `changes_requested`, 1 finding, 58 s | `approved` in the same session, 11 s |

Both reviewers honoured the schema (Claude via `structured_output`, Codex via the `-o` file), echoed the helper-supplied version string, and cited real file locations from the read-only checkout. `REVIEW_LOOP_REVIEWER=1` on the helper's own environment exits 2 before any reviewer spawns. Timeout kills the reviewer's whole process group and removes the Codex schema/output temp directory (unit-tested with fake CLIs).

## Step 3 — session persistence, live results

`review-loop plan` was run twice on `docs/two-agent-review-plan.md` (Claude author → Codex reviewer) from separate helper processes on 2026-09-18. State lives under `.git/review-loop/`; no session id was passed on the command line.

| Call | Result |
|---|---|
| Round 1 (new artifact, intent file) | `approved`, 44.3 s; artifact id and Codex thread id saved |
| Round 2 (`--response-file`, same path) | `changes_requested`, 2 findings, 47.6 s, resumed in the **same** thread (`argv` shows `resume <thread>`); round 2/3 |
| `review-loop status <path>` | shows both rounds, the session id, and the per-round findings |

Codex's round-2 findings were verified and fixed before the PR: (a) two concurrent first calls on an unknown path could each invent an id before any lock — path lookup/creation now happens under an index lock (`Store.reserve`); (b) a first call interrupted mid-review never persisted its thread id — the runner now streams stdout and reports the session id via `on_session_id` as soon as it appears (Claude: before spawn), and the CLI saves it immediately; Ctrl-C terminates the reviewer tree and keeps the id.

## Step 4 — snapshots and stale checks, live results

Each review freezes the plan and intent under `.git/review-loop/snapshots/<id>/<version>/` and the prompt points the reviewer at that frozen copy. After the reviewer returns, the helper re-hashes the live file and intent; a different version discards the verdict as `stale` (exit 3), which consumes no round and keeps the session. Repository HEAD is recorded per round as context only. Live on 2026-09-18 with Codex as reviewer:

| Call | Result |
|---|---|
| Round 1, plan file appended to 8 s into the review | `stale` ("plan file changed during review; verdict discarded"), 64.5 s, round 0, thread id saved |
| Re-run unchanged, no flags | `changes_requested`, 3 findings, 27.8 s, resumed in the same thread, round 1/3 |
| `status` | history shows both entries with their snapshot dirs and HEAD |

Exit codes: 0 approved, 1 changes requested, 2 error, 3 stale.

## Step 5 — PR loop, live results

`review-loop pr <number>` takes the PR number and intent; title, body, diff and head/base SHAs come from `gh`. The head revision is fetched from `refs/pull/<n>/head`, checked against the SHA `gh` reported, and checked out detached under `.git/review-loop/worktrees/<id>`; the helper runs the saved `--test-command` there, restores the worktree, and puts the output in the prompt. Version = `<head sha>+<intent hash>`. Live on 2026-09-18 against this repository's PR #8 (the Step 5 branch itself) with Codex as reviewer and `--post-comment`:

| Call | Result |
|---|---|
| Round 1, first attempt | `error` in 2.7 s: Codex rejected the output schema (`required` must list every property). Real bug, fixed and pushed. |
| Re-run right after the push | `stale` (exit 3): `gh pr view` still returned the old head, the post-review check saw the new one. No round consumed, session kept. |
| Re-run | `changes_requested`, 3 findings (test command optional, intent not re-checked, tests dirtied the worktree). All valid, fixed. Comment posted by the helper. |
| Round 2 with `--response-file` | `changes_requested`, 1 finding (failed post-review `gh` lookup reported as stale instead of error). Fixed. |
| Round 3 with `--response-file` | `approved`, 27.4 s, round 3/3, same Codex thread since the first call. |

The loop found four real defects in its own implementation before a human review. Comments are plain issue comments labelled with reviewer, version, round and verdict; no GitHub review or approval is submitted. Known limitation: the PR head is fetched from the remote named `origin`.
