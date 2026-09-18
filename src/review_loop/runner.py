"""Run one reviewer call in the foreground with a recursion guard and timeout cleanup."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
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
    on_session_id: Callable[[str], None] | None = None,
) -> ReviewOutcome:
    """Run one review. `on_session_id` fires once, as soon as a *new* session's id is
    known (before the reviewer is spawned when the adapter chooses it, otherwise as soon
    as the reviewer announces it on stdout), so callers can persist it before the call
    ends and a later interruption cannot orphan the session."""
    check_recursion_guard()
    adapter = get_adapter(reviewer)
    resume = session_id is not None
    if not resume:
        session_id = adapter.new_session_id()
        if session_id and on_session_id:
            on_session_id(session_id)
            on_session_id = None
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
        pump = _Pump(proc, adapter, on_session_id if not resume else None)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            stdout, _stderr = pump.finish()
            sid = session_id or adapter.session_id_from_partial(stdout)
            return _outcome(reviewer, sid, None, f"reviewer timed out after {timeout:g}s", started)
        except KeyboardInterrupt:
            _kill_tree(proc)
            stdout, _stderr = pump.finish()
            sid = session_id or adapter.session_id_from_partial(stdout)
            return _outcome(reviewer, sid, None, "interrupted; reviewer terminated", started)
        stdout, stderr = pump.finish()
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


class _Pump:
    """Drain stdout/stderr on threads; report the session id the moment it appears."""

    def __init__(self, proc: subprocess.Popen, adapter, on_session_id) -> None:
        self._proc = proc
        self._adapter = adapter
        self._cb = on_session_id
        self._out: list[str] = []
        self._err: list[str] = []
        self._threads = [
            threading.Thread(target=self._read_out, daemon=True),
            threading.Thread(target=self._read_err, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _read_out(self) -> None:
        for line in self._proc.stdout:
            self._out.append(line)
            if self._cb:
                sid = self._adapter.session_id_from_partial("".join(self._out))
                if sid:
                    cb, self._cb = self._cb, None
                    cb(sid)

    def _read_err(self) -> None:
        self._err.append(self._proc.stderr.read())

    def finish(self) -> tuple[str, str]:
        self._proc.wait()
        for t in self._threads:
            t.join()
        return "".join(self._out), "".join(self._err)


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
