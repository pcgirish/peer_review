# review-loop usage

`review-loop` runs one review round of a plan or pull request with the opposite
coding agent and keeps that reviewer's session for every later round on the same
artifact. It is the helper behind the `peer-review` skill; the skill decides when to
call it, the helper decides everything else (reviewer, prompt, validation, state).

## Concepts

**Artifact.** A plan file (identified by its repository-relative path) or a pull
request (identified by number). Each artifact gets an 8-character id and its own
reviewer session, round count, and history.

**Roles.** `--author` is the agent that wrote the artifact. The reviewer is always the
other one: author `claude` → reviewer `codex`, author `codex` → reviewer `claude`.
Roles are saved on the first call and reused.

**Intent.** The user's original request, verbatim, read from `--intent-file` and
saved. Later calls reuse the saved text; editing the file has no effect unless
`--intent-file` is passed again, which replaces the saved intent and, because the
intent is part of the version identity, starts a new version.

**Version.** Plans: hash of the plan content + hash of the intent. PRs: head commit SHA
+ hash of the intent. The reviewer must echo the version it reviewed; the helper
compares versions before and after the call and discards the verdict as `stale` if
anything moved.

**Round.** One valid, non-stale verdict. Default limit is 3 per artifact. Errors and
stale results do not consume a round.

**Session.** The reviewer's CLI session (Claude session id or Codex thread id). Saved
the moment it is known, resumed on every later round, retired only by `reset`.

## Commands

All commands accept `--cwd <dir>` (repository to operate in; defaults to the current
directory) and read/write state under that repository's Git common directory, so
linked worktrees share it. Relative file arguments resolve against `--cwd`.

### `plan`

```text
review-loop plan --file <path> --intent-file <path> --author <claude|codex> [--timeout N]
review-loop plan --file <path> [--response-file <path>] [--intent-file <path>]
review-loop plan --id <artifact-id> --file <new-path> ...
```

- First call on a path needs `--intent-file` and `--author`.
- Later calls on the same path reuse the artifact. Pass `--response-file` with what you
  fixed and where you disagree; the reviewer reads it verbatim.
- `--id` only when the file moved. The helper re-maps the artifact to the new path and
  rejects a path already owned by another artifact.
- `--intent-file` on a later call replaces the saved intent (changes the version).

### `pr`

```text
review-loop pr <number> --intent-file <path> --author <claude|codex> \
    --test-command "<shell command>" [--post-comment] [--timeout N]
review-loop pr <number> [--response-file <path>] [--post-comment]
```

- `<number>` is a pull request in the repository of the checkout, fetched with
  `gh pr view`. The head commit is fetched from the remote named `origin`.
