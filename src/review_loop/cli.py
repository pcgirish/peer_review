"""`review-loop` command line. Step 2 scope: one plan review call, no persisted state yet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from review_loop.adapters import AGENTS, reviewer_for
from review_loop.prompt import build_reviewer_prompt
from review_loop.runner import DEFAULT_TIMEOUT, RecursionGuardError, run_review
from review_loop.version import plan_version

EXIT_APPROVED, EXIT_CHANGES, EXIT_ERROR = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="review-loop", description="Two-agent peer review helper.")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Review a plan file with the opposite agent.")
    plan.add_argument("--file", required=True, type=Path, help="plan file to review")
    plan.add_argument("--intent-file", required=True, type=Path, help="verbatim user request")
    plan.add_argument("--author", required=True, choices=AGENTS, help="agent that wrote the plan")
    plan.add_argument("--response-file", type=Path, help="author response to the previous round")
    plan.add_argument("--session-id", help="existing reviewer session to resume")
    plan.add_argument("--cwd", type=Path, default=Path.cwd(), help="reviewer working directory")
    plan.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds")
    plan.set_defaults(func=_cmd_plan)
    return p


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise CliError(f"cannot read {what} {path}: {e.strerror}") from e


class CliError(Exception):
    pass


def _cmd_plan(args: argparse.Namespace) -> dict:
    content = _read(args.file, "plan file")
    intent = _read(args.intent_file, "intent file")
    response = _read(args.response_file, "response file") if args.response_file else None
    reviewer = reviewer_for(args.author)
    version = plan_version(content, intent)
    try:
        rel = args.file.resolve().relative_to(args.cwd.resolve())
    except ValueError:
        rel = args.file
    prompt = build_reviewer_prompt(
        kind="plan",
        version=version,
        intent=intent,
        artifact_label=f"Plan file: {rel}",
        artifact_content=content,
        author_response=response,
    )
    outcome = run_review(
        reviewer,
        prompt=prompt,
        kind="plan",
        version=version,
        cwd=args.cwd,
        session_id=args.session_id,
        timeout=args.timeout,
    )
    return {"version": version, **outcome.to_dict()}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.func(args)
    except (CliError, RecursionGuardError, ValueError) as e:
        print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return EXIT_ERROR
    print(json.dumps(result, indent=2))
    return {"approved": EXIT_APPROVED, "changes_requested": EXIT_CHANGES}.get(
        result["status"], EXIT_ERROR
    )


if __name__ == "__main__":
    sys.exit(main())
