# peer_agent

Two-agent peer review for coding agents. When Claude Code authors a plan or pull
request, Codex CLI reviews it; when Codex authors, Claude reviews. One reviewer
session is kept per artifact, so every revision goes back to the reviewer that
already knows the history.

## Why

A single agent reviewing its own work misses what it misread the first time. A
second, independent model with a persistent view of the artifact catches real
defects and scope growth, and its memory of earlier rounds keeps the exchange
short. This project makes that loop a one-line command inside either CLI, with
guard rails that discourage scope growth and prevent approving stale input or
running forever.

While building it, the loop reviewed its own pull requests and found four real
defects before a human looked (see `docs/feasibility-probe.md`).

## What it does

- `review-loop plan` reviews a plan file against the user's original request.
- `review-loop pr <number>` reviews a GitHub pull request: fetches it with `gh`,
  checks the head out into a detached worktree, runs the project's tests, and hands
  the diff and test output to the reviewer. Optionally posts each verdict as a plain PR comment.
- The reviewer answers in validated JSON: `approved` or `changes_requested`, with
  blocking findings and suggestions. Malformed output is an error, never approval.
- Inputs that change during a review are reported as `stale`; that round does not
  count. After three rounds without approval the loop stops and asks the user.
- A `peer-review` skill, shared by both CLIs, tells the authoring agent when to call
  the helper and what to do with each result.

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- `claude` (Claude Code) and `codex` (Codex CLI) on PATH, both logged in
- `gh` (GitHub CLI) logged in, for PR reviews

## Install

Inside this repository nothing needs installing: `uv sync`, then `uv run review-loop`.
Both CLIs pick up the skill from `.agents/skills` (Codex) and `.claude/skills` (Claude
Code, a symlink to the same directory).

For use in other repositories:

```sh
scripts/install-peer-review.sh
```

This installs `review-loop` on PATH with `uv tool install --editable` and symlinks the
skill into `~/.claude/skills/peer-review` and `~/.codex/skills/peer-review`.

## Usage

Write the user's request verbatim to a file, then:

```sh
# Plan written by Claude, reviewed by Codex
review-loop plan --file docs/my-plan.md --intent-file intent.md --author claude

# After revising the plan: same path, add your response to the last round
review-loop plan --file docs/my-plan.md --response-file response.md

# Pull request written by Codex, reviewed by Claude; helper runs the tests first
review-loop pr 42 --intent-file intent.md --author codex \
    --test-command "uv run pytest -q" --post-comment

# After pushing a revision
review-loop pr 42 --response-file response.md --post-comment

review-loop status 42            # rounds, session, per-round findings
review-loop extend 42 --to 5     # raise the round limit (user's call)
review-loop reset 42             # retire the session and start over (user's call)
```

Output is JSON on stdout. Exit codes:

| exit | status |
|---|---|
| 0 | `approved` |
| 1 | `changes_requested` |
| 2 | `error` (including the round limit) |
| 3 | `stale`: inputs changed during the review; rerun |

From an agent session, ask for the skill by name ("use the peer-review skill") or
let it activate when a plan or PR is ready. Full details: [docs/usage.md](docs/usage.md).

## Development

```sh
uv sync
uv run pytest -q
uv run ruff check src tests && uv run ruff format --check src tests
```

Design and history: [docs/two-agent-review-plan.md](docs/two-agent-review-plan.md)
and [docs/feasibility-probe.md](docs/feasibility-probe.md). Contributor workflow:
[AGENTS.md](AGENTS.md).
