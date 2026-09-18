# Two-Agent Peer Review — Minimal Plan

Status: Steps 1–2 done (see [feasibility-probe.md](feasibility-probe.md)). `review-loop plan` performs one review call with either reviewer; state, locks, and round limits (Steps 3–4), the PR loop (5), and skill installation (6) are not built.

## Goal and Boundary

When Claude authors a plan or PR, Codex reviews it; when Codex authors, Claude reviews. Each artifact keeps the same reviewer session across all revisions and wrapper restarts. The author continues in its working session.

This is a personal workflow for two cooperating agents using the user's existing tools and GitHub account. It is not a hardened orchestration service. Review quality and session continuity are the priorities.

## Review Scope — Prevent Design Growth

A finding blocks only when it identifies a concrete failure of an agreed requirement or a material defect in the proposed implementation. Explain the trigger, consequence, and smallest sufficient fix. Hypothetical production requirements, optional hardening, preferences, and broader features are suggestions.

The helper includes this Review Scope section in its reviewer prompt. The author evaluates findings rather than accepting every suggestion. If a proposed safeguard adds substantial machinery, first consider removing the feature that needs it. Neither agent may expand the agreed scope; unresolved tradeoffs go to the user. Stop after three completed rounds without approval.

## Minimal Design

- One Python helper with Claude and Codex adapters; a thin skill in each author CLI invokes it.
- One persistent reviewer session per plan or PR. Different artifacts have separate sessions. Persist its ID immediately and never silently replace it or select the latest session.
- PR input: PR number and original feature/bug request. Infer the repository from the checkout; fetch PR details through existing GitHub access. Ask for repository identity only if ambiguous.
- Plan input: file and original request. On an unknown repository-relative path, the helper assigns an ID and saves the mapping. On a known path, it reuses the existing artifact and session and reports the reused ID. Supply `--id` only when the file moves; update its path mapping and reject a path already owned by another artifact. A content hash identifies the version, not the session.
- Store state under a namespaced directory in the Git common directory so linked worktrees share it. Save session ID, roles, stable reviewer working directory, round count/limit, original intent, reviewed version, and feedback. Separate clones are out of scope.
- Use atomic state writes and one OS-held lock per artifact. Reject overlaps. Process exit releases the lock; do not add manual lock recovery.
- The helper owns the reviewer prompt. Pass user requirements verbatim where available, issue links when present, and separately labeled author responses. Do not ask the user to prepare information already in context.

## Helper Contract

Proposed interface, not yet implemented:

```text
review-loop plan --file <path> --intent-file <path> --author <claude|codex>
review-loop plan --file <same-path> --response-file <path>
review-loop plan --id <id> --file <moved-path> --response-file <path>
review-loop pr <number> --intent-file <path> --author <claude|codex>
review-loop pr <number> --response-file <path>
review-loop status <artifact>
review-loop extend <artifact> --to <total-limit>
review-loop reset <artifact>
```

The helper returns a stable artifact key. Both review commands accept intent and response files; later calls reuse saved intent and roles unless explicitly updated. `extend` requires user direction and keeps session/count. `reset` requires user direction, retires the old session, resets count/limit to 0/3, and preserves history.

The reviewer returns JSON with `verdict` (`approved` or `changes_requested`), `reviewed_version` (the helper-supplied version string), `blocking_findings` (string array), and `suggestions` (string array). PR reviews also require `tests_run` (array of objects with string `command`, integer `exit_code`, and string `run_by` of `helper` or `reviewer`) and a string `tests_not_run_reason` when no tests ran. Prefer structured output if supported; otherwise strip one enclosing Markdown code fence before parsing the JSON. The helper validates fields and types; approval requires no blocking findings, and changes requested requires at least one. Invalid output is an error, never approval. The helper runs the project test command before invoking a read-only reviewer and passes the output in the prompt; the reviewer records those as `run_by: helper`. Existing CI is not a test either party ran.

Version identity is plan content hash + intent hash for plans, and PR head SHA + intent hash for PRs. Record repository HEAD and PR base commit as review context only; changes to either do not automatically invalidate approval. Review the captured plan snapshot or PR revision. The helper independently compares version identity before and after review: changes produce `stale`; failed checks produce `error`. Neither counts as a completed round. Keep the plan hash check so approval of an older snapshot is not presented as approval of a changed file. Approval covers the recorded version in the inspected context; it is not a guarantee of compatibility with a subsequently changed base.

A round is one valid, non-stale verdict. Three is the default total limit. Stop automatic retries on errors or stale results; report the issue. Session loss requires an explicit reset. Interrupted attempts may be rerun in the saved session; exactly-once execution is unnecessary.

## Practical Controls

The helper sets `REVIEW_LOOP_REVIEWER=1` for reviewer calls and refuses nested calls with that flag. This guards accidental recursion, not deliberate bypass.

Reviewers do not edit author files, push, merge, comment, or submit GitHub approvals. Configure available read-only/tool restrictions and document what they actually enforce. A filesystem sandbox does not make shared GitHub credentials read-only. Strong isolation against hostile repository content is outside this initial scope; do not describe prompt instructions as enforced security.

Use foreground execution with a timeout that fits the author host's measured limit, including cleanup time. If that cannot support useful reviews, resolve the limitation before building workers. Keep reviewer working directories stable if resume requires it.

If plan mode blocks snapshot writes or helper execution, report the review as pending until permitted. Do not bypass host restrictions. Automatic invocation through skills/instructions is best-effort; mandatory hooks are deferred.

## Small Execution Steps

| Step | Build and verify |
|---|---|
| 1. Probe feasibility | Create/resume sessions in both CLIs across processes; check working-directory dependence, tool restrictions, foreground time limits, and plan-mode permissions. Record limitations. |
| 2. One review call | Implement both adapters, helper-owned prompt, JSON validation, recursion guard, and timeout cleanup. Test both directions and failure responses. |
| 3. Preserve sessions | Add artifact identities, state, OS locks, and status/reset/extend. Test known-path reuse, explicit file moves, wrapper restart, linked worktrees, overlap rejection, interrupted calls, and session loss. |
| 4. Complete plan loop | Add snapshots, intent/responses, version checks, and round limits. Verify revision review stays in the same session and stale inputs never approve. |
| 5. Add PR loop | Accept PR number and intent; retrieve context automatically. Verify head movement becomes stale, base-only movement does not, and pushed revisions reuse the same session. |
| 6. Integrate skills | Install the shared skill for both CLIs. Try a real plan and PR in each author direction; verify fixes, disagreement handling, and stopping at the limit. |

No implementation is authorized by this document. Defer detached workers, leases, crash settlement, tmux, publication, hardened isolation, context telemetry, and mandatory hooks until a demonstrated need warrants them.

Skill draft: [peer-review/SKILL.md](peer-review/SKILL.md).
