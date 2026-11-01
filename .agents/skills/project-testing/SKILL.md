---
name: project-testing
description: peer_review test-writing skill — what each change must cover, the fake claude/codex executables, temp git repos, and conventions. Use when writing or reviewing tests in this repo.
---

# Testing Skill

TDD: write the failing test first (RED), make it pass (GREEN), then refactor.

## Coverage for each change
1. Happy path: the expected JSON on stdout and the right exit code (0/1/2/3).
2. Failure path: bad input, a missing file, a failing `gh` or agent call, or a timeout
   gives `error`, never `approved`.
3. State: rounds, session reuse, `stale`, and locks behave as specified.

## Conventions
- Never call real `claude`, `codex`, or the GitHub API. Use the fake executables in
  `tests/conftest.py` (select behaviour with `FAKE_MODE`; inspect calls via `FAKE_LOG`).
- `gh` is faked the same way (the `fake_pr` fixture, with a local bare `origin`); no network.
- Git-dependent tests create a temporary repo in `tmp_path`; never touch this checkout.
- Each test is isolated: no shared state, no reliance on test order.
- Keep timeouts short so the suite stays fast.

## What not to test
- The agent CLIs' own behaviour, `gh`, or `git` internals.

Run before every commit: `uv run pytest -q`
