from review_loop.prompt import REVIEW_SCOPE, build_reviewer_prompt, parse_json_output


def test_prompt_contains_scope_intent_content_and_version():
    p = build_reviewer_prompt(
        kind="plan",
        version="v1",
        intent="Build the widget.",
        artifact_label="Plan file: docs/plan.md",
        artifact_content="# Plan\nstep one",
    )
    assert REVIEW_SCOPE.strip() in p
    assert "A finding blocks only when" in p
    assert "reviewed_version" in p and "v1" in p
    assert "Build the widget." in p
    assert "# Plan\nstep one" in p
    assert "docs/plan.md" in p
    assert "Do not edit" in p


def test_prompt_first_round_has_no_response_section():
    p = build_reviewer_prompt(
        kind="plan", version="v1", intent="i", artifact_label="l", artifact_content="c"
    )
    assert "Author response" not in p


def test_prompt_includes_labeled_author_response():
    p = build_reviewer_prompt(
        kind="plan",
        version="v2",
        intent="i",
        artifact_label="l",
        artifact_content="c",
        author_response="Fixed the null check.",
    )
    assert "Author response" in p
    assert "Fixed the null check." in p


def test_pr_prompt_includes_helper_test_output():
    p = build_reviewer_prompt(
        kind="pr",
        version="sha+ih",
        intent="i",
        artifact_label="PR #7",
        artifact_content="diff",
        tests=[{"command": "uv run pytest", "exit_code": 1, "output": "FAILED x"}],
    )
    assert "run_by" in p and "helper" in p
    assert "uv run pytest" in p
    assert "exit code 1" in p
    assert "FAILED x" in p


def test_pr_prompt_without_tests_explains_reason():
    p = build_reviewer_prompt(
        kind="pr",
        version="v",
        intent="i",
        artifact_label="PR #7",
        artifact_content="diff",
        tests=[],
    )
    assert "tests_not_run_reason" in p


def test_parse_json_output_plain_and_fenced():
    assert parse_json_output('{"a": 1}') == {"a": 1}
    assert parse_json_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_output('  ```\n{"a": 1}\n```  ') == {"a": 1}


def test_parse_json_output_invalid_raises():
    import pytest

    from review_loop.schema import InvalidReviewError

    with pytest.raises(InvalidReviewError, match="JSON"):
        parse_json_output("not json")
    with pytest.raises(InvalidReviewError, match="JSON"):
        parse_json_output("```\n{\n```")
