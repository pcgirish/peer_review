---
name: peer-review
description: Request review from the opposite coding agent when a plan is ready or a PR changes, preserving the same reviewer session across revisions. Do not invoke from reviewer sessions.
---

# Peer Review

Draft: the `review-loop` helper is not built or installed. If unavailable, report the dependency and stop. Do not claim a review occurred.

## Invoke

Use Claude as reviewer when the author is Codex, and Codex when the author is Claude. Do not invoke if `REVIEW_LOOP_REVIEWER=1`.

Prepare an intent file from verbatim available user requirements and corrections, with issue links when present. Label interpretations separately. Prepare a response file for subsequent rounds explaining fixes or disagreements.

Proposed helper interface:

```text
review-loop plan --file <path> --intent-file <path> --author <claude|codex>
review-loop plan --file <same-path> --response-file <path>
review-loop plan --id <returned-id> --file <moved-path> --response-file <path>
review-loop pr <number> --intent-file <path> --author <claude|codex>
review-loop pr <number> --response-file <path>
```

Infer repository context locally. Ask only for missing requirements or ambiguous repository identity. Do not manually assemble GitHub context. For revisions at the same plan path, the helper reuses the existing artifact and reports its ID. Supply that ID with `--id` only after a file move; do not register the moved file as a new plan. The helper owns session selection, reviewer instructions, validation, and limits; do not replace these yourself.

If host plan mode blocks needed writes or execution, report review as pending until permitted.

## Handle Results

- `approved`: report the reviewed version and relevant remaining suggestions.
- `changes_requested`: assess findings against agreed requirements. Fix concrete defects within authorized scope or explain disagreement, then resubmit using the same artifact identity. Suggestions are optional, not new requirements.
- `stale` or `error`: report the problem and stop automatic retries. Never treat either as approval or silently start a replacement session.
- Limit reached: present unresolved findings and wait for user direction. Use `review-loop extend <artifact> --to <total-limit>` only when directed.
- Lost reviewer session: report it; use `review-loop reset <artifact>` only when directed. Use `review-loop status <artifact>` to inspect state.

Do not let peer review expand the task. Require a concrete failure and smallest sufficient fix for blocking findings. If a fix introduces substantial machinery, reconsider the feature or ask the user to resolve the tradeoff. Agreement between agents does not authorize extra scope, publishing, or merging.

Automatic skill selection is best-effort, not an enforced gate.
