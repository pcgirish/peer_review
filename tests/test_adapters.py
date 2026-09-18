import json
import tempfile
import uuid
from pathlib import Path

import pytest

from review_loop.adapters import get_adapter
from review_loop.adapters.claude import ClaudeAdapter
from review_loop.adapters.codex import CodexAdapter
from review_loop.schema import InvalidReviewError

SCHEMA = {"type": "object"}


@pytest.fixture(autouse=True)
def _tmpdir_under_tmp_path(tmp_path, monkeypatch):
    """Codex build() creates temp files; keep them under pytest's tmp_path."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def test_get_adapter_by_name():
    assert isinstance(get_adapter("claude"), ClaudeAdapter)
    assert isinstance(get_adapter("codex"), CodexAdapter)
    with pytest.raises(ValueError):
        get_adapter("gemini")


def test_reviewer_for_author_is_the_other_agent():
    assert get_adapter("claude").name == "claude"
    from review_loop.adapters import reviewer_for

    assert reviewer_for("claude") == "codex"
    assert reviewer_for("codex") == "claude"


# --- Claude -----------------------------------------------------------------


def test_claude_new_session_is_uuid():
    sid = ClaudeAdapter().new_session_id()
    assert uuid.UUID(sid)


def test_claude_build_new_session(tmp_path):
    inv = ClaudeAdapter().build(prompt="P", schema=SCHEMA, session_id="sid-1", workdir=tmp_path)
    argv = inv.argv
    assert argv[0] == "claude"
    assert "-p" in argv
    assert argv[argv.index("--session-id") + 1] == "sid-1"
    assert "--resume" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert {"Write", "Edit", "NotebookEdit"} <= set(disallowed.split(","))
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv[-1] == "P"
    assert inv.stdin_devnull is True
    assert inv.output_file is None


def test_claude_build_resume(tmp_path):
    inv = ClaudeAdapter().build(
        prompt="P", schema=SCHEMA, session_id="sid-1", workdir=tmp_path, resume=True
    )
    assert inv.argv[inv.argv.index("--resume") + 1] == "sid-1"
    assert "--session-id" not in inv.argv


def test_claude_parse_structured_output():
    out = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "session_id": "sid-1",
            "structured_output": {"verdict": "approved"},
            "result": "ignored",
        }
    )
    res = ClaudeAdapter().parse(stdout=out, session_id="sid-1", output_file=None)
    assert res.session_id == "sid-1"
    assert res.review == {"verdict": "approved"}


def test_claude_parse_falls_back_to_result_text():
    out = json.dumps(
        {"type": "result", "is_error": False, "session_id": "sid-1", "result": '{"v": 1}'}
    )
    res = ClaudeAdapter().parse(stdout=out, session_id="sid-1", output_file=None)
    assert res.review == {"v": 1}


def test_claude_parse_error_result():
    out = json.dumps({"type": "result", "is_error": True, "session_id": "sid-1", "result": "boom"})
    with pytest.raises(InvalidReviewError, match="boom"):
        ClaudeAdapter().parse(stdout=out, session_id="sid-1", output_file=None)


def test_claude_parse_garbage():
    with pytest.raises(InvalidReviewError):
        ClaudeAdapter().parse(stdout="nope", session_id="sid-1", output_file=None)


# --- Codex ------------------------------------------------------------------


def test_codex_new_session_is_none():
    assert CodexAdapter().new_session_id() is None


def test_codex_build_new_session(tmp_path):
    inv = CodexAdapter().build(prompt="P", schema=SCHEMA, session_id=None, workdir=tmp_path)
    argv = inv.argv
    assert argv[:2] == ["codex", "exec"]
    assert "--json" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in argv
    schema_file = argv[argv.index("--output-schema") + 1]
    assert json.loads(Path(schema_file).read_text()) == SCHEMA
    assert inv.output_file is not None
    assert argv[argv.index("-o") + 1] == str(inv.output_file)
    assert "resume" not in argv
    assert argv[-1] == "P"
    assert inv.stdin_devnull is True
    assert set(inv.temp_files) >= {inv.output_file}


def test_codex_build_resume_puts_flags_before_resume(tmp_path):
    inv = CodexAdapter().build(
        prompt="P", schema=SCHEMA, session_id="t-1", workdir=tmp_path, resume=True
    )
    argv = inv.argv
    i = argv.index("resume")
    assert argv[i + 1] == "t-1"
    assert argv[-1] == "P"
    assert argv.index("--sandbox") < i
    assert argv.index("--output-schema") < i
    assert argv.index("-o") < i


def test_codex_parse_thread_started_and_output_file(tmp_path):
    out_file = tmp_path / "out.json"
    out_file.write_text('{"verdict": "approved"}')
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t-9"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "x"}}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )
    res = CodexAdapter().parse(stdout=stdout, session_id=None, output_file=out_file)
    assert res.session_id == "t-9"
    assert res.review == {"verdict": "approved"}


def test_codex_parse_falls_back_to_agent_message(tmp_path):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t-9"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '```json\n{"v":1}\n```'},
                }
            ),
        ]
    )
    res = CodexAdapter().parse(stdout=stdout, session_id=None, output_file=tmp_path / "missing")
    assert res.review == {"v": 1}


def test_codex_parse_keeps_known_session_id_on_resume(tmp_path):
    stdout = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}})
    res = CodexAdapter().parse(stdout=stdout, session_id="t-1", output_file=tmp_path / "missing")
    assert res.session_id == "t-1"


def test_codex_parse_without_any_session_id_fails(tmp_path):
    stdout = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}})
    with pytest.raises(InvalidReviewError, match="thread_id"):
        CodexAdapter().parse(stdout=stdout, session_id=None, output_file=tmp_path / "missing")


def test_codex_parse_error_event(tmp_path):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t-9"}),
            json.dumps({"type": "error", "message": "quota exceeded"}),
        ]
    )
    with pytest.raises(InvalidReviewError, match="quota exceeded"):
        CodexAdapter().parse(stdout=stdout, session_id=None, output_file=tmp_path / "missing")
