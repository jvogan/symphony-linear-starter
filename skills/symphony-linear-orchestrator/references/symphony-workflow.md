# Symphony Workflow

## Workflow anatomy

A useful Symphony workflow has two parts:

1. YAML frontmatter for tracker, workspace, hooks, agent limits, and worker command
2. Worker instructions that tell the agent how to behave on each issue

## Recommended defaults

- Pin the worker model in the workflow.
- Start with `max_concurrent_agents: 1`.
- Use `workspace-write` or the narrowest sandbox that still lets workers do the job.
- Require the worker to read repo guidance before changing files.
- Require a final status comment before the worker changes issue state.

## Robust clone pattern

Use a defensive `after_create` hook instead of assuming `git clone ... .` will always behave cleanly:

```bash
rm -rf ./* ./.[!.]* 2>/dev/null || true
git clone --depth 1 <clone-url> . || {
  git clone --depth 1 <clone-url> repo
  shopt -s dotglob && mv repo/* repo/.git . 2>/dev/null
  rm -rf repo
}
```

## Manual orchestrator closeout

This starter assumes manual orchestrator review as the default closeout path:

- worker validates the issue
- worker posts a final comment
- worker moves the issue to `In Review`
- orchestrator reviews and integrates the output
- orchestrator decides whether to move the issue to `Done`

Do not default to automatic PR creation, snapshot promotion, or machine-specific background hooks in the first version of a public starter.

## Pinned model guidance

Pick a model deliberately and pin it in the workflow. Do not leave worker model selection implicit. The starter templates assume a strong coding model with medium reasoning effort, but you should adjust the pinned model to match the actual repo and cost tolerance.

