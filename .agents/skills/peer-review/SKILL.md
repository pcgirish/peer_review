---
name: peer-review
description: Request review from the opposite coding agent (Claude authors → Codex reviews, Codex authors → Claude reviews) when a plan is ready or a PR is opened or revised. One reviewer session per artifact across revisions. Use for any plan or PR review in this repo; never from a reviewer session.
---

# Peer Review

The `review-loop` helper owns reviewer selection, session persistence, the reviewer
prompt, output validation, stale detection, and the round limit. This skill only
decides when to call it and what to do with the result.

## Preconditions

- Do not invoke when `REVIEW_LOOP_REVIEWER=1` is set (you are the reviewer).
- Run `uv run review-loop …` in this repo, or plain `review-loop …` where it was
  installed with `scripts/install-peer-review.sh`. Outside the repository pass
  `--cwd <repo>`; relative file arguments resolve against it.
- If the host's plan mode blocks the helper from writing state or running, report
  the review as *pending* and stop. Do not claim a review happened.

## Inputs you prepare

- **Intent file** (first call only): the user's request verbatim, plus corrections
  and issue links if any. Label your own interpretations separately. Do not ask the
  user for information already in context.
- **Response file** (later rounds): what you fixed, and where you disagree and why.
  Keep it factual; the reviewer reads it verbatim.
- **Test command** (PR, first call only): the project's test command, e.g.
  `uv run pytest -q`. The helper runs it on the PR head before each review.

## Commands

```text
review-loop plan --file <path> --intent-file <path> --author <claude|codex>
review-loop plan --file <same-path> --response-file <path>
review-loop plan --id <id> --file <moved-path> --response-file <path>   # only after a move
review-loop pr <number> --intent-file <path> --author <claude|codex> --test-command "<cmd>" [--post-comment]
review-loop pr <number> --response-file <path> [--post-comment]
review-loop status <artifact-id | path | pr-number>
review-loop extend <artifact> --to <total>      # only when the user directs
review-loop reset <artifact>                    # only when the user directs
```

`--author` is you. Later calls reuse the saved intent, roles, and test command.
A revision at the same path or PR number reuses the artifact and session; pass
`--id` only when a plan file moved. Never assemble PR context by hand; the helper
fetches it with `gh`. Never pass a session id yourself.

`--post-comment` is opt-in: the helper (not the reviewer) posts each validated
verdict as a plain PR comment labelled with reviewer, version, and round. When
using it, reply on the PR with your response text before pushing a revision. No
GitHub review or approval is ever submitted by either agent.

## Results (JSON on stdout; exit code)

| status | exit | what to do |
|---|---|---|
| `approved` | 0 | Report the reviewed version. Mention remaining suggestions; they are optional. |
| `changes_requested` | 1 | Verify each blocking finding against the agreed requirements. Fix real defects and feature gaps within scope; push back in the response file where a finding is wrong or out of scope. Resubmit at the same path/number. |
| `stale` | 3 | The plan, intent, or PR head changed during the review. No round used. Rerun once the inputs are settled; do not loop. |
| `error` | 2 | Report the error and stop. Never treat it as approval; never start a replacement session yourself. |

Round limit reached (error mentioning `extend`/`reset`): present the unresolved
findings and wait for the user. Lost or broken reviewer session: report it; `reset`
only when told. `status` shows rounds, session, and per-round findings.

## Scope discipline

Review must not grow the task. A finding blocks only for a concrete failure of an
agreed requirement or a material defect; it must name the trigger, consequence, and
smallest sufficient fix. Hypothetical hardening and preferences are suggestions. If a
fix needs substantial machinery, question the feature first. Agreement between the
two agents does not authorize extra scope, publishing, or merging.
