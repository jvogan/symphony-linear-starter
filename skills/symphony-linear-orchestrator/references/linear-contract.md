# Linear Contract

This starter assumes Linear is the execution tracker. Every issue dispatched to Symphony should follow the same contract.

## Required issue sections

```markdown
## Summary
<one or two sentence goal>

## Acceptance Criteria
- [ ] <specific, testable assertion>

## Validation Commands
```bash
<exact command>
```

## Touched Areas
- `path/to/file` - reason

## Dependencies
Blocked by: PROJ-123

## Risk Notes
- <known risk or caution>

## Complexity
tier: medium

<!-- symphony:schema
schema_version: 1
touched_areas:
  - path/to/file
complexity: medium
-->
```

## Section rules

- `Summary`: explain what changes and why.
- `Acceptance Criteria`: use concrete, testable statements.
- `Validation Commands`: provide exact copy-paste shell commands from repo root.
- `Touched Areas`: list the files or directories expected to change.
- `Dependencies`: mirror the real Linear blocker graph.
- `Risk Notes`: call out large files, fragile areas, or external dependencies.
- `Complexity`: use `small`, `medium`, or `large`.

## Required Linear states

- `Backlog`
- `Todo`
- `In Progress`
- `In Review`
- `Done`

## Planning guidance

- Keep most work in `Backlog`.
- Only activate the first wave at the beginning, but size that wave to fill your available worker slots.
- Split large or ambiguous work before dispatch.
- Put validation commands in the issue body, not only in chat.
- Keep `Touched Areas` specific enough to support parallel work without overlap.
- Design dependencies so the orchestrator can keep the queue moving instead of waiting on one giant ticket.
