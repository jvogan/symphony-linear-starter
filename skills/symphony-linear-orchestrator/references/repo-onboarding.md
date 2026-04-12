# Repo Onboarding

## What the target repo should provide

Every repo handed to Symphony should have durable guidance that a worker can read cold.

At minimum, the target repo's `AGENTS.md` should cover:

- how to run the project
- how to test and lint it
- important coding patterns or conventions
- high-risk areas or sharp edges
- secret, credential, and sensitive-data handling rules
- any repository-specific workflows workers must follow

## What belongs in repo-local guidance

Put repo-specific instructions in the target repo:

- exact build and test commands
- architecture or folder conventions
- secrets handling rules
- what data must never be copied into Linear, workflow files, or learnings logs
- service boundaries
- high-risk files and stateful operations
- stable learnings that workers should know before they start
- stable workspace anchor paths that prove the checkout is correct

## What stays in this starter skill

Keep cross-repo operating guidance here:

- how the orchestrator, Symphony, and Linear interact
- how issue bodies should be structured
- how to bootstrap a workflow
- common recovery tactics
- how to run the self-improvement loop

## Recommended target repo artifact layout

After bootstrapping, the target repo should gain:

```text
.orchestration/
  <workflow-name>.WORKFLOW.md
  RUNBOOK.md
  LEARNINGS.md
  <workflow-name>.BRIEF.md
  LINEAR_ISSUE_TEMPLATE.md
  AGENTS_ADDITIONS.md
```

The operator should review those files, then merge the needed parts into the repo's durable guidance.

## Promotion loop

Use `.orchestration/LEARNINGS.md` as the short-term journal for run outcomes. Promote stable repo-specific rules into `AGENTS.md`, repeated operator steps into `RUNBOOK.md`, and shared cross-repo patterns back into this starter skill.
