"""Reviewer CLI adapters."""

from __future__ import annotations

from review_loop.adapters.base import Adapter, AdapterOutput, Invocation

AGENTS = ("claude", "codex")


def get_adapter(name: str) -> Adapter:
    if name == "claude":
        from review_loop.adapters.claude import ClaudeAdapter

        return ClaudeAdapter()
    if name == "codex":
        from review_loop.adapters.codex import CodexAdapter

        return CodexAdapter()
    raise ValueError(f"unknown agent {name!r}; expected one of {AGENTS}")


def reviewer_for(author: str) -> str:
    if author not in AGENTS:
        raise ValueError(f"unknown author {author!r}; expected one of {AGENTS}")
    return "codex" if author == "claude" else "claude"


__all__ = ["AGENTS", "Adapter", "AdapterOutput", "Invocation", "get_adapter", "reviewer_for"]
