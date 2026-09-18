"""Run the project's test command on behalf of a read-only reviewer."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_CHARS = 20_000


@dataclass(frozen=True)
class TestOutput:
    command: str
    exit_code: int
    output: str
    run_by: str = "helper"

    def to_prompt_dict(self) -> dict:
        return {"command": self.command, "exit_code": self.exit_code, "output": self.output}

    def to_tests_run_entry(self) -> dict:
        return {"command": self.command, "exit_code": self.exit_code, "run_by": self.run_by}


def run_tests(
    command: str, *, cwd: Path, timeout: float, max_chars: int = DEFAULT_MAX_CHARS
) -> TestOutput:
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
        out = (out or "") + f"\n[review-loop] test command timed out after {timeout:g}s\n"
        code = 124
        time.sleep(0)
    return TestOutput(command, code, _truncate(out or "", max_chars))


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars // 4
    tail = max_chars - head
    return f"{text[:head]}\n\n[... truncated {len(text) - max_chars} chars ...]\n\n{text[-tail:]}"
