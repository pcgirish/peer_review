"""Claude Code adapter: `claude -p` with a pre-chosen session id and structured output."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from review_loop.adapters.base import AdapterOutput, Invocation
from review_loop.prompt import parse_json_output
from review_loop.schema import InvalidReviewError

# Tool removal only; Bash is not sandboxed by Claude Code. See docs/feasibility-probe.md.
DISALLOWED_TOOLS = "Write,Edit,NotebookEdit"


class ClaudeAdapter:
    name = "claude"

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def build(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        session_id: str | None,
        workdir: Path,
        resume: bool = False,
    ) -> Invocation:
        if not session_id:
            raise ValueError("claude adapter requires a session id (use new_session_id())")
        argv = ["claude", "-p"]
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
        argv += [
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--disallowedTools",
            DISALLOWED_TOOLS,
            "--permission-mode",
            "plan",
            "--permission-prompts",
            "none",
            prompt,
        ]
        return Invocation(argv=argv, stdin_devnull=True, output_file=None)

    def parse(
        self, *, stdout: str, session_id: str | None, output_file: Path | None
    ) -> AdapterOutput:
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise InvalidReviewError(f"claude output is not JSON: {stdout.strip()[:200]!r}") from e
        if not isinstance(result, dict):
            raise InvalidReviewError("claude output is not a result object")
        if result.get("is_error"):
            raise InvalidReviewError(f"claude reported an error: {result.get('result')!r}")
        sid = result.get("session_id") or session_id
        if not sid:
            raise InvalidReviewError("claude output has no session_id")
        if session_id and sid != session_id:
            raise InvalidReviewError(
                f"claude returned session {sid!r} but {session_id!r} was requested; "
                "the saved session was not resumed"
            )
        if "structured_output" in result:
            return AdapterOutput(session_id=sid, review=result["structured_output"])
        text = result.get("result")
        if not isinstance(text, str):
            raise InvalidReviewError("claude output has neither structured_output nor result text")
        return AdapterOutput(session_id=sid, review=parse_json_output(text))

    def session_id_from_partial(self, stdout: str) -> str | None:
        return None
