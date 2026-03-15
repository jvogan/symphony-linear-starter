# Repo Onboarding

## What the target repo should provide

Every repo handed to Symphony should have durable guidance that a worker can read cold.

At minimum, the target repo's `AGENTS.md` should cover:

- how to run the project
- how to test and lint it
- important coding patterns or conventions
- high-risk areas or sharp edges
- any repository-specific workflows workers must follow

## What belongs in repo-local guidance

Put repo-specific instructions in the target repo:

- exact build and test commands
- architecture or folder conventions
- secrets handling rules
- service boundaries
- high-risk files and stateful operations

## What stays in this starter skill

Keep cross-repo operating guidance here:

- how the orchestrator, Symphony, and Linear interact
- how issue bodies should be structured
- how to bootstrap a workflow
- common recovery tactics

## Recommended target repo artifact layout

After bootstrapping, the target repo should gain:

```text
.orchestration/
  <workflow-name>.WORKFLOW.md
  <workflow-name>.BRIEF.md
  LINEAR_ISSUE_TEMPLATE.md
  AGENTS_ADDITIONS.md
```

The operator should review those files, then merge the needed parts into the repo's durable guidance.

