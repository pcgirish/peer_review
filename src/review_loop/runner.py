"""Run one reviewer call in the foreground with a recursion guard and timeout cleanup."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from review_loop.adapters import get_adapter
from review_loop.schema import (
    InvalidReviewError,
    Kind,
    ReviewResult,
    review_schema,
    validate_review,
)

REVIEWER_ENV = "REVIEW_LOOP_REVIEWER"
DEFAULT_TIMEOUT = 540.0
_KILL_GRACE = 2.0

Status = Literal["approved", "changes_requested", "error"]


class RecursionGuardError(RuntimeError):
    """Raised when the helper is invoked from inside a reviewer session."""


@dataclass(frozen=True)
class ReviewOutcome:
    status: Status
    reviewer: str
    session_id: str | None
    review: ReviewResult | None
    error: str | None
    elapsed: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reviewer": self.reviewer,
            "session_id": self.session_id,
            "review": self.review.to_dict() if self.review else None,
            "error": self.error,
            "elapsed_seconds": round(self.elapsed, 1),
        }


def check_recursion_guard(env: dict[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    if env.get(REVIEWER_ENV) == "1":
        raise RecursionGuardError(
            f"{REVIEWER_ENV}=1 is set: review-loop must not be invoked from a reviewer session"
        )


def run_review(
    reviewer: str,
    *,
    prompt: str,
    kind: Kind,
    version: str,
    cwd: Path,
    session_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ReviewOutcome:
    check_recursion_guard()
    adapter = get_adapter(reviewer)
    resume = session_id is not None
    if not resume:
        session_id = adapter.new_session_id()
    inv = adapter.build(
        prompt=prompt,
        schema=review_schema(kind),
        session_id=session_id,
        workdir=cwd,
        resume=resume,
    )
    env = {**os.environ, REVIEWER_ENV: "1"}
    started = time.monotonic()
    stdout = ""
    error: str | None = None
    try:
        try:
            proc = subprocess.Popen(
                inv.argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL if inv.stdin_devnull else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as e:
            return _outcome(reviewer, session_id, None, f"cannot start {inv.argv[0]}: {e}", started)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            stdout, stderr = proc.communicate()
            sid = session_id or adapter.session_id_from_partial(stdout)
            return _outcome(reviewer, sid, None, f"reviewer timed out after {timeout:g}s", started)
        detail = f" (exit {proc.returncode}; stderr: {stderr.strip()[:300]!r})"
        if proc.returncode != 0:
            sid = session_id or adapter.session_id_from_partial(stdout)
            msg = _error_message(adapter, stdout) or "reviewer exited with a nonzero status"
            return _outcome(reviewer, sid, None, f"{msg}{detail}", started)
        try:
            parsed = adapter.parse(
                stdout=stdout, session_id=session_id, output_file=inv.output_file
            )
        except InvalidReviewError as e:
            sid = session_id or adapter.session_id_from_partial(stdout)
            return _outcome(reviewer, sid, None, f"{e}{detail}", started)
        try:
            review = validate_review(parsed.review, kind=kind, expected_version=version)
        except InvalidReviewError as e:
            return _outcome(reviewer, parsed.session_id, None, str(e), started)
        return _outcome(reviewer, parsed.session_id, review, error, started)
    finally:
        _cleanup(inv.temp_files)


def _error_message(adapter, stdout: str) -> str | None:
    """Surface the reviewer's own error text (e.g. rate limit) when its output carries one."""
    try:
        adapter.parse(stdout=stdout, session_id=None, output_file=None)
    except InvalidReviewError as e:
        return str(e)
    return None


def _outcome(
    reviewer: str, sid: str | None, review: ReviewResult | None, error: str | None, started: float
) -> ReviewOutcome:
    status: Status = "error" if review is None else review.verdict
    return ReviewOutcome(status, reviewer, sid, review, error, time.monotonic() - started)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the reviewer's whole process group.

    SIGTERM first, then SIGKILL to the group unconditionally: a descendant that
    ignores SIGTERM would otherwise keep the stdout/stderr pipes open and hang
    the following `communicate()`.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + _KILL_GRACE
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _cleanup(paths: list[Path]) -> None:
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        except OSError:
            pass
