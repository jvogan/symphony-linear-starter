# Self-Improvement Loop

## Goal

This starter should get better after each real run. The orchestrator is responsible for turning repeated lessons into durable guidance instead of leaving them in chat threads or Linear comments.

## Durable artifacts

- `.orchestration/RUNBOOK.md`: the current operator procedure for this repo
- `.orchestration/LEARNINGS.md`: raw observations, incidents, and candidate improvements from recent runs
- `AGENTS.md`: stable repo-specific worker guidance that should be readable cold
- `.orchestration/LINEAR_ISSUE_TEMPLATE.md`: the current issue-shaping contract
- `.orchestration/<workflow-name>.WORKFLOW.md`: the current runtime defaults

## After every execution wave

1. Review everything in `In Review` and integrate or reject it.
2. Record new observations in `LEARNINGS.md` if the wave exposed a repeated failure mode, review bottleneck, issue-shaping problem, or useful tactic.
3. Update `RUNBOOK.md` with any operator checklist item or recovery step that should happen again next time.
4. Promote stable learnings:
   - repo-specific worker instruction -> `AGENTS.md`
   - issue-shaping improvement -> `LINEAR_ISSUE_TEMPLATE.md`
   - runtime or clone improvement -> workflow file
   - cross-repo operating pattern -> shared skill references
5. Mark promoted learnings clearly so the learnings log stays useful instead of becoming a graveyard.

## Promotion triggers

Promote a learning when:

- the same failure happened twice
- the same review correction was repeated across issues
- the same validation command had to be added more than once
- the same operator reminder keeps showing up in chat or comments
- a workaround proved reliable enough to become normal procedure

## What belongs where

- `LEARNINGS.md`: fresh observations, incident notes, and candidate changes
- `RUNBOOK.md`: the repeatable operator checklist and repo-specific recovery moves
- `AGENTS.md`: stable worker-facing rules and repo conventions
- workflow file: runtime defaults, hooks, worker behavior
- issue template: better issue structure, validation expectations, and scope control

## Avoid

- do not dump full chat transcripts into learnings
- do not keep stable guidance only in `LEARNINGS.md`
- do not update `AGENTS.md` with one-off incidents that are still unproven
- do not let the runbook drift from what the operator actually does
