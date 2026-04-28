# Symphony Workflow

## Workflow anatomy

A useful Symphony workflow has two parts:

1. YAML frontmatter for tracker, routing filters, workspace assertions, guardrails, hooks, agent limits, and worker command
2. Worker instructions that tell the agent how to behave on each issue

## Recommended defaults

- Pin the worker model in the workflow.
- Declare `campaign.mode`, `campaign.routing_label`, `campaign.trust`, and `campaign.integration_owner` so closeout behavior is explicit.
- Start with `max_concurrent_agents: 1` for the first real run.
- Scale out only after the repo has a clean baseline, the first wave is bounded, and the review loop is actually keeping up.
- Use `workspace-write` or the narrowest sandbox that still lets workers do the job.
- Use `shell_environment_policy.include_only` for worker commands. Add only the environment variables the worker actually needs.
- Require the worker to read repo guidance before changing files.
- Require a final status comment before the worker changes issue state.
- Plan the first wave to keep all active slots busy without creating touched-area overlap.
- Add `workspace.assertions.required_branch` plus `workspace.assertions.required_paths` so a bad checkout fails fast.
- Add `guardrails.no_progress` so obviously stuck runs can be requeued instead of burning tokens indefinitely.
- Use `tracker.issue_filters.labels` when multiple workflows share one Linear project.
- Treat everyone who can create or edit routed Linear issues as part of the trusted execution boundary.

## Lane defaults

Use explicit lanes instead of one implicit model for everything:

- `sym:small` -> `gpt-5.4-mini` with `medium` reasoning
- `sym:medium` -> `gpt-5.4-mini` with `high` reasoning
- `sym:large` -> `gpt-5.4` with `high` reasoning
- `sym:content` -> `gpt-5.4-mini` with `medium` reasoning

If you are only standing up one workflow, default to the `sym:medium` lane and keep the issue graph conservative.

## Bootstrap contract

Use a defensive `after_create` hook, then assert that the workspace looks like the intended repo before the worker does real exploration.

The minimum bootstrap contract is:

- `.git` exists in the workspace root
- the checked out branch matches `workspace.assertions.required_branch`
- every `workspace.assertions.required_paths` entry exists
- each declared touched area has a nearest existing parent inside the workspace

## Closeout contract

The starter template uses this explicit campaign metadata:

```yaml
campaign:
  mode: orchestrator-review
  routing_label: "sym:medium"
  trust: trusted-operators
  integration_owner: orchestrator
```

This is human- and preflight-readable metadata. It keeps the worker prompt, Linear routing label, and operator responsibility aligned. If you change the closeout mode, update the worker prompt in the same edit.

The defensive clone pattern is still useful:

```bash
rm -rf ./* ./.[!.]* 2>/dev/null || true
git clone --depth 1 <clone-url> . || {
  git clone --depth 1 <clone-url> repo
  bash -c 'shopt -s dotglob && mv repo/* repo/.git .' 2>/dev/null
  rm -rf repo
}
```

But the clone pattern alone is not enough. The orchestrator should prefer fast bootstrap failure and requeue over letting a worker reason against the wrong filesystem for a full run.

## No-progress guardrails

Treat no-progress stop-loss as a first-class workflow concern.

- Measure progress against the active workspace, not only the final promoted snapshot or final comment.
- Consider tracked diffs, untracked files under declared touched areas, and real file modifications as progress.
- Ignore orchestration noise such as temporary lockfiles or bookkeeping files.
- When a run hits the no-progress threshold, requeue it with a short operator note and suppress immediate redispatch until the issue or workflow has materially changed.

## Manual orchestrator closeout

This starter assumes manual orchestrator review as the default closeout path:

- worker validates the issue
- worker posts a final comment
- worker moves the issue to `In Review`
- orchestrator reviews and integrates the output
- orchestrator decides whether to move the issue to `Done`
- orchestrator records any reusable learning in `LEARNINGS.md` and updates `RUNBOOK.md` or workflow defaults if the same pattern should be repeated

In practice, fast runs depend on tight integration loops. Review active workspaces every few minutes, integrate validated output as soon as it is usable, and move completed issues forward quickly so blocked work can start.

Fast teams also close the loop after each wave. If a retry exposed a clone problem, a ticket was too large, or review kept catching the same gap, update the runbook or workflow immediately instead of carrying the same operational debt into the next run.

Do not default to automatic PR creation, snapshot promotion, or machine-specific background hooks in the first version of a public starter.

If you later add snapshot promotion, keep it single-worker or prove that touched areas cannot overlap. A broad `rsync --delete` style promotion hook can delete another worker's output when concurrent workspaces diverge.

## Pinned model guidance

Pick a model deliberately and pin it in the workflow. Do not leave worker model selection implicit. The starter templates assume an explicit lane, but you should still adjust the pinned model, reasoning level, and concurrency to match the actual repo and cost tolerance.
