"""Fake `claude` and `codex` executables for runner tests.

Behaviour is selected with FAKE_MODE: ok | changes | bogus | garbage | error | okfail | newsession | hang.
FAKE_LOG receives one line per invocation: the JSON of {argv, env subset, child_pid}.
"""

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


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "fake.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("REVIEW_LOOP_REVIEWER", raising=False)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_PR", raising=False)
    return log
