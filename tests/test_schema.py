import pytest

from review_loop.schema import InvalidReviewError, review_schema, validate_review

VERSION = "abc123+def456"


def approved(**extra):
    return {
        "verdict": "approved",
        "reviewed_version": VERSION,
        "blocking_findings": [],
        "suggestions": ["nit"],
        **extra,
    }


def changes(**extra):
    return {
        "verdict": "changes_requested",
        "reviewed_version": VERSION,
        "blocking_findings": ["it breaks"],
        "suggestions": [],
        **extra,
    }


def test_plan_schema_requires_core_fields():
    s = review_schema("plan")
    assert set(s["required"]) == {"verdict", "reviewed_version", "blocking_findings", "suggestions"}
    assert s["additionalProperties"] is False


def test_pr_schema_requires_tests_run():
    s = review_schema("pr")
    assert "tests_run" in s["required"]
    item = s["properties"]["tests_run"]["items"]
    assert set(item["required"]) == {"command", "exit_code", "run_by"}
    assert item["properties"]["run_by"]["enum"] == ["helper", "reviewer"]


def test_valid_plan_approval():
    r = validate_review(approved(), kind="plan", expected_version=VERSION)
    assert r.verdict == "approved"
    assert r.suggestions == ["nit"]
    assert r.blocking_findings == []


def test_valid_plan_changes_requested():
    r = validate_review(changes(), kind="plan", expected_version=VERSION)
    assert r.verdict == "changes_requested"
    assert r.blocking_findings == ["it breaks"]


def test_approval_with_blocking_findings_is_invalid():
    with pytest.raises(InvalidReviewError, match="blocking"):
        validate_review(approved(blocking_findings=["x"]), kind="plan", expected_version=VERSION)


def test_changes_requested_without_findings_is_invalid():
    with pytest.raises(InvalidReviewError, match="blocking"):
        validate_review(changes(blocking_findings=[]), kind="plan", expected_version=VERSION)


def test_unknown_verdict_is_invalid():
    with pytest.raises(InvalidReviewError, match="verdict"):
        validate_review(approved(verdict="looks_good"), kind="plan", expected_version=VERSION)


def test_wrong_reviewed_version_is_invalid():
    with pytest.raises(InvalidReviewError, match="reviewed_version"):
        validate_review(approved(reviewed_version="other"), kind="plan", expected_version=VERSION)


@pytest.mark.parametrize(
    "field", ["verdict", "reviewed_version", "blocking_findings", "suggestions"]
)
def test_missing_field_is_invalid(field):
    obj = approved()
    del obj[field]
    with pytest.raises(InvalidReviewError, match=field):
        validate_review(obj, kind="plan", expected_version=VERSION)


def test_wrong_types_are_invalid():
    with pytest.raises(InvalidReviewError, match="blocking_findings"):
        validate_review(approved(blocking_findings="none"), kind="plan", expected_version=VERSION)
    with pytest.raises(InvalidReviewError, match="suggestions"):
        validate_review(approved(suggestions=[1]), kind="plan", expected_version=VERSION)


def test_extra_field_is_invalid():
    with pytest.raises(InvalidReviewError, match="unexpected"):
        validate_review(approved(extra="x"), kind="plan", expected_version=VERSION)


def test_non_object_is_invalid():
    with pytest.raises(InvalidReviewError):
        validate_review(["approved"], kind="plan", expected_version=VERSION)


def test_pr_requires_tests_run():
    with pytest.raises(InvalidReviewError, match="tests_run"):
        validate_review(approved(), kind="pr", expected_version=VERSION)


def test_pr_with_helper_tests():
    obj = approved(tests_run=[{"command": "uv run pytest", "exit_code": 0, "run_by": "helper"}])
    r = validate_review(obj, kind="pr", expected_version=VERSION)
    assert r.tests_run[0].run_by == "helper"
    assert r.tests_not_run_reason is None


def test_pr_no_tests_needs_reason():
    with pytest.raises(InvalidReviewError, match="tests_not_run_reason"):
        validate_review(approved(tests_run=[]), kind="pr", expected_version=VERSION)
    r = validate_review(
        approved(tests_run=[], tests_not_run_reason="no test command"),
        kind="pr",
        expected_version=VERSION,
    )
    assert r.tests_not_run_reason == "no test command"


def test_pr_tests_run_bad_entry():
    bad = [{"command": "x", "exit_code": "0", "run_by": "helper"}]
    with pytest.raises(InvalidReviewError, match="exit_code"):
        validate_review(approved(tests_run=bad), kind="pr", expected_version=VERSION)
    bad = [{"command": "x", "exit_code": 0, "run_by": "ci"}]
    with pytest.raises(InvalidReviewError, match="run_by"):
        validate_review(approved(tests_run=bad), kind="pr", expected_version=VERSION)


def test_pr_tests_run_extra_field_is_invalid():
    bad = [{"command": "x", "exit_code": 0, "run_by": "helper", "notes": "n"}]
    with pytest.raises(InvalidReviewError, match="tests_run\\[0\\]"):
        validate_review(approved(tests_run=bad), kind="pr", expected_version=VERSION)