- `--test-command` is required on the first review of a PR and saved. Before each
  review the helper checks the PR head out into a detached worktree under the state
  directory, runs the command there, then restores the worktree. The test output goes
  to the reviewer as `run_by: helper`; the reviewer is asked not to run or change
  anything itself (see [What the reviewer sees](#what-the-reviewer-sees) for what is
  actually enforced).
- `--timeout` (default 540 s) is one deadline for the whole round: tests plus the
  reviewer call. If the tests use it up the reviewer is not started.
- `--post-comment` makes the helper post the validated verdict as a plain PR comment
  labelled with reviewer, version, round and verdict. It is an issue comment, never a
  GitHub review or approval. When using it, reply on the PR with your response before
  pushing a revision so the thread reads in order.
- The PR body and diff (truncated at 200,000 characters) are included in the prompt;
  the reviewer can also read the checked-out head.

### `status`, `extend`, `reset`

```text
review-loop status <artifact>
review-loop extend <artifact> --to <total-limit>
review-loop reset <artifact>
```

`<artifact>` is an id, a plan path, or a PR number. `status` prints the full state
including per-round findings and the author's responses. `extend` raises the round
limit and keeps the session. `reset` retires the session, sets rounds/limit back to
0/3, and keeps history. Both are for the user to direct, not the author agent.

## Output

Every command that runs prints one JSON object on stdout (argument errors are
reported by argparse on stderr with exit 2). A `plan` result:

```json
{
  "artifact": "0e0ef5b5",
  "path": "docs/plan.md",
  "round": 1,
  "limit": 3,
  "version": "51df055ce28c+2018a00e5929",
  "status": "changes_requested",
  "reviewer": "claude",
  "session_id": "8b1e1148-…",
  "review": {
    "verdict": "changes_requested",
    "reviewed_version": "51df055ce28c+2018a00e5929",
    "blocking_findings": ["…"],
    "suggestions": ["…"]
  },
  "error": null,
  "elapsed_seconds": 24.3
}
```

A `pr` result has `"pr": 42` instead of `path`, the version is `<head sha>+<intent
hash>`, and `review` additionally carries `tests_run`:

```json
  "review": {
    "verdict": "approved",
    "reviewed_version": "58be8bae…+3f9e87972042",
    "blocking_findings": [],
    "suggestions": [],
    "tests_run": [{"command": "uv run pytest -q", "exit_code": 0, "run_by": "helper"}],
    "tests_not_run_reason": null
  }
```

With `--post-comment`, `comment` holds the comment URL or `comment_error` explains
why posting failed. Errors print `{"status": "error", "error": "…"}`.

| exit | status | meaning |
|---|---|---|
| 0 | `approved` | No blocking findings for this version. Suggestions are optional. |
| 1 | `changes_requested` | At least one blocking finding. Fix real defects; push back in the response file where a finding is wrong or out of scope. |
| 2 | `error` | Reviewer failure, invalid output, round limit, locked artifact, gh failure, tests exhausted the timeout, etc. Never treat as approval. |
| 3 | `stale` | Plan, intent, or PR head changed during the review. Verdict discarded, no round used. Rerun once inputs settle. |

## What the reviewer sees

The helper owns the prompt. It contains: the artifact (plan snapshot, or PR metadata,
diff, and the path of a detached checkout of the head, which the reviewer is asked not to modify), the intent verbatim, the author's response for
this round if any, the helper's test results for PRs, the version string to echo, and
the review-scope rule: a finding blocks only for a concrete failure of an agreed
requirement or a material defect, stated with trigger, consequence, and smallest
sufficient fix. Everything else is a suggestion.

Reviewers run with `REVIEW_LOOP_REVIEWER=1` and the helper refuses to start when
that variable is set. This guards against accidental recursion only; a reviewer that
unsets the variable can bypass it.

What is enforced differs by reviewer:

- **Codex** runs under `--sandbox read-only`: filesystem writes are blocked by the
  CLI. Network and the shared GitHub credentials are not restricted by it.
- **Claude** runs in plan mode with the Write, Edit and NotebookEdit tools disallowed.
  Its Bash tool is not sandboxed, so shell commands can still write, and the prompt's
  instruction not to run or change anything is a request, not a guarantee.

Neither setting makes GitHub credentials read-only. The reviewer never posts, pushes,
or approves by design; only the helper posts comments, and only with `--post-comment`.

## State

```text
<git-common-dir>/review-loop/
  index.json             "<kind>:<path>" -> artifact id
  artifacts/<id>.json    roles, session, intent, rounds, limit, history
  locks/<id>.lock        one OS lock per artifact; overlapping calls are rejected
  snapshots/<id>/<version>/   frozen inputs of each reviewed version
  worktrees/<id>/        detached checkout of the PR head (PRs only)
```

State is per repository. Separate clones do not share it.

## Skill

`.agents/skills/peer-review/SKILL.md` is the shared skill; `.claude/skills` is a
symlink to the same directory. `scripts/install-peer-review.sh` installs the helper
with `uv tool install --editable` and links the skill into `~/.claude/skills` and
`~/.codex/skills`. In a session, say "use the peer-review skill" or let it activate
when a plan or PR is ready. The skill never runs from a reviewer session.

## Limitations

- PR heads are fetched from the remote named `origin`.
- Reviews run in the foreground with one timeout; there are no detached workers.
- Automatic skill activation is best-effort; there are no mandatory hooks.
- Only the Codex reviewer is filesystem-sandboxed; the Claude reviewer's shell is
  not. Neither is isolated from GitHub credentials its CLI can already use.
