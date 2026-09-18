import json

import pytest

from review_loop.cli import main


@pytest.fixture
def plan_files(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\nDo it.")
    intent = tmp_path / "intent.md"
    intent.write_text("Build the thing.")
    return plan, intent


def run_cli(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize("author,reviewer", [("claude", "codex"), ("codex", "claude")])
def test_plan_review_both_directions(fake_bin, plan_files, tmp_path, capsys, author, reviewer):
    plan, intent = plan_files
    code, out, _ = run_cli(
        capsys,
        "plan",
        "--file",
        str(plan),
        "--intent-file",
        str(intent),
        "--author",
        author,
        "--cwd",
        str(tmp_path),
        "--timeout",
        "10",
    )
    assert code == 0
    result = json.loads(out)
    assert result["status"] == "approved"
    assert result["reviewer"] == reviewer
    assert result["session_id"]
    assert "+" in result["version"]
    entry = json.loads(fake_bin.read_text().splitlines()[0])
    prompt = entry["argv"][-1]
    assert "Build the thing." in prompt and "# Plan\nDo it." in prompt
    assert result["version"] in prompt


def test_plan_review_changes_requested_exit_1(fake_bin, plan_files, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "changes")
    plan, intent = plan_files
    code, out, _ = run_cli(
        capsys,
        "plan",
        "--file",
        str(plan),
        "--intent-file",
        str(intent),
        "--author",
        "claude",
        "--cwd",
        str(tmp_path),
        "--timeout",
        "10",
    )
    assert code == 1
    assert json.loads(out)["review"]["blocking_findings"] == ["bad"]


def test_plan_review_error_exit_2(fake_bin, plan_files, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    plan, intent = plan_files
    code, out, _ = run_cli(
        capsys,
        "plan",
        "--file",
        str(plan),
        "--intent-file",
        str(intent),
        "--author",
        "claude",
        "--cwd",
        str(tmp_path),
        "--timeout",
        "10",
    )
    assert code == 2
    assert json.loads(out)["status"] == "error"


def test_plan_review_with_session_and_response(fake_bin, plan_files, tmp_path, capsys):
    plan, intent = plan_files
    resp = tmp_path / "resp.md"
    resp.write_text("Fixed it.")
    code, out, _ = run_cli(
        capsys,
        "plan",
        "--file",
        str(plan),
        "--intent-file",
        str(intent),
        "--author",
        "claude",
        "--response-file",
        str(resp),
        "--session-id",
        "thread-7",
        "--cwd",
        str(tmp_path),
        "--timeout",
        "10",
    )
    assert code == 0
    assert json.loads(out)["session_id"] == "thread-7"
    entry = json.loads(fake_bin.read_text().splitlines()[0])
    assert "resume" in entry["argv"]
    assert "Fixed it." in entry["argv"][-1]


def test_recursion_guard_exit_2(fake_bin, plan_files, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("REVIEW_LOOP_REVIEWER", "1")
    plan, intent = plan_files
    code, out, _err = run_cli(
        capsys,
        "plan",
        "--file",
        str(plan),
        "--intent-file",
        str(intent),
        "--author",
        "claude",
        "--cwd",
        str(tmp_path),
    )
    assert code == 2
    assert "REVIEW_LOOP_REVIEWER" in json.loads(out)["error"]


def test_missing_plan_file_exit_2(fake_bin, tmp_path, capsys):
    intent = tmp_path / "i.md"
    intent.write_text("x")
    code, out, _ = run_cli(
        capsys,
        "plan",
        "--file",
        str(tmp_path / "nope.md"),
        "--intent-file",
        str(intent),
        "--author",
        "claude",
        "--cwd",
        str(tmp_path),
    )
    assert code == 2
    assert "nope.md" in json.loads(out)["error"]
