"""Fake `claude` and `codex` executables for runner tests.

Behaviour is selected with FAKE_MODE: ok | changes | bogus | garbage | error | okfail | newsession | hang.
FAKE_LOG receives one line per invocation: the JSON of {argv, env subset, child_pid}.
"""

import json
import os
import stat
import textwrap

import pytest

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, re, subprocess, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    argv = sys.argv[1:]
    sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else argv[argv.index("--resume") + 1]
    if mode == "newsession":
        sid = "replacement"
    child = None
    if mode == "hang":
        child = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 300"])
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv, "reviewer": os.environ.get("REVIEW_LOOP_REVIEWER"),
                            "child_pid": child.pid if child else None, "cwd": os.getcwd()}) + "\\n")
    m = re.search(r"Version under review: `([^`]+)`", argv[-1])
    version = os.environ.get("FAKE_VERSION") or (m.group(1) if m else "v1")
    if mode == "hang":
        time.sleep(300)
    if mode == "garbage":
        print("I refuse to answer in JSON"); sys.exit(0)
    if mode == "error":
        print(json.dumps({"type": "result", "is_error": True, "session_id": sid, "result": "rate limited"})); sys.exit(1)
    if mode == "changes":
        so = {"verdict": "changes_requested", "reviewed_version": version, "blocking_findings": ["bad"], "suggestions": []}
    elif mode == "bogus":
        so = {"verdict": "approved", "reviewed_version": version, "blocking_findings": ["bad"], "suggestions": []}
    else:
        so = {"verdict": "approved", "reviewed_version": version, "blocking_findings": [], "suggestions": ["s"]}
    if os.environ.get("FAKE_PR"):
        so["tests_run"] = [{"command": "uv run pytest", "exit_code": 0, "run_by": "helper"}]
    print(json.dumps({"type": "result", "is_error": False, "session_id": sid, "structured_output": so}))
    if mode == "okfail":
        sys.exit(1)
    """
)

FAKE_CODEX = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, re, subprocess, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    argv = sys.argv[1:]
    tid = argv[argv.index("resume") + 1] if "resume" in argv else "thread-new"
    if mode == "newsession":
        tid = "replacement"
    out = argv[argv.index("-o") + 1]
    child = None
    if mode == "hang":
        child = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 300"])
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv, "reviewer": os.environ.get("REVIEW_LOOP_REVIEWER"),
                            "child_pid": child.pid if child else None, "cwd": os.getcwd()}) + "\\n")
    m = re.search(r"Version under review: `([^`]+)`", argv[-1])
    version = os.environ.get("FAKE_VERSION") or (m.group(1) if m else "v1")
    print(json.dumps({"type": "thread.started", "thread_id": tid}), flush=True)
    if mode == "hang":
        time.sleep(300)
    if mode == "garbage":
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "no json here"}})); sys.exit(0)
    if mode == "error":
        print(json.dumps({"type": "error", "message": "rate limited"})); sys.exit(1)
    if mode == "changes":
        so = {"verdict": "changes_requested", "reviewed_version": version, "blocking_findings": ["bad"], "suggestions": []}
    elif mode == "bogus":
        so = {"verdict": "approved", "reviewed_version": version, "blocking_findings": ["bad"], "suggestions": []}
    else:
        so = {"verdict": "approved", "reviewed_version": version, "blocking_findings": [], "suggestions": ["s"]}
    if os.environ.get("FAKE_PR"):
        so["tests_run"] = [{"command": "uv run pytest", "exit_code": 0, "run_by": "helper"}]
    with open(out, "w") as f:
        json.dump(so, f)
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(so)}}))
    print(json.dumps({"type": "turn.completed", "usage": {}}))
    if mode == "okfail":
        sys.exit(1)
    """
)


FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    state = json.load(open(os.environ["FAKE_GH_STATE"]))
    argv = sys.argv[1:]
    with open(os.environ["FAKE_GH_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv}) + "\\n")
    if state.get("fail"):
        print("gh: HTTP 502", file=sys.stderr); sys.exit(1)
    if argv[:2] == ["pr", "view"]:
        fields = argv[argv.index("--json") + 1].split(",")
        print(json.dumps({k: state["pr"][k] for k in fields})); sys.exit(0)
    if argv[:2] == ["pr", "diff"]:
        print(state["diff"], end=""); sys.exit(0)
    if argv[:2] == ["pr", "comment"]:
        body = open(argv[argv.index("--body-file") + 1]).read()
        with open(os.environ["FAKE_GH_LOG"], "a") as f:
            f.write(json.dumps({"comment": body, "number": argv[2]}) + "\\n")
        print("https://github.com/o/r/pull/%s#issuecomment-1" % argv[2]); sys.exit(0)
    print("fake gh: unsupported", argv, file=sys.stderr); sys.exit(2)
    """
)


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX), ("gh", FAKE_GH)):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "fake.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("FAKE_GH_STATE", str(tmp_path / "gh-state.json"))
    monkeypatch.delenv("REVIEW_LOOP_REVIEWER", raising=False)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_PR", raising=False)
    return log


def _git(cwd, *args):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A committed git repo at tmp_path/repo (kept apart from fake_bin's files)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("x")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


class FakePr:
    """A repo with a bare origin holding refs/pull/<n>/head, plus the fake gh's state file."""

    def __init__(self, repo, origin, number, state_path, gh_log):
        self.repo, self.origin, self.number = repo, origin, number
        self.state_path, self.gh_log = state_path, gh_log
        self.base_sha = _git(repo, "rev-parse", "HEAD")
        self.head_sha = self.push_revision("feature work")
        self.diff = "diff --git a/f.py b/f.py\n+print('hi')\n"
        self.write_state()

    def push_revision(self, msg):
        """Commit on top of the current PR head (detached) and publish it as refs/pull/n/head."""
        (self.repo / "f.py").write_text(f"# {msg}\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", msg)
        sha = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "push", "-q", "origin", f"HEAD:refs/pull/{self.number}/head")
        self.head_sha = sha
        return sha

    def write_state(self, **overrides):
        pr = {
            "number": self.number,
            "url": f"https://github.com/o/r/pull/{self.number}",
            "title": "Add f",
            "body": "Adds f.py",
            "headRefOid": self.head_sha,
            "baseRefOid": self.base_sha,
            "baseRefName": "main",
            "headRefName": "feature",
        }
        pr.update(overrides.pop("pr", {}))
        state = {"pr": pr, "diff": self.diff, **overrides}
        self.state_path.write_text(json.dumps(state))

    def gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]


@pytest.fixture
def fake_pr(fake_bin, repo, tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    return FakePr(repo, origin, 7, tmp_path / "gh-state.json", tmp_path / "gh.log")
