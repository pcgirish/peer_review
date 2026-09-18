---
name: project-testing
description: peer_agent test-writing skill — coverage requirements (happy path, auth fail, validation fail, ownership), unit vs integration layout, conventions. Use when writing or reviewing tests in this repo.
---

# Testing Skill

Use when writing or reviewing tests.

## Coverage Requirements
Every new endpoint needs at minimum:
1. Happy path (200/201 response, correct shape)
2. Auth failure (401 when token missing or invalid)
3. Validation failure (422/400 when required fields missing)

## Conventions
- Never share state between tests — each test is isolated
- Unit tests: pure functions with mocked dependencies
- Integration tests: full HTTP / service-level tests

## What NOT to test
- Framework internals
- Third-party library behaviour

Run before every commit: `# fill in test command`
