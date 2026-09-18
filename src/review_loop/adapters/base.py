from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Invocation:
    """A fully built reviewer command. `temp_files` are removed by the runner."""

    argv: list[str]
    stdin_devnull: bool = True
    output_file: Path | None = None
    temp_files: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class AdapterOutput:
    session_id: str
    review: Any  # parsed JSON, not yet validated


class Adapter(Protocol):
    name: str

    def new_session_id(self) -> str | None:
        """Pre-chosen session id, or None if the CLI assigns one on first call."""

    def build(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        session_id: str | None,
        workdir: Path,
        resume: bool = False,
    ) -> Invocation: ...

    def parse(
        self, *, stdout: str, session_id: str | None, output_file: Path | None
    ) -> AdapterOutput: ...

    def session_id_from_partial(self, stdout: str) -> str | None:
        """Best-effort session id from incomplete output (e.g. after a timeout)."""
