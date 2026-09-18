"""Codex CLI adapter: `codex exec --json --sandbox read-only --output-schema … -o …`.

Global flags must precede `resume`. The thread id is only known from the first
`thread.started` event, so the runner must persist it as soon as it is seen.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from review_loop.adapters.base import AdapterOutput, Invocation
from review_loop.prompt import parse_json_output
from review_loop.schema import InvalidReviewError


class CodexAdapter:
    name = "codex"

    def new_session_id(self) -> None:
        return None

    def build(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        session_id: str | None,
        workdir: Path,
        resume: bool = False,
    ) -> Invocation:
        if resume and not session_id:
            raise ValueError("codex resume requires a thread id")
        tmpdir = Path(tempfile.mkdtemp(prefix="review-loop-codex-"))
        schema_file = tmpdir / "schema.json"
        schema_file.write_text(json.dumps(schema))
        output_file = tmpdir / "review.json"
        argv = [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema_file),
            "-o",
            str(output_file),
        ]
        if resume:
            argv += ["resume", session_id]  # type: ignore[list-item]
        argv.append(prompt)
        return Invocation(
            argv=argv,
            stdin_devnull=True,
            output_file=output_file,
            temp_files=[schema_file, output_file, tmpdir],
        )

    @staticmethod
    def _events(stdout: str):
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def session_id_from_partial(self, stdout: str) -> str | None:
        for ev in self._events(stdout):
            if ev.get("type") == "thread.started" and ev.get("thread_id"):
                return str(ev["thread_id"])
        return None

    def parse(
        self, *, stdout: str, session_id: str | None, output_file: Path | None
    ) -> AdapterOutput:
        thread_id = self.session_id_from_partial(stdout) or session_id
        last_message: str | None = None
        for ev in self._events(stdout):
            if ev.get("type") == "error":
                raise InvalidReviewError(f"codex reported an error: {ev.get('message')!r}")
            item = ev.get("item") if ev.get("type") == "item.completed" else None
            if isinstance(item, dict) and item.get("type") == "agent_message":
                last_message = item.get("text")
        if not thread_id:
            raise InvalidReviewError("codex output has no thread.started event (thread_id unknown)")
        if output_file is not None and output_file.is_file():
            text = output_file.read_text()
            if text.strip():
                return AdapterOutput(session_id=thread_id, review=parse_json_output(text))
        if last_message is None:
            raise InvalidReviewError("codex produced no structured output and no agent message")
        return AdapterOutput(session_id=thread_id, review=parse_json_output(last_message))
