"""Thin wrapper over the `gh` CLI plus a detached worktree of the PR head.

The reviewer inspects the PR revision from that worktree (read-only); the helper
runs the project's tests there too. The helper, never the reviewer, posts
comments; nothing here submits a GitHub review or approval.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_FIELDS = "number,url,title,body,headRefOid,baseRefOid,baseRefName,headRefName"


class GitHubError(Exception):
    pass


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    title: str
    body: str
    head_sha: str
    base_sha: str
    base_ref: str
    head_ref: str


def _run(argv: list[str], cwd: Path, *, what: str) -> str:
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise GitHubError(f"cannot run {argv[0]} to {what}: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        raise GitHubError(f"{argv[0]} failed to {what}: {err[:300]}")
    return proc.stdout


def fetch_pr(number: int, *, cwd: Path) -> PullRequest:
    raw = _run(["gh", "pr", "view", str(number), "--json", _FIELDS], cwd, what="read the PR")
    try:
        d = json.loads(raw)
        return PullRequest(
            number=int(d["number"]),
            url=str(d["url"]),
            title=str(d.get("title") or ""),
            body=str(d.get("body") or ""),
            head_sha=str(d["headRefOid"]),
            base_sha=str(d["baseRefOid"]),
            base_ref=str(d["baseRefName"]),
            head_ref=str(d.get("headRefName") or ""),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise GitHubError(f"unexpected `gh pr view` output: {e}") from e


def head_sha(number: int, *, cwd: Path) -> str:
    return fetch_pr(number, cwd=cwd).head_sha


def pr_diff(number: int, *, cwd: Path) -> str:
    return _run(["gh", "pr", "diff", str(number)], cwd, what="read the PR diff")


def post_comment(number: int, body: str, *, cwd: Path) -> str:
    """Post a plain PR comment as the current gh user; returns its URL."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
        path = Path(fh.name)
    try:
        out = _run(
            ["gh", "pr", "comment", str(number), "--body-file", str(path)],
            cwd,
            what="post the comment",
        )
    finally:
        path.unlink(missing_ok=True)
    return out.strip().splitlines()[-1] if out.strip() else ""


def checkout_revision(number: int, sha: str, *, dest: Path, cwd: Path) -> Path:
    """Fetch refs/pull/<n>/head and check that exact revision out, detached, at `dest`.

    `sha` (what `gh` reported) must be what the ref points at; otherwise the PR moved
    between the two lookups and the caller should treat that as an error/stale."""
    _git(cwd, "fetch", "-q", "origin", f"refs/pull/{number}/head", what="fetch the PR head")
    fetched = _git(cwd, "rev-parse", "FETCH_HEAD", what="resolve the fetched PR head").strip()
    if fetched != sha:
        raise GitHubError(
            f"{sha} is not the head of refs/pull/{number}/head (fetched {fetched}); "
            "the PR moved, run the review again"
        )
    if (dest / ".git").exists():
        restore_worktree(dest, sha)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(cwd, "worktree", "add", "-q", "--detach", str(dest), sha, what="create PR worktree")
    return dest


def restore_worktree(dest: Path, sha: str) -> None:
    """Make `dest` exactly `sha`: drop tracked edits and untracked files (ignored files,
    such as a test venv, are kept)."""
    _git(dest, "checkout", "-q", "--detach", sha, what="update the PR worktree")
    _git(dest, "reset", "-q", "--hard", sha, what="reset the PR worktree")
    _git(dest, "clean", "-fdq", what="clean the PR worktree")


def _git(cwd: Path, *args: str, what: str) -> str:
    return _run(["git", "-C", str(cwd), *args], cwd, what=what)
