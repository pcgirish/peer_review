"""Version identity: plan content hash + intent hash; PR head SHA + intent hash."""

from __future__ import annotations

import hashlib

_LEN = 12


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_LEN]


def plan_version(content: str, intent: str) -> str:
    return f"{_h(content)}+{_h(intent)}"


def pr_version(head_sha: str, intent: str) -> str:
    return f"{head_sha}+{_h(intent)}"
