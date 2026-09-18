"""`review-loop` command line: plan review with persisted per-artifact state (Steps 2-3)."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from review_loop.adapters import AGENTS, reviewer_for
from review_loop.github import (
    GitHubError,
    checkout_revision,
    fetch_pr,
    head_sha,
    post_comment,
    pr_diff,
    restore_worktree,
)
from review_loop.prompt import build_reviewer_prompt
from review_loop.runner import DEFAULT_TIMEOUT, RecursionGuardError, ReviewOutcome, run_review
from review_loop.state import ArtifactState, StateError, Store, repo_head, repo_paths
from review_loop.tests_runner import _truncate, run_tests
from review_loop.version import plan_version, pr_version

EXIT_APPROVED, EXIT_CHANGES, EXIT_ERROR, EXIT_STALE = 0, 1, 2, 3


class CliError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="review-loop", description="Two-agent peer review helper.")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Review a plan file with the opposite agent.")
    plan.add_argument("--file", required=True, type=Path, help="plan file to review")
    plan.add_argument("--intent-file", type=Path, help="verbatim user request (first call)")
    plan.add_argument("--author", choices=AGENTS, help="agent that wrote the plan (first call)")
    plan.add_argument("--response-file", type=Path, help="author response to the previous round")
    plan.add_argument("--id", help="existing artifact id; only when the file moved")
    plan.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT, help="seconds (> 0)")
    _add_cwd(plan)
    plan.set_defaults(func=_cmd_plan)

    pr = sub.add_parser("pr", help="Review a pull request with the opposite agent.")
    pr.add_argument("number", type=int, help="pull request number in the checkout's repository")
    pr.add_argument("--intent-file", type=Path, help="verbatim user request (first call)")
    pr.add_argument("--author", choices=AGENTS, help="agent that wrote the PR (first call)")
    pr.add_argument("--response-file", type=Path, help="author response to the previous round")
    pr.add_argument(
        "--test-command", help="shell command the helper runs on the PR head (saved for reuse)"
    )
    pr.add_argument(
        "--post-comment",
        action="store_true",
        help="post the validated verdict as a plain PR comment (helper posts, never the reviewer)",
    )
    pr.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT, help="seconds (> 0)")
    _add_cwd(pr)
    pr.set_defaults(func=_cmd_pr)

    status = sub.add_parser("status", help="Show an artifact's saved state.")
    status.add_argument("artifact", help="artifact id or repo-relative path")
    _add_cwd(status)
    status.set_defaults(func=_cmd_status)

    reset = sub.add_parser("reset", help="Retire the reviewer session and reset rounds to 0/3.")
    reset.add_argument("artifact", help="artifact id or repo-relative path")
    _add_cwd(reset)
    reset.set_defaults(func=_cmd_reset)

    extend = sub.add_parser("extend", help="Raise the total round limit; keeps session and count.")
    extend.add_argument("artifact", help="artifact id or repo-relative path")
    extend.add_argument("--to", required=True, type=int, help="new total round limit")
    _add_cwd(extend)
    extend.set_defaults(func=_cmd_extend)
    return p


def _add_cwd(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cwd", type=Path, default=Path.cwd(), help="repository working directory")


def _timeout(text: str) -> float:
    try:
        value = float(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid timeout {text!r}") from e
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite number greater than zero")
    return value


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise CliError(f"cannot read {what} {path}: {e.strerror}") from e


def _store(cwd: Path) -> tuple[Store, Path]:
    paths = repo_paths(cwd)
    return Store(paths.common_dir), paths.toplevel


def _rel_path(file: Path, toplevel: Path) -> str:
    try:
        return file.resolve().relative_to(toplevel).as_posix()
    except ValueError as e:
        raise CliError(f"{file} is not inside the repository at {toplevel}") from e


# --- plan --------------------------------------------------------------------


def _cmd_plan(args: argparse.Namespace) -> dict[str, Any]:
    content = _read(args.file, "plan file")
    store, toplevel = _store(args.cwd)
    rel = _rel_path(args.file, toplevel)

    # Identity first (reserve is index-locked), then the artifact lock, then a fresh
    # load: nothing read before the lock is allowed to reach disk.
    artifact_id = store.load(args.id).id if args.id else store.reserve("plan", rel).id

    with store.lock(artifact_id):
        state = store.load(artifact_id)
        if state.path != rel:
            if not args.id:
                raise CliError(f"artifact {artifact_id} is at {state.path!r}, not {rel!r}")
            state = store.move(artifact_id, rel)

        response = _apply_inputs(state, args, toplevel, store)
        version = plan_version(content, state.intent)
        resumed = state.session_id is not None
        snapshot = store.write_snapshot(
            state.id, version, {"plan.md": content, "intent.md": state.intent}
        )
        head = repo_head(toplevel)
        prompt = build_reviewer_prompt(
            kind="plan",
            version=version,
            intent=state.intent,
            artifact_label=(
                f"Plan file: {rel} (frozen copy under review: {snapshot / 'plan.md'}; "
                "the working-tree file may change while you review)"
            ),
            artifact_content=content,
            author_response=response,
        )
        outcome = run_review(
            state.reviewer,
            prompt=prompt,
            kind="plan",
            version=version,
            cwd=Path(state.reviewer_cwd),
            session_id=state.session_id,
            timeout=args.timeout,
            on_session_id=lambda sid: _persist_session(store, state, sid),
        )
        outcome = _stale_check(outcome, args, state, version)
        _record(
            state,
            outcome,
            version,
            response,
            resumed=resumed,
            context={"snapshot": str(snapshot), "head": head},
        )
        store.save(state)
    return {
        "artifact": state.id,
        "path": state.path,
        "round": state.rounds,
        "limit": state.limit,
        "version": version,
        **outcome.to_dict(),
    }


def _apply_inputs(
    state: ArtifactState, args: argparse.Namespace, toplevel: Path, store: Store
) -> str | None:
    """Merge this call's flags into the locked state, persist, and enforce the limit.
    Returns the author response text, if any."""
    noun = "plan" if state.kind == "plan" else "PR"
    if args.intent_file:
        state.intent = _read(args.intent_file, "intent file")
    if args.author:
        state.author = args.author
        state.reviewer = reviewer_for(args.author)
    if getattr(args, "test_command", None):
        state.test_command = args.test_command
    if state.intent is None:
        raise CliError(f"--intent-file is required on the first review of a {noun}")
    if state.author is None or state.reviewer is None:
        raise CliError(f"--author is required on the first review of a {noun}")
    if state.kind == "pr" and state.test_command is None:
        # the helper runs the project's tests before every PR review (plan contract)
        raise CliError("--test-command is required on the first review of a PR")
    response = _read(args.response_file, "response file") if args.response_file else None
    if state.reviewer_cwd is None:
        state.reviewer_cwd = str(toplevel)
    store.save(state)
    if state.rounds >= state.limit:
        raise CliError(
            f"round limit reached ({state.rounds}/{state.limit}) for artifact {state.id}; "
            "use `extend` or `reset`"
        )
    return response


# --- pr ----------------------------------------------------------------------


def _cmd_pr(args: argparse.Namespace) -> dict[str, Any]:
    # --timeout is one deadline for the whole round: helper tests + reviewer call
    deadline = time.monotonic() + args.timeout
    store, toplevel = _store(args.cwd)
    artifact_id = store.reserve("pr", str(args.number)).id
    with store.lock(artifact_id):
        state = store.load(artifact_id)
        response = _apply_inputs(state, args, toplevel, store)
        pr = fetch_pr(args.number, cwd=toplevel)
        version = pr_version(pr.head_sha, state.intent)
        resumed = state.session_id is not None
        worktree = checkout_revision(
            pr.number, pr.head_sha, dest=store.worktree_dir(state.id), cwd=toplevel
        )
        test_output = run_tests(
            state.test_command, cwd=worktree, timeout=max(deadline - time.monotonic(), 0.0)
        )
        tests = [test_output.to_prompt_dict()]
        if deadline - time.monotonic() <= 0:
            raise CliError(
                f"tests exhausted the round's timeout ({args.timeout:g}s); reviewer not started"
            )
        # tests may write into the worktree; the reviewer must see the PR head, nothing else
        restore_worktree(worktree, pr.head_sha)
        diff = pr_diff(pr.number, cwd=toplevel)
        snapshot = store.write_snapshot(
            state.id,
            version,
            {
                "pr.json": json.dumps(pr.__dict__, indent=2),
                "diff.patch": diff,
                "intent.md": state.intent,
            },
        )
        prompt = build_reviewer_prompt(
            kind="pr",
            version=version,
            intent=state.intent,
            artifact_label=(
                f"Pull request #{pr.number}: {pr.title} ({pr.url}); head {pr.head_sha} "
                f"(branch {pr.head_ref}) onto {pr.base_ref}@{pr.base_sha}. The head revision is "
                f"checked out read-only at {worktree.resolve()}; review that checkout, not the "
                "working tree"
            ),
            artifact_content=_pr_content(pr.body, diff),
            author_response=response,
            tests=tests,
        )
        # recomputed last: restoring the worktree, fetching the diff and writing the
        # snapshot also spend the round's budget
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError(
                f"preparing the review exhausted the round's timeout ({args.timeout:g}s); "
                "reviewer not started"
            )
        outcome = run_review(
            state.reviewer,
            prompt=prompt,
            kind="pr",
            version=version,
            cwd=Path(state.reviewer_cwd),
            session_id=state.session_id,
            timeout=remaining,
            on_session_id=lambda sid: _persist_session(store, state, sid),
        )
        outcome = _pr_stale_check(outcome, args, state, pr.head_sha, version, toplevel)
        context: dict[str, Any] = {
            "snapshot": str(snapshot),
            "pr_head": pr.head_sha,
            "pr_base": pr.base_sha,
            "head": repo_head(toplevel),
            # the helper's own record, independent of what the reviewer echoes
            "tests": [{**test_output.to_tests_run_entry(), "output": test_output.output}],
        }
        extra: dict[str, Any] = {}
        if args.post_comment and outcome.review is not None:
            body = _comment_body(state, outcome, version, rounds=state.rounds + 1)
            try:
                extra["comment"] = post_comment(pr.number, body, cwd=toplevel)
            except GitHubError as e:
                extra["comment_error"] = str(e)
        _record(state, outcome, version, response, resumed=resumed, context={**context, **extra})
        store.save(state)
    return {
        "artifact": state.id,
        "pr": pr.number,
        "round": state.rounds,
        "limit": state.limit,
        "version": version,
        **outcome.to_dict(),
        **extra,
    }


def _pr_stale_check(
    outcome: ReviewOutcome,
    args: argparse.Namespace,
    state: ArtifactState,
    head: str,
    version: str,
    toplevel: Path,
) -> ReviewOutcome:
    """Re-derive the full version (head sha + intent) after the review; any drift means
    the verdict covers a revision that no longer exists."""
    if outcome.status == "error":
        return outcome
    try:
        now_intent = _read(args.intent_file, "intent file") if args.intent_file else state.intent
    except CliError as e:
        return outcome.stale(f"inputs changed during review: {e}")
    try:
        now_head = head_sha(args.number, cwd=toplevel)
    except GitHubError as e:
        # not a confirmed change: the verdict is unverifiable, which is an error, not stale
        return ReviewOutcome(
            "error",
            outcome.reviewer,
            outcome.session_id,
            None,
            f"could not verify the PR head after the review: {e}",
            outcome.elapsed,
        )
    if pr_version(now_head, now_intent) == version:
        return outcome
    changed = []
    if now_head != head:
        changed.append(f"PR head moved during review ({head[:12]} -> {now_head[:12]})")
    if now_intent != state.intent:
        changed.append("intent changed during review")
    return outcome.stale("; ".join(changed) + "; verdict discarded")


_DIFF_MAX_CHARS = 200_000


def _pr_content(body: str, diff: str) -> str:
    parts = ["### PR description", "", body.strip() or "(empty)", "", "### Diff", "", "```diff"]
    parts += [_truncate(diff, _DIFF_MAX_CHARS).rstrip(), "```"]
    return "\n".join(parts)


def _comment_body(
    state: ArtifactState, outcome: ReviewOutcome, version: str, *, rounds: int
) -> str:
    review = outcome.review
    assert review is not None
    header = (
        f"**review-loop** — reviewer: `{state.reviewer}` · version `{version}` · "
        f"round {rounds}/{state.limit} · verdict: **{review.verdict}**"
    )
    lines = [header, ""]
    if review.blocking_findings:
        lines += ["**Blocking findings**", *[f"- {f}" for f in review.blocking_findings], ""]
    if review.suggestions:
        lines += ["**Suggestions**", *[f"- {s}" for s in review.suggestions], ""]
    if review.tests_run:
        lines += [
            "**Tests run**",
            *[f"- `{t.command}` → exit {t.exit_code} ({t.run_by})" for t in review.tests_run],
            "",
        ]
    elif review.tests_not_run_reason:
        lines += [f"**Tests not run:** {review.tests_not_run_reason}", ""]
    footer = (
        "_Posted by the helper on the reviewer's behalf; this is not a GitHub review or approval. "
        "Author: reply on this PR with your response before pushing a revision._"
    )
    lines.append(footer)
    return "\n".join(lines) + "\n"


def _stale_check(
    outcome: ReviewOutcome, args: argparse.Namespace, state: ArtifactState, version: str
) -> ReviewOutcome:
    """Re-derive the version from the live inputs after the review. The reviewer saw the
    snapshot; if the file or intent moved on, its verdict must not be reported for the
    new content (approval of an older snapshot is not approval of a changed file)."""
    if outcome.status == "error":
        return outcome
    try:
        now_content = _read(args.file, "plan file")
        now_intent = _read(args.intent_file, "intent file") if args.intent_file else state.intent
    except CliError as e:
        return outcome.stale(f"inputs changed during review: {e}")
    if plan_version(now_content, now_intent) == version:
        return outcome
    changed = []
    if plan_version(now_content, state.intent) != version:
        changed.append("plan file")
    if now_intent != state.intent:
        changed.append("intent")
    return outcome.stale(f"{' and '.join(changed)} changed during review; verdict discarded")


def _persist_session(store: Store, state: ArtifactState, sid: str) -> None:
    """Save a new session id the moment it is known, so an interrupted first call
    resumes the same reviewer session next time instead of silently starting another."""
    state.session_id = sid
    store.save(state)


def _record(
    state: ArtifactState,
    outcome: ReviewOutcome,
    version: str,
    response: str | None,
    *,
    resumed: bool,
    context: dict[str, Any] | None = None,
) -> None:
    if outcome.session_id and not resumed:
        state.session_id = outcome.session_id
    counted = outcome.status in ("approved", "changes_requested")
    if counted:
        state.rounds += 1
        state.reviewed_version = version
    state.last_status = outcome.status
    entry: dict[str, Any] = {
        "round": state.rounds if counted else None,
        "version": version,
        "status": outcome.status,
        "author_response": response,
        **(context or {}),
    }
    if outcome.review:
        entry["blocking_findings"] = outcome.review.blocking_findings
        entry["suggestions"] = outcome.review.suggestions
    if outcome.error:
        entry["error"] = outcome.error
    state.history.append(entry)
    if resumed and outcome.error and "was not resumed" in outcome.error:
        object.__setattr__(
            outcome, "error", f"{outcome.error}. Session lost: run `review-loop reset {state.id}`"
        )


# --- status / reset / extend ---------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    store, _ = _store(args.cwd)
    return store.resolve(args.artifact).to_dict()


def _cmd_reset(args: argparse.Namespace) -> dict[str, Any]:
    store, _ = _store(args.cwd)
    artifact_id = store.resolve(args.artifact).id
    with store.lock(artifact_id):
        state = store.load(artifact_id)
        state.reset()
        store.save(state)
    return state.to_dict()


def _cmd_extend(args: argparse.Namespace) -> dict[str, Any]:
    store, _ = _store(args.cwd)
    artifact_id = store.resolve(args.artifact).id
    with store.lock(artifact_id):
        state = store.load(artifact_id)
        if args.to <= state.limit:
            raise CliError(f"--to must exceed the current limit ({state.limit})")
        state.limit = args.to
        store.save(state)
    return state.to_dict()


# --- entry point -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.func(args)
    except (CliError, StateError, GitHubError, RecursionGuardError, ValueError) as e:
        print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return EXIT_ERROR
    print(json.dumps(result, indent=2))
    if args.command not in ("plan", "pr"):
        return EXIT_APPROVED
    return {"approved": EXIT_APPROVED, "changes_requested": EXIT_CHANGES, "stale": EXIT_STALE}.get(
        result["status"], EXIT_ERROR
    )


if __name__ == "__main__":
    sys.exit(main())
