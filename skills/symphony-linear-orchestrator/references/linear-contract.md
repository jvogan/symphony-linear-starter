# Linear Contract

This starter assumes Linear is the execution tracker. Every issue dispatched to Symphony should follow the same contract.

Do not hand-author the schema block when you can avoid it. Prefer rendering or normalizing issue bodies with `scripts/issue_schema.py` so the human-readable sections and `<!-- symphony:schema -->` comment stay aligned.

## Required issue sections

```markdown
## Summary
<one or two sentence goal>

## Acceptance Criteria
- [ ] <specific, testable assertion>

> Do not include secrets, credentials, tokens, session cookies, personal data, or raw customer payloads in this issue body. Use redacted identifiers and secure stores instead.

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

## Routing labels

When multiple workflows share one Linear project, route them explicitly:

- `sym:small`
- `sym:medium`
- `sym:large`
- `sym:content`
- `release:ready` for issues whose PRs can be processed by the Release Manager lane

Keep routing labels in Linear metadata rather than inventing a markdown section for them in the issue body.

Only trusted operators should be able to create or edit issues that match a routed Symphony label. Once an issue enters the active queue, its title, body, comments, labels, and blockers become instructions and context for an autonomous worker.

## Sensitive data rules

- Do not paste secrets, credentials, tokens, session cookies, personal data, or raw customer payloads into the issue body.
- Validation commands should refer to secure stores or redacted fixtures instead of inline secrets.
- If a bug only reproduces with real customer data, summarize the shape of the data and store the sensitive material outside Linear.
- Risk notes should call out sensitive boundaries without reproducing the underlying data.

## Planning guidance

- Keep most work in `Backlog`.
- Keep the first real execution wave small and conservative.
- Only activate the first wave at the beginning, but size that wave to fill your available worker slots.
- Split large or ambiguous work before dispatch.
- Put validation commands in the issue body, not only in chat.
- Keep `Touched Areas` specific enough to support parallel work without overlap.
- Keep issue bodies free of secrets and personal data, even when copying stack traces or API examples.
- Design dependencies so the orchestrator can keep the queue moving instead of waiting on one giant ticket.
- For autonomous release flow, workers must attach a GitHub PR URL in their final `<!-- symphony-outcome -->` comment and use `release:ready`; they must not merge or deploy directly.
- When the same issue-shaping mistake repeats, update `LINEAR_ISSUE_TEMPLATE.md` after the wave instead of fixing it one ticket at a time forever.
