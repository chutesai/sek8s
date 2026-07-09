# Project Instructions

AGENT.md is the single source of truth for this repo. It is imported below so its
contents load into context at session start — do not duplicate it here.

@../AGENT.md

## Working in this repo

- **AGENT.md governs all work.** Identity, stack, hard rules, patterns, and architecture
  live there (imported above). Follow those constraints for every change.
- **Use spec templates** for task planning when appropriate. For features, bugfixes, or
  refactors, use the Prompt Contracts structure in [docs/specs/templates/](../docs/specs/templates/):
  - [feature.md](../docs/specs/templates/feature.md) — Goal, Constraints, Output Format, Failure Conditions
  - [bugfix.md](../docs/specs/templates/bugfix.md) — reproduction steps, root cause, regression prevention
  - [refactor.md](../docs/specs/templates/refactor.md) — scope, before/after, migration strategy

See [.claude/rules/](rules/) for additional always-on rules (e.g. shell environment setup).
