"""Reviewer output contract: JSON schema plus strict validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["plan", "pr"]
Verdict = Literal["approved", "changes_requested"]

RUN_BY = ["helper", "reviewer"]
VERDICTS = ["approved", "changes_requested"]


class InvalidReviewError(Exception):
    """Reviewer output that does not satisfy the contract. Never treated as approval."""


@dataclass(frozen=True)
class TestRun:
    command: str
    exit_code: int
    run_by: str


@dataclass(frozen=True)
class ReviewResult:
    verdict: Verdict
    reviewed_version: str
    blocking_findings: list[str]
    suggestions: list[str]
    tests_run: list[TestRun] = field(default_factory=list)
    tests_not_run_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "verdict": self.verdict,
            "reviewed_version": self.reviewed_version,
            "blocking_findings": list(self.blocking_findings),
            "suggestions": list(self.suggestions),
        }
        if self.tests_run or self.tests_not_run_reason is not None:
            d["tests_run"] = [t.__dict__ for t in self.tests_run]
        if self.tests_not_run_reason is not None:
            d["tests_not_run_reason"] = self.tests_not_run_reason
        return d


def review_schema(kind: Kind) -> dict[str, Any]:
    """JSON schema handed to the reviewer CLI (Claude --json-schema, Codex --output-schema)."""
    properties: dict[str, Any] = {
        "verdict": {"type": "string", "enum": VERDICTS},
        "reviewed_version": {"type": "string"},
        "blocking_findings": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    }
    required = ["verdict", "reviewed_version", "blocking_findings", "suggestions"]
    if kind == "pr":
        properties["tests_run"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "run_by": {"type": "string", "enum": RUN_BY},
                },
                "required": ["command", "exit_code", "run_by"],
                "additionalProperties": False,
            },
        }
        properties["tests_not_run_reason"] = {"type": "string"}
        required.append("tests_run")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _str_list(obj: dict[str, Any], name: str) -> list[str]:
    v = obj.get(name)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise InvalidReviewError(f"{name} must be an array of strings")
    return list(v)


def _tests_run(obj: dict[str, Any]) -> list[TestRun]:
    v = obj.get("tests_run")
    if not isinstance(v, list):
        raise InvalidReviewError("tests_run must be an array")
    out: list[TestRun] = []
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            raise InvalidReviewError(f"tests_run[{i}] must be an object")
        extra = set(item) - {"command", "exit_code", "run_by"}
        if extra:
            raise InvalidReviewError(f"tests_run[{i}] has unexpected fields: {sorted(extra)}")
        cmd, code, who = item.get("command"), item.get("exit_code"), item.get("run_by")
        if not isinstance(cmd, str):
            raise InvalidReviewError(f"tests_run[{i}].command must be a string")
        if not isinstance(code, int) or isinstance(code, bool):
            raise InvalidReviewError(f"tests_run[{i}].exit_code must be an integer")
        if who not in RUN_BY:
            raise InvalidReviewError(f"tests_run[{i}].run_by must be one of {RUN_BY}")
        out.append(TestRun(cmd, code, who))
    return out


def validate_review(obj: Any, *, kind: Kind, expected_version: str) -> ReviewResult:
    """Validate parsed reviewer JSON. Raises InvalidReviewError on any deviation."""
    if not isinstance(obj, dict):
        raise InvalidReviewError("review must be a JSON object")
    allowed = set(review_schema(kind)["properties"])
    for name in ("verdict", "reviewed_version", "blocking_findings", "suggestions"):
        if name not in obj:
            raise InvalidReviewError(f"missing field: {name}")
    unexpected = sorted(set(obj) - allowed)
    if unexpected:
        raise InvalidReviewError(f"unexpected fields: {', '.join(unexpected)}")

    verdict = obj["verdict"]
    if verdict not in VERDICTS:
        raise InvalidReviewError(f"verdict must be one of {VERDICTS}")
    version = obj["reviewed_version"]
    if not isinstance(version, str):
        raise InvalidReviewError("reviewed_version must be a string")
    if version != expected_version:
        raise InvalidReviewError(
            f"reviewed_version mismatch: reviewer reported {version!r}, expected {expected_version!r}"
        )
    blocking = _str_list(obj, "blocking_findings")
    suggestions = _str_list(obj, "suggestions")
    if verdict == "approved" and blocking:
        raise InvalidReviewError("approved verdict must have no blocking findings")
    if verdict == "changes_requested" and not blocking:
        raise InvalidReviewError("changes_requested verdict requires at least one blocking finding")

    tests_run: list[TestRun] = []
    reason: str | None = None
    if kind == "pr":
        if "tests_run" not in obj:
            raise InvalidReviewError("missing field: tests_run")
        tests_run = _tests_run(obj)
        reason = obj.get("tests_not_run_reason")
        if reason is not None and not isinstance(reason, str):
            raise InvalidReviewError("tests_not_run_reason must be a string")
        if not tests_run and not reason:
            raise InvalidReviewError("tests_not_run_reason is required when tests_run is empty")
    return ReviewResult(verdict, version, blocking, suggestions, tests_run, reason)
