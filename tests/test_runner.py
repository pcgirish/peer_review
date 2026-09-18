import json
import os
import time

import pytest

from review_loop.runner import RecursionGuardError, run_review


def log_entries(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_approved_round_trip(fake_bin, tmp_path, reviewer):
    out = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "approved"
    assert out.review.suggestions == ["s"]
    assert out.session_id
    assert out.error is None
    entry = log_entries(fake_bin)[0]
    assert entry["reviewer"] == "1", "child must see REVIEW_LOOP_REVIEWER=1"
    assert entry["cwd"] == str(tmp_path)
    assert entry["argv"][-1] == "P"


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_changes_requested(fake_bin, tmp_path, reviewer, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "changes")
    out = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "changes_requested"
    assert out.review.blocking_findings == ["bad"]


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_resume_uses_saved_session(fake_bin, tmp_path, reviewer):
    first = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    second = run_review(
        reviewer,
        prompt="P2",
        kind="plan",
        version="v1",
        cwd=tmp_path,
        timeout=10,
        session_id=first.session_id,
    )
    assert second.session_id == first.session_id
    argv = log_entries(fake_bin)[1]["argv"]
    assert ("--resume" in argv) or ("resume" in argv)


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
@pytest.mark.parametrize(
    "mode,match", [("garbage", "JSON"), ("error", "rate limited"), ("bogus", "blocking")]
)
def test_bad_reviewer_output_is_error_not_approval(
    fake_bin, tmp_path, reviewer, monkeypatch, mode, match
):
    monkeypatch.setenv("FAKE_MODE", mode)
    out = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "error"
    assert out.review is None
    assert match in out.error


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_version_mismatch_is_error(fake_bin, tmp_path, reviewer, monkeypatch):
    monkeypatch.setenv("FAKE_VERSION", "other")
    out = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "error"
    assert "reviewed_version" in out.error


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_pr_kind_validates_tests_run(fake_bin, tmp_path, reviewer, monkeypatch):
    out = run_review(reviewer, prompt="P", kind="pr", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "error" and "tests_run" in out.error
    monkeypatch.setenv("FAKE_PR", "1")
    out = run_review(reviewer, prompt="P", kind="pr", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "approved"
    assert out.review.tests_run[0].run_by == "helper"


def test_recursion_guard_refuses_nested_call(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_LOOP_REVIEWER", "1")
    with pytest.raises(RecursionGuardError):
        run_review("claude", prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    assert not fake_bin.exists(), "reviewer must not have been spawned"


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_timeout_kills_process_tree_and_cleans_up(fake_bin, tmp_path, reviewer, monkeypatch):
    import tempfile

    monkeypatch.setenv("FAKE_MODE", "hang")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    t0 = time.monotonic()
    out = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=1)
    assert time.monotonic() - t0 < 10
    assert out.status == "error"
    assert "timed out" in out.error
    entry = log_entries(fake_bin)[0]
    time.sleep(0.2)
    assert not pid_alive(entry["child_pid"]), "grandchild sleep must be killed too"
    if reviewer == "codex":
        assert out.session_id == "thread-new", "thread_id seen before timeout must be surfaced"
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("review-loop-")]
    assert leftovers == []


def test_missing_executable_is_error(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = run_review("codex", prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=5)
    assert out.status == "error"
    assert "codex" in out.error


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_nonzero_exit_with_valid_output_is_error(fake_bin, tmp_path, reviewer, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "okfail")
    out = run_review(reviewer, prompt="P", kind="plan", version="v1", cwd=tmp_path, timeout=10)
    assert out.status == "error"
    assert out.review is None
    assert "exit" in out.error and "1" in out.error
    assert out.session_id, "session id discovered before the failure must be kept"


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_resume_with_replaced_session_is_error(fake_bin, tmp_path, reviewer, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "newsession")
    out = run_review(
        reviewer,
        prompt="P",
        kind="plan",
        version="v1",
        cwd=tmp_path,
        timeout=10,
        session_id="saved",
    )
    assert out.status == "error"
    assert out.review is None
    assert out.session_id == "saved"
    assert "saved" in out.error
