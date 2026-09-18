---
name: feature-scaffold
description: Scaffold a new feature end-to-end following peer_agent's conventions (schema/model/service/route/tests, wired up and security-checked). Use when asked to add or scaffold a new feature. Ask for the feature name if not given.
---

# Feature Scaffold Skill

1. Define types / schemas for the feature
2. Implement service / business logic
3. Create route / handler with auth middleware
4. Register route
5. Create tests
6. Run the `project-security` skill and verify the feature passes all checks
7. Run `# fill in test command` and `# fill in lint command` — fix any issues before finishing
