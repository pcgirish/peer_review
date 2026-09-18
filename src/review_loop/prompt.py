"""Helper-owned reviewer prompt and lenient JSON extraction."""

from __future__ import annotations

import json
import re
from typing import Any

from review_loop.schema import InvalidReviewError, Kind

# Verbatim from docs/two-agent-review-plan.md, "Review Scope — Prevent Design Growth".
REVIEW_SCOPE = """\
## Review Scope — Prevent Design Growth

A finding blocks only when it identifies a concrete failure of an agreed requirement or a \
material defect in the proposed implementation. Explain the trigger, consequence, and smallest \
sufficient fix. Hypothetical production requirements, optional hardening, preferences, and \
broader features are suggestions.

The author evaluates findings rather than accepting every suggestion. If a proposed safeguard \
adds substantial machinery, first consider removing the feature that needs it. Neither agent may \
expand the agreed scope; unresolved tradeoffs go to the user.
"""

_FENCE = re.compile(r"\A\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n\s*```\s*\Z", re.DOTALL)


def build_reviewer_prompt(
    *,
    kind: Kind,
    version: str,
    intent: str,
    artifact_label: str,
    artifact_content: str,
    author_response: str | None = None,
    tests: list[dict[str, Any]] | None = None,
) -> str:
    noun = "plan" if kind == "plan" else "pull request"
    parts: list[str] = [
        (
            f"You are the peer reviewer for a {noun}. Review it as a staff engineer would: "
            "challenge assumptions, spot edge cases, check security."
        ),
        "",
        (
            "Rules: Do not edit files, push, merge, comment on GitHub, or submit GitHub approvals. "
            "You may read the repository to verify claims."
        ),
        "",
        REVIEW_SCOPE,
        "## Original request (verbatim from the user)",
        "",
        intent.strip(),
        "",
    ]
    if author_response is not None:
        parts += ["## Author response to the previous round", "", author_response.strip(), ""]
    parts += [
        f"## {artifact_label}",
        "",
        f"Version under review: `{version}`",
        "",
        artifact_content,
        "",
    ]
    if kind == "pr":
        parts += _tests_section(tests or [])
    parts += [
        "## Required output",
        "",
        "Respond with a single JSON object and nothing else, matching the schema you were given:",
        f'- `reviewed_version`: exactly "{version}"',
        '- `verdict`: "approved" (no blocking findings) or "changes_requested" (at least one)',
        "- `blocking_findings`: concrete failures only, each with trigger, consequence, and smallest fix",
        "- `suggestions`: everything non-blocking",
    ]
    if kind == "pr":
        parts += [
            (
                "- `tests_run`: every test command whose result you relied on, with `run_by` "
                '"helper" for the runs listed above and "reviewer" for any you ran yourself'
            ),
            "- `tests_not_run_reason`: a string when `tests_run` is empty, otherwise `null`",
        ]
    return "\n".join(parts) + "\n"


def _tests_section(tests: list[dict[str, Any]]) -> list[str]:
    out = ['## Tests run by the helper (record these with run_by "helper")', ""]
    if not tests:
        out += [
            (
                "No tests were run by the helper. If you cannot run tests yourself, set "
                "`tests_not_run_reason`. Existing CI is not a test either party ran."
            ),
            "",
        ]
        return out
    for t in tests:
        out += [
            f"### `{t['command']}` — exit code {t['exit_code']}",
            "",
            "```",
            str(t.get("output", "")).rstrip(),
            "```",
            "",
        ]
    return out


def parse_json_output(text: str) -> Any:
    """Parse JSON, stripping one enclosing Markdown code fence if present."""
    m = _FENCE.match(text)
    body = m.group(1) if m else text
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        snippet = text.strip()[:200]
        raise InvalidReviewError(f"reviewer output is not valid JSON ({e.msg}): {snippet!r}") from e
