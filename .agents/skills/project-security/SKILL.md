---
name: project-security
description: peer_agent security skill — password/secret handling, auth checklist, input validation, OWASP top-10, and a step-by-step security audit. Use when writing auth, handling user data, touching any security boundary, or when asked to review/audit security in this repo.
---

# Security Skill

Use this whenever you're writing auth, handling user data, touching any
security boundary, or asked to audit the codebase's security.

## Password / Secret Handling
- Hash passwords with bcrypt, cost factor ≥ 12
- Use cryptographically secure RNG for tokens — never uuid4()
- Never log, return, or serialize password hashes
- Secrets in environment / config only — never in code or comments

## Input Validation
- Validate all inputs at system boundaries before use
- Never use string interpolation in SQL — ORM or parameterized queries only
- Validate and sanitize string lengths on all user-facing fields

## OWASP Top 10
- [ ] Injection: ORM only, no raw SQL with user input
- [ ] Broken auth: token expiry, rotation, rate limiting
- [ ] Sensitive data: no secrets in logs, responses, or git
- [ ] XSS: escape output, set CSP headers
- [ ] IDOR: ownership check on every resource
- [ ] Security misconfiguration: .env never committed

## Full Security Audit
When explicitly asked for a security audit, work through:
1. Search for hardcoded secrets, tokens, or passwords in all files
2. Confirm .env is in .gitignore and not committed
3. Report findings as: CRITICAL / HIGH / MEDIUM / LOW
